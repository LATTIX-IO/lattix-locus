# Sandboxing

Lattix Locus implements a **three-tier hybrid sandbox** that adapts to deployment context while maintaining Codex-grade kernel-level isolation guarantees.

## Goals

- kernel-level or VM-backed isolation per host platform
- strong filesystem confinement through read-only rootfs and explicit mount allowlists
- mediated network egress with domain-level allowlisting
- real tool execution through a jail abstraction instead of direct host subprocesses
- hybrid deployment: laptop/desktop, Docker Compose, and Kubernetes from the same codebase

## Three-Tier Hybrid Model

The `SandboxManager` (in `locus_runtime/sandbox.py`) auto-detects the strongest available isolation and selects it automatically.

### Tier 1: Kernel Sandbox (Laptop/Desktop — No Docker Required)

When `bubblewrap` (Linux) or `/usr/bin/sandbox-exec` (macOS) is available, Locus uses **direct kernel-level sandboxing** with no Docker daemon:

**Linux (bubblewrap + seccomp):**
- Read-only root filesystem (`--ro-bind / /`)
- Explicit writable mounts only for allowed paths
- Sensitive subpaths re-protected even inside writable parents (`.git`, `.locus`, `.ssh`, `.gnupg`, `.aws`, `.kube`)
- PID, user, IPC, and network namespace isolation (`--unshare-*`)
- `--new-session` (prevents signal injection from parent terminal)
- `--die-with-parent` (cleanup on crash)
- Custom seccomp BPF profile (see below)

**macOS (seatbelt):**
- Generated seatbelt profile with `deny default` base
- File read allowed only to specified readable roots
- File write allowed only to specified writable roots + `/tmp`
- Network: localhost-only when `allow_network=False`, full when enabled
- Hardcoded `/usr/bin/sandbox-exec` path (prevents PATH injection)

**Windows (AppContainer + Job Object):** the `windows-appcontainer` strategy
(`locus_runtime/sandbox.py` → `locus_runtime/win_sandbox.py`) confines the
child via Win32 directly — no Docker, no WSL:
- **AppContainer (default tier):** low-privilege, capability-gated execution with
  default-deny filesystem. The bound worktree is ACL-granted to the container SID
  (`icacls`), so the agent can read/write its workspace and nothing else. Network
  capabilities (`internetClient`) are granted only when `allow_network=True`. On
  par with bwrap (Linux) / seatbelt (macOS).
- **Job Object (fallback tier):** memory limit + active-process cap + kill-on-job-close
  so a runaway/forkbomb child is bounded and dies with the launcher. Used when
  AppContainer setup fails. **Note:** this tier bounds *resources* but does NOT
  confine filesystem or network — see `LOCUS_WIN_SANDBOX_REQUIRE_APPCONTAINER`
  below to fail closed instead of silently degrading to it.

#### Windows agent toolchain (LOCUS-333)

Inside the AppContainer only binaries readable by `ALL APPLICATION PACKAGES` run
(`cmd`, `git`). The user's Python, Git-bash and WSL `bash` are unreachable, and
Locus never widens ACLs on directories it does not own. Instead Locus ships a
small toolchain into a directory it owns, `<app_home>/toolchain`
(`%LOCALAPPDATA%\Lattix\Locus\toolchain`, or `LOCUS_APP_HOME\toolchain`):

| Component | Pinned artifact | Licence |
|---|---|---|
| BusyBox-w64 (`sh` + POSIX utilities) | `busybox-w64u-FRP-6075-g169694ebd.exe` (amd64), `busybox-w64a-FRP-6075-g169694ebd.exe` (arm64), from `https://frippery.org/files/busybox/` | GPL-2.0-only |
| CPython embeddable package | `python-3.14.8-embed-{amd64,arm64}.zip` from `https://www.python.org/ftp/python/3.14.8/` | PSF-2.0 |

- **Fetch:** the desktop app fetches it on first run (`ensure_agent_toolchain`).
  To fetch it on demand, run `lattix native-fetch-toolchain`. Every artifact is
  checked against its pinned sha256 (`locus_tooling/native_binaries.py`) before
  anything is extracted. A mismatch fails closed. Re-running is a no-op while
  each component's install stamp matches its pin.
- **Grant:** the Locus AppContainer SID (profile `com.lattix.locus.agent`) gets
  read+execute on the toolchain directory, and on nothing else:
  `icacls <toolchain> /grant *<SID>:(OI)(CI)RX /T`. The grant runs at install
  time, and the launcher re-checks it before each AppContainer launch
  (`--toolchain-root`). A per-SID stamp file makes the grant idempotent. Locus
  grants only on a directory named `toolchain` that is not a reparse point and
  carries Locus's `.locus-toolchain` marker. Only a per-app AppContainer SID is
  accepted, never `ALL APPLICATION PACKAGES`. The container cannot write to the
  toolchain.
- **Use:** on Windows, `run_shell` runs `busybox.exe sh -c …`. The executor maps
  `sh`/`bash` to BusyBox and `python`/`python3` to the toolchain interpreter.
  The toolchain directories come first on the sandbox `PATH`. The mapping
  happens only *after* the gateway allowed the logical name: tool_jail
  allowlists `sh`/`python`, and a path to a toolchain binary is denied. Without
  an installed toolchain these commands fail with exit 127 and the fetch hint.
- **Python:** the embeddable package keeps its isolating `._pth` file, so it
  ignores the registry, user site-packages and host `PYTHON*` variables. The
  `._pth` file enables `import site` only so that a Locus `sitecustomize.py`
  restores normal `sys.path[0]` (script directory, or the current directory for
  `-c`/`-m`) and `PYTHONPATH` handling. Scripts, `python -m <module>` and the
  standard library therefore work in the workspace. There is no pip in the
  toolchain, and nothing is installed into the toolchain from inside the
  sandbox, which cannot write to it. `python -m pytest` works only when pytest
  is importable from the workspace (for example a vendored copy on
  `PYTHONPATH`). Known noise: each interpreter start prints
  `Failed to find real location of …python.exe` on stderr. This happens because
  `GetFinalPathNameByHandle` needs list access on ancestor directories, which
  the AppContainer deliberately lacks. The warning is harmless.
- **BusyBox quirk:** ash `exec`s the last command of `sh -c`. BusyBox-w32's
  exec emulation silently fails when the shell's parent (the launcher) is
  outside the container. The executor therefore wraps the script as
  `{ <script>\n}; exit $?`, which keeps the exit status.
- **Licences:** BusyBox is GPL-2.0-only. Locus downloads the unmodified upstream
  binary separately and runs it as a separate program (mere aggregation); the
  source is at <https://frippery.org/busybox/>. CPython is under the PSF licence
  (`LICENSE.txt` ships inside the toolchain directory).
- **Updating:** bump `PYTHON_EMBED_VERSION` / `BUSYBOX_BUILD` in
  `locus_tooling/native_binaries.py` together with every per-arch sha256. Take
  the CPython values from python.org's published `sha256_sum` for the release
  files and the BusyBox values from frippery.org's `SHA256SUM`. Then update this
  table and run `tests/unit/test_win_toolchain.py`. Finally run the real
  AppContainer test: fetch into a temp app home with
  `provision_toolchain(<home>)`, then run
  `LOCUS_TEST_TOOLCHAIN_APP_HOME=<home> pytest tests/policy/test_jail_tiers_opa.py`.
  A version change installs into a new `python-<ver>` directory. The old one can
  be deleted.

**When to use:** Local development on a laptop or desktop where Docker is not installed or too heavy. This is the fastest mode (~1ms startup on Linux/macOS).

### Tier 2: Hardened Docker (Docker Compose — Local-Secure)

When Docker is available but kernel sandbox tools are not (or when deploying via Docker Compose):

```
docker run --rm \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --read-only \
  --user=1000:1000 \
  --ipc=private \
  --security-opt=seccomp=/path/to/seccomp-strict.json \
  --network=none \
  --memory=512m \
  --cpus=1.0 \
  --pids-limit=256 \
  --tmpfs /tmp:rw,noexec,nosuid,size=100m \
  -v /workspace:/workspace:rw \
  python:3.12.10-slim-bookworm <command>
```

**Security flags:**
- `--cap-drop=ALL` — drop all Linux capabilities
- `--security-opt=no-new-privileges` — prevent privilege escalation
- `--read-only` — read-only root filesystem
- `--user=1000:1000` — non-root execution
- `--ipc=private` — IPC namespace isolation
- `--security-opt=seccomp=seccomp-strict.json` — custom seccomp profile
- `--network=none` — full network isolation when disabled
- `--memory=512m` / `--cpus=1.0` / `--pids-limit=256` — resource limits

**When to use:** Local-secure deployment via `docker-compose.yml` or any environment where Docker is the orchestration layer.

### Tier 3: K8s with gVisor/Kata (Hosted — Cloud Native)

For production Kubernetes deployments:

- **gVisor (`runsc`)**: User-space kernel intercepts all syscalls; even a kernel 0-day in the guest does not escape the sandbox. Default for `hosted` profile.
- **Kata Containers**: Lightweight VM with dedicated kernel; hardware-level isolation for regulated workloads. Optional via `sandbox.kata.enabled=true` in Helm values.

The `SandboxManager` returns pod spec metadata (RuntimeClass, securityContext, resource limits) that the workflow engine uses to create K8s Jobs/Pods.

**When to use:** Production cloud deployment via Helm chart.

## Strategy Auto-Detection

The `SandboxManager` selects strategy in this priority order:

1. **K8s mode** — if `LOCUS_RUNTIME_PROFILE=hosted` or `KUBERNETES_SERVICE_HOST` is set
2. **Kernel bubblewrap** — if `bwrap` is on PATH (Linux)
3. **Kernel seatbelt** — if `/usr/bin/sandbox-exec` exists (macOS)
4. **Windows AppContainer** — on Windows when `LOCUS_RUNTIME_PROFILE` is `local-native`/`native`, or `LOCUS_FORCE_WINDOWS_APPCONTAINER=1`
5. **Restricted process** — under `local-native`/`native` the manager is Dockerless and never falls back to a Docker daemon
6. **Hardened Docker** — if `docker` is on PATH (non-native profiles)
7. **Restricted process** — last-resort fallback with no sandbox (gated behind `LOCUS_ALLOW_RESTRICTED_PROCESS_SANDBOX`; off by default → fails closed)

Override with `SandboxManager(force_strategy=IsolationStrategy.HARDENED_DOCKER)`.

## Seccomp Profile

The custom seccomp profile at `docker/sandbox/seccomp-strict.json` blocks:

| Category | Blocked Syscalls |
|----------|-----------------|
| Debugging/tracing | `ptrace`, `process_vm_readv`, `process_vm_writev`, `kcmp` |
| io_uring (major attack surface) | `io_uring_setup`, `io_uring_enter`, `io_uring_register` |
| Kernel modules | `init_module`, `finit_module`, `delete_module` |
| System modification | `reboot`, `kexec_load`, `swapon`, `swapoff`, `acct`, `settimeofday` |
| Mount/namespace escape | `mount`, `umount2`, `pivot_root`, `unshare`, `setns` |
| Credential theft | `keyctl`, `request_key`, `add_key` |
| BPF/rootkit | `bpf`, `perf_event_open` |
| Privilege escalation | `setuid`, `setgid`, `setreuid`, `setregid`, `setresuid`, etc. |
| Raw device access | `open_by_handle_at`, `name_to_handle_at`, `quotactl` |

Violation response: `EPERM` (operation not permitted), not SIGKILL.

## Network Egress Control

When `allow_network=True`, sandboxed tools route through the Squid egress proxy (`sandbox-egress-gateway:3128`) which enforces:

- **Domain allowlist** (fail-closed): only `.openai.com`, `.anthropic.com`, `.googleapis.com`, `.github.com`, `.pypi.org`, `.npmjs.org` by default
- **Port restrictions**: only 80 (HTTP) and 443 (HTTPS)
- **Header scrubbing**: `X-Forwarded-For` stripped, `Via` header disabled
- **No caching**: `cache deny all`

Extend the allowlist in `docker/sandbox/squid.conf`.

When `allow_network=False`: `--network=none` (Docker) or `--unshare-net` (bubblewrap) provides complete network isolation at the namespace level.

## Capability Tokens

Agent tool execution is scoped by HMAC-SHA256 capability tokens:

```python
CapabilityClaims:
  agent_id: str
  allowed_tools: list[str]
  allowed_read_paths: list[str]
  allowed_write_paths: list[str]
  max_tool_calls: int
  iat: int   # Issued-at timestamp
  exp: int   # Expiration timestamp (default: 10 minutes)
```

Tokens are verified before tool execution. Expired tokens are rejected.

## Install / Autodetect

The `SandboxManager` auto-detects available sandbox backends at runtime. For local desktop deployment without Docker:

**Linux:** Install bubblewrap: `apt install bubblewrap` (Debian/Ubuntu) or `dnf install bubblewrap` (Fedora/RHEL).

**macOS:** Seatbelt is built into macOS. No installation needed.

**Windows:** No installation needed — the `windows-appcontainer` strategy uses
built-in Win32 AppContainer + Job Object APIs (active under the `local-native`
profile or via `LOCUS_FORCE_WINDOWS_APPCONTAINER=1`). WSL2-with-bubblewrap or
Docker Desktop remain optional alternatives.

## Security Model

- Read-only root filesystem by default (all tiers)
- Explicit input staging and output collection via allowed paths
- Allowlisted executable set
- Allowlisted destination hosts (domain-level via Squid)
- Custom seccomp BPF profile blocking 40+ dangerous syscalls
- Non-root execution (UID 1000)
- Resource limits (memory, CPU, PID)
- OPA-backed policy checks for filesystem, network, and jail posture
- Time-limited capability tokens with HMAC-SHA256 verification

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCUS_RUNTIME_PROFILE` | `local-lightweight` | Sandbox tier selection hint |
| `LOCUS_SECCOMP_PROFILE` | `docker/sandbox/seccomp-strict.json` | Path to custom seccomp profile |
| `SANDBOX_RUNNER_IMAGE` | `python:3.12.10-slim-bookworm` | Docker image for tool execution |
| `SANDBOX_INTERNAL_NETWORK` | `locus-sandbox-internal` | Docker network for sandbox containers |
| `SANDBOX_EGRESS_GATEWAY` | `sandbox-egress-gateway:3128` | Squid proxy address |
| `LOCUS_FORCE_WINDOWS_APPCONTAINER` | _(unset)_ | Force the Windows AppContainer strategy even outside the `local-native` profile |
| `LOCUS_WIN_SANDBOX_TIER` | `appcontainer` | Windows confinement tier: `appcontainer` (default) or `job` (resource-only baseline) |
| `LOCUS_WIN_SANDBOX_REQUIRE_APPCONTAINER` | _(unset)_ | Fail **closed** if AppContainer can't be established instead of silently degrading to the resource-only Job-Object tier. Set for the hostile-code threat model where losing filesystem/network confinement is unacceptable |
| `LOCUS_ALLOW_RESTRICTED_PROCESS_SANDBOX` | _(unset)_ | Permit the last-resort no-isolation fallback. Off by default → execution fails closed when no real sandbox backend is available |
