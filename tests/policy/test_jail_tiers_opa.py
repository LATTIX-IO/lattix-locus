"""Principal decision 2026-10-03 ("Accept real OS jails") against the real Rego.

The executors derive their jail facts from the strategy they really launch with;
these tests feed those facts through the real gateway + OPA sidecar:

* AppContainer + Job Object with require_appcontainer allows; without the require
  flag (a possible Job-Object-only downgrade) it denies.
* local-direct, restricted-process and "no sandbox on this host" deny, the last
  with an actionable reason.
* An evaluation container (docker-exec) is a jail only for an ``evals`` session
  and a container without network; only the evals harness's gateway accepts
  ``evals`` sessions.
* On a Windows host that can create AppContainers, a real command runs through
  the gateway inside the AppContainer tier (skipped elsewhere).

Skips without an OPA binary (``LOCUS_OPA_BIN``); CI sets ``LOCUS_REQUIRE_OPA=1``.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from locus_runtime.gateway import (
    EVALS_PROFILE,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
    JailFacts,
)
from locus_runtime.harness.executor import (
    DockerContainerExecutor,
    LocalDirectExecutor,
    LocalSandboxExecutor,
)
from locus_runtime.policy_engine import OpaSidecarEngine
from locus_runtime.sandbox import (
    IsolationStrategy,
    SandboxManager,
    windows_appcontainer_supported,
)

ROOT = "/workspace/project"


def _caps(**overrides: object) -> Capabilities:
    base = Capabilities(
        allowed_tools=frozenset({"read_file", "write_file", "process_exec"}),
        read_roots=(ROOT,),
        write_roots=(ROOT,),
        allowed_executables=("bash", "git", "python", "cmd"),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture()
def audit() -> list[GatewayAuditRecord]:
    return []


@pytest.fixture()
def gateway(opa_engine: OpaSidecarEngine, audit: list[GatewayAuditRecord]) -> Gateway:
    """A backend-style gateway: it does not accept evals sessions."""
    return Gateway(opa_engine, audit.append)


@pytest.fixture()
def eval_gateway(opa_engine: OpaSidecarEngine, audit: list[GatewayAuditRecord]) -> Gateway:
    """The evaluation harness's gateway (apps/evals builds it this way)."""
    return Gateway(opa_engine, audit.append, allow_eval_sessions=True)


def _session(gateway: Gateway, **overrides: object) -> GatewaySession:
    return gateway.open_session(
        run_id="run-jail", principal="alice", engine="harness", capabilities=_caps(**overrides)
    )


def _decide(session: GatewaySession, jail: JailFacts, executable: str = "bash"):  # noqa: ANN202
    return session.authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command=f"{executable} -c true",
        executable=executable,
        jail=jail,
    )


# --- POSIX tiers ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["kernel-bwrap", "kernel-seatbelt", "hardened-docker"])
def test_posix_tiers_allow_with_readonly_root_and_non_root_uid(gateway: Gateway, tier: str) -> None:
    jail = JailFacts(
        strategy=tier, readonly_rootfs=True, run_as_user="1000:1000", allow_network=False
    )
    assert _decide(_session(gateway), jail).outcome == "allow"


# --- Windows AppContainer (facts derived by the executor) ----------------------------------


def _appcontainer_executor(tmp_path: Path) -> LocalSandboxExecutor:
    return LocalSandboxExecutor(
        tmp_path,
        manager=SandboxManager(force_strategy=IsolationStrategy.WINDOWS_APPCONTAINER),
    )


def test_appcontainer_facts_from_executor_are_allowed(gateway: Gateway, tmp_path: Path) -> None:
    facts = _appcontainer_executor(tmp_path).jail_facts()
    assert (facts.strategy, facts.appcontainer, facts.job_object, facts.require_appcontainer) == (
        "windows-appcontainer",
        True,
        True,
        True,
    )
    assert facts.allow_network is False
    assert _decide(_session(gateway), facts, executable="cmd").outcome == "allow"


def test_appcontainer_without_require_flag_is_denied(gateway: Gateway, tmp_path: Path) -> None:
    executor = _appcontainer_executor(tmp_path)
    executor._require_appcontainer = False  # noqa: SLF001 - the launcher may then degrade
    facts = executor.jail_facts()
    assert facts.appcontainer is False and facts.require_appcontainer is False
    decision = _decide(_session(gateway), facts, executable="cmd")
    assert decision.outcome == "deny"
    assert "tool_jail.appcontainer_not_required" in decision.reasons


def test_job_object_only_is_denied(gateway: Gateway) -> None:
    jail = JailFacts(
        strategy="windows-appcontainer",
        allow_network=False,
        appcontainer=False,
        job_object=True,
        require_appcontainer=True,
    )
    assert _decide(_session(gateway), jail, executable="cmd").outcome == "deny"


# --- host execution and missing sandboxes ---------------------------------------------------


def test_local_direct_is_denied(gateway: Gateway, tmp_path: Path) -> None:
    decision = _decide(_session(gateway), LocalDirectExecutor(tmp_path).jail_facts())
    assert decision.outcome == "deny"
    assert "tool_jail.host_exec_not_confined" in decision.reasons


def test_restricted_process_is_denied(gateway: Gateway, tmp_path: Path) -> None:
    executor = LocalSandboxExecutor(
        tmp_path, manager=SandboxManager(force_strategy=IsolationStrategy.RESTRICTED_PROCESS)
    )
    decision = _decide(_session(gateway), executor.jail_facts())
    assert decision.outcome == "deny"
    assert "tool_jail.host_exec_not_confined" in decision.reasons


def test_no_sandbox_denies_exec_with_actionable_reason(gateway: Gateway, tmp_path: Path) -> None:
    hint = "no confining sandbox available on this host: install bubblewrap"
    root = tmp_path.resolve()
    executor = LocalSandboxExecutor(
        root, gateway_session=_session(gateway, read_roots=(str(root),), write_roots=(str(root),))
    )
    executor.unavailable_reason = hint
    result = executor.run(["bash", "-c", "true"])
    assert result.gateway is not None and result.gateway.outcome == "deny"
    assert "tool_jail.no_confining_sandbox" in result.gateway.reasons
    assert hint in result.stderr


# --- evaluation containers -------------------------------------------------------------------


def _container(mode: str, session: GatewaySession | None = None) -> DockerContainerExecutor:
    return DockerContainerExecutor(
        "swe-instance-1",
        workdir_path="/testbed",
        gateway_session=session,
        inspect_network_mode=lambda _cid: mode,
    )


def _eval_session(gateway: Gateway) -> GatewaySession:
    return gateway.open_session(
        run_id="eval-1",
        principal="locus-evals",
        engine="evals",
        capabilities=_caps(
            read_roots=("/testbed",), write_roots=("/testbed",), runtime_profile=EVALS_PROFILE
        ),
    )


def test_eval_container_allowed_for_evals_profile_without_network(eval_gateway: Gateway) -> None:
    session = _eval_session(eval_gateway)
    assert _decide(session, _container("none").jail_facts()).outcome == "allow"


def test_eval_container_with_network_is_denied(eval_gateway: Gateway) -> None:
    session = _eval_session(eval_gateway)
    for mode in ("bridge", "host", ""):  # "" = docker inspect failed: unknown counts as on
        decision = _decide(session, _container(mode).jail_facts())
        assert decision.outcome == "deny", mode


def test_eval_container_denied_for_a_normal_run(gateway: Gateway, eval_gateway: Gateway) -> None:
    for gw in (gateway, eval_gateway):
        decision = _decide(_session(gw), _container("none").jail_facts())
        assert decision.outcome == "deny"
        assert "tool_jail.eval_container_needs_evals_profile_and_no_network" in decision.reasons


def test_backend_gateway_refuses_evals_sessions(gateway: Gateway) -> None:
    with pytest.raises(ValueError, match="evals"):
        _eval_session(gateway)


def test_eval_container_exec_runs_through_the_gate(eval_gateway: Gateway, monkeypatch) -> None:  # noqa: ANN001
    """End to end through the executor: allowed, then the docker exec is spawned."""
    import subprocess

    from locus_runtime.harness import executor as executor_module

    spawned: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        spawned.append(list(cmd))
        assert not any(key.upper().startswith("LOCUS_") for key in kwargs["env"])
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setenv("LOCUS_FAKE_TOKEN", "should-not-leak")
    monkeypatch.setattr(executor_module.subprocess, "run", fake_run)
    executor = _container("none", _eval_session(eval_gateway))
    result = executor.run_shell("pytest -q")
    assert result.exit_code == 0 and result.stdout == "ok\n"
    assert spawned and spawned[0][:2] == ["docker", "exec"]


# --- real AppContainer execution (Windows only) ------------------------------------------------

requires_appcontainer = pytest.mark.skipif(
    not windows_appcontainer_supported(), reason="AppContainer tier needs a Windows host"
)


@requires_appcontainer
def test_real_command_runs_in_appcontainer_through_the_gateway(
    gateway: Gateway,
    audit: list[GatewayAuditRecord],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path_factory.mktemp("appcontainer-ws").resolve()
    outside = tmp_path_factory.mktemp("appcontainer-outside").resolve()
    (outside / "secret.txt").write_text("host-only", encoding="utf-8")
    monkeypatch.setenv("LOCUS_FAKE_API_TOKEN", "must-not-reach-the-agent")
    session = _session(
        gateway,
        read_roots=(str(workspace),),
        write_roots=(str(workspace),),
        allowed_executables=("cmd",),
    )
    executor = LocalSandboxExecutor(
        workspace,
        manager=SandboxManager(force_strategy=IsolationStrategy.WINDOWS_APPCONTAINER),
        gateway_session=session,
    )

    result = executor.run(
        ["cmd", "/c", "echo locus-appcontainer-ok& echo written>inside.txt& set"], timeout=60
    )
    if result.exit_code != 0 and "AppContainer confinement is required" in result.stderr:
        pytest.skip(f"AppContainer tier cannot be created on this host: {result.stderr[-300:]}")
    assert audit[-1].outcome == "allow"
    assert result.exit_code == 0, result.stderr
    assert result.backend == "windows-appcontainer"
    assert "locus-appcontainer-ok" in result.stdout
    assert (workspace / "inside.txt").read_text(encoding="utf-8").strip() == "written"
    # Minimal environment: no LOCUS_* or token-like variables reach the agent command.
    assert "LOCUS_" not in result.stdout.upper()
    assert "must-not-reach-the-agent" not in result.stdout
    # AppContainer is default-deny outside the granted workspace.
    blocked = executor.run(["cmd", "/c", "type", str(outside / "secret.txt")], timeout=60)
    assert blocked.gateway is None  # the gateway allowed it; the OS refused it
    assert blocked.exit_code != 0
    assert "host-only" not in blocked.stdout
    assert os.name == "nt"
