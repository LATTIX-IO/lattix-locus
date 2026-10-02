"""Native (Dockerless) process supervisor — the seam that replaces ``docker
compose up`` for the ``local-native`` install.

It starts xFrontier's sidecars as managed local processes (Postgres+pgvector,
Neo4j for **world models**, NATS, Ollama, optional Redis/OPA, plus the backend
and frontend), waits for each to pass a health check, and derives the env that
makes the backend's existing graceful-degrade memory pipeline light up — in
particular the Neo4j world-graph tier (``Neo4jRunGraph`` /
``FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED``).

Design notes:
- ``build_native_plan`` is PURE and fully unit-testable: it discovers binaries,
  decides which services are in/out, and computes the env + warnings together so
  the world-model env exactly tracks Neo4j availability. No process is launched.
- ``NativeSupervisor`` does the runtime (spawn / health-wait / stop) with
  injectable ``spawn`` / ``run`` / ``probe`` / ``sleep`` so tests need no real
  binaries.
- The hosted/Docker profile is untouched: this module is only reached via the
  new ``lattix native-*`` CLI commands.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .common import default_app_home, source_repo_root
from .native_secrets import ensure_secret


class NativeLauncherError(RuntimeError):
    """A required native sidecar (e.g. a missing binary) blocked startup."""


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class NativeConfig:
    app_home: Path = field(default_factory=default_app_home)
    bind_host: str = "127.0.0.1"
    # Where to find bundled binaries before falling back to PATH.
    bin_dir: Path | None = None
    projects_root: str = ""

    # Toggles — world models default ON because they're a core memory capability.
    enable_world_models: bool = True  # Neo4j world-graph projection
    enable_redis: bool = True  # short-term memory cache (WAL fallback if absent)
    enable_opa: bool = False  # policy engine (optional locally)
    enable_caddy: bool = True  # reverse proxy /api -> backend, / -> frontend
    serve_frontend: bool = True
    # Multi-process A2A agents: run apps/workers agents as confined localhost
    # subprocesses speaking signed A2A (the signed-envelope transport stays real
    # defense-in-depth). Their tool execution is confined by the Track 0
    # LocalSandboxExecutor (auto-on under local-native); we do NOT network-jail
    # the server itself because it needs loopback to accept A2A envelopes.
    enable_agents: bool = True
    agent_roster: list[tuple[str, int]] = field(
        default_factory=lambda: [("research", 8081), ("code", 8082), ("review", 8083)]
    )
    # Desktop/frozen mode: the packaged exe IS the backend and serves uvicorn
    # in-process, so the supervisor must NOT spawn a separate `python -m uvicorn`
    # backend (there's no python interpreter in a PyInstaller bundle → it would
    # re-spawn the exe → startup loop). False ⇒ no backend ServiceSpec.
    manage_backend: bool = True
    # Override where the Next.js standalone (server.js) lives. In a bundle this is
    # the staged resources dir; empty ⇒ the source checkout's .next/standalone.
    frontend_dir: str = ""
    # Desktop/first-run mode: instead of raising when a sidecar binary is absent,
    # degrade so the app still boots (Postgres missing → SQLite state; NATS
    # missing → A2A bus off / agents in-proc). The strict `native-up` path leaves
    # this False so a misconfigured server install fails loudly.
    degrade_when_missing: bool = False

    # Ports.
    gateway_port: int = 80
    backend_port: int = 8000
    frontend_port: int = 3000
    postgres_port: int = 5432
    neo4j_bolt_port: int = 7687
    nats_port: int = 4222
    ollama_port: int = 11434
    redis_port: int = 6379
    opa_port: int = 8181

    # Postgres identity (password resolved via native_secrets, never hard-coded).
    postgres_user: str = "frontier"
    postgres_db: str = "frontier"
    neo4j_user: str = "neo4j"

    ollama_model: str = "gpt-oss:20b"


# --------------------------------------------------------------------------- #
# Service model
# --------------------------------------------------------------------------- #
@dataclass
class HealthCheck:
    kind: str = "none"  # "tcp" | "http" | "none"
    host: str = "127.0.0.1"
    port: int = 0
    path: str = "/"
    timeout_s: float = 60.0
    interval_s: float = 0.5


@dataclass
class Step:
    """A one-shot command run before (pre) or after (post) a service starts."""

    argv: list[str]
    skip_if_exists: str | None = None  # skip when this path already exists


@dataclass
class ServiceSpec:
    name: str
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    data_dir: str | None = None
    health: HealthCheck = field(default_factory=HealthCheck)
    pre_start: list[Step] = field(default_factory=list)
    post_start: list[Step] = field(default_factory=list)
    required: bool = True


@dataclass
class NativePlan:
    services: list[ServiceSpec]
    env: dict[str, str]
    warnings: list[str]

    def service_names(self) -> list[str]:
        return [s.name for s in self.services]


# --------------------------------------------------------------------------- #
# Binary discovery
# --------------------------------------------------------------------------- #
def _which(names: list[str], bin_dir: Path | None) -> str | None:
    """Resolve the first available binary: bundled ``bin_dir`` first, then PATH."""
    exe_suffixes = (".exe", ".cmd", ".bat") if os.name == "nt" else ("",)
    if bin_dir:
        for name in names:
            for suffix in exe_suffixes:
                candidate = bin_dir / f"{name}{suffix}"
                if candidate.exists():
                    return str(candidate)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


WhichFn = Callable[[list[str], Path | None], "str | None"]


# --------------------------------------------------------------------------- #
# Plan builder (pure)
# --------------------------------------------------------------------------- #
def build_native_plan(config: NativeConfig, *, which: WhichFn = _which) -> NativePlan:
    """Discover binaries, decide the service set, and compute the backend env +
    warnings together. Pure: launches nothing."""
    home = Path(config.app_home)
    data = home / "data"
    logs = home / "logs"  # noqa: F841 - reserved for supervisor log redirection
    # Default the bundled-binary dir to app_home/bin so `native-fetch` and
    # `native-up` agree without extra config.
    bin_dir = Path(config.bin_dir) if config.bin_dir else (home / "bin")
    host = config.bind_host

    services: list[ServiceSpec] = []
    warnings: list[str] = []
    env: dict[str, str] = {
        "FRONTIER_RUNTIME_PROFILE": "local-native",
        "FRONTIER_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR": "true",
        "FRONTIER_MEMORY_ENABLE_LONG_TERM": "true",
        "FRONTIER_MEMORY_CONSOLIDATION_ENABLED": "true",
        "FRONTIER_MEMORY_HYBRID_RETRIEVAL_ENABLED": "true",
        # No-proxy native topology: the browser hits the backend directly, so we
        # don't need Caddy/Envoy. The backend allows the frontend origin via CORS.
        "NEXT_PUBLIC_API_BASE_URL": f"http://{host}:{config.backend_port}",
        "FRONTEND_ORIGIN": f"http://{host}:{config.frontend_port}",
    }
    if config.projects_root:
        env["FRONTIER_PROJECTS_ROOT"] = config.projects_root

    # Secrets: materialize without ever committing them.
    api_token = ensure_secret("FRONTIER_API_BEARER_TOKEN", app_home=home)
    pg_password = ensure_secret("POSTGRES_PASSWORD", app_home=home)
    env["FRONTIER_API_BEARER_TOKEN"] = api_token
    # Keep signed A2A real for the native multi-process agents (defense in depth).
    env["A2A_JWT_SECRET"] = ensure_secret("A2A_JWT_SECRET", app_home=home)
    env.setdefault("A2A_JWT_ALG", "HS256")
    env.setdefault("A2A_JWT_ISS", "lattix-frontier")
    env.setdefault("A2A_JWT_AUD", "frontier-runtime")
    roster_subjects = ",".join(agent_id for agent_id, _port in config.agent_roster)
    env["A2A_TRUSTED_SUBJECTS"] = f"backend,orchestrator,coordinator,{roster_subjects}".rstrip(",")

    # --- Postgres (REQUIRED): state store + long-term pgvector memory ---------
    pg_bin = which(["postgres"], bin_dir)
    pg_ctl = which(["pg_ctl"], bin_dir)
    initdb = which(["initdb"], bin_dir)
    psql = which(["psql"], bin_dir)
    if not pg_bin:
        if config.degrade_when_missing:
            # First-run / pre-fetch: fall back to SQLite state so the app boots.
            # Long-term vector memory (pgvector) is disabled until Postgres lands.
            sqlite_path = str(data / "state" / "frontier-state.db")
            env["FRONTIER_SQLITE_STATE_PATH"] = sqlite_path
            env["FRONTIER_MEMORY_ENABLE_LONG_TERM"] = "false"
            warnings.append(
                "postgres not present yet; using SQLite state (long-term vector memory "
                "disabled until Postgres is provisioned on first run)."
            )
        else:
            raise NativeLauncherError(
                "Postgres ('postgres') not found on PATH or in the bundled bin dir. "
                "Postgres+pgvector is required for the native install (state + long-term memory)."
            )
    else:
        pg_data = str(data / "postgres")
        pg_pre: list[Step] = []
        if initdb:
            pg_pre.append(
                Step(
                    argv=[initdb, "-D", pg_data, "-U", config.postgres_user, "--auth=trust"],
                    skip_if_exists=str(Path(pg_data) / "PG_VERSION"),
                )
            )
        pg_post: list[Step] = []
        if psql:
            admin = f"postgresql://{config.postgres_user}@{host}:{config.postgres_port}/postgres"
            pg_post.append(Step(argv=[psql, admin, "-c", f"CREATE DATABASE {config.postgres_db}"]))
            db = f"postgresql://{config.postgres_user}@{host}:{config.postgres_port}/{config.postgres_db}"
            pg_post.append(Step(argv=[psql, db, "-c", "CREATE EXTENSION IF NOT EXISTS vector"]))
        else:
            warnings.append(
                "psql not found; create the 'frontier' DB + 'vector' extension manually."
            )
        services.append(
            ServiceSpec(
                name="postgres",
                argv=[pg_ctl, "-D", pg_data, "-o", f"-p {config.postgres_port}", "start"]
                if pg_ctl
                else [pg_bin, "-D", pg_data, "-p", str(config.postgres_port)],
                data_dir=pg_data,
                health=HealthCheck("tcp", host, config.postgres_port, timeout_s=60),
                pre_start=pg_pre,
                post_start=pg_post,
                required=True,
            )
        )
        env["POSTGRES_DSN"] = (
            f"postgresql://{config.postgres_user}:{pg_password}"
            f"@{host}:{config.postgres_port}/{config.postgres_db}"
        )

    # --- World models (Postgres relational graph — no Neo4j, no Java) ---------
    # The world-graph lives in the same Postgres that backs state + long-term
    # memory (PostgresWorldGraph), so it's available exactly when Postgres is.
    if config.enable_world_models and "POSTGRES_DSN" in env:
        env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] = "true"
    else:
        env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] = "false"
        if config.enable_world_models:
            warnings.append(
                "world models requested but Postgres is not present yet; the world-graph "
                "tier turns on once Postgres is provisioned (short-term memory still works)."
            )

    # --- NATS (signed A2A bus for multi-process agents) -----------------------
    nats_bin = which(["nats-server"], bin_dir)
    if not nats_bin and config.degrade_when_missing:
        warnings.append("nats not present yet; A2A bus disabled (agents run in-process).")
    elif not nats_bin:
        raise NativeLauncherError(
            "NATS ('nats-server') not found; it's required as the A2A bus for the native agents."
        )
    elif nats_bin:
        services.append(
            ServiceSpec(
                name="nats",
                argv=[nats_bin, "-a", host, "-p", str(config.nats_port)],
                health=HealthCheck("tcp", host, config.nats_port, timeout_s=30),
                required=True,
            )
        )
        env["NATS_URL"] = f"nats://{host}:{config.nats_port}"

    # --- Ollama (REQUIRED for local models) -----------------------------------
    ollama_bin = which(["ollama"], bin_dir)
    if ollama_bin:
        services.append(
            ServiceSpec(
                name="ollama",
                argv=[ollama_bin, "serve"],
                env={"OLLAMA_HOST": f"{host}:{config.ollama_port}", "OLLAMA_KEEP_ALIVE": "30m"},
                health=HealthCheck(
                    "http", host, config.ollama_port, path="/api/tags", timeout_s=60
                ),
                post_start=[Step(argv=[ollama_bin, "pull", config.ollama_model])],
                required=False,
            )
        )
    else:
        warnings.append("ollama not found; local model serving will be unavailable.")
    env["OLLAMA_BASE_URL"] = f"http://{host}:{config.ollama_port}"

    # --- Redis (OPTIONAL): short-term memory cache, WAL fallback otherwise ----
    if config.enable_redis:
        redis_bin = which(["redis-server"], bin_dir)
        if redis_bin:
            services.append(
                ServiceSpec(
                    name="redis",
                    argv=[redis_bin, "--bind", host, "--port", str(config.redis_port)],
                    health=HealthCheck("tcp", host, config.redis_port, timeout_s=20),
                    required=False,
                )
            )
            env["REDIS_URL"] = f"redis://{host}:{config.redis_port}/0"
        else:
            warnings.append("redis-server not found; short-term memory will use the WAL fallback.")

    # --- OPA (OPTIONAL): policy engine ----------------------------------------
    if config.enable_opa:
        opa_bin = which(["opa", "opa.exe"], bin_dir)
        if opa_bin:
            services.append(
                ServiceSpec(
                    name="opa",
                    argv=[
                        opa_bin,
                        "run",
                        "--server",
                        f"--addr={host}:{config.opa_port}",
                        "policies/",
                    ],
                    health=HealthCheck("http", host, config.opa_port, path="/health", timeout_s=20),
                    required=False,
                )
            )
        else:
            warnings.append("opa not found; policy evaluation will be skipped locally.")

    # --- Backend: the FastAPI control plane -----------------------------------
    # Skipped in desktop/frozen mode (manage_backend=False) — the packaged exe
    # serves uvicorn in-process instead (see desktop_main).
    root = source_repo_root()
    python = which(["python", "python3"], bin_dir) or sys.executable
    backend_dir = root / "apps" / "backend"
    if config.manage_backend:
        services.append(
            ServiceSpec(
                name="backend",
                argv=[
                    python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    host,
                    "--port",
                    str(config.backend_port),
                ],
                cwd=str(backend_dir),
                env={
                    "PYTHONPATH": os.pathsep.join(
                        p for p in (str(backend_dir), str(root), os.getenv("PYTHONPATH", "")) if p
                    )
                },
                health=HealthCheck(
                    "http", host, config.backend_port, path="/healthz", timeout_s=120
                ),
                required=True,
            )
        )

    # --- Frontend (OPTIONAL): Next.js standalone build ------------------------
    if config.serve_frontend:
        node = which(["node"], bin_dir)
        standalone = (
            Path(config.frontend_dir) / "server.js"
            if config.frontend_dir
            else root / "apps" / "frontend" / ".next" / "standalone" / "server.js"
        )
        if node and standalone.exists():
            services.append(
                ServiceSpec(
                    name="frontend",
                    argv=[node, str(standalone)],
                    env={
                        "PORT": str(config.frontend_port),
                        "HOSTNAME": host,
                        # Client uses same-origin /api (baked at build); the Next
                        # rewrite proxies it to the backend so the session cookie
                        # is first-party. Server-side SSR talks to the backend
                        # directly via the absolute internal URL.
                        "NEXT_PUBLIC_API_BASE_URL": "/api",
                        "API_BASE_URL_INTERNAL": f"http://{host}:{config.backend_port}",
                        "FRONTIER_BACKEND_PROXY_URL": f"http://{host}:{config.backend_port}",
                    },
                    health=HealthCheck("http", host, config.frontend_port, path="/", timeout_s=60),
                    required=False,
                )
            )
        elif not node:
            warnings.append("node not found; the native UI will not be served (API still runs).")
        else:
            warnings.append(
                "frontend standalone build not found (run 'next build'); UI not served (API still runs)."
            )

    # --- Multi-process A2A agents (confined subprocesses over localhost) ------
    agent_specs, agent_warnings = _agent_service_specs(config, which=which, repo_root=root)
    services.extend(agent_specs)
    warnings.extend(agent_warnings)

    return NativePlan(services=services, env=env, warnings=warnings)


def _agent_service_specs(
    config: NativeConfig, *, which: WhichFn = _which, repo_root: Path | None = None
) -> tuple[list[ServiceSpec], list[str]]:
    """ServiceSpecs for the apps/workers agents as native localhost subprocesses.

    Each runs the agent FastAPI app (``uvicorn app:app``) on its own port and
    verifies signed A2A envelopes. The process inherits the plan env (A2A signing
    secret, NATS_URL, ``local-native`` profile) from the supervisor, so its
    in-process harness confines tool/file execution via ``LocalSandboxExecutor``
    (Track 0). We intentionally keep loopback for the server (A2A transport);
    confinement is on the agent's *actions*, not its socket.
    """
    warnings: list[str] = []
    if not config.enable_agents:
        return [], warnings
    root = Path(repo_root) if repo_root else source_repo_root()
    template_dir = root / "apps" / "workers" / "services" / "AGENT_SERVICE_TEMPLATE"
    workers_dir = root / "apps" / "workers"
    if not (template_dir / "app.py").exists():
        warnings.append("agent service template not found; skipping native agent subprocesses.")
        return [], warnings
    python = which(["python", "python3"], config.bin_dir) or sys.executable
    pythonpath = os.pathsep.join(
        p for p in (str(workers_dir), str(root), os.getenv("PYTHONPATH", "")) if p
    )
    host = config.bind_host
    specs: list[ServiceSpec] = []
    seen_ports: set[int] = set()
    for agent_id, port in config.agent_roster:
        if port in seen_ports:
            warnings.append(f"agent '{agent_id}' port {port} collides; skipping.")
            continue
        seen_ports.add(port)
        specs.append(
            ServiceSpec(
                name=f"agent-{agent_id}",
                argv=[
                    python,
                    "-m",
                    "uvicorn",
                    "app:app",
                    "--app-dir",
                    str(template_dir),
                    "--host",
                    host,
                    "--port",
                    str(port),
                ],
                env={
                    "AGENT_ID": agent_id,
                    "SERVICE_NAME": f"agent-{agent_id}",
                    "AGENT_PORT": str(port),
                    "PYTHONPATH": pythonpath,
                    # Confine this agent's tool execution under the OS sandbox.
                    "FRONTIER_SANDBOX_AGENTS": "1",
                },
                health=HealthCheck("http", host, port, path="/healthz", timeout_s=45),
                required=False,
            )
        )
    return specs, warnings


# --------------------------------------------------------------------------- #
# Runtime supervisor
# --------------------------------------------------------------------------- #
def _default_probe(check: HealthCheck) -> bool:
    if check.kind == "none":
        return True
    if check.kind == "tcp":
        try:
            with socket.create_connection((check.host, check.port), timeout=2.0):
                return True
        except OSError:
            return False
    if check.kind == "http":
        try:
            import httpx

            resp = httpx.get(f"http://{check.host}:{check.port}{check.path}", timeout=2.0)
            return resp.status_code < 500
        except Exception:  # noqa: BLE001
            return False
    return False


def _default_spawn(argv: list[str], *, env: dict[str, str], cwd: str | None) -> Any:
    import subprocess

    return subprocess.Popen(argv, env=env, cwd=cwd)


def _default_run(argv: list[str], *, env: dict[str, str]) -> int:
    import subprocess

    return subprocess.run(argv, env=env, check=False).returncode


SpawnFn = Callable[..., Any]
RunFn = Callable[..., int]
ProbeFn = Callable[[HealthCheck], bool]


class NativeSupervisor:
    """Starts/stops a :class:`NativePlan`'s services in dependency order.

    Injectables (``spawn``/``run``/``probe``/``sleep``) keep it testable without
    real binaries.
    """

    def __init__(
        self,
        plan: NativePlan,
        *,
        spawn: SpawnFn | None = None,
        run: RunFn | None = None,
        probe: ProbeFn | None = None,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._plan = plan
        self._spawn = spawn or _default_spawn
        self._run = run or _default_run
        self._probe = probe or _default_probe
        self._sleep = sleep or time.sleep
        self._log = log or (lambda _m: None)
        self._procs: dict[str, Any] = {}

    def _run_steps(self, steps: list[Step], env: dict[str, str]) -> None:
        for step in steps:
            if step.skip_if_exists and Path(step.skip_if_exists).exists():
                self._log(f"skip step (exists): {step.skip_if_exists}")
                continue
            self._run(step.argv, env=env)

    def _wait_health(self, svc: ServiceSpec) -> bool:
        check = svc.health
        if check.kind == "none":
            return True
        deadline = check.timeout_s
        waited = 0.0
        while waited <= deadline:
            if self._probe(check):
                return True
            self._sleep(check.interval_s)
            waited += check.interval_s
        return False

    def start_all(self) -> dict[str, str]:
        base_env = {**os.environ, **self._plan.env}
        for svc in self._plan.services:
            if svc.data_dir:
                Path(svc.data_dir).mkdir(parents=True, exist_ok=True)
            svc_env = {**base_env, **svc.env}
            self._run_steps(svc.pre_start, svc_env)
            self._log(f"start {svc.name}: {' '.join(svc.argv)}")
            self._procs[svc.name] = self._spawn(svc.argv, env=svc_env, cwd=svc.cwd)
            if not self._wait_health(svc):
                if svc.required:
                    self.stop_all()
                    raise NativeLauncherError(
                        f"required service '{svc.name}' failed its health check"
                    )
                self._log(f"WARN: optional service '{svc.name}' did not become healthy; continuing")
                continue
            self._run_steps(svc.post_start, svc_env)
        return self.status()

    def serve(self, *, poll_interval: float = 2.0) -> None:
        """Start everything and block until interrupted or a required service
        dies, then tear down. This is the foreground entrypoint a desktop shell
        (Tauri) spawns as its backend sidecar."""
        self.start_all()
        try:
            while True:
                self._sleep(poll_interval)
                for svc in self._plan.services:
                    if not svc.required:
                        continue
                    proc = self._procs.get(svc.name)
                    poll = getattr(proc, "poll", None)
                    if proc is not None and callable(poll) and poll() is not None:
                        raise NativeLauncherError(f"required service '{svc.name}' exited")
        except (KeyboardInterrupt, NativeLauncherError) as exc:
            self._log(f"shutting down: {exc or 'interrupted'}")
        finally:
            self.stop_all()

    def stop_all(self) -> None:
        for name in reversed(list(self._procs.keys())):
            proc = self._procs.pop(name, None)
            if proc is None:
                continue
            self._terminate_tree(proc)

    @staticmethod
    def _terminate_tree(proc: Any) -> None:
        """Kill a child AND its descendants (node/ollama/postgres spawn their own
        children, so a bare terminate() would orphan them)."""
        pid = getattr(proc, "pid", None)
        if os.name == "nt" and pid:
            try:
                import subprocess

                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    check=False,
                    capture_output=True,
                )
                return
            except Exception:  # noqa: BLE001
                pass
        # POSIX / fallback: terminate the process (kill the group if possible).
        try:
            if os.name != "nt" and pid:
                import signal as _signal

                os.killpg(os.getpgid(pid), _signal.SIGTERM)
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            terminate = getattr(proc, "terminate", None)
            if callable(terminate):
                terminate()
        except Exception:  # noqa: BLE001
            pass

    def status(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for svc in self._plan.services:
            proc = self._procs.get(svc.name)
            if proc is None:
                out[svc.name] = "stopped"
                continue
            poll = getattr(proc, "poll", None)
            rc = poll() if callable(poll) else None
            out[svc.name] = "running" if rc is None else f"exited({rc})"
        return out
