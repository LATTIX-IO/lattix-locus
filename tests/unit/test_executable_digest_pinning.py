"""LOCUS-433: bounded executable digest pinning without a second execution sink."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from locus_runtime import pod_tool_broker as catalog_module
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.pod_tool_broker import BrokerTool, SandboxedPodToolBroker
from tests.unit.test_pod_tool_broker import setup


def _pinned(tmp_path, monkeypatch):
    _, receipts, lease, _, registry, invocations = setup(tmp_path, monkeypatch)
    item = registry["fixed_tool"]
    binary = Path(item.argv[0])
    expected = hashlib.sha256(binary.read_bytes()).hexdigest()
    tool = BrokerTool(item.name, item.argv, item.cwd, item.max_seconds, expected)
    return (SandboxedPodToolBroker(receipts, {"fixed_tool": tool}),
            receipts, lease, invocations, binary, tool)


def _count_receipts(receipts) -> int:
    with receipts.reservations._connect() as db:
        return db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0]


def test_digest_pinned_tool_authorizes_same_single_fenced_dispatch(tmp_path, monkeypatch):
    app, receipts, lease, invocations, _, tool = _pinned(tmp_path, monkeypatch)
    assert app._tools["fixed_tool"].executable_sha256 == tool.executable_sha256
    assert len(app.catalog_digest) == 64
    result = app.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="digest-allowed",
    )
    assert result.receipt.state == "succeeded"
    assert len(invocations) == 1
    assert invocations[0][1]["shell"] is False
    assert [event for event, _ in receipts.audit(result.receipt.action_id)] == [
        "prepared", "dispatching", "fence_guarded", "succeeded",
    ]


def test_modified_executable_is_denied_before_receipt_and_runner(tmp_path, monkeypatch):
    app, receipts, lease, invocations, binary, _ = _pinned(tmp_path, monkeypatch)
    old = binary.read_bytes()
    binary.write_bytes(b"X" * len(old))
    with pytest.raises(AdmissionDenied, match="digest mismatch"):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="replaced")
    assert not invocations
    assert _count_receipts(receipts) == 0


def test_wrong_administrator_digest_is_denied(tmp_path, monkeypatch):
    app, receipts, lease, invocations, binary, item = _pinned(tmp_path, monkeypatch)
    wrong = "0" * 64 if item.executable_sha256 != "0" * 64 else "1" * 64
    unauthorized = SandboxedPodToolBroker(receipts, {
        "fixed_tool": BrokerTool(
            item.name, item.argv, item.cwd, item.max_seconds, wrong,
        ),
    })
    assert unauthorized.catalog_digest != app.catalog_digest
    with pytest.raises(AdmissionDenied, match="digest mismatch"):
        unauthorized.execute(
            lease, tool_name="fixed_tool", action_idempotency_key="wrong-digest",
        )
    assert binary.is_file()
    assert not invocations
    assert _count_receipts(receipts) == 0


def test_digest_scope_limit_denied_before_dispatch(tmp_path, monkeypatch):
    app, receipts, lease, invocations, binary, _ = _pinned(tmp_path, monkeypatch)
    assert len(binary.read_bytes()) > 4
    monkeypatch.setattr(catalog_module, "_MAX_PINNED_EXECUTABLE_BYTES", 4)
    with pytest.raises(AdmissionDenied, match="digest scope"):
        app.execute(lease, tool_name="fixed_tool", action_idempotency_key="too-large")
    assert not invocations
    assert _count_receipts(receipts) == 0


@pytest.mark.parametrize("value", [
    "", "0" * 63, "0" * 65, "A" * 64, "x" * 64, b"0" * 64, 14,
])
def test_invalid_pinned_digest_fails_registration(value):
    with pytest.raises(ValueError, match="canonical SHA-256"):
        BrokerTool(
            "fixed_tool", ("/usr/bin/approved",), "/tmp", 10, value,
        )


def test_unpinned_legacy_catalog_preserves_existing_policy(tmp_path, monkeypatch):
    app, receipts, lease, _, mapping, invokes = setup(tmp_path, monkeypatch)
    assert mapping["fixed_tool"].executable_sha256 is None
    out = app.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="unpinned-legacy",
    )
    assert out.receipt.state == "succeeded"
    assert len(invokes) == 1
    assert _count_receipts(receipts) == 1


def test_digest_attestation_does_not_log_binary_bytes(tmp_path, monkeypatch):
    app, receipts, lease, invocations, binary, _ = _pinned(tmp_path, monkeypatch)
    secret_marker = "TOP-SECRET-MOCK-EXE"
    binary.write_bytes(secret_marker.encode("ascii"))
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    pinned = BrokerTool("fixed_tool", (str(binary), "--fixed"), str(tmp_path), 10, digest)
    verified = SandboxedPodToolBroker(receipts, {"fixed_tool": pinned})
    out = verified.execute(
        lease, tool_name="fixed_tool", action_idempotency_key="digest-privacy",
    )
    assert out.receipt.state == "succeeded"
    assert len(invocations) == 1
    assert secret_marker not in repr(verified)
    assert secret_marker not in repr(out)
    with receipts.reservations._connect() as db:
        row = db.execute(
            "SELECT fingerprint, outcome_digest FROM pod_action_receipt"
        ).fetchone()
    assert secret_marker not in repr(tuple(row))


def test_mutated_measurement_metadata_fails_closed(tmp_path, monkeypatch):
    app, receipts, lease, invocations, _, _ = _pinned(tmp_path, monkeypatch)
    original_fstat = catalog_module.os.fstat
    calls = 0

    def changing_fstat(fd):
        nonlocal calls
        stat_result = original_fstat(fd)
        calls += 1
        if calls == 2:
            from types import SimpleNamespace
            return SimpleNamespace(
                st_mode=stat_result.st_mode, st_size=stat_result.st_size,
                st_dev=stat_result.st_dev, st_ino=stat_result.st_ino,
                st_mtime_ns=stat_result.st_mtime_ns + 1,
                st_ctime_ns=stat_result.st_ctime_ns,
            )
        return stat_result

    monkeypatch.setattr(catalog_module.os, "fstat", changing_fstat)
    with pytest.raises(AdmissionDenied, match="changed during measurement"):
        app.execute(
            lease, tool_name="fixed_tool",
            action_idempotency_key="modified-during-measurement",
        )
    assert not invocations
    assert _count_receipts(receipts) == 0
