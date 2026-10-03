"""LOCUS-332: gateway PEP decisions with a fake policy engine (no OPA needed)."""

from __future__ import annotations

import dataclasses

import pytest

from locus_runtime import gateway as gw
from locus_runtime.gateway import (
    BudgetFigures,
    Capabilities,
    Gateway,
    GatewayAction,
    GatewayAuditRecord,
    GatewayDecision,
    JailFacts,
    RiskClass,
    classify_command,
    classify_risk,
    summarize_args,
    verify_decision,
)
from tests.gateway_support import FakeEngine, installed

ROOT = "/work/repo"
JAILED = JailFacts(strategy="kernel-bwrap", readonly_rootfs=True, run_as_user="1000:1000")


def _caps(**overrides: object) -> Capabilities:
    base = Capabilities(
        allowed_tools=frozenset(
            {"read_file", "write_file", "process_exec", "list_issues", "send_email"}
        ),
        read_roots=(ROOT,),
        write_roots=(ROOT,),
        allowed_executables=("bash", "git"),
        allowed_egress_hosts=("api.example.com",),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


class _Audit:
    def __init__(self) -> None:
        self.records: list[GatewayAuditRecord] = []

    def __call__(self, record: GatewayAuditRecord) -> None:
        self.records.append(record)


def _gateway(engine: FakeEngine | None = None, **kwargs: object) -> tuple[Gateway, _Audit]:
    audit = _Audit()
    return Gateway(engine or FakeEngine(), audit, **kwargs), audit  # type: ignore[arg-type]


def _session(gateway: Gateway, **cap_overrides: object) -> gw.GatewaySession:
    return gateway.open_session(
        run_id="run-1", principal="alice", engine="harness", capabilities=_caps(**cap_overrides)
    )


# --- risk classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q", RiskClass.R1),
        ("git status && git diff", RiskClass.R1),
        ("rm -rf build/", RiskClass.R1),
        ("curl https://example.com/data.json", RiskClass.R2),
        ("git push origin main", RiskClass.R3),
        ("gh pr merge 12 --squash", RiskClass.R3),
        ("pip install requests", RiskClass.R3),
        ("npm publish", RiskClass.R3),
        ("curl -X POST https://api.example.com/send -d @body.json", RiskClass.R3),
        ("scp out.tar host:/tmp", RiskClass.R3),
        ("rm -rf /var/data", RiskClass.R3),
        ("cat ~/.ssh/id_rsa", RiskClass.R4),
        ("cat ~/.aws/credentials", RiskClass.R4),
    ],
)
def test_classify_command(command: str, expected: RiskClass) -> None:
    assert classify_command(command) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"kind": "file_read", "target": "/etc/passwd"}, RiskClass.R0),
        ({"kind": "file_write", "target": f"{ROOT}/src/a.py"}, RiskClass.R1),
        ({"kind": "file_write", "target": "/home/u/other/a.py"}, RiskClass.R2),
        ({"kind": "file_write", "target": "/home/u/.ssh/authorized_keys"}, RiskClass.R4),
        ({"kind": "file_write", "target": "/srv/policies/agent_policy.rego"}, RiskClass.R4),
        ({"kind": "network_egress", "target": "api.example.com"}, RiskClass.R2),
        ({"kind": "tool_call", "tool": "list_issues"}, RiskClass.R0),
        ({"kind": "tool_call", "tool": "github__search_code"}, RiskClass.R0),
        ({"kind": "tool_call", "tool": "create_issue"}, RiskClass.R2),
        ({"kind": "tool_call", "tool": "weather"}, RiskClass.R2),
        ({"kind": "tool_call", "tool": "weather", "method": "GET"}, RiskClass.R0),
        ({"kind": "tool_call", "tool": "records", "method": "DELETE"}, RiskClass.R3),
        ({"kind": "mcp_tool_call", "tool": "gmail__sendEmail"}, RiskClass.R3),
        ({"kind": "tool_call", "tool": "invoice", "args": {"amount": 10}}, RiskClass.R3),
        ({"kind": "tool_call", "tool": "get_secret"}, RiskClass.R4),
        ({"kind": "tool_call", "tool": "disable_audit"}, RiskClass.R4),
        ({"kind": "tool_call", "tool": "update_policy"}, RiskClass.R4),
        ({"kind": "teleport"}, RiskClass.R4),
    ],
)
def test_classify_risk_table(kwargs: dict, expected: RiskClass) -> None:
    assert classify_risk(write_roots=(ROOT,), policy_dir="/srv/policies", **kwargs) == expected


def test_classification_is_deterministic() -> None:
    first = [classify_command("git push"), classify_risk(kind="tool_call", tool="send_email")]
    second = [classify_command("git push"), classify_risk(kind="tool_call", tool="send_email")]
    assert first == second


# --- redaction -------------------------------------------------------------------------


def test_args_summary_never_carries_raw_secrets() -> None:
    raw_token = "sk-abcdefghijklmnopqrstuv"
    summary = summarize_args(
        {
            "command": f'curl -H "Authorization: Bearer {raw_token}" https://u:hunter2@h/x',
            "api_key": "AKIAABCDEFGHIJKLMNOP",
            "file_text": "PRIVATE CONTENT",
        }
    )
    flat = repr(summary)
    for secret in (raw_token, "hunter2", "AKIAABCDEFGHIJKLMNOP", "PRIVATE CONTENT"):
        assert secret not in flat
    assert summary["file_text"] == "<15 chars>"


def test_action_target_and_command_are_redacted() -> None:
    gateway, audit = _gateway()
    session = _session(gateway)
    session.authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command="curl -H 'Authorization: Bearer ghp_abcdefghijklmnopqrstuvwxyz' https://x",
        executable="bash",
        jail=JAILED,
    )
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in repr(audit.records[0])


# --- fail closed -----------------------------------------------------------------------


def test_engine_down_denies() -> None:
    gateway, audit = _gateway(FakeEngine(running=False))
    decision = _session(gateway).authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py")
    assert decision.outcome == "deny"
    assert "policy_engine_unavailable" in decision.reasons
    assert audit.records[0].outcome == "deny"


def test_engine_exception_denies() -> None:
    gateway, _ = _gateway(FakeEngine(raises=True))
    decision = _session(gateway).authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py")
    assert decision.outcome == "deny"
    assert any(reason.startswith(gw.REASON_ENGINE_ERROR) for reason in decision.reasons)


def test_unauthenticated_caller_denies_without_consulting_policy() -> None:
    engine = FakeEngine()
    gateway, audit = _gateway(engine)
    action = GatewayAction.create(
        caller=gw.GatewayCaller(
            run_id="run-1", principal="alice", engine="harness", token="forged"
        ),
        kind="file_read",
        tool="view",
        target=f"{ROOT}/a.py",
    )
    decision = gateway.authorize(action)
    assert decision.outcome == "deny"
    assert decision.reasons == (gw.REASON_UNAUTHENTICATED,)
    assert engine.calls == []
    assert audit.records[0].reasons == (gw.REASON_UNAUTHENTICATED,)


def test_stolen_token_with_other_identity_denies() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    impostor = dataclasses.replace(session.caller, run_id="run-2")
    action = GatewayAction.create(caller=impostor, kind="file_read", tool="view", target="x")
    assert gateway.authorize(action).outcome == "deny"


def test_closed_session_denies() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    session.close()
    assert session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a").outcome == "deny"


def test_no_installed_gateway_denies_unbound_callers() -> None:
    with installed(None):
        decision = gw.authorize_action(None, kind="file_read", tool="view", target="/x")
    assert decision.outcome == "deny"
    assert decision.reasons == (gw.REASON_NOT_INSTALLED,)


def test_real_gateway_denies_unbound_callers() -> None:
    gateway, _ = _gateway()
    with installed(gateway):
        decision = gw.authorize_action(None, kind="file_read", tool="view", target="/x")
    assert decision.outcome == "deny"
    assert decision.reasons == (gw.REASON_UNAUTHENTICATED,)


def test_audit_failure_denies() -> None:
    def broken(record: GatewayAuditRecord) -> None:
        raise OSError("disk full")

    gateway = Gateway(FakeEngine(), broken)
    decision = _session(gateway).authorize(kind="file_read", tool="view", target=f"{ROOT}/a")
    assert decision.outcome == "deny"
    assert gw.REASON_AUDIT_UNAVAILABLE in decision.reasons


def test_policy_deny_wins() -> None:
    gateway, _ = _gateway(FakeEngine({"filesystem_access": False}))
    decision = _session(gateway).authorize(
        kind="file_write", tool="str_replace_editor", target=f"{ROOT}/a"
    )
    assert decision.outcome == "deny"
    assert "filesystem_access.not_allowed" in decision.reasons


def test_decisions_are_not_cached() -> None:
    engine = FakeEngine()
    gateway, _ = _gateway(engine)
    session = _session(gateway)
    assert session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a").outcome == "allow"
    engine.running = False
    assert session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a").outcome == "deny"


# --- allow / ask / deny by risk ------------------------------------------------------


def test_r1_workspace_exec_allows() -> None:
    gateway, _ = _gateway()
    decision = _session(gateway).authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command="pytest -q",
        executable="bash",
        jail=JAILED,
    )
    assert decision.outcome == "allow"
    assert decision.risk == RiskClass.R1
    assert decision.allowed and verify_decision(decision)


def test_r3_asks_without_grant() -> None:
    gateway, audit = _gateway()
    decision = _session(gateway).authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command="git push origin main",
        executable="bash",
        jail=JAILED,
    )
    assert decision.outcome == "ask"
    assert decision.risk == RiskClass.R3
    assert gw.REASON_APPROVAL_REQUIRED in decision.reasons
    assert not decision.allowed
    assert audit.records[-1].outcome == "ask"


def test_r4_denies_even_when_policy_allows() -> None:
    gateway, _ = _gateway()
    decision = _session(gateway).authorize(kind="tool_call", tool="get_secret", target="vault")
    assert decision.outcome == "deny"
    assert gw.REASON_R4_PROHIBITED in decision.reasons


def test_supervised_tier_asks_for_r2() -> None:
    gateway, _ = _gateway()
    session = _session(gateway, autonomy_tier="supervised")
    decision = session.authorize(kind="network_egress", tool="fetch", target="api.example.com")
    assert decision.outcome == "ask"


def test_approval_is_single_use_and_exact() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    push = {
        "kind": "process_exec",
        "tool": "execute_bash",
        "target": ROOT,
        "executable": "bash",
        "jail": JAILED,
    }
    first = session.authorize(command="git push origin main", **push)
    assert first.outcome == "ask"
    gateway.approvals.approve("run-1", first.fingerprint, "alice")
    # A different command is a different action: still asks.
    assert session.authorize(command="git push origin other", **push).outcome == "ask"
    approved = session.authorize(command="git push origin main", **push)
    assert approved.outcome == "allow"
    assert gw.REASON_APPROVED in approved.reasons
    assert session.authorize(command="git push origin main", **push).outcome == "ask"


def test_approval_never_overrides_policy_deny() -> None:
    engine = FakeEngine()
    gateway, _ = _gateway(engine)
    session = _session(gateway)
    first = session.authorize(kind="tool_call", tool="send_email", target="mail")
    assert first.outcome == "ask"
    gateway.approvals.approve("run-1", first.fingerprint, "alice")
    engine.allow["agent_policy"] = False
    assert session.authorize(kind="tool_call", tool="send_email", target="mail").outcome == "deny"


def test_grant_seam_turns_ask_into_allow() -> None:
    class AllGrants:
        def covers(self, action: GatewayAction, capabilities: Capabilities) -> bool:
            return True

    gateway, _ = _gateway(grants=AllGrants())
    decision = _session(gateway).authorize(kind="tool_call", tool="send_email", target="mail")
    assert decision.outcome == "allow"
    assert gw.REASON_GRANT in decision.reasons


def test_caller_cannot_lower_its_risk_class() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    action = session.action(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command="git push",
        executable="bash",
        jail=JAILED,
    )
    forged = dataclasses.replace(action, risk=RiskClass.R0)
    assert gateway.authorize(forged).outcome == "ask"


def test_hand_built_decision_is_not_allowed() -> None:
    fake = GatewayDecision(outcome="allow", reasons=(), audit_id="x", policy_version="v")
    assert not verify_decision(fake)
    assert not fake.allowed


# --- policy mapping ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "policies"),
    [
        (
            {"kind": "file_read", "tool": "view", "target": f"{ROOT}/a"},
            ["agent_policy", "filesystem_access"],
        ),
        (
            {"kind": "file_write", "tool": "edit", "target": f"{ROOT}/a"},
            ["agent_policy", "filesystem_access"],
        ),
        (
            {
                "kind": "process_exec",
                "tool": "execute_bash",
                "target": ROOT,
                "command": "ls",
                "executable": "bash",
            },
            ["agent_policy", "tool_jail"],
        ),
        (
            {"kind": "network_egress", "tool": "fetch", "target": "api.example.com"},
            ["agent_policy", "network_egress"],
        ),
        (
            {"kind": "mcp_tool_call", "tool": "gh__list_issues", "target": "h", "egress_host": "h"},
            ["agent_policy", "network_egress"],
        ),
        ({"kind": "tool_call", "tool": "list_issues", "target": "x"}, ["agent_policy"]),
    ],
)
def test_policy_families_per_action_kind(kwargs: dict, policies: list[str]) -> None:
    engine = FakeEngine()
    gateway, _ = _gateway(engine)
    _session(gateway).authorize(**kwargs)
    assert [policy for policy, _ in engine.calls] == policies


def test_budget_policy_only_when_budget_present() -> None:
    engine = FakeEngine()
    gateway, _ = _gateway(engine)
    session = _session(gateway, budget=BudgetFigures(tokens_used=10, max_tokens=100))
    session.authorize(kind="tool_call", tool="list_issues", target="x")
    budget_inputs = [payload for policy, payload in engine.calls if policy == "budget_policy"]
    assert budget_inputs == [{"tokens_used": 10, "max_tokens": 100}]
    agent_input = next(payload for policy, payload in engine.calls if policy == "agent_policy")
    assert agent_input["budget"] == {"tokens_used": 10, "max_tokens": 100}


def test_policy_inputs_use_session_capabilities_and_canonical_operations() -> None:
    engine = FakeEngine()
    gateway, _ = _gateway(engine)
    _session(gateway).authorize(
        kind="process_exec",
        tool="execute_bash",
        target=ROOT,
        command="echo secret=abc",
        executable="bash",
        jail=JAILED,
    )
    agent_input = dict(engine.calls[0][1])
    jail_input = dict(engine.calls[1][1])
    assert agent_input["tool"] == "process_exec"
    assert "process_exec" in agent_input["allowed_tools"]
    assert jail_input == {
        "command": ["bash"],
        "allowed_executables": ["bash", "git"],
        "readonly_rootfs": True,
        "run_as_user": "1000:1000",
        "allow_network": True,
        "require_egress_mediation": False,
        "allowed_hosts": [],
        "requested_hosts": [],
    }


# --- audit ---------------------------------------------------------------------------------


def test_every_decision_is_audited_with_required_fields() -> None:
    gateway, audit = _gateway()
    session = _session(gateway)
    outcomes = [
        session.authorize(kind="file_read", tool="view", target=f"{ROOT}/a.py").outcome,
        session.authorize(kind="tool_call", tool="send_email", target="mail").outcome,
        session.authorize(kind="tool_call", tool="get_secret", target="vault").outcome,
    ]
    assert outcomes == ["allow", "ask", "deny"]
    assert [record.outcome for record in audit.records] == outcomes
    record = audit.records[1]
    metadata = record.as_metadata()
    for key in (
        "principal",
        "run_id",
        "action_kind",
        "tool",
        "target",
        "gateway_outcome",
        "reasons",
        "policy_version",
        "risk_class",
    ):
        assert metadata[key] not in (None, "", []), key
    assert (record.principal, record.run_id, record.risk_class) == ("alice", "run-1", "R3")
    assert record.policy_version == "sha256:fake"
