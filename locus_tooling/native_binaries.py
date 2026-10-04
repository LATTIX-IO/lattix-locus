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
    # dir layout: write launcher shims into bin_dir. The agent toolchain turns this
    # off: its binaries are invoked by absolute path / PATH inside the sandbox, and a
    # .cmd shim would need cmd.exe there.
    write_shims: bool = True


# Versions mirror the docker-compose images so native == hosted parity. Overridable.
def _ver(env_key: str, default: str) -> str:
    return str(os.getenv(env_key) or "").strip() or default


def _nats_spec(os_name: str, arch: str) -> BinarySpec:
    v = _ver("LOCUS_NATS_VERSION", "2.11.0")
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
    v = _ver("LOCUS_CADDY_VERSION", "2.8.4")
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
    v = _ver("LOCUS_POSTGRES_VERSION", "16.4.0")
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


def _opa_spec(os_name: str, arch: str) -> BinarySpec:
    # The policy engine the gateway needs (it fails closed without one). Version
    # and per-platform sha256 are pinned in opa_release, shared with the desktop
    # workflows that bundle the same build; no env override (unpinned = unverified).
    from .opa_release import OpaReleaseError, asset_for

    try:
        asset = asset_for(os_name, arch)
    except OpaReleaseError as exc:
        raise UnsupportedPlatformError(str(exc)) from exc
    return BinarySpec(
        name="opa",
        exe="opa" + _exe_suffix(os_name),
        kind="auto",
        url=asset.url,
        archive="raw",
        sha256=asset.sha256,
    )


# World-models live in Postgres (relational graph) — no Neo4j, no Java/JRE.
_BUILDERS: dict[str, Callable[[str, str], BinarySpec]] = {
    "nats-server": _nats_spec,
    "caddy": _caddy_spec,
    "ollama": _ollama_spec,
    "postgres": _postgres_spec,
    "opa": _opa_spec,
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
    if not spec.write_shims:
        return _resolve_in_tree(root, spec.member_rel)
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
# OPA is the gateway's policy engine; without it every model and tool call is
# denied (fail closed). The desktop bundle ships its own (opa_release), so first
# run skips it there.
DEFAULT_TARGETS = ("nats-server", "ollama", "postgres", "opa")


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


# --------------------------------------------------------------------------- #
# Windows agent toolchain (LOCUS-333)
# --------------------------------------------------------------------------- #
# Inside the Windows AppContainer only binaries readable by ALL APPLICATION
# PACKAGES run (cmd, git, ...): the user's Python, Git-bash and WSL bash are not
# reachable. Locus therefore ships its own small toolchain into a Locus-owned
# directory (``<app_home>/toolchain``) and grants read+execute on that directory
# only, to the Locus AppContainer SID (see ``locus_runtime.win_toolchain``).
#
# Pinned artifacts (P28 provenance: official URL + sha256; P29 licence):
# * CPython "embeddable package" -- python.org, PSF-2.0. The sha256 values are the
#   ones python.org publishes for the release files (downloads API ``sha256_sum``).
# * BusyBox-w64 -- frippery.org (Ron Yorston), GPL-2.0-only. Fetched as a separate,
#   unmodified binary and run as a separate program (mere aggregation); source at
#   https://frippery.org/busybox/. The sha256 values are from frippery.org's SHA256SUM.
# Bumping a version means updating the version, every per-arch sha256 and the docs
# (docs/SANDBOXING.md, "Windows agent toolchain"). There is deliberately no env
# override: an unpinned toolchain would bypass verification.
TOOLCHAIN_DIRNAME = "toolchain"
TOOLCHAIN_MARKER = ".locus-toolchain"  # proves the directory was created by Locus
INSTALL_STAMP = ".locus-installed"  # written last: the component is complete

PYTHON_EMBED_VERSION = "3.14.8"
_PYTHON_EMBED_SHA256 = {
    "amd64": "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310",
    "arm64": "155be84ccb57c6331cf0e39001c78a1dfac3be62f403f3ff5f2e29b80dda7ebe",
}

BUSYBOX_BUILD = "FRP-6075-g169694ebd"
# amd64: the UTF-8 ("u") build (Windows 10 1903+); the arm64 build is UTF-8 already.
_BUSYBOX_ASSETS = {
    "amd64": (
        f"busybox-w64u-{BUSYBOX_BUILD}.exe",
        "6e263d154d8548d1eb936f65d1d8312c80df31c45974e48d6335e4dcc0f4f34c",
    ),
    "arm64": (
        f"busybox-w64a-{BUSYBOX_BUILD}.exe",
        "e67f873d19d58c535cc9f0c4965ffd622e19b7bab87e3da89cb2185fb54464d7",
    ),
}

BUSYBOX_SUBDIR = "busybox"
BUSYBOX_EXE = "busybox.exe"


def python_embed_subdir(version: str = PYTHON_EMBED_VERSION) -> str:
    return f"python-{version}"


def _python_tag(version: str) -> str:
    major, minor = version.split(".")[:2]
    return f"python{major}{minor}"


def toolchain_dir(app_home: Path) -> Path:
    return Path(app_home) / TOOLCHAIN_DIRNAME


def _require_windows(name: str, os_name: str, arch: str) -> None:
    if os_name != "windows":
        raise UnsupportedPlatformError(f"'{name}' is part of the Windows agent toolchain only")
    if arch not in {"amd64", "arm64"}:
        raise UnsupportedPlatformError(f"unsupported arch '{arch}' for '{name}'")


def _python_embed_spec(os_name: str, arch: str) -> BinarySpec:
    _require_windows("python-embed", os_name, arch)
    v = PYTHON_EMBED_VERSION
    return BinarySpec(
        name="python-embed",
        exe="python.exe",
        kind="auto",
        url=f"https://www.python.org/ftp/python/{v}/python-{v}-embed-{arch}.zip",
        archive="zip",
        sha256=_PYTHON_EMBED_SHA256[arch],
        layout="dir",
        install_subdir=python_embed_subdir(v),
        member_rel="python.exe",
        write_shims=False,
        note="CPython embeddable package (PSF-2.0)",
    )


def _busybox_spec(os_name: str, arch: str) -> BinarySpec:
    _require_windows("busybox", os_name, arch)
    asset, digest = _BUSYBOX_ASSETS[arch]
    return BinarySpec(
        name="busybox",
        exe=BUSYBOX_EXE,
        kind="auto",
        url=f"https://frippery.org/files/busybox/{asset}",
        archive="raw",
        sha256=digest,
        layout="single",
        note="BusyBox-w64 (GPL-2.0-only), unmodified upstream binary",
    )


_TOOLCHAIN_BUILDERS: dict[str, Callable[[str, str], BinarySpec]] = {
    "python-embed": _python_embed_spec,
    "busybox": _busybox_spec,
}
TOOLCHAIN_COMPONENTS = tuple(_TOOLCHAIN_BUILDERS)


def resolve_toolchain_spec(name: str, os_name: str, arch: str) -> BinarySpec:
    builder = _TOOLCHAIN_BUILDERS.get(name)
    if builder is None:
        raise UnsupportedPlatformError(f"no toolchain spec for '{name}'")
    return builder(os_name, arch)


def toolchain_component_dir(root: Path, spec: BinarySpec) -> Path:
    return Path(root) / (spec.install_subdir if spec.layout == "dir" else BUSYBOX_SUBDIR)


# The embeddable package runs isolated via its ._pth file: no registry PythonPath,
# no user site-packages, no PYTHON* host variables -- the toolchain behaves the same
# on every host. ``import site`` is enabled only so the sitecustomize below runs.
_SITECUSTOMIZE = '''"""Locus agent toolchain: normal sys.path[0] / PYTHONPATH behaviour.

The embeddable CPython runs isolated because of its ._pth file, which also drops
the script directory (or the current directory for -c, -m and stdin) from
sys.path and ignores PYTHONPATH. That breaks ``python script.py`` and
``python -m pkg`` in an agent workspace, so re-add exactly what a regular CPython
adds. The sandbox never passes the host PYTHONPATH; only the agent's own shell
can set it. Written by Locus (locus_tooling.native_binaries); do not edit.
"""

import os
import sys


def _locus_safe_path_requested():
    args = sys.orig_argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-c", "-m", "-") or not arg.startswith("-"):
            return False
        if arg.startswith("--"):
            i += 1
            continue
        letters = arg[1:]
        if letters in ("W", "X"):
            i += 2
            continue
        if letters[:1] in ("W", "X"):
            i += 1
            continue
        if "P" in letters or "I" in letters:
            return True
        i += 1
    return False


def _locus_fix_path():
    if _locus_safe_path_requested():
        return
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 in ("", "-c", "-"):
        path0 = ""
    elif argv0 == "-m":
        path0 = os.getcwd()
    elif os.path.isfile(argv0) and not argv0.lower().endswith((".zip", ".pyz")):
        path0 = os.path.dirname(os.path.abspath(argv0))
    else:
        path0 = None  # directories / zipapps: CPython already inserted them
    extra = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    head = ([path0] if path0 is not None else []) + extra
    for entry in reversed(head):
        if entry not in sys.path:
            sys.path.insert(0, entry)


_locus_fix_path()
del _locus_fix_path, _locus_safe_path_requested
'''


def configure_embedded_python(python_dir: Path, version: str = PYTHON_EMBED_VERSION) -> None:
    """Rewrite the embeddable package's ``._pth`` (stdlib zip + home + ``import site``)
    and add the Locus ``sitecustomize.py``. Idempotent."""
    tag = _python_tag(version)
    home = Path(python_dir)
    (home / f"{tag}._pth").write_text(f"{tag}.zip\n.\nimport site\n", encoding="utf-8")
    (home / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")


def _stamp_text(spec: BinarySpec) -> str:
    return f"{spec.url} {spec.sha256}"


def toolchain_component_installed(root: Path, spec: BinarySpec) -> bool:
    """The component's install stamp matches the pinned url + sha256."""
    stamp = toolchain_component_dir(root, spec) / INSTALL_STAMP
    try:
        return stamp.read_text(encoding="utf-8").strip() == _stamp_text(spec)
    except OSError:
        return False


def ensure_toolchain_marker(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    marker = root / TOOLCHAIN_MARKER
    if not marker.is_file():
        marker.write_text(
            "Locus-managed Windows agent toolchain. Read+execute is granted to the Locus "
            "AppContainer SID only. Safe to delete; Locus fetches it again.\n",
            encoding="utf-8",
        )


GrantFn = Callable[[Path], None]


def _default_toolchain_grant(root: Path) -> None:
    from locus_runtime.win_toolchain import grant_toolchain_access

    grant_toolchain_access(root)


@dataclass
class ToolchainReport:
    root: str = ""
    installed: dict[str, str] = field(default_factory=dict)
    present: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)
    granted: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed


def provision_toolchain(
    app_home: Path,
    *,
    os_name: str | None = None,
    arch: str | None = None,
    download: DownloadFn = _default_download,
    extract: ExtractFn = _default_extract,
    verify: VerifyFn = _default_verify,
    grant: GrantFn | None = _default_toolchain_grant,
) -> ToolchainReport:
    """Fetch (first run / on demand) the Windows agent toolchain into
    ``<app_home>/toolchain`` and grant the Locus AppContainer read+execute on it.

    Every artifact is sha256-verified before extraction; a mismatch fails closed
    (nothing is extracted, the component is reported failed). Components whose
    install stamp matches the pinned url + sha256 are left alone, so re-running is
    cheap. ``grant`` (default: ``win_toolchain.grant_toolchain_access``) runs only
    when every component is present; it is idempotent.
    """
    detected_os, detected_arch = current_platform()
    os_name = os_name or detected_os
    arch = arch or detected_arch
    root = toolchain_dir(app_home)
    report = ToolchainReport(root=str(root))
    try:
        specs = [resolve_toolchain_spec(name, os_name, arch) for name in TOOLCHAIN_COMPONENTS]
    except UnsupportedPlatformError as exc:
        report.failed["toolchain"] = str(exc)
        return report
    ensure_toolchain_marker(root)
    for spec in specs:
        component = toolchain_component_dir(root, spec)
        if toolchain_component_installed(root, spec):
            report.present[spec.name] = str(component)
            continue
        if not spec.sha256:  # never install an unpinned toolchain component
            report.failed[spec.name] = "sha256 not pinned"
            continue
        try:
            target = root if spec.layout == "dir" else component
            installed = fetch_and_install(
                spec, target, download=download, extract=extract, verify=verify
            )
            if spec.name == "python-embed":
                configure_embedded_python(component)
            (component / INSTALL_STAMP).write_text(_stamp_text(spec) + "\n", encoding="utf-8")
            report.installed[spec.name] = str(installed)
        except Exception as exc:  # noqa: BLE001 - reported; the toolchain stays unusable
            report.failed[spec.name] = str(exc)
    if report.ok and grant is not None:
        try:
            grant(root)
            report.granted = True
        except Exception as exc:  # noqa: BLE001 - fail closed: reported, toolchain unusable
            report.failed["grant"] = str(exc)
    return report
