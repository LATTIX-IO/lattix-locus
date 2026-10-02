# Desktop Installer — Workstream Status

Status of the `desktop-installer` branch: shipping xFrontier as a native,
Dockerless desktop app (MSI/EXE, .dmg, .deb/.AppImage) that runs the multi-agent
backend locally with OS-native sandboxing, while keeping the containerized
`hosted` profile intact for SaaS.

> Living document. Update as gaps close. Last reviewed against `HEAD` on the
> `desktop-installer` branch.

## Thesis

"Installed, not containerized." Collapse the Docker Compose stack into a native
install whose isolation boundary is the OS (bwrap / seatbelt / AppContainer),
not a container runtime — without losing the multi-agent architecture or strong
confinement of agent tool execution.

## Implemented (verified)

- **Native shell** — Tauri v2 app at `apps/desktop-tauri/`: system tray,
  hide-to-tray, one-click GitHub-Releases updater, splash with first-run
  progress, deterministic process-tree teardown on quit.
- **Frozen backend sidecar** — `frontier-backend` (PyInstaller,
  `packaging/frontier-backend.spec`), entry `frontier_tooling/desktop_main.py` →
  `frontier_tooling/desktop.py`. The exe *is* the backend (serves uvicorn
  in-process) and supervises native sidecars.
- **De-containerization** — `frontier_tooling/native_launcher.py` replaces
  `docker compose up` with a pure-planner + process supervisor. Per service:
  - Postgres+pgvector → native binaries (fetched first-run)
  - Neo4j (Java) → **dropped**; world graph runs on Postgres (`PostgresWorldGraph`)
  - Vault → `frontier_tooling/native_secrets.py` (OS-backed)
  - Casdoor/OIDC → native local-password auth + bootstrap operator
  - Caddy/Envoy/Squid → backend serves direct + CORS (no proxy)
  - NATS / Ollama → native binaries; Redis / OPA optional
  - state store → SQLite fast-boot; Postgres/long-term memory attaches later
- **`local-native` profile** — the dual-path switch
  (`FRONTIER_RUNTIME_PROFILE=local-native`); `hosted`/Docker path untouched.
- **Agent tool-execution isolation on all three OSes** — `frontier_runtime/sandbox.py`:
  `KERNEL_BWRAP` (Linux), `KERNEL_SEATBELT` (macOS), `WINDOWS_APPCONTAINER`
  (Windows, via `frontier_runtime/win_sandbox.py`). No Docker required.
- **Cross-OS CI** — `.github/workflows/desktop-release.yml` matrixes
  windows/macos/ubuntu → `.msi/.exe/.dmg/.deb/.AppImage`.
- **Fail-closed Windows confinement** — `FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER`
  makes a failed AppContainer setup raise instead of silently degrading to the
  resource-only Job-Object tier (commit `84428ed`). Documented in
  `docs/SANDBOXING.md`.
- **Windows isolation CI validation** — `ci.yml` `windows-sandbox` job runs the
  win32-gated AppContainer + Job-Object launch-path smoke tests on a
  `windows-latest` runner (commit `47146d4`). Previously these ran nowhere
  (backend tests are ubuntu-only).

## Ships vs gated

The frozen desktop bundle runs agents **in-process** via the harness
(`desktop_config` sets `enable_agents=False` / `manage_backend=False` — a frozen
exe cannot spawn `python -m uvicorn`). Tool execution is still OS-sandboxed
(`FRONTIER_SANDBOX_AGENTS=1` → `LocalSandboxExecutor`).

The full **multi-process signed-A2A confined agent roster** (research/code/review
over NATS) runs via `lattix native-up` (source/server install), not the shipped
bundle. Both topologies are real; the strongest is the `native-up` path.

## Residual gaps

| # | Gap | Status | Needs |
|---|-----|--------|-------|
| 1 | AppContainer e2e validation | launch paths now validated in CI (`windows-sandbox` job, commit `47146d4`); adversarial escape-testing still outstanding | a hostile-exec test harness on the Windows runner |
| 2 | `icacls` grant cleanup — grants accumulate on a fixed AppContainer SID (`com.lattix.xfrontier.agent`), never revoked | open | Windows validation (revoke risks locking the agent out of its worktree) |
| 3 | `_run_with_job_object` fail-open — continues unconfined if `AssignProcessToJobObject` fails | open | Windows validation (tightening may break legit nested-job: CI/containers) |
| 4 | Signing / notarization — `HAS_WIN_CERT` conditional; macOS notarization absent | open | external certs in CI secrets |
| 5 | First-run download weight — Postgres/NATS/Ollama binaries + ~13 GB model on first launch | open | UX/infra decision (bundle vs fetch) |
| 6 | Long-term memory deferred on desktop — first session pins SQLite, `FRONTIER_MEMORY_ENABLE_LONG_TERM=false`; pgvector/world-graph light up on a later launch | by design | confirm acceptable, or warm-provision |

## Security controls (Windows confinement)

| Variable | Default | Effect |
|----------|---------|--------|
| `FRONTIER_WIN_SANDBOX_TIER` | `appcontainer` | Tier: `appcontainer` (capability-confined) or `job` (resource-only baseline) |
| `FRONTIER_WIN_SANDBOX_REQUIRE_APPCONTAINER` | unset | Fail closed if AppContainer can't be established (no silent downgrade to Job tier). Set for the hostile-code threat model. **Do not default on until gap #1 is validated** — it would turn working installs into hard failures |
| `FRONTIER_FORCE_WINDOWS_APPCONTAINER` | unset | Force the AppContainer strategy outside the `local-native` profile |
| `FRONTIER_ALLOW_RESTRICTED_PROCESS_SANDBOX` | unset | Permit the last-resort no-isolation fallback (off → fails closed) |

## Threat model note

For a local single-user install, the boundary that matters is: agent code can't
escape its workspace, wreck the host, or exfiltrate beyond an allowlist — which
the OS sandboxes (bwrap/seatbelt/AppContainer) provide. The microVM/enclave-grade
isolation is reserved for the multi-tenant `hosted` profile, which keeps the
container/gVisor path. Do not carry the hosted threat model onto the desktop.
