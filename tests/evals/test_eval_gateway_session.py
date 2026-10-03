"""apps/evals acts through explicit ``evals`` gateway sessions (LOCUS-332).

* every instance opens its own session with read_file/write_file/process_exec,
  workspace-scoped roots and the ``evals`` run profile, and every agent action of
  the run is authorized through it;
* the eval gateway accepts ``evals`` sessions, a backend-style gateway does not;
* SWE-bench instance containers start with networking disabled.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from tests.gateway_support import FakeEngine

requires_bash_git = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)


def test_eval_capabilities_are_workspace_scoped_with_the_evals_profile() -> None:
    from locus_evals.gateway_session import EVAL_OPERATIONS, eval_capabilities

    caps = eval_capabilities("/work/inst-1")
    assert caps.allowed_tools == EVAL_OPERATIONS == {"read_file", "write_file", "process_exec"}
    assert caps.read_roots == caps.write_roots == ("/work/inst-1",)
    assert caps.runtime_profile == gw.EVALS_PROFILE
    assert caps.allowed_executables


def test_only_the_eval_gateway_accepts_evals_sessions(tmp_path: Path) -> None:
    from locus_evals.gateway_session import build_eval_gateway, open_eval_session

    eval_gateway = build_eval_gateway(tmp_path, engine=FakeEngine())
    session = open_eval_session(eval_gateway, run_id="eval-x", root=str(tmp_path))
    assert session.capabilities.runtime_profile == "evals"
    backend_gateway = gw.Gateway(FakeEngine(), lambda _r: None)
    with pytest.raises(ValueError, match="evals"):
        open_eval_session(backend_gateway, run_id="eval-x", root=str(tmp_path))


def test_eval_gateway_audits_to_jsonl(tmp_path: Path) -> None:
    from locus_evals.gateway_session import build_eval_gateway, open_eval_session

    eval_gateway = build_eval_gateway(tmp_path, engine=FakeEngine())
    session = open_eval_session(eval_gateway, run_id="eval-a", root=str(tmp_path))
    session.authorize(kind="file_read", tool="view", target=str(tmp_path / "a.py"))
    lines = (tmp_path / "gateway-audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"engine": "evals"' in lines[0]


@requires_bash_git
def test_run_eval_routes_every_action_through_an_evals_session(tmp_path: Path) -> None:
    from locus_evals.config import EvalConfig
    from locus_evals.runner import run_eval

    engine = FakeEngine()
    audit: list[gw.GatewayAuditRecord] = []
    gateway = gw.Gateway(engine, audit.append, allow_eval_sessions=True)
    executors: list[LocalDirectExecutor] = []

    def factory(root: Path, session: gw.GatewaySession) -> LocalDirectExecutor:
        assert session.capabilities.runtime_profile == "evals"
        assert session.capabilities.write_roots == (str(root),)
        ex = LocalDirectExecutor(root, gateway_session=session)
        executors.append(ex)
        return ex

    config = EvalConfig(mode="plumbing", dataset="synthetic-mini", seeds=[0])
    config.instance_ids = ["syn-add-sign"]
    run = run_eval(config, output_dir=tmp_path / "out", gateway=gateway, executor_factory=factory)
    assert run.summary["resolve_rate_mean"] == 1.0
    assert len(executors) == 1
    kinds = {record.action_kind for record in audit}
    assert {"process_exec", "file_write"} <= kinds
    assert {record.engine for record in audit} == {"evals"}
    assert {record.run_id for record in audit} == {"eval-syn-add-sign-seed-0"}
    jail_inputs: list[dict[str, Any]] = [p for name, p in engine.calls if name == "tool_jail"]
    assert jail_inputs and {p["runtime_profile"] for p in jail_inputs} == {"evals"}
    # Sessions are closed after the instance: later actions are unauthenticated.
    late = executors[0].run(["git", "status"])
    assert late.gateway is not None and late.gateway.outcome == "deny"
    assert gw.REASON_UNAUTHENTICATED in late.gateway.reasons


def test_swebench_instance_containers_have_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from locus_evals import docker_env

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append(list(cmd))
        assert not any(key.upper().startswith("LOCUS_") for key in kwargs.get("env", {}))
        return subprocess.CompletedProcess(cmd, 0, stdout="cid-1\n", stderr="")

    monkeypatch.setenv("LOCUS_API_BEARER_TOKEN", "x")
    monkeypatch.setattr(docker_env.subprocess, "run", fake_run)
    with docker_env.instance_container("astropy__astropy-1", docker_host="tcp://r:2376") as cid:
        assert cid == "cid-1"
    run_cmd = calls[0]
    assert run_cmd[:2] == ["docker", "run"]
    assert run_cmd[run_cmd.index("--network") + 1] == "none"


def test_swebench_tasks_bind_the_instance_session(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from locus_evals import datasets
    from locus_evals.gateway_session import build_eval_gateway, open_eval_session

    monkeypatch.setattr(datasets, "_load_swebench_statements", lambda ids: {})
    eval_gateway = build_eval_gateway(tmp_path, engine=FakeEngine())
    opened: list[gw.GatewaySession] = []

    def session_for(iid: str) -> gw.GatewaySession:
        session = open_eval_session(eval_gateway, run_id=f"eval-{iid}", root="/testbed")
        opened.append(session)
        return session

    [task] = datasets.swebench_tasks(
        ["inst-1"], container_resolver=lambda _iid: "cid-1", session_for=session_for
    )
    assert task.executor.gateway_session is opened[0]
