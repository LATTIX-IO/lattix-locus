"""LOCUS-432: a pod's registered maximum risk class is a hard PEP boundary.

The real gateway is exercised with deterministic policy-engine/grant fixtures,
never a test-only authorizer that bypasses its PEP.
"""

from __future__ import annotations

import dataclasses

import pytest

from locus_runtime import gateway as gw
from locus_runtime.policy_engine import Decision
from tests.gateway_support import FakeEngine

ROOT = "/work/repo"


class _AllowAllGrants:
    def covers(self, action: gw.GatewayAction, capabilities: gw.Capabilities) -> bool:
        return True


def _setup(*, ceiling: gw.RiskClass | None, engine: FakeEngine | None = None,
           grants: object | None = None) -> tuple[gw.Gateway, gw.GatewaySession, list[gw.GatewayAuditRecord]]:
    records: list[gw.GatewayAuditRecord] = []
    gateway = gw.Gateway(engine or FakeEngine(), records.append, grants=grants)
    session = gateway.open_session(
        run_id="task:pod-1",
        principal="principal-a",
        engine="ephemeral-expert",
        capabilities=gw.Capabilities(
            allowed_tools=frozenset({
                "read_file", "write_file", "process_exec", "llm_call", "network_egress",
                "send_email", "list_issues", "mcp__send_email", "ui_click", "browser_navigate",
            }),
            read_roots=(ROOT,),
            write_roots=(ROOT,),
            allowed_egress_hosts=("api.example.com", "models.example.com"),
            max_risk_class=ceiling,
        ),
    )
    return gateway, session, records


@pytest.mark.parametrize(
    ("ceiling", "kind", "kwargs", "expected"),
    [
        (gw.RiskClass.R0, "file_read", {"tool": "view", "target": ROOT + "/brief.txt"}, "allow"),
        (gw.RiskClass.R0, "file_write", {"tool": "edit", "target": ROOT + "/brief.txt"}, "deny"),
        (gw.RiskClass.R1, "model_call", {"tool": "model:ollama", "egress_host": "127.0.0.1"}, "allow"),
        (gw.RiskClass.R1, "model_call", {"tool": "model:openai", "egress_host": "models.example.com"}, "deny"),
        (gw.RiskClass.R1, "network_egress", {"tool": "fetch", "target": "api.example.com"}, "deny"),
        (gw.RiskClass.R2, "model_call", {"tool": "model:openai", "egress_host": "models.example.com"}, "allow"),
        (gw.RiskClass.R2, "tool_call", {"tool": "list_issues"}, "allow"),
        (gw.RiskClass.R2, "tool_call", {"tool": "send_email"}, "deny"),
        (gw.RiskClass.R2, "mcp_tool_call", {"tool": "mcp__send_email"}, "deny"),
        (gw.RiskClass.R3, "mcp_tool_call", {"tool": "mcp__send_email"}, "ask"),
        (gw.RiskClass.R2, "process_exec", {"tool": "execute_bash", "command": "git push origin main"}, "deny"),
    ],
)
def test_every_mediated_action_respects_risk_ceiling(
    ceiling: gw.RiskClass, kind: str, kwargs: dict[str, object], expected: str
) -> None:
    _, session, audit = _setup(ceiling=ceiling)
    decision = session.authorize(kind=kind, **kwargs)
    assert decision.outcome == expected
    assert gw.verify_decision(decision)
    assert audit[-1].risk_class == decision.risk.label
    if expected == "deny":
        assert gw.REASON_RISK_CEILING in decision.reasons
        assert audit[-1].outcome == "deny"


def test_computer_use_risk_ceiling_applies_to_perceived_ui_facts() -> None:
    _, session, _ = _setup(ceiling=gw.RiskClass.R2)
    ordinary = gw.UiFacts(surface="desktop", control="click", name="Open")
    destructive = gw.UiFacts(surface="desktop", control="click", name="Delete account")
    assert session.authorize(kind="ui_click", tool="ui_click", ui=ordinary).outcome == "allow"
    verdict = session.authorize(kind="ui_click", tool="ui_click", ui=destructive)
    assert verdict.risk == gw.RiskClass.R3
    assert verdict.outcome == "deny"
    assert gw.REASON_RISK_CEILING in verdict.reasons


def test_policy_risk_floor_applies_before_risk_ceiling() -> None:
    class RaisingPolicy(FakeEngine):
        def decide(self, policy: str, input: dict) -> Decision:  # noqa: A002
            result = super().decide(policy, input)
            if policy == "filesystem_access":
                return dataclasses.replace(result, outputs={"risk_floor": 3})
            return result

    _, session, records = _setup(ceiling=gw.RiskClass.R1, engine=RaisingPolicy())
    result = session.authorize(kind="file_read", tool="view", target=ROOT + "/summary.txt")
    assert result.risk == gw.RiskClass.R3
    assert result.outcome == "deny"
    assert gw.REASON_RISK_CEILING in result.reasons
    assert records[-1].risk_class == "R3"


def test_forged_lower_action_risk_does_not_bypass_ceiling() -> None:
    gateway, session, _ = _setup(ceiling=gw.RiskClass.R2)
    action = session.action(kind="process_exec", tool="execute_bash", command="git push main")
    forged = dataclasses.replace(action, risk=gw.RiskClass.R0)
    result = gateway.authorize(forged)
    assert result.risk == gw.RiskClass.R3
    assert result.outcome == "deny"
    assert gw.REASON_RISK_CEILING in result.reasons


def test_approval_and_standing_grants_cannot_override_ceiling() -> None:
    gateway, session, _ = _setup(ceiling=gw.RiskClass.R2, grants=_AllowAllGrants())
    kwargs = {"kind": "tool_call", "tool": "send_email", "target": "mail"}
    action = session.action(**kwargs)
    gateway.approvals.approve(session.caller.run_id, action.fingerprint, "principal-a")
    denied = session.authorize(**kwargs)
    assert denied.outcome == "deny"
    assert gw.REASON_RISK_CEILING in denied.reasons
    assert gw.REASON_GRANT not in denied.reasons
    assert gw.REASON_APPROVED not in denied.reasons


def test_ceiling_is_registered_server_side_not_taken_from_mutable_session_mirror() -> None:
    gateway, session, _ = _setup(ceiling=gw.RiskClass.R0)
    session.capabilities = dataclasses.replace(session.capabilities, max_risk_class=None)
    assert session.authorize(
        kind="file_write", tool="edit", target=ROOT + "/brief.txt"
    ).outcome == "deny"
    session.report_budget(gw.BudgetFigures(tokens_used=1, max_tokens=100))
    assert session.authorize(
        kind="file_write", tool="edit", target=ROOT + "/brief.txt"
    ).outcome == "deny"


@pytest.mark.parametrize("bad", [gw.RiskClass.R4, "R1", 1, -1, True])
def test_invalid_risk_ceiling_is_rejected_at_session_admission(bad: object) -> None:
    gateway = gw.Gateway(FakeEngine(), lambda _: None)
    caps = gw.Capabilities(max_risk_class=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_risk_class"):
        gateway.open_session(run_id="r", principal="a", engine="e", capabilities=caps)


def test_legacy_sessions_keep_existing_r3_approval_semantics() -> None:
    _, session, _ = _setup(ceiling=None)
    result = session.authorize(kind="tool_call", tool="send_email", target="mail")
    assert result.outcome == "ask"
    assert gw.REASON_RISK_CEILING not in result.reasons
    result = session.authorize(kind="tool_call", tool="get_secret", target="vault")
    assert result.outcome == "deny"
    assert gw.REASON_R4_PROHIBITED in result.reasons


def test_session_risk_ceiling_denies_even_when_caller_action_has_higher_risk_metadata() -> None:
    gateway, session, _ = _setup(ceiling=gw.RiskClass.R0)
    action = session.action(kind="file_read", tool="view", target=ROOT + "/a.txt")
    marked = dataclasses.replace(action, risk=gw.RiskClass.R3)
    decision = gateway.authorize(marked)
    assert decision.outcome == "deny"
    assert decision.risk == gw.RiskClass.R3
