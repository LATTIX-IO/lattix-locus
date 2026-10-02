"""Per-OS sidecar binary provisioning for the native (Dockerless) install.

``build_native_plan`` discovers binaries in ``bin_dir`` (then PATH). This module
*populates* ``bin_dir``: it resolves the right download per (os, arch), fetches,
verifies (optional pinned sha256), extracts the executable, and marks it runnable.

Two classes of sidecar:
- **auto** — single static binaries with a clean per-platform release artifact
  (``nats-server``, ``caddy``, and ``ollama`` on Linux). These are fetched.
- **manual** — large multi-file distributions that need extra runtime/steps and
  are unsafe to one-shot fetch: **Postgres+pgvector** (the pgvector extension must
  be added to the PG install) and **Neo4j** (needs a JRE). For these we surface the
  official URL + the extra step so the operator installs them deliberately.

All network/FS effects go through injectable ``download``/``extract``/``verify``
callables so the logic is unit-tested without touching the network.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class UnsupportedPlatformError(RuntimeError):
    """No download is defined for this (binary, os, arch)."""


# --------------------------------------------------------------------------- #
# Platform detection
# --------------------------------------------------------------------------- #
def current_platform() -> tuple[str, str]:
    """Return normalized ``(os, arch)`` — os in {linux,darwin,windows};
    arch in {amd64,arm64}."""
    system = platform.system().lower()
    os_name = {"linux": "linux", "darwin": "darwin", "windows": "windows"}.get(system, system)
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64", "x64"}:
        arch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = machine
    return os_name, arch


def _exe_suffix(os_name: str) -> str:
    return ".exe" if os_name == "windows" else ""


# --------------------------------------------------------------------------- #
# Spec model + manifest
# --------------------------------------------------------------------------- #
@dataclass
class BinarySpec:
    name: str  # logical sidecar name (matches native_launcher discovery)
    exe: str  # filename to land in bin_dir (incl. .exe on Windows)
    kind: str  # "auto" | "manual"
    url: str = ""
    archive: str = ""  # "raw" | "zip" | "tar.gz" | "tar.xz" | "jar"
    member: str | None = None  # single layout: path of the exe within the archive
    sha256: str | None = None
    note: str = ""
    # --- multi-file "dir" layout (Neo4j, JRE, Postgres, Ollama) -------------
    layout: str = "single"  # "single" | "dir"
    install_subdir: str = ""  # dir layout: extract the whole tree under bin_dir/<this>
    member_rel: str = ""  # dir layout: path of the primary exe within the tree (basename-matched)
    extra_shims: list[tuple[str, str]] = field(default_factory=list)  # (shim_name, rel_path)
    # --- nested archive (zonky jar contains an inner .txz) ------------------
    nested_glob: str | None = None  # glob of the inner archive inside the outer
    nested_archive: str = ""  # archive type of the inner ("tar.xz")
    runtime_dep: str = ""  # informational: e.g. neo4j needs "jre"


# Versions mirror the docker-compose images so native == hosted parity. Overridable.
def _ver(env_key: str, default: str) -> str:
    return str(os.getenv(env_key) or "").strip() or default


def _nats_spec(os_name: str, arch: str) -> BinarySpec:
    v = _ver("FRONTIER_NATS_VERSION", "2.11.0")
    ext = "zip" if os_name == "windows" else "tar.gz"
    base = f"nats-server-v{v}-{os_name}-{arch}"
    suffix = _exe_suffix(os_name)
    return BinarySpec(
        name="nats-server",
        exe=f"nats-server{suffix}",
        kind="auto",
        url=f"https://github.com/nats-io/nats-server/releases/download/v{v}/{base}.{ext}",
        archive="zip" if ext == "zip" else "tar.gz",
        member=f"nats-server{suffix}",
    )


def _caddy_spec(os_name: str, arch: str) -> BinarySpec:
    v = _ver("FRONTIER_CADDY_VERSION", "2.8.4")
    ext = "zip" if os_name == "windows" else "tar.gz"
    suffix = _exe_suffix(os_name)
    return BinarySpec(
        name="caddy",
        exe=f"caddy{suffix}",
        kind="auto",
        url=f"https://github.com/caddyserver/caddy/releases/download/v{v}/caddy_{v}_{os_name}_{arch}.{ext}",
        archive="zip" if ext == "zip" else "tar.gz",
        member=f"caddy{suffix}",
    )


def _ollama_spec(os_name: str, arch: str) -> BinarySpec:
    if os_name == "linux":
        # The tgz lays down bin/ollama + lib/; keep the tree and shim bin/ollama.
        return BinarySpec(
            name="ollama",
            exe="ollama",
            kind="auto",
            url=f"https://ollama.com/download/ollama-linux-{arch}.tgz",
            archive="tar.gz",
            layout="dir",
            install_subdir="ollama-dist",  # must differ from the "ollama" shim in bin_dir
            member_rel="bin/ollama",
        )
    return BinarySpec(
        name="ollama",
        exe="ollama" + _exe_suffix(os_name),
        kind="manual",
        url="https://ollama.com/download",
        note="Install the Ollama app/installer for macOS/Windows, then ensure 'ollama' is on PATH.",
    )


def _postgres_spec(os_name: str, arch: str) -> BinarySpec:
    # Zonky embedded-postgres: a Maven jar (zip) whose payload is a .txz containing
    # a full PG install. Two-stage extract (jar → txz → tree), then shim the bins.
    v = _ver("FRONTIER_POSTGRES_VERSION", "16.4.0")
    z_os = {"linux": "linux", "darwin": "darwin", "windows": "windows"}.get(os_name, os_name)
    z_arch = {"amd64": "amd64", "arm64": "arm64v8"}.get(arch, arch)
    artifact = f"embedded-postgres-binaries-{z_os}-{z_arch}"
    suffix = _exe_suffix(os_name)
    return BinarySpec(
        name="postgres",
        exe="postgres" + suffix,
        kind="auto",
        url=(
            "https://repo1.maven.org/maven2/io/zonky/test/postgres/"
            f"{artifact}/{v}/{artifact}-{v}.jar"
        ),
        archive="jar",
        layout="dir",
        install_subdir=f"postgres-{v}",
        member_rel=f"bin/postgres{suffix}",
        extra_shims=[("initdb", f"bin/initdb{suffix}"), ("psql", f"bin/psql{suffix}")],
        nested_glob="*.txz",
        nested_archive="tar.xz",
    )


# World-models live in Postgres (relational graph) — no Neo4j, no Java/JRE.
_BUILDERS: dict[str, Callable[[str, str], BinarySpec]] = {
    "nats-server": _nats_spec,
    "caddy": _caddy_spec,
    "ollama": _ollama_spec,
    "postgres": _postgres_spec,
}


def resolve_spec(name: str, os_name: str, arch: str) -> BinarySpec:
    builder = _BUILDERS.get(name)
    if builder is None:
        raise UnsupportedPlatformError(f"no provisioning spec for '{name}'")
    if arch not in {"amd64", "arm64"}:
        raise UnsupportedPlatformError(f"unsupported arch '{arch}' for '{name}'")
    return builder(os_name, arch)


# --------------------------------------------------------------------------- #
# Default IO (injectable)
# --------------------------------------------------------------------------- #
def _default_download(url: str, dest: Path) -> None:
    from urllib.request import urlopen

    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(url) as resp, open(dest, "wb") as fh:  # noqa: S310 - pinned manifest URLs
        shutil.copyfileobj(resp, fh)


def _default_verify(path: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest.lower() != expected_sha256.lower():
        raise ValueError(f"sha256 mismatch for {path.name}: got {digest}, want {expected_sha256}")


def _make_executable(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def _default_extract(archive_path: Path, spec: BinarySpec, bin_dir: Path) -> Path:
    """Install from the archive into ``bin_dir``. Returns the primary executable
    (a real binary for single layout; a shim pointing into the tree for dir)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if spec.layout == "dir":
        return _extract_dir(archive_path, spec, bin_dir)
    return _extract_single(archive_path, spec, bin_dir)


def _extract_single(archive_path: Path, spec: BinarySpec, bin_dir: Path) -> Path:
    """Extract one member (by basename) into ``bin_dir/spec.exe``."""
    target = bin_dir / spec.exe
    want = Path(spec.member or spec.exe).name
    if spec.archive == "raw":
        shutil.move(str(archive_path), str(target))
        return target
    if spec.archive in {"zip", "jar"}:
        with zipfile.ZipFile(archive_path) as zf:
            name = _match_member(zf.namelist(), want)
            with zf.open(name) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        return target
    if spec.archive in {"tar.gz", "tar.xz"}:
        mode = "r:gz" if spec.archive == "tar.gz" else "r:xz"
        with tarfile.open(archive_path, mode) as tf:
            name = _match_member(tf.getnames(), want)
            src = tf.extractfile(tf.getmember(name))
            if src is None:
                raise ValueError(f"could not read '{name}' from {archive_path.name}")
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
        return target
    raise ValueError(f"unknown archive type '{spec.archive}'")


def _extract_dir(archive_path: Path, spec: BinarySpec, bin_dir: Path) -> Path:
    """Extract a whole distribution (optionally jar→inner-archive), then write
    shims in ``bin_dir`` so ``native_launcher._which`` finds the binaries."""
    root = bin_dir / (spec.install_subdir or spec.name)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    _extract_all(archive_path, spec.archive, root)
    if spec.nested_glob:
        inner = next(iter(sorted(root.rglob(spec.nested_glob))), None)
        if inner is None:
            raise ValueError(
                f"nested archive '{spec.nested_glob}' not found in {archive_path.name}"
            )
        _extract_all(inner, spec.nested_archive, root)
        try:
            inner.unlink()
        except OSError:
            pass
    primary: Path | None = None
    for shim_name, rel in [(_shim_base(spec.exe), spec.member_rel), *spec.extra_shims]:
        target = _resolve_in_tree(root, rel)
        _make_executable(target)
        shim = _write_shim(bin_dir, shim_name, target)
        primary = primary or shim
    if primary is None:
        raise ValueError(f"no shim produced for {spec.name}")
    return primary


def _shim_base(exe: str) -> str:
    """Logical shim name: strip a platform suffix so 'neo4j.bat'/'postgres.exe'
    become 'neo4j'/'postgres' (the .cmd shim adds its own extension on Windows)."""
    lowered = exe.lower()
    for suffix in (".exe", ".bat", ".cmd"):
        if lowered.endswith(suffix):
            return exe[: -len(suffix)]
    return exe


def _extract_all(archive_path: Path, archive_type: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive_type in {"zip", "jar"}:
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest)
        return
    if archive_type in {"tar.gz", "tar.xz"}:
        mode = "r:gz" if archive_type == "tar.gz" else "r:xz"
        with tarfile.open(archive_path, mode) as tf:
            tf.extractall(dest, filter="data")  # 3.12+ safe extraction
        return
    raise ValueError(f"cannot extract archive type '{archive_type}'")


def _resolve_in_tree(root: Path, rel: str) -> Path:
    """Find ``rel`` under ``root`` — exact path first, else by basename anywhere
    (distribution layouts often nest one extra directory level)."""
    direct = root / rel
    if direct.exists():
        return direct
    want = Path(rel).name
    for found in root.rglob(want):
        if found.is_file():
            return found
    raise ValueError(f"'{rel}' not found under {root}")


def _write_shim(bin_dir: Path, name: str, target: Path) -> Path:
    """Write a launcher shim ``bin_dir/<name>`` that execs ``target`` so the
    distribution binary is discoverable by name in bin_dir."""
    if os.name == "nt":
        shim = bin_dir / f"{name}.cmd"
        shim.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
        return shim
    shim = bin_dir / name
    shim.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
    _make_executable(shim)
    return shim


def _match_member(names: list[str], want_basename: str) -> str:
    for n in names:
        if Path(n).name == want_basename:
            return n
    raise ValueError(f"'{want_basename}' not found in archive (members: {names[:8]}…)")


DownloadFn = Callable[[str, Path], None]
ExtractFn = Callable[[Path, BinarySpec, Path], Path]
VerifyFn = Callable[[Path, str], None]
WhichFn = Callable[[list[str], "Path | None"], "str | None"]


# --------------------------------------------------------------------------- #
# Fetch + provision
# --------------------------------------------------------------------------- #
def fetch_and_install(
    spec: BinarySpec,
    bin_dir: Path,
    *,
    download: DownloadFn = _default_download,
    extract: ExtractFn = _default_extract,
    verify: VerifyFn = _default_verify,
) -> Path:
    """Download → (optional verify) → extract → chmod. Returns the installed path."""
    if spec.kind != "auto":
        raise UnsupportedPlatformError(f"'{spec.name}' is not auto-fetchable: {spec.note}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp = bin_dir / f".{spec.name}.download"
    try:
        download(spec.url, tmp)
        if spec.sha256:
            verify(tmp, spec.sha256)
        installed = extract(tmp, spec, bin_dir)
        _make_executable(installed)
        return installed
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


@dataclass
class ProvisionReport:
    installed: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)  # already on PATH / bin_dir
    manual: dict[str, str] = field(default_factory=dict)  # needs deliberate install
    failed: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _which(names: list[str], bin_dir: Path | None) -> str | None:
    suffixes = ("", ".exe", ".cmd", ".bat") if os.name == "nt" else ("",)
    if bin_dir:
        for name in names:
            for suffix in suffixes:
                candidate = bin_dir / f"{name}{suffix}"
                if candidate.exists():
                    return str(candidate)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


# Default sidecars to provision for a native install (world-models ride on Postgres).
DEFAULT_TARGETS = ("nats-server", "ollama", "postgres")


def provision(
    names: list[str],
    bin_dir: Path,
    *,
    os_name: str | None = None,
    arch: str | None = None,
    which: WhichFn = _which,
    download: DownloadFn = _default_download,
    extract: ExtractFn = _default_extract,
    verify: VerifyFn = _default_verify,
) -> ProvisionReport:
    """Provision the named sidecars into ``bin_dir`` for the current platform.

    Already-present binaries (PATH or bin_dir) are skipped. ``auto`` specs are
    fetched; ``manual`` specs are reported with their official URL + extra steps.
    """
    detected_os, detected_arch = current_platform()
    os_name = os_name or detected_os
    arch = arch or detected_arch
    report = ProvisionReport()
    for name in names:
        try:
            spec = resolve_spec(name, os_name, arch)
        except UnsupportedPlatformError as exc:
            report.failed[name] = str(exc)
            continue
        if which([spec.exe, name], bin_dir):
            report.skipped[name] = "already available"
            continue
        if spec.kind == "manual":
            report.manual[name] = f"{spec.url} — {spec.note}"
            continue
        try:
            path = fetch_and_install(
                spec, bin_dir, download=download, extract=extract, verify=verify
            )
            report.installed[name] = str(path)
            if not spec.sha256:
                report.warnings.append(
                    f"{name}: checksum not pinned; verify integrity before production use."
                )
        except Exception as exc:  # noqa: BLE001 - record, don't abort the batch
            report.failed[name] = str(exc)
    return report
