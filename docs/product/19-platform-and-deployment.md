# 19 · Platform and Deployment

## 1. Principles

1. **Desktop-native, Docker-optional.** A signed installer on Windows and macOS gives a working system with no Docker, no WSL and no admin-only services. Docker is used only when present, for the hardened container sandbox tier.
2. **Always on, UI optional.** The runtime is a per-user background service; the desktop app and browser UI are clients.
3. **One process tree per user.** No shared multi-user server on a machine; each OS user has their own instance and domain.
4. **Windows and macOS are equal.** Linux desktop is best-effort; Linux headless is supported for a home-lab "second instance" (a peer, not a server).
5. **Honest packaging.** What the installer says it installs is what runs (no deployed-but-unused components).

## 2. Runtime components

| Component | Role | Implementation |
|---|---|---|
| **locus-service** | Control plane, run manager, gateway, router, tracker, memory | Python 3.12 FastAPI app packaged with PyInstaller (carry); `main.py` split along domains first (see [20](20-roadmap-and-decisions.md)) |
| **Native helper** | Computer use, HUD, panic key, OS permissions | Signed native binary per platform (Rust, or Swift on macOS and C# on Windows); local IPC only |
| **Desktop shell** | Window, tray, updater, deep links | Tauri 2 (carry from `ae4d703`) |
| **Database** | Tasks, memory, audit, runs, boards | Embedded PostgreSQL + pgvector (carry). **Open question O-06:** SQLite + sqlite-vec for a lighter footprint |
| **Judge** | Laya System One model | ONNX Runtime in-process or a small sidecar; ≈2 GB RAM |
| **Local models** | Inference | Ollama managed or detected; other OpenAI-compatible servers detected |
| **Egress proxy** | Per-run allowlists | In-process forward proxy in the service |
| **Sandboxes** | Tool and engine isolation | seatbelt (macOS), AppContainer (Windows), bwrap (Linux), Docker when present |

Removed from the desktop profile: Envoy, OPA server, Vault server, NATS, Squid, Casdoor, Jaeger (OpenTelemetry export remains available to a local or remote collector).

## 3. Platform specifics

| Concern | Windows | macOS |
|---|---|---|
| Service | Per-user background process started at login (Task Scheduler or Startup registration) | LaunchAgent |
| Secrets | Credential Manager / DPAPI | Keychain |
| Computer use permissions | UI Automation; UIAccess for elevated windows (signed, installed in a secure location) | Accessibility + Screen Recording (TCC), prompted at first run |
| Sandbox | AppContainer (restore) | seatbelt profiles |
| Packaging | Signed MSI/MSIX | Notarized, signed DMG (currently unsigned: must fix for H1) |
| Updates | Signed delta updates; rollback to previous version | Same |

## 4. Resource budgets

Idle (no runs, UI closed): ≤ 400 MB RAM excluding local models and Laya, ≤ 1% CPU. Laya adds ≈2 GB when loaded; it loads on demand and unloads after idle. Local model memory is reported per model in Library → Engines.

## 5. Data and backup

- Data root chosen at install (default in the user profile); encrypted at rest.
- One-click encrypted backup (database + boards + audit checkpoints + settings) to a chosen folder; restore on a new device re-pairs as a new device of the same principal.
- Uninstall never deletes the data root without an explicit second confirmation.

## 6. Optional profiles

| Profile | Status | Use |
|---|---|---|
| **Desktop** (default) | Supported | The product |
| **Headless peer** | Supported (H2) | A home-lab or workstation instance with GPU models, paired as a peer; same security core |
| **Compose stack** | Developer only | Local development and integration tests |
| **Helm / hosted** | Unsupported, kept compiling | Not a product target under the P2P decision; retained only so existing CI doesn't rot. Revisit if an org deployment is requested |

## 7. Observability

Structured logs (redacted), OpenTelemetry traces and metrics exportable to a local collector; a built-in diagnostics bundle (logs, versions, posture, no content) for support. Telemetry to any external party is off and has no hidden default.

## 8. Quality gates (release blocking)

- All Python gates green (pytest, ruff, ruff format, mypy on the gated scope), frontend vitest and build, policy tests and parity tests.
- Computer-use scenario suite on Windows and macOS with zero safety escapes.
- Security CI: SAST, secret scan, SCA, SBOM, provenance check, DAST on the local API.
- Installer smoke tests on clean Windows and macOS VMs: install → first run → local chat → delegated run → uninstall.
