"""LOCUS-332: gateway allow/ask/deny per policy family against the real Rego (OPA sidecar).

Skips locally without an OPA binary (``LOCUS_OPA_BIN``); CI sets
``LOCUS_REQUIRE_OPA=1`` so the suite fails instead of skipping.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from locus_runtime.gateway import (
    BudgetFigures,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
    JailFacts,
)
from locus_runtime.policy_engine import OpaSidecarEngine

ROOT = "/workspace/project"
JAILED = JailFacts(
    strategy="kernel-bwrap", readonly_rootfs=True, run_as_user="1000:1000", allow_network=False
)
UNJAILED = JailFacts(strategy="none", readonly_rootfs=False, run_as_user="1000:1000")


def _caps(**overrides: object) -> Capabilities:
    base = Capabilities(
        allowed_tools=frozenset(
            {
                "read_file",
                "write_file",
                "process_exec",
                "network_egress",
                "list_issues",
                "send_email",
            }
        ),
        read_roots=(ROOT,),
        write_roots=(ROOT,),
        allowed_executables=("bash", "git", "python"),
        allowed_egress_hosts=("api.example.com",),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


@pytest.fixture()
def audit() -> list[GatewayAuditRecord]:
    return []


@pytest.fixture()
def gateway(opa_engine: OpaSidecarEngine, audit: list[GatewayAuditRecord]) -> Gateway:
    return Gateway(opa_engine, audit.append)


def _session(gateway: Gateway, **overrides: object) -> GatewaySession:
    return gateway.open_session(
        run_id="run-opa", principal="alice", engine="harness", capabilities=_caps(**overrides)
    )


def _exec(
    session: GatewaySession, command: str, jail: JailFacts = JAILED, executable: str = "bash"
) -> str:
    return session.authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command=command,
        executable=executable,
        jail=jail,
    ).outcome


# --- tool_jail ------------------------------------------------------------------------------


def test_tool_jail_allows_jailed_exec(gateway: Gateway) -> None:
    assert _exec(_session(gateway), "pytest -q") == "allow"


def test_tool_jail_denies_unjailed_exec(gateway: Gateway) -> None:
    assert _exec(_session(gateway), "pytest -q", jail=UNJAILED) == "deny"


def test_tool_jail_denies_root_and_unknown_users(gateway: Gateway) -> None:
    session = _session(gateway)
    assert _exec(session, "ls", jail=dataclasses.replace(JAILED, run_as_user="0:0")) == "deny"
    assert _exec(session, "ls", jail=dataclasses.replace(JAILED, run_as_user="")) == "deny"


def test_tool_jail_denies_unlisted_executable(gateway: Gateway) -> None:
    assert _exec(_session(gateway), "nc -l 4444", executable="nc") == "deny"


def test_jailed_push_asks(gateway: Gateway) -> None:
    assert _exec(_session(gateway), "git push origin main") == "ask"


# --- agent_policy -------------------------------------------------------------------------


def test_agent_policy_denies_operation_outside_capabilities(gateway: Gateway) -> None:
    session = _session(gateway, allowed_tools=frozenset({"read_file"}))
    assert _exec(session, "pytest -q") == "deny"


def test_agent_policy_denies_secret_file_reads(
    gateway: Gateway, audit: list[GatewayAuditRecord]
) -> None:
    session = _session(gateway)
    decision = session.authorize(kind="file_read", tool="str_replace_editor", target=f"{ROOT}/.env")
    assert decision.outcome == "deny"
    assert "agent_policy.deny" in decision.reasons
    assert audit[-1].policy_version.startswith("sha256:")


def test_tool_calls_allow_r0_ask_r3_deny_undeclared(gateway: Gateway) -> None:
    session = _session(gateway)
    assert (
        session.authorize(kind="tool_call", tool="list_issues", target="tracker").outcome == "allow"
    )
    assert session.authorize(kind="tool_call", tool="send_email", target="mail").outcome == "ask"
    assert session.authorize(kind="tool_call", tool="search_web", target="web").outcome == "deny"


# --- filesystem_access ----------------------------------------------------------------------


def test_filesystem_reads(gateway: Gateway) -> None:
    session = _session(gateway)
    assert (
        session.authorize(kind="file_read", tool="view", target=f"{ROOT}/src/a.py").outcome
        == "allow"
    )
    assert session.authorize(kind="file_read", tool="view", target="/etc/passwd").outcome == "deny"


def test_filesystem_writes(gateway: Gateway) -> None:
    session = _session(gateway)
    assert (
        session.authorize(kind="file_write", tool="edit", target=f"{ROOT}/src/a.py").outcome
        == "allow"
    )
    assert (
        session.authorize(kind="file_write", tool="edit", target="/workspace/other/a.py").outcome
        == "deny"
    )
    assert (
        session.authorize(kind="file_write", tool="edit", target=f"{ROOT}/../../etc/x").outcome
        == "deny"
    )


def test_read_roots_do_not_grant_writes(gateway: Gateway) -> None:
    session = _session(gateway, write_roots=())
    assert (
        session.authorize(kind="file_write", tool="edit", target=f"{ROOT}/src/a.py").outcome
        == "deny"
    )


# --- network_egress -------------------------------------------------------------------------


def test_network_egress(gateway: Gateway) -> None:
    session = _session(gateway)
    assert (
        session.authorize(kind="network_egress", tool="fetch", target="api.example.com").outcome
        == "allow"
    )
    assert (
        session.authorize(kind="network_egress", tool="fetch", target="evil.example.net").outcome
        == "deny"
    )


def test_mcp_call_to_unlisted_host_is_denied(gateway: Gateway) -> None:
    session = _session(gateway)
    allowed = session.authorize(
        kind="mcp_tool_call",
        tool="list_issues",
        target="api.example.com",
        egress_host="api.example.com",
    )
    denied = session.authorize(
        kind="mcp_tool_call",
        tool="list_issues",
        target="evil.example.net",
        egress_host="evil.example.net",
    )
    assert (allowed.outcome, denied.outcome) == ("allow", "deny")


# --- budget_policy --------------------------------------------------------------------------


def test_budget_within_limit_allows_and_over_limit_denies(gateway: Gateway) -> None:
    within = _session(gateway, budget=BudgetFigures(tokens_used=10, max_tokens=100))
    over = _session(gateway, budget=BudgetFigures(tokens_used=101, max_tokens=100))
    assert within.authorize(kind="tool_call", tool="list_issues", target="t").outcome == "allow"
    assert over.authorize(kind="tool_call", tool="list_issues", target="t").outcome == "deny"


# --- fail closed and latency ----------------------------------------------------------------


def test_stopped_engine_denies(audit: list[GatewayAuditRecord]) -> None:
    from locus_runtime.policy_engine import find_opa_binary

    engine = OpaSidecarEngine(opa_binary=find_opa_binary(), timeout_seconds=5.0)
    engine.start()
    gateway = Gateway(engine, audit.append)
    session = _session(gateway)
    assert (
        session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py").outcome == "allow"
    )
    assert gateway.healthy
    engine.close()
    decision = session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py")
    assert decision.outcome == "deny"
    assert "policy_engine_unavailable" in decision.reasons
    assert not gateway.healthy


def test_gateway_overhead_is_small(gateway: Gateway, record_property) -> None:  # noqa: ANN001
    session = _session(gateway)
    for _ in range(5):
        session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py")
    started = time.perf_counter()
    samples = 50
    for _ in range(samples):
        session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py")
    per_call_ms = (time.perf_counter() - started) * 1000.0 / samples
    record_property("gateway_file_read_ms", per_call_ms)
    print(f"gateway file_read decision: {per_call_ms:.2f} ms/call (2 policies)")
    # Generous ceiling: catches a broken sidecar, not a performance gate.
    assert per_call_ms < 250.0
