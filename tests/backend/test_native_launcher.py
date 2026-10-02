"""Track B: native (Dockerless) launcher — plan derivation, world-model gating,
binary discovery, supervisor start/stop. Pure/unit; no real binaries or stack."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from frontier_tooling import native_launcher as nl  # noqa: E402
from frontier_tooling import native_secrets as ns  # noqa: E402


def _which_factory(available: set[str]):
    """Fake binary resolver: a name resolves iff it's in `available`."""

    def _which(names: list[str], bin_dir):
        for name in names:
            if name in available:
                return f"/usr/bin/{name}"
        return None

    return _which


# Every infra binary present.
_ALL = {
    "postgres",
    "pg_ctl",
    "initdb",
    "psql",
    "neo4j",
    "nats-server",
    "ollama",
    "redis-server",
    "opa",
}


def _config(tmp_path, **kw) -> nl.NativeConfig:
    return nl.NativeConfig(app_home=tmp_path, **kw)


# --- plan: env derivation ----------------------------------------------------
def test_profile_and_core_env(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    assert plan.env["FRONTIER_RUNTIME_PROFILE"] == "local-native"
    assert plan.env["POSTGRES_DSN"].startswith("postgresql://frontier:")
    assert plan.env["POSTGRES_DSN"].endswith("@127.0.0.1:5432/frontier")
    assert plan.env["NATS_URL"] == "nats://127.0.0.1:4222"
    assert plan.env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    # No-proxy native topology: the browser hits the backend directly.
    assert plan.env["NEXT_PUBLIC_API_BASE_URL"] == "http://127.0.0.1:8000"
    assert plan.env["FRONTEND_ORIGIN"] == "http://127.0.0.1:3000"


def test_backend_service_required(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    backend = next(s for s in plan.services if s.name == "backend")
    assert backend.required is True
    assert "uvicorn" in backend.argv and "app.main:app" in backend.argv
    assert backend.health.kind == "http" and backend.health.path == "/healthz"
    # NATS (infra) must be healthy before the control plane comes up.
    names = plan.service_names()
    assert names.index("nats") < names.index("backend")


def test_world_models_ride_on_postgres_no_neo4j(tmp_path):
    # World-models now live in the bundled Postgres — no Neo4j, no JRE, no NEO4J_* env.
    plan = nl.build_native_plan(
        _config(tmp_path, enable_world_models=True), which=_which_factory(_ALL)
    )
    assert "neo4j" not in plan.service_names()
    assert "NEO4J_URI" not in plan.env and "NEO4J_PASSWORD" not in plan.env
    assert plan.env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] == "true"
    assert "POSTGRES_DSN" in plan.env


def test_world_models_off_when_postgres_absent_degrade(tmp_path):
    # No Postgres yet (first-run/degrade) → world-graph tier off until it lands.
    avail = _ALL - {"postgres", "pg_ctl", "initdb", "psql"}
    plan = nl.build_native_plan(
        _config(tmp_path, enable_world_models=True, degrade_when_missing=True),
        which=_which_factory(avail),
    )
    assert plan.env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] == "false"
    assert any("world models requested" in w.lower() for w in plan.warnings)


def test_world_models_off_disables_projection(tmp_path):
    plan = nl.build_native_plan(
        _config(tmp_path, enable_world_models=False), which=_which_factory(_ALL)
    )
    assert "neo4j" not in plan.service_names()
    assert plan.env["FRONTIER_MEMORY_GRAPH_PROJECTION_ENABLED"] == "false"


# --- plan: required vs optional binaries ------------------------------------
def test_missing_postgres_raises(tmp_path):
    with pytest.raises(nl.NativeLauncherError):
        nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL - {"postgres"}))


def test_missing_nats_raises(tmp_path):
    with pytest.raises(nl.NativeLauncherError):
        nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL - {"nats-server"}))


def test_optional_redis_missing_is_warning_not_error(tmp_path):
    avail = _ALL - {"redis-server"}
    plan = nl.build_native_plan(_config(tmp_path, enable_redis=True), which=_which_factory(avail))
    assert "redis" not in plan.service_names()
    assert "REDIS_URL" not in plan.env
    assert any("redis-server not found" in w for w in plan.warnings)


def test_redis_present_sets_url(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path, enable_redis=True), which=_which_factory(_ALL))
    assert "redis" in plan.service_names()
    assert plan.env["REDIS_URL"] == "redis://127.0.0.1:6379/0"


# --- plan: postgres pgvector bootstrap --------------------------------------
def test_postgres_initdb_and_pgvector_steps(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    pg = next(s for s in plan.services if s.name == "postgres")
    # initdb pre-start is guarded by PG_VERSION (idempotent).
    assert any("initdb" in step.argv[0] and step.skip_if_exists for step in pg.pre_start)
    # post-start creates the DB and the pgvector extension.
    joined = [" ".join(step.argv) for step in pg.post_start]
    assert any("CREATE DATABASE frontier" in j for j in joined)
    assert any("CREATE EXTENSION IF NOT EXISTS vector" in j for j in joined)


# --- supervisor: ordering, health gating, stop ------------------------------
def test_supervisor_starts_in_order_and_waits_health(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    spawned: list[str] = []
    ran: list[list[str]] = []

    class _Proc:
        def poll(self):
            return None  # still running

        def terminate(self):
            pass

    def _spawn(argv, *, env, cwd):
        spawned.append(argv[0])
        return _Proc()

    def _run(argv, *, env):
        ran.append(argv)
        return 0

    sup = nl.NativeSupervisor(
        plan, spawn=_spawn, run=_run, probe=lambda check: True, sleep=lambda s: None
    )
    status = sup.start_all()
    # postgres is the first service started (it backs state + long-term memory).
    assert plan.services[0].name == "postgres"
    assert "pg_ctl" in spawned[0] or "postgres" in spawned[0]
    assert all(v == "running" for v in status.values())
    # pgvector post-start commands actually ran.
    assert any("vector" in " ".join(a) for a in ran)


def test_supervisor_raises_when_required_unhealthy(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))

    class _Proc:
        def poll(self):
            return None

        def terminate(self):
            pass

    # postgres (required, first) never becomes healthy.
    def _probe(check):
        return False

    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: _Proc(),
        run=lambda argv, *, env: 0,
        probe=_probe,
        sleep=lambda s: None,
    )
    with pytest.raises(nl.NativeLauncherError):
        sup.start_all()


def test_pre_start_skipped_when_marker_exists(tmp_path):
    # Pre-create PG_VERSION so initdb is skipped (idempotent re-run).
    pg_data = tmp_path / "data" / "postgres"
    pg_data.mkdir(parents=True)
    (pg_data / "PG_VERSION").write_text("16", encoding="utf-8")
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    ran: list[list[str]] = []

    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: type(
            "P", (), {"poll": lambda self: None, "terminate": lambda self: None}
        )(),
        run=lambda argv, *, env: ran.append(argv) or 0,
        probe=lambda check: True,
        sleep=lambda s: None,
    )
    sup.start_all()
    assert not any("initdb" in a[0] for a in ran)


# --- secrets: env-first, then generate+persist ------------------------------
def test_secret_env_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_TEST_SECRET", "from-env")
    assert ns.get_secret("MY_TEST_SECRET", app_home=tmp_path) == "from-env"


def test_secret_generate_and_persist_file_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("GEN_SECRET_X", raising=False)
    # Force the file backend by making keyring unavailable.
    monkeypatch.setattr(ns, "_keyring_set", lambda name, value: False)
    monkeypatch.setattr(ns, "_keyring_get", lambda name: None)
    first = ns.ensure_secret("GEN_SECRET_X", app_home=tmp_path)
    assert first
    # Second call returns the SAME persisted value (idempotent).
    second = ns.ensure_secret("GEN_SECRET_X", app_home=tmp_path)
    assert first == second


# --- B3: multi-process A2A agent subprocesses -------------------------------
def test_agents_in_plan_by_default(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    names = plan.service_names()
    for agent in ("agent-research", "agent-code", "agent-review"):
        assert agent in names
    # A2A trusted subjects cover the backend + the roster.
    subjects = plan.env["A2A_TRUSTED_SUBJECTS"]
    assert "backend" in subjects and "research" in subjects and "code" in subjects


def test_agent_spec_env_and_argv(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    code = next(s for s in plan.services if s.name == "agent-code")
    assert code.env["AGENT_ID"] == "code"
    assert code.env["AGENT_PORT"] == "8082"
    assert code.env["FRONTIER_SANDBOX_AGENTS"] == "1"  # tool exec confined (Track 0)
    assert "PYTHONPATH" in code.env and "workers" in code.env["PYTHONPATH"]
    assert "uvicorn" in code.argv and "app:app" in code.argv
    assert "--app-dir" in code.argv and "8082" in code.argv
    assert code.health.kind == "http" and code.health.path == "/healthz"
    assert code.required is False  # agents are optional; backend can run in-proc


def test_agents_start_after_nats(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path), which=_which_factory(_ALL))
    names = plan.service_names()
    assert names.index("nats") < names.index("agent-research")


def test_agents_disabled(tmp_path):
    plan = nl.build_native_plan(_config(tmp_path, enable_agents=False), which=_which_factory(_ALL))
    assert not any(n.startswith("agent-") for n in plan.service_names())


def test_custom_agent_roster(tmp_path):
    cfg = _config(tmp_path, agent_roster=[("planner", 8090), ("verifier", 8091)])
    plan = nl.build_native_plan(cfg, which=_which_factory(_ALL))
    names = plan.service_names()
    assert "agent-planner" in names and "agent-verifier" in names
    assert "agent-research" not in names
    assert "planner" in plan.env["A2A_TRUSTED_SUBJECTS"]
