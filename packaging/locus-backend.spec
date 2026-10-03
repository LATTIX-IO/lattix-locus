# PyInstaller spec for the Lattix Locus desktop backend sidecar.
#
# Builds a single `locus-backend` executable from the desktop supervisor
# entrypoint (locus_tooling/desktop_main.py). Tauri spawns this as its
# `externalBin` sidecar; it brings up every native service then blocks.
#
# Build:  pyinstaller packaging/locus-backend.spec
# Output: dist/locus-backend(.exe)
#
# NOTE: the backend has a large dependency graph (FastAPI, LangGraph, psycopg,
# neo4j, structlog, OpenTelemetry, …). `collect_all` pulls data/hidden imports
# for the packages most likely to be missed; expand `_DYNAMIC_PKGS` as build
# warnings surface missing modules. This spec is a vetted starting point, not a
# guaranteed one-shot build — it must be exercised on each target OS in CI.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

_ROOT = Path(SPECPATH).resolve().parent  # repo root (packaging/ is one level down)
_ENTRY = _ROOT / "locus_tooling" / "desktop_main.py"

# Make the backend package importable when frozen.
sys.path.insert(0, str(_ROOT / "apps" / "backend"))
sys.path.insert(0, str(_ROOT))

_DYNAMIC_PKGS = [
    "app",            # apps/backend/app — the FastAPI control plane
    "locus_runtime",
    "locus_tooling",
    "langgraph",
    "langchain_core",
    "fastapi",
    "uvicorn",
    "psycopg",
    "neo4j",
    "structlog",
    "pydantic",
    "yaml",
]

datas, binaries, hiddenimports = [], [], []
for pkg in _DYNAMIC_PKGS:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        # Optional/absent package — keep going; build warnings will flag gaps.
        hiddenimports += collect_submodules(pkg) if pkg in {"app", "locus_runtime"} else []

# OS keychain for native secrets (LOCUS-315; keyring is MIT). keyring discovers
# its backends through the "keyring.backends" entry points, so the dist-info
# metadata must ship too, and every platform backend is imported dynamically.
hiddenimports += [
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.macOS.api",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
    "keyring.backends.kwallet",
    "keyring.backends.chainer",
    "keyring.backends.fail",
    "keyring.backends.null",
    "jaraco.classes",
    "jaraco.context",
    "jaraco.functools",
]
datas += copy_metadata("keyring")
# Platform-specific backend dependencies (absent on other OSes — skip quietly).
for _pkg in ("win32ctypes", "secretstorage", "jeepney"):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

# Ship the seed agents + workflows so they auto-seed (published, with inlined
# prompts and full graphs) on first launch — no manual import needed. The backend
# resolves these under _MEIPASS via _repository_root() when frozen.
for _sub in ("agents", "workflows"):
    _src = _ROOT / "examples" / _sub
    if _src.is_dir():
        datas.append((str(_src), f"examples/{_sub}"))

block_cipher = None

a = Analysis(
    [str(_ENTRY)],
    pathex=[str(_ROOT), str(_ROOT / "apps" / "backend")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="locus-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep stdout/stderr so Tauri can surface backend logs
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,  # signing is done by the Tauri bundler, not here
    entitlements_file=None,
)
