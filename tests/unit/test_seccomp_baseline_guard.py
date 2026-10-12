"""LOCUS-433 increment 23: seccomp syscall-deny integrity before broker admission."""
from __future__ import annotations

import json
import subprocess

import pytest

from locus_runtime import sandbox as sandbox_module
from locus_runtime.pod_reservations import AdmissionDenied
from tests.unit.test_pod_sandbox_broker import cmd, docker, fixture


def _configured_profile(tmp_path, monkeypatch, raw: bytes) -> None:
    root = tmp_path / "trusted-seccomp"
    root.mkdir(exist_ok=True)
    profile = root / "seccomp-strict.json"
    profile.write_bytes(raw)
    monkeypatch.setattr(sandbox_module, "SANDBOX_SECURITY_ROOT", root)
    monkeypatch.setenv("LOCUS_SECCOMP_PROFILE", str(profile))


def _baseline() -> dict:
    return json.loads(sandbox_module.DEFAULT_SECCOMP_PROFILE_PATH.read_text())


def _changed(mutation: str) -> bytes:
    profile = _baseline()
    if mutation == "missing-required":
        profile["syscalls"][0]["names"].remove("ptrace")
    elif mutation == "allow-protected":
        profile["syscalls"][0]["action"] = "SCMP_ACT_ALLOW"
    elif mutation == "additional-allow":
        profile["syscalls"].append({"names": ["ptrace"], "action": "SCMP_ACT_ALLOW"})
    elif mutation == "conditional-arch":
        profile["syscalls"][0]["includes"] = {"arches": ["SCMP_ARCH_X86"]}
    elif mutation == "conditional-arguments":
        profile["syscalls"][0]["args"] = [{"index": 0, "value": 1, "op": "SCMP_CMP_EQ"}]
    elif mutation == "invalid-errno":
        profile["syscalls"][0]["errnoRet"] = 0
    elif mutation == "invalid-default":
        profile["defaultAction"] = "SCMP_ACT_TRACE"
    elif mutation == "missing-rules":
        profile["syscalls"] = []
    elif mutation == "bad-name":
        profile["syscalls"][0]["names"].append(None)
    else:
        raise AssertionError(f"unknown test mutation: {mutation}")
    return json.dumps(profile, sort_keys=True).encode("utf-8")


@pytest.mark.parametrize("mutation", [
    "missing-required", "allow-protected", "additional-allow",
    "conditional-arch", "conditional-arguments", "invalid-errno",
    "invalid-default", "missing-rules", "bad-name",
])
def test_weakened_seccomp_rules_denied_before_gateway_receipt_or_docker(
    tmp_path, monkeypatch, mutation,
):
    _configured_profile(tmp_path, monkeypatch, _changed(mutation))
    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker seccomp baseline denied"):
        app.execute(lease, cmd(tmp_path, key="seccomp-rejected"))
    assert invocations == []
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM pod_action_receipt",
        ).fetchone()[0] == 0


@pytest.mark.parametrize("raw", [
    b"{not-json}",
    b"[]",
    b'{"defaultAction":"SCMP_ACT_ALLOW","syscalls":[],"syscalls":[]}',
    b"{" + b" " * 65536 + b"}",
])
def test_invalid_or_oversized_seccomp_document_fails_closed(
    tmp_path, monkeypatch, raw,
):
    _configured_profile(tmp_path, monkeypatch, raw)
    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    with pytest.raises(AdmissionDenied, match="Docker seccomp baseline denied"):
        app.execute(lease, cmd(tmp_path, key="invalid-seccomp"))
    assert not invocations
    with receipts.reservations._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM pod_action_receipt",
        ).fetchone()[0] == 0


def test_packaged_seccomp_profile_satisfies_required_deny_baseline(
    tmp_path, monkeypatch,
):
    _configured_profile(
        tmp_path, monkeypatch,
        sandbox_module.DEFAULT_SECCOMP_PROFILE_PATH.read_bytes(),
    )
    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    result = app.execute(lease, cmd(tmp_path, key="seccomp-valid"))
    assert result.receipt.state == "succeeded"
    assert len(invocations) == 1
    argv, kwargs = invocations[0]
    assert any(x.startswith("--security-opt=seccomp=") for x in argv)
    assert "--network=none" in argv
    assert "--cap-drop=ALL" in argv
    assert kwargs["shell"] is False
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL


def test_additional_unconditional_denial_is_compatible(tmp_path, monkeypatch):
    profile = _baseline()
    profile["syscalls"].append({"names": ["chroot"], "action": "SCMP_ACT_ERRNO"})
    _configured_profile(
        tmp_path, monkeypatch,
        json.dumps(profile).encode("utf-8"),
    )
    invocations = docker(monkeypatch)
    app, receipts, lease, _ = fixture(tmp_path)
    assert app.execute(lease, cmd(tmp_path, key="seccomp-stronger")).receipt.state == "succeeded"
    assert len(invocations) == 1
