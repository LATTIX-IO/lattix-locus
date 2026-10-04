# Lattix Locus — Desktop Shell (Tauri v2)

A thin, auditable desktop wrapper. It spawns **one** backend sidecar — the
packaged native supervisor — which brings up every local service (Postgres +
pgvector, **Neo4j world models**, NATS, Ollama, the confined agents, the FastAPI
backend, and the Next.js frontend) with **no Docker**, then opens a webview at
the local UI. The heavy lifting stays in Python (`locus_tooling`), so the Rust
layer is just a window + lifecycle manager.

```
Tauri shell  ──spawns──▶  locus-backend (PyInstaller)  ──native_launcher──▶  Postgres / Neo4j / NATS / Ollama / agents / backend / frontend
     │                                                                                          │
     └───────────────── webview navigates to http://127.0.0.1:3000 once /healthz is green ──────┘
```

## Layout

| Path | Purpose |
| --- | --- |
| `src-tauri/tauri.conf.json` | Bundle targets, `externalBin` sidecar, signing + updater config |
| `src-tauri/src/main.rs` | Spawn the sidecar, wait for `/healthz`, navigate to the UI |
| `src-tauri/capabilities/default.json` | The bundled loading page (local origin): `core:default`, sidecar spawn, panic-hotkey status |
| `src-tauri/capabilities/desktop-ui.json` | The UI at `http://127.0.0.1:3000` (a **remote** origin to Tauri): exactly the app commands it invokes, event listen/unlisten and the app version |
| `src-tauri/build.rs` | App ACL manifest: one `allow-<command>` permission per command in `generate_handler!` |
| `src-tauri/loading/index.html` | Splash shown while services start |
| `../../packaging/locus-backend.spec` | PyInstaller spec for the backend sidecar |
| `locus_tooling/desktop_main.py` | The sidecar entrypoint (runs the supervisor in the foreground) |

## Prerequisites (not installable on the dev box used so far)

- **Rust** toolchain + Tauri v2 CLI (`cargo install tauri-cli --version "^2"`).
- **PyInstaller** (`pip install pyinstaller`) to build the backend sidecar.
- **Node** (to produce the Next.js standalone build the supervisor serves).
- For signed releases: a **Windows code-signing cert** (Authenticode) and an
  **Apple Developer ID** + notarization credentials.

## Build

```bash
# 1. Backend sidecar  →  dist/locus-backend(.exe)
pyinstaller packaging/locus-backend.spec

# 2. Place it where Tauri expects externalBin, with the target-triple suffix:
#    e.g. apps/desktop-tauri/src-tauri/bin/locus-backend-x86_64-pc-windows-msvc.exe
#    (Tauri appends the triple; copy/rename accordingly per target.)

# 3. The policy engine (required: the gateway denies everything without it).
#    The pinned OPA release, sha256-verified, beside the backend for the
#    self-check, then as the externalBin `sidecars/locus-opa-<triple>(.exe)`:
python -m locus_tooling.opa_release fetch --triple x86_64-pc-windows-msvc --dest dist/locus-opa.exe
dist/locus-backend.exe --self-check   # fails unless OPA runs over the bundled policies
#    copy to apps/desktop-tauri/src-tauri/sidecars/locus-opa-x86_64-pc-windows-msvc.exe

# 4. Vendor the sidecar binaries the supervisor needs (nats/caddy/ollama/...):
python -m locus_tooling.cli native-fetch        # → app-home/bin (dev)
#    For a self-contained bundle, copy these into src-tauri/bin/ as resources.

# 5. Build the desktop app
cd apps/desktop-tauri/src-tauri
cargo tauri build       # produces MSI/NSIS (Win), .dmg/.app (mac), .deb/AppImage (Linux)
```

## IPC permissions (Tauri ACL)

Once the backend is healthy the shell navigates the window to the UI served by
the bundled Next server at `http://127.0.0.1:3000`. Tauri treats that as a
**remote** origin and rejects every IPC call from it that no capability covers,
app commands included (`Command confirm_browser_tier not allowed by ACL`).
`build.rs` therefore declares an app manifest (`tauri_build::AppManifest`) that
generates `allow-<command>` for each command in `generate_handler!`, and
`capabilities/desktop-ui.json` grants exactly those to the main window on that
one origin, plus `core:event:allow-listen`/`allow-unlisten` and
`core:app:allow-version`. No dialog, fs or other plugin permission: native
confirmations are shown from Rust. Adding a command means adding it to both the
manifest and the capability; `tests/backend/test_desktop_packaging.py` fails
otherwise.

## Policy engine (OPA)

The gateway evaluates `policies/*.rego` with OPA and denies every model call,
tool call and computer-use action when it cannot (fail closed). The bundle ships
the pinned OPA release (`locus_tooling/opa_release.py`: version and one sha256
per platform) as the externalBin `locus-opa`, installed beside the backend
sidecar; the supervisor sets `LOCUS_OPA_BIN` to it before the backend starts, and
the Rego policies ship inside the sidecar (`packaging/locus-backend.spec`). If it
is missing anyway, chat shows "Policy engine missing: reinstall Lattix Locus".

## Code signing

- **Windows (Authenticode):** set `bundle.windows.certificateThumbprint` in
  `tauri.conf.json` (or the `TAURI_SIGNING_*` env) to your cert thumbprint; the
  NSIS/MSI bundler signs the installer. `timestampUrl` is preconfigured.
- **macOS (notarization):** set `bundle.macOS.signingIdentity` to your Developer
  ID Application identity and provide notarization creds via env
  (`APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`); `hardenedRuntime` is on.

## Auto-update: Dev and Stable channels (LOCUS-349, D-26)

Code: `src-tauri/src/updates.rs` (channel setting, background checks, Dev
auto-install, version handshake) and the sidebar panel
`apps/frontend/src/components/navigation/platform-update-panel.tsx`. Commands:
`get_update_status`, `set_update_channel` (`"dev"` or `"stable"` only),
`check_for_update`, `install_update_and_restart`; events `update-status`,
`update-available`, `backend-version-mismatch`.

* **Stable** (default): a banner "Update available: Update & Restart"; installs
  on click.
* **Dev**: downloads by itself, waits until the backend reports no active run
  and holds the loop (`POST /system/update/prepare`), stops the sidecar,
  installs, restarts.

The updater only ever uses the two compiled-in URLs
(`releases/download/channel-{dev,stable}/latest.json`) and verifies every update
against `plugins.updater.pubkey`. Updater bundles are built only by
`.github/workflows/desktop-dev.yml` when the `TAURI_SIGNING_PRIVATE_KEY` secret
exists; without it no update metadata is published and the check just reports
an error (no banner). Key generation and setup: `docs/INSTALLER.md`, "Principal
setup: the updater signing key". A local `scripts/build-desktop.ps1` build makes
no updater bundles and carries no backend version stamp, so its version check is
skipped with a warning.

## Icons

Tauri needs an icon set (`.ico`/`.icns`/png) under `src-tauri/icons/`. Generate
them once from a single square source PNG (≥1024×1024):

```bash
cargo tauri icon path/to/lattix-logo.png   # writes src-tauri/icons/*
```

The build won't bundle without these; they're intentionally not committed as
placeholders (a real logo source is required).

## Releasing a signed build (CI runbook)

The installers are produced by `.github/workflows/desktop-release.yml` — they are
**not** built on a dev machine. To cut a signed release:

1. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `WINDOWS_PFX_BASE64` — your Authenticode cert (`base64 -w0 cert.pfx`)
   - `WINDOWS_PFX_PASSWORD` — the PFX password
   - `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — the
     updater minisign key, used by the Dev channel workflow only (see
     `docs/INSTALLER.md` for generation and the pubkey swap).
   - (later) `APPLE_CERTIFICATE` / `APPLE_ID` / `APPLE_PASSWORD` / `APPLE_TEAM_ID`
     to enable macOS notarization.
2. **Generate + commit icons** (above).
3. **Host the pgvector artifacts** once via `.github/workflows/pgvector-build.yml`
   so first-run fetch can install the extension (else vector search degrades to
   keyword; the relational world-graph is unaffected).
4. **Tag the release:** `git tag v0.1.0 && git push origin v0.1.0` (or run the
   workflow manually). The matrix builds Windows/macOS/Linux, signs the Windows
   `.msi`/`.exe`, and uploads all installers as artifacts/release assets.
5. **Verify Windows:** download the `.msi`, `signtool verify /pa <file>`, install
   on a clean VM, launch → first-run fetch → working multi-agent run.

## Status / what's environment-gated

The Python integration layer (`locus_tooling/desktop.py`, `desktop_main.py`,
the supervisor `serve()` loop, and the `native-serve` CLI) is implemented and
unit-tested. The Rust shell, PyInstaller build, icon assets, code-signing, and
notarization require the toolchains/certs above and a per-OS CI matrix — they are
**not** exercisable on the constrained dev box and must be validated in CI.
