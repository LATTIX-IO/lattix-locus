"""The frozen-bundle self-check imports every critical module (LOCUS-342) and
proves the bundled policy engine runs (OPA): without it the gateway denies every
model and tool call, so a bundle that lacks it must fail CI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from locus_runtime import policy_engine
from locus_tooling import desktop, desktop_main, opa_release

_OK_ENGINE = {"ok": True, "binary": "opa", "version": opa_release.OPA_VERSION, "policies": 9}


def test_self_check_passes_in_a_complete_environment(capsys, monkeypatch) -> None:
    monkeypatch.setattr(desktop_main, "_check_policy_engine", lambda: dict(_OK_ENGINE))
    assert desktop_main.self_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert set(report["modules"]) == set(desktop_main._SELF_CHECK_MODULES)
    assert report["policy_engine"]["ok"] is True
    assert report["build_version"] == ""  # a source checkout carries no stamp


def test_self_check_fails_when_a_module_is_missing(capsys, monkeypatch) -> None:
    monkeypatch.setattr(desktop_main, "_check_policy_engine", lambda: dict(_OK_ENGINE))
    monkeypatch.setattr(
        desktop_main,
        "_SELF_CHECK_MODULES",
        (*desktop_main._SELF_CHECK_MODULES, "locus_missing_mod"),
    )
    assert desktop_main.self_check() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["modules"]["locus_missing_mod"].startswith("error: ModuleNotFoundError")


def test_self_check_fails_when_the_policy_engine_is_missing(capsys, monkeypatch) -> None:
    missing = {"ok": False, "binary": "", "version": "", "error": "OPA binary not found"}
    monkeypatch.setattr(desktop_main, "_check_policy_engine", lambda: missing)
    assert desktop_main.self_check() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["policy_engine"]["error"] == "OPA binary not found"


def test_self_check_fails_when_the_policy_check_raises(capsys, monkeypatch) -> None:
    def boom() -> dict[str, object]:
        raise RuntimeError("no engine")

    monkeypatch.setattr(desktop_main, "_check_policy_engine", boom)
    assert desktop_main.self_check() == 1
    assert json.loads(capsys.readouterr().out)["policy_engine"]["ok"] is False


def _frozen_at(monkeypatch: pytest.MonkeyPatch, exe_dir: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "locus-backend.exe"))


def _probe_ok(binary: object, **_: object) -> opa_release.OpaProbe:
    return opa_release.OpaProbe(True, str(binary), opa_release.OPA_VERSION)


def test_frozen_check_requires_the_bundled_opa(tmp_path, monkeypatch) -> None:
    _frozen_at(monkeypatch, tmp_path)
    # A system OPA (LOCUS_OPA_BIN, PATH) does not stand in for the bundled one.
    other = tmp_path / "elsewhere-opa"
    other.write_bytes(b"x")
    monkeypatch.setenv("LOCUS_OPA_BIN", str(other))
    report = desktop_main._check_policy_engine()
    assert report["ok"] is False
    assert report["error"] == "OPA binary not found"
    assert report["binary"] == str(tmp_path.resolve() / opa_release.bundled_binary_name())


def test_frozen_check_runs_the_bundled_opa_and_starts_the_engine(tmp_path, monkeypatch) -> None:
    _frozen_at(monkeypatch, tmp_path)
    bundled = tmp_path.resolve() / opa_release.bundled_binary_name()
    bundled.write_bytes(b"x")
    seen: dict[str, object] = {}

    def fake_probe(
        binary: object, *, expected_version: str | None = None, **_: object
    ) -> opa_release.OpaProbe:
        seen["probe"] = (str(binary), expected_version)
        return opa_release.OpaProbe(True, str(binary), opa_release.OPA_VERSION)

    class FakeEngine:
        policy_version = "sha256:test"

        def __init__(self, *, opa_binary: str, policy_dir: Path) -> None:
            seen["engine"] = (opa_binary, Path(policy_dir))

        def start(self) -> FakeEngine:
            seen["started"] = True
            return self

        def close(self) -> None:
            seen["closed"] = True

    monkeypatch.setattr(opa_release, "probe", fake_probe)
    monkeypatch.setattr(policy_engine, "OpaSidecarEngine", FakeEngine)
    report = desktop_main._check_policy_engine()
    assert report["ok"] is True, report
    # The bundled binary must report the pinned version.
    assert seen["probe"] == (str(bundled), opa_release.OPA_VERSION)
    assert seen["engine"] == (str(bundled), policy_engine.default_policy_dir())
    assert seen["started"] is True and seen["closed"] is True
    assert report["policies"] == len(policy_engine.policy_files(policy_engine.default_policy_dir()))
    assert report["policy_version"] == "sha256:test"


def test_check_fails_when_the_engine_does_not_start(tmp_path, monkeypatch) -> None:
    _frozen_at(monkeypatch, tmp_path)
    (tmp_path / opa_release.bundled_binary_name()).write_bytes(b"x")

    class BrokenEngine:
        policy_version = ""

        def __init__(self, **_: object) -> None:
            pass

        def start(self) -> BrokenEngine:
            raise RuntimeError("OPA sidecar exited during startup (rc=1): compile error")

        def close(self) -> None:
            pass

    monkeypatch.setattr(opa_release, "probe", _probe_ok)
    monkeypatch.setattr(policy_engine, "OpaSidecarEngine", BrokenEngine)
    report = desktop_main._check_policy_engine()
    assert report["ok"] is False
    assert "compile error" in str(report["error"])


def test_check_fails_when_no_policies_are_bundled(tmp_path, monkeypatch) -> None:
    _frozen_at(monkeypatch, tmp_path)
    (tmp_path / opa_release.bundled_binary_name()).write_bytes(b"x")
    empty = tmp_path / "no-policies"
    empty.mkdir()
    monkeypatch.setenv("LOCUS_POLICY_DIR", str(empty))
    monkeypatch.setattr(opa_release, "probe", _probe_ok)
    report = desktop_main._check_policy_engine()
    assert report["ok"] is False
    assert "no Rego policies bundled" in str(report["error"])


def test_check_starts_a_real_opa_when_one_is_installed() -> None:
    binary = policy_engine.find_opa_binary()
    if not binary:
        pytest.skip("no OPA binary here (CI's policy job installs one)")
    try:
        subprocess.run([binary, "version"], capture_output=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("the OPA binary found here does not run")
    assert desktop.is_frozen() is False
    report = desktop_main._check_policy_engine()
    assert report["ok"] is True, report
    assert int(str(report["policies"])) > 0
