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
# neo4j, OpenTelemetry, …). `collect_all` pulls data/hidden imports
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
    # Agent runtime (LOCUS-361): Deep Agents on LangChain/LangGraph 1.x. Deep
    # Agents loads its harness profiles and middleware dynamically, and its hard
    # dependencies (Anthropic/Google integrations, LangSmith) import lazily.
    "deepagents",
    "langchain",
    "langchain_anthropic",
    "langchain_google_genai",
    "langsmith",
    "fastapi",
    "uvicorn",
    "psycopg",
    "neo4j",
    "pydantic",
    "yaml",
    # Agent browser (computer use, LOCUS-346): collect_all ships playwright's
    # Node driver (playwright/driver: node + package/cli.js) as data, which the
    # frozen backend runs to launch Chromium and first-run uses to install it
    # (locus_tooling.desktop_firstrun.ensure_playwright_chromium). Chromium
    # itself is NOT bundled; it lands in <app_home>/playwright on first run.
    "playwright",
    # User browser (LOCUS-350): registrable sites from tldextract's bundled
    # Public Suffix List snapshot (data file), no network fetch.
    "tldextract",
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
# Biscuit capability grants (LOCUS-334; biscuit-python, Apache-2.0): a compiled
# extension module imported by locus_runtime.grants.
hiddenimports += ["biscuit_auth"]
# Playwright's sync API runs on greenlet (compiled) and pyee; the browser module
# is imported lazily by the computer-use wiring.
hiddenimports += [
    "greenlet",
    "pyee",
    "playwright.sync_api",
    "playwright._impl._driver",
    "locus_runtime.computer_use.browser",
    # User browser (LOCUS-350): the frozen backend is also the native-messaging
    # host (desktop_main dispatches on the browser's launch arguments).
    "locus_runtime.computer_use.user_browser.driver",
    "locus_runtime.computer_use.user_browser.native_host",
]
# AI observability (LOCUS-375; OpenTelemetry, Apache-2.0): the API finds its
# context implementation and the SDK its resource detectors through entry
# points, so their dist-info metadata must ship; the OTLP/HTTP exporter is
# imported lazily, only when an external exporter is enabled.
hiddenimports += [
    "opentelemetry.context.contextvars_context",
    "opentelemetry.sdk.resources",
    "opentelemetry.exporter.otlp.proto.http.trace_exporter",
]
for _dist in ("opentelemetry-api", "opentelemetry-sdk"):
    datas += copy_metadata(_dist)
# Agent runtime (LOCUS-361): the deep-agents runtime refuses a stack whose
# installed versions differ from the audited pins (importlib.metadata), so the
# dist-info of every audited package must ship; langchain-core and langsmith
# also read their own versions at import.
hiddenimports += [
    "locus_runtime.harness.deep_agents.runtime",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.memory",
]
for _dist in (
    "deepagents",
    "langchain",
    "langchain-core",
    "langgraph",
    "langgraph-checkpoint",
    "langgraph-checkpoint-sqlite",
    "langsmith",
):
    datas += copy_metadata(_dist)
# The CI version stamp (written before this build) is imported dynamically.
hiddenimports += ["locus_tooling._build_stamp"]
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

# The gateway's Rego policies (deployable modules only, not policies/tests/).
# locus_runtime.policy_engine resolves them at <_MEIPASS>/policies when frozen;
# without them the bundled OPA (locus-opa, a Tauri externalBin) has nothing to
# evaluate and every decision denies. `--self-check` starts the engine on them.
_REGO = sorted((_ROOT / "policies").glob("*.rego"))
if not _REGO:
    raise SystemExit("no policies/*.rego to bundle")
for _rego in _REGO:
    datas.append((str(_rego), "policies"))

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
