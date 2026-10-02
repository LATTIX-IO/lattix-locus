# Lattix xFrontier — Desktop Shell (Tauri v2)

A thin, auditable desktop wrapper. It spawns **one** backend sidecar — the
packaged native supervisor — which brings up every local service (Postgres +
pgvector, **Neo4j world models**, NATS, Ollama, the confined agents, the FastAPI
backend, and the Next.js frontend) with **no Docker**, then opens a webview at
the local UI. The heavy lifting stays in Python (`frontier_tooling`), so the Rust
layer is just a window + lifecycle manager.

```
Tauri shell  ──spawns──▶  frontier-backend (PyInstaller)  ──native_launcher──▶  Postgres / Neo4j / NATS / Ollama / agents / backend / frontend
     │                                                                                          │
     └───────────────── webview navigates to http://127.0.0.1:3000 once /healthz is green ──────┘
```

## Layout

| Path | Purpose |
| --- | --- |
| `src-tauri/tauri.conf.json` | Bundle targets, `externalBin` sidecar, signing + updater config |
| `src-tauri/src/main.rs` | Spawn the sidecar, wait for `/healthz`, navigate to the UI |
| `src-tauri/capabilities/default.json` | v2 permissions (spawn sidecar, navigate, updater) |
| `src-tauri/loading/index.html` | Splash shown while services start |
| `../../packaging/frontier-backend.spec` | PyInstaller spec for the backend sidecar |
| `frontier_tooling/desktop_main.py` | The sidecar entrypoint (runs the supervisor in the foreground) |

## Prerequisites (not installable on the dev box used so far)

- **Rust** toolchain + Tauri v2 CLI (`cargo install tauri-cli --version "^2"`).
- **PyInstaller** (`pip install pyinstaller`) to build the backend sidecar.
- **Node** (to produce the Next.js standalone build the supervisor serves).
- For signed releases: a **Windows code-signing cert** (Authenticode) and an
  **Apple Developer ID** + notarization credentials.

## Build

```bash
# 1. Backend sidecar  →  dist/frontier-backend(.exe)
pyinstaller packaging/frontier-backend.spec

# 2. Place it where Tauri expects externalBin, with the target-triple suffix:
#    e.g. apps/desktop-tauri/src-tauri/bin/frontier-backend-x86_64-pc-windows-msvc.exe
#    (Tauri appends the triple; copy/rename accordingly per target.)

# 3. Vendor the sidecar binaries the supervisor needs (nats/caddy/ollama/...):
python -m frontier_tooling.cli native-fetch        # → app-home/bin (dev)
#    For a self-contained bundle, copy these into src-tauri/bin/ as resources.

# 4. Build the desktop app
cd apps/desktop-tauri/src-tauri
cargo tauri build       # produces MSI/NSIS (Win), .dmg/.app (mac), .deb/AppImage (Linux)
```

## Code signing

- **Windows (Authenticode):** set `bundle.windows.certificateThumbprint` in
  `tauri.conf.json` (or the `TAURI_SIGNING_*` env) to your cert thumbprint; the
  NSIS/MSI bundler signs the installer. `timestampUrl` is preconfigured.
- **macOS (notarization):** set `bundle.macOS.signingIdentity` to your Developer
  ID Application identity and provide notarization creds via env
  (`APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID`); `hardenedRuntime` is on.

## Auto-update (one-click "Update & Restart")

UX: when an update is available the app shows a slim banner → **Update &
Restart** → a confirm ("the app will close, install, and restart") → the shell
silently downloads + installs the signed update and relaunches. Wiring:
`UpdateBanner` (frontend) calls the Rust commands `check_for_update` /
`install_update_and_restart` (`src-tauri/src/main.rs`), which use the
`tauri-plugin-updater` (`update.download_and_install()` → `app.restart()`).

The updater plugin is **always compiled in but inactive by default**, so a
keyless dev build still works (the check just errors → no banner). To enable it:

1. **Generate a signing keypair** (once):
   ```bash
   cargo tauri signer generate -w "$HOME/.tauri/lattix-updater.key"
   ```
   This prints a **public key** and writes the private key. The public key is
   safe to commit.
2. **Put the public key** in `src-tauri/updater.conf.json` → `plugins.updater.pubkey`
   (replace `REPLACE_WITH_MINISIGN_PUBLIC_KEY`) and set `endpoints` to where you
   host `latest.json`.
3. **Build with it enabled**:
   - Local: set `TAURI_SIGNING_PRIVATE_KEY` (+ `_PASSWORD`) and re-run
     `scripts/build-desktop.ps1` — it auto-applies `--config updater.conf.json`
     when both the pubkey and the signing key are present.
   - CI: `desktop-release.yml` already builds with `--config updater.conf.json`
     and the `TAURI_SIGNING_PRIVATE_KEY` secret, emitting the signed
     `latest.json` update manifest. Host it (or the GitHub release) at the
     `endpoints` URL.

Until you do this, the app simply never shows the update banner — everything
else works.

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
     updater minisign key (`cargo tauri signer generate`); put the **public** key
     in `tauri.conf.json` → `plugins.updater.pubkey`.
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

The Python integration layer (`frontier_tooling/desktop.py`, `desktop_main.py`,
the supervisor `serve()` loop, and the `native-serve` CLI) is implemented and
unit-tested. The Rust shell, PyInstaller build, icon assets, code-signing, and
notarization require the toolchains/certs above and a per-OS CI matrix — they are
**not** exercisable on the constrained dev box and must be validated in CI.
