"""The pinned OPA release (policy engine) the desktop bundles and native installs
fetch: pins, fail-closed download verification, version probe, and discovery of
the bundled binary before the backend starts."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from locus_runtime import policy_engine
from locus_tooling import desktop, desktop_firstrun, opa_release
from locus_tooling import native_binaries as nb

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


# --------------------------------------------------------------------------- #
# Pins
# --------------------------------------------------------------------------- #
def test_pins_are_the_repo_wide_opa_version_with_a_sha256_per_platform() -> None:
    assert opa_release.OPA_VERSION == "0.68.0"
    # The same version CI's policy job and docker-compose use.
    assert "downloads/v0.68.0/opa_linux_amd64_static" in (WORKFLOWS / "ci.yml").read_text(
        encoding="utf-8"
    )
    assert "openpolicyagent/opa:0.68.0-static" in (ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    for key, asset in opa_release.ASSETS.items():
        assert len(asset.sha256) == 64 and int(asset.sha256, 16) >= 0, key
        assert asset.url == (
            "https://github.com/open-policy-agent/opa/releases/download/v0.68.0/" + asset.name
        )
    # Every desktop target has a pinned build.
    for triple in (
        "x86_64-pc-windows-msvc",
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu",
    ):
        assert opa_release.asset_for_triple(triple).sha256


def test_every_desktop_workflow_target_has_a_pinned_opa() -> None:
    for name in ("desktop-dev.yml", "desktop-release.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for triple in opa_release.TRIPLES:
            if f'"triple": "{triple}"' in text:
                assert opa_release.asset_for_triple(triple)


def test_unknown_targets_fail_closed() -> None:
    with pytest.raises(opa_release.OpaReleaseError):
        opa_release.asset_for_triple("riscv64gc-unknown-linux-gnu")
    with pytest.raises(opa_release.OpaReleaseError):
        opa_release.asset_for("windows", "arm64")


# --------------------------------------------------------------------------- #
# Fetch: verify before anything lands at the destination
# --------------------------------------------------------------------------- #
def _asset_for(payload: bytes) -> opa_release.OpaAsset:
    return opa_release.OpaAsset("opa_test", hashlib.sha256(payload).hexdigest())


def test_fetch_writes_the_binary_only_after_the_digest_matches(tmp_path: Path) -> None:
    payload = b"opa-binary"
    asset = _asset_for(payload)
    urls: list[str] = []

    def download(url: str, dest: Path) -> None:
        urls.append(url)
        dest.write_bytes(payload)

    dest = tmp_path / "out" / "locus-opa"
    assert opa_release.fetch(asset, dest, download=download) == dest
    assert dest.read_bytes() == payload
    assert urls == [asset.url]
    assert not any(p.name.endswith(".download") for p in dest.parent.iterdir())


def test_fetch_refuses_a_digest_mismatch_and_leaves_nothing(tmp_path: Path) -> None:
    asset = _asset_for(b"the real thing")
    dest = tmp_path / "locus-opa"

    def download(url: str, target: Path) -> None:  # noqa: ARG001
        target.write_bytes(b"tampered")

    with pytest.raises(opa_release.OpaReleaseError, match="sha256 mismatch"):
        opa_release.fetch(asset, dest, download=download)
    assert list(tmp_path.iterdir()) == []


def test_fetch_wraps_download_errors(tmp_path: Path) -> None:
    def download(url: str, target: Path) -> None:  # noqa: ARG001
        raise OSError("network down")

    with pytest.raises(opa_release.OpaReleaseError, match="could not download"):
        opa_release.fetch(_asset_for(b"x"), tmp_path / "opa", download=download)


def test_cli_fetch_fails_for_an_unpinned_target(tmp_path: Path, capsys) -> None:
    rc = opa_release.main(["fetch", "--triple", "sparc-sun-solaris", "--dest", str(tmp_path / "o")])
    assert rc == 1
    assert "no pinned OPA" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Probe: `opa version`
# --------------------------------------------------------------------------- #
_VERSION_OUT = "Version: 0.68.0\nBuild Commit: abc\nGo Version: go1.22\n"


def _runner(stdout: str, returncode: int = 0):  # noqa: ANN202
    def run(argv):  # noqa: ANN001, ANN202
        assert list(argv)[1:] == ["version"]
        return subprocess.CompletedProcess(list(argv), returncode, stdout=stdout, stderr="")

    return run


def test_probe_accepts_the_pinned_version(tmp_path: Path) -> None:
    binary = tmp_path / "opa"
    binary.write_bytes(b"x")
    result = opa_release.probe(binary, run=_runner(_VERSION_OUT))
    assert result.ok and result.version == "0.68.0"


def test_probe_rejects_missing_wrong_version_and_failures(tmp_path: Path) -> None:
    assert not opa_release.probe(None).ok
    assert opa_release.probe(tmp_path / "absent").detail == "OPA binary not found"
    binary = tmp_path / "opa"
    binary.write_bytes(b"x")
    wrong = opa_release.probe(binary, run=_runner("Version: 0.60.0\n"))
    assert not wrong.ok and "not the pinned 0.68.0" in wrong.detail
    # Any version is accepted when none is expected (a source checkout).
    assert opa_release.probe(binary, expected_version=None, run=_runner("Version: 0.60.0\n")).ok
    assert not opa_release.probe(binary, run=_runner(_VERSION_OUT, returncode=2)).ok
    assert not opa_release.probe(binary, run=_runner("garbage")).ok

    def raising(argv):  # noqa: ANN001, ANN202
        raise OSError("exec format error")

    assert "could not run OPA" in opa_release.probe(binary, run=raising).detail


# --------------------------------------------------------------------------- #
# Discovery: the desktop supervisor points the backend at the bundled OPA
# --------------------------------------------------------------------------- #
def _frozen_at(monkeypatch: pytest.MonkeyPatch, exe_dir: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "locus-backend.exe"))


def test_bundled_opa_sits_beside_the_frozen_sidecar(tmp_path: Path, monkeypatch) -> None:
    assert desktop.bundled_opa_binary() is None  # a checkout has no bundle
    _frozen_at(monkeypatch, tmp_path)
    expected = tmp_path.resolve() / opa_release.bundled_binary_name()
    assert desktop.bundled_opa_binary() == expected
    assert opa_release.bundled_binary_name(windows=True) == "locus-opa.exe"
    assert opa_release.bundled_binary_name(windows=False) == "locus-opa"


def test_configure_bundled_opa_sets_locus_opa_bin(tmp_path: Path, monkeypatch) -> None:
    _frozen_at(monkeypatch, tmp_path)
    bundled = tmp_path.resolve() / opa_release.bundled_binary_name()
    env: dict[str, str] = {}
    assert desktop.configure_bundled_opa(env) is None  # not shipped: nothing set
    assert "LOCUS_OPA_BIN" not in env
    bundled.write_bytes(b"x")
    assert desktop.configure_bundled_opa(env) == bundled
    assert env["LOCUS_OPA_BIN"] == str(bundled)
    # The policy engine then finds exactly that binary.
    monkeypatch.setenv("LOCUS_OPA_BIN", env["LOCUS_OPA_BIN"])
    assert policy_engine.find_opa_binary() == str(bundled)


def test_configure_bundled_opa_keeps_an_explicit_existing_binary(
    tmp_path: Path, monkeypatch
) -> None:
    _frozen_at(monkeypatch, tmp_path)
    (tmp_path / opa_release.bundled_binary_name()).write_bytes(b"x")
    mine = tmp_path / "my-opa"
    mine.write_bytes(b"x")
    env = {"LOCUS_OPA_BIN": str(mine)}
    assert desktop.configure_bundled_opa(env) == mine
    assert env["LOCUS_OPA_BIN"] == str(mine)
    # A stale explicit path does not hide the bundled binary.
    env = {"LOCUS_OPA_BIN": str(tmp_path / "gone")}
    assert (
        desktop.configure_bundled_opa(env) == tmp_path.resolve() / opa_release.bundled_binary_name()
    )


def test_supervisor_configures_opa_before_starting_the_backend() -> None:
    source = (ROOT / "locus_tooling" / "desktop_main.py").read_text(encoding="utf-8")
    main = source[source.index("def main()") :]
    assert main.index("configure_bundled_opa()") < main.index("run_desktop_supervisor()")
    assert "Policy engine missing" in main


def test_frozen_bundle_ships_the_policies() -> None:
    spec = (ROOT / "packaging" / "locus-backend.spec").read_text(encoding="utf-8")
    assert '(_ROOT / "policies").glob("*.rego")' in spec
    assert 'datas.append((str(_rego), "policies"))' in spec


# --------------------------------------------------------------------------- #
# Native fallback: the pinned OPA is a default first-run target
# --------------------------------------------------------------------------- #
def test_native_binaries_fetch_the_pinned_opa() -> None:
    assert "opa" in nb.DEFAULT_TARGETS
    spec = nb.resolve_spec("opa", "windows", "amd64")
    assert spec.kind == "auto" and spec.archive == "raw" and spec.exe == "opa.exe"
    assert spec.sha256 == opa_release.ASSETS[("windows", "amd64")].sha256
    assert spec.url == opa_release.ASSETS[("windows", "amd64")].url
    assert nb.resolve_spec("opa", "linux", "arm64").exe == "opa"
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_spec("opa", "windows", "arm64")


def test_native_provision_verifies_the_opa_digest(tmp_path: Path) -> None:
    def download(url: str, dest: Path) -> None:  # noqa: ARG001
        dest.write_bytes(b"not opa")

    report = nb.provision(
        ["opa"],
        tmp_path,
        os_name="linux",
        arch="amd64",
        which=lambda names, bin_dir: None,
        download=download,
    )
    assert "opa" in report.failed and "sha256 mismatch" in report.failed["opa"]
    assert not (tmp_path / "opa").exists()


def test_first_run_skips_opa_when_the_bundle_ships_it(tmp_path: Path, monkeypatch) -> None:
    seen: list[list[str]] = []

    def provision(targets: list[str], bin_dir: Path) -> nb.ProvisionReport:  # noqa: ARG001
        seen.append(list(targets))
        return nb.ProvisionReport()

    monkeypatch.delenv("LOCUS_OPA_BIN", raising=False)
    desktop_firstrun.ensure_sidecars(
        tmp_path / "bin", model=None, progress=lambda _m: None, provision=provision
    )
    bundled = tmp_path / "locus-opa"
    bundled.write_bytes(b"x")
    monkeypatch.setenv("LOCUS_OPA_BIN", str(bundled))
    desktop_firstrun.ensure_sidecars(
        tmp_path / "bin", model=None, progress=lambda _m: None, provision=provision
    )
    assert "opa" in seen[0]
    assert "opa" not in seen[1] and set(seen[1]) == set(nb.DEFAULT_TARGETS) - {"opa"}


# --------------------------------------------------------------------------- #
# Workflows: every desktop build bundles the verified OPA and self-checks it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["desktop-dev.yml", "desktop-release.yml"])
def test_desktop_workflows_bundle_the_verified_opa(name: str) -> None:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    steps = data["jobs"]["build"]["steps"]
    sidecar = next(s for s in steps if s.get("name") == "Build backend sidecar")["run"]
    fetch = sidecar.index("python -m locus_tooling.opa_release fetch")
    assert '--dest "dist/locus-opa${{ matrix.ext }}"' in sidecar
    # Fetched (and verified) before the self-check that runs it.
    self_check = sidecar.index('"dist/locus-backend${{ matrix.ext }}" --self-check')
    assert sidecar.index("pyinstaller packaging") < fetch < self_check
    assert (
        '"apps/desktop-tauri/src-tauri/sidecars/locus-opa-${{ matrix.triple }}${{ matrix.ext }}"'
        in sidecar
    )
