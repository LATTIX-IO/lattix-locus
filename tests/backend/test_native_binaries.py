"""Track B: per-OS sidecar binary provisioning — manifest resolution, fetch
orchestration, skip/manual/fail reporting. Offline (injected download/extract)."""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from frontier_tooling import native_binaries as nb  # noqa: E402


# --- platform detection ------------------------------------------------------
def test_current_platform_normalizes(monkeypatch):
    monkeypatch.setattr(nb.platform, "system", lambda: "Linux")
    monkeypatch.setattr(nb.platform, "machine", lambda: "x86_64")
    assert nb.current_platform() == ("linux", "amd64")
    monkeypatch.setattr(nb.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(nb.platform, "machine", lambda: "arm64")
    assert nb.current_platform() == ("darwin", "arm64")


# --- manifest resolution -----------------------------------------------------
def test_nats_spec_linux_and_windows():
    lin = nb.resolve_spec("nats-server", "linux", "amd64")
    assert lin.kind == "auto" and lin.archive == "tar.gz"
    assert "nats-server-v" in lin.url and lin.url.endswith(".tar.gz")
    assert lin.exe == "nats-server" and lin.member == "nats-server"
    win = nb.resolve_spec("nats-server", "windows", "amd64")
    assert win.archive == "zip" and win.exe == "nats-server.exe" and win.url.endswith(".zip")


def test_caddy_spec_url_shape():
    spec = nb.resolve_spec("caddy", "darwin", "arm64")
    assert spec.kind == "auto"
    assert "caddy_" in spec.url and "_darwin_arm64" in spec.url


def test_ollama_auto_on_linux_manual_elsewhere():
    assert nb.resolve_spec("ollama", "linux", "amd64").kind == "auto"
    assert nb.resolve_spec("ollama", "windows", "amd64").kind == "manual"


def test_postgres_is_auto_dir_nested():
    pg = nb.resolve_spec("postgres", "linux", "amd64")
    assert pg.kind == "auto" and pg.layout == "dir"
    assert pg.archive == "jar" and pg.nested_glob == "*.txz" and pg.nested_archive == "tar.xz"
    assert any(name == "initdb" for name, _ in pg.extra_shims)
    assert any(name == "psql" for name, _ in pg.extra_shims)


def test_no_java_no_neo4j_specs():
    # World-models live in Postgres now; Neo4j + the JRE are gone from the manifest.
    for removed in ("neo4j", "jre"):
        with pytest.raises(nb.UnsupportedPlatformError):
            nb.resolve_spec(removed, "linux", "amd64")
    assert "neo4j" not in nb.DEFAULT_TARGETS and "jre" not in nb.DEFAULT_TARGETS


def test_unsupported_name_and_arch():
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_spec("does-not-exist", "linux", "amd64")
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_spec("nats-server", "linux", "ppc64le")


# --- extract (real archives, tiny synthetic payloads) -----------------------
def test_extract_from_tar_gz(tmp_path):
    payload = tmp_path / "nats-server"
    payload.write_bytes(b"#!/bin/sh\necho hi\n")
    arc = tmp_path / "a.tar.gz"
    with tarfile.open(arc, "w:gz") as tf:
        tf.add(payload, arcname="nats-server-v2.11.0-linux-amd64/nats-server")
    spec = nb.resolve_spec("nats-server", "linux", "amd64")
    bin_dir = tmp_path / "bin"
    out = nb._default_extract(arc, spec, bin_dir)
    assert out == bin_dir / "nats-server" and out.read_bytes().startswith(b"#!")


def test_extract_from_zip(tmp_path):
    arc = tmp_path / "a.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        zf.writestr("caddy.exe", b"MZbinary")
    spec = nb.resolve_spec("caddy", "windows", "amd64")
    bin_dir = tmp_path / "bin"
    out = nb._default_extract(arc, spec, bin_dir)
    assert out == bin_dir / "caddy.exe" and out.read_bytes() == b"MZbinary"


def test_extract_missing_member_raises(tmp_path):
    arc = tmp_path / "a.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        zf.writestr("something-else", b"x")
    spec = nb.resolve_spec("caddy", "windows", "amd64")
    with pytest.raises(ValueError):
        nb._default_extract(arc, spec, tmp_path / "bin")


# --- fetch_and_install with injected IO -------------------------------------
def test_fetch_and_install_happy_path(tmp_path):
    spec = nb.resolve_spec("nats-server", "linux", "amd64")
    bin_dir = tmp_path / "bin"
    calls = {}

    def _download(url, dest):
        calls["url"] = url
        dest.write_bytes(b"archive-bytes")

    def _extract(archive_path, s, bd):
        assert archive_path.read_bytes() == b"archive-bytes"
        bd.mkdir(parents=True, exist_ok=True)
        out = bd / s.exe
        out.write_bytes(b"binary")
        return out

    out = nb.fetch_and_install(spec, bin_dir, download=_download, extract=_extract)
    assert out == bin_dir / "nats-server" and out.read_bytes() == b"binary"
    assert calls["url"] == spec.url
    # The temp download file is cleaned up.
    assert not (bin_dir / ".nats-server.download").exists()


def test_fetch_verifies_sha_when_pinned(tmp_path):
    spec = nb.resolve_spec("nats-server", "linux", "amd64")
    spec.sha256 = "deadbeef"
    bad = {"verified": False}

    def _verify(path, expected):
        bad["verified"] = True
        raise ValueError("sha mismatch")

    with pytest.raises(ValueError):
        nb.fetch_and_install(
            spec,
            tmp_path / "bin",
            download=lambda u, d: d.write_bytes(b"x"),
            extract=lambda a, s, b: b / s.exe,
            verify=_verify,
        )
    assert bad["verified"] is True


def test_fetch_refuses_manual_spec(tmp_path):
    # ollama on Windows is a manual install (app/installer), not auto-fetchable.
    spec = nb.resolve_spec("ollama", "windows", "amd64")
    assert spec.kind == "manual"
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.fetch_and_install(spec, tmp_path / "bin")


# --- provision report --------------------------------------------------------
def test_provision_skips_present_fetches_auto_reports_manual(tmp_path):
    bin_dir = tmp_path / "bin"
    fetched: list[str] = []

    # nats already present -> skipped; postgres/neo4j auto -> fetched;
    # ollama on WINDOWS is manual -> reported (no fetch attempt).
    def _which(names, bd):
        return "/usr/bin/nats-server" if "nats-server" in names else None

    def _download(url, dest):
        dest.write_bytes(b"a")

    def _extract(archive_path, spec, bd):
        fetched.append(spec.name)
        bd.mkdir(parents=True, exist_ok=True)
        out = bd / spec.exe
        out.write_bytes(b"bin")
        return out

    report = nb.provision(
        ["nats-server", "postgres", "ollama"],
        bin_dir,
        os_name="windows",
        arch="amd64",
        which=_which,
        download=_download,
        extract=_extract,
    )
    assert "nats-server" in report.skipped
    assert "postgres" in report.installed
    assert "ollama" in report.manual
    assert set(fetched) == {"postgres"}
    # Unpinned checksum surfaces a warning.
    assert any("checksum not pinned" in w for w in report.warnings)


# --- dir layout + nested extraction + shims ---------------------------------
def test_extract_dir_writes_shim(tmp_path):
    # Plain dir layout (no nesting): ollama-linux tgz lays down bin/ollama.
    import io
    import tarfile as _tf

    arc = tmp_path / "ollama.tgz"
    payload = b"#!/bin/sh\necho ollama\n"
    with _tf.open(arc, "w:gz") as tf:
        info = _tf.TarInfo("bin/ollama")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    spec = nb.resolve_spec("ollama", "linux", "amd64")
    bin_dir = tmp_path / "bin"
    shim = nb._default_extract(arc, spec, bin_dir)
    # A shim named after the logical binary lands directly in bin_dir.
    expected = bin_dir / ("ollama.cmd" if nb.os.name == "nt" else "ollama")
    assert shim == expected and expected.exists()
    # The real binary is extracted under the install subdir.
    assert list((bin_dir / spec.install_subdir).rglob("ollama"))


def test_extract_dir_nested_jar_then_txz(tmp_path):
    # Zonky-style: a .jar (zip) containing an inner .txz with bin/postgres.
    import io
    import tarfile as _tf

    inner = tmp_path / "inner.txz"
    payload = b"PGBINARY"
    with _tf.open(inner, "w:xz") as tf:
        for rel in ("bin/postgres", "bin/initdb", "bin/psql"):
            info = _tf.TarInfo(rel)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    jar = tmp_path / "pg.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.write(inner, arcname="postgres-binaries.txz")
    spec = nb.resolve_spec("postgres", "linux", "amd64")
    bin_dir = tmp_path / "bin"
    shim = nb._default_extract(jar, spec, bin_dir)
    base = "postgres.cmd" if nb.os.name == "nt" else "postgres"
    assert shim == bin_dir / base and shim.exists()
    # extra shims for initdb + psql are produced too.
    for name in ("initdb", "psql"):
        s = bin_dir / (f"{name}.cmd" if nb.os.name == "nt" else name)
        assert s.exists()
    # the inner archive is removed after extraction.
    assert not any(bin_dir.rglob("*.txz"))


def test_provision_records_failure_without_aborting(tmp_path):
    def _which(names, bd):
        return None

    def _download(url, dest):
        raise OSError("network down")

    report = nb.provision(
        ["nats-server", "caddy"],
        tmp_path / "bin",
        os_name="linux",
        arch="amd64",
        which=_which,
        download=_download,
    )
    # Both auto fetches failed, but the batch completed and recorded both.
    assert set(report.failed) == {"nats-server", "caddy"}
