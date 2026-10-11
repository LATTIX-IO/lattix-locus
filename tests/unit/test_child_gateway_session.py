"""LOCUS-432 hardened delegation: no authority, budget, identity or taint widening."""

import dataclasses
from math import inf, nan

import pytest

from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from tests.gateway_support import FakeEngine


def _budget(*, tokens=1000, used=0, duration=60, cost=5.0):
    return BudgetFigures(
        tokens_used=used, max_tokens=tokens, duration_used_seconds=0,
        max_duration_seconds=duration, cost_used_usd=0, max_cost_usd=cost
    )


def make(*, parent_tier="tiered", parent_budget=None):
    gateway = Gateway(FakeEngine(), lambda _: None)
    caps = Capabilities(
        allowed_tools=frozenset({"list_issues", "send_email"}),
        read_roots=("/work",),
        write_roots=("/work",),
        allowed_executables=("bash",),
        allowed_egress_hosts=("api.example.com",),
        allowed_apps=("editor",),
        denied_apps=("vault",),
        max_actions=10,
        max_tool_calls=20,
        max_risk_class=RiskClass.R2,
        autonomy_tier=parent_tier,
        budget=parent_budget or _budget(),
    )
    parent = gateway.open_session(
        run_id="run-1", principal="alice", engine="parent", capabilities=caps
    )
    child_caps = dataclasses.replace(
        caps,
        allowed_tools=frozenset({"list_issues"}),
        read_roots=("/work/reports"),
        max_actions=3,
        max_tool_calls=5,
        max_risk_class=RiskClass.R0,
        budget=_budget(tokens=200, duration=20, cost=1.0),
    )
    return gateway, parent, child_caps


def test_child_uses_parent_principal_and_scopes_action_authorization():
    gateway, parent, caps = make()
    child = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    assert child.caller.principal == "alice"
    assert child.caller.run_id.startswith("run-1:pod1:")
    assert child.authorize(kind="tool_call", tool="list_issues").outcome == "allow"
    assert child.authorize(kind="tool_call", tool="send_email").outcome == "deny"


@pytest.mark.parametrize("change", [
    {"allowed_tools": frozenset({"list_issues", "send_email", "admin"})},
    {"read_roots": ("/",)},
    {"write_roots": ("/other",)},
    {"allowed_egress_hosts": ("evil.test",)},
    {"allowed_executables": ("bash", "powershell")},
    {"allowed_apps": ("editor", "vault")},
    {"denied_apps": ()},
    {"max_actions": 100},
    {"max_actions": None},
    {"max_actions": 0},
    {"max_tool_calls": 0},
    {"max_tool_calls": 100},
    {"max_risk_class": RiskClass.R3},
    {"max_risk_class": None},
    {"data_classification": "restricted"},
    {"runtime_profile": "evals"},
    {"autonomy_tier": "envelope-autonomous"},
    {"budget": None},
    {"budget": _budget(tokens=2000)},
    {"budget": _budget(tokens=200, duration=120)},
    {"budget": _budget(tokens=200, cost=8.0)},
    {"budget": _budget(tokens=200, used=201)},
    {"budget": dataclasses.replace(_budget(), max_tokens=inf)},
    {"budget": dataclasses.replace(_budget(), max_tokens=nan)},
    {"budget": dataclasses.replace(_budget(), duration_used_seconds=None)},
])
def test_reject_authority_or_resource_expansion(change):
    gateway, parent, caps = make()
    with pytest.raises(PermissionError):
        gateway.open_child_session(
            parent, run_id="run-1:pod1", engine="worker",
            capabilities=dataclasses.replace(caps, **change)
        )


def test_parent_without_budget_does_not_allow_budgetless_child():
    gateway, parent, caps = make()
    # Fresh child is always bounded, even if a legacy parent had no budget.
    with pytest.raises(PermissionError, match="finite budget"):
        gateway.open_child_session(
            parent, run_id="run-1:pod1", engine="worker",
            capabilities=dataclasses.replace(caps, budget=None)
        )


def test_parent_used_budget_cannot_be_reset():
    gateway, parent, caps = make(parent_budget=_budget(used=80))
    with pytest.raises(PermissionError, match="tokens_used"):
        gateway.open_child_session(
            parent, run_id="run-1:pod1", engine="worker", capabilities=caps
        )


def test_child_can_tighten_autonomy_from_tiered_to_supervised():
    gateway, parent, caps = make()
    child = gateway.open_child_session(
        parent, run_id="run-1:pod1", engine="worker",
        capabilities=dataclasses.replace(caps, autonomy_tier="supervised")
    )
    assert child.capabilities.autonomy_tier == "supervised"


def test_child_cannot_relax_supervised_parent():
    gateway, parent, caps = make(parent_tier="supervised")
    with pytest.raises(PermissionError, match="autonomy tier"):
        gateway.open_child_session(
            parent, run_id="run-1:pod1", engine="worker",
            capabilities=dataclasses.replace(caps, autonomy_tier="tiered")
        )


def test_parent_revoke_denies_descendants_and_new_spawns():
    gateway, parent, caps = make()
    child = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    grandchild = gateway.open_child_session(
        child, run_id=child.caller.run_id + ":subpod", engine="reviewer", capabilities=caps
    )
    parent.close()
    assert child.authorize(kind="tool_call", tool="list_issues").outcome == "deny"
    assert grandchild.authorize(kind="tool_call", tool="list_issues").outcome == "deny"
    with pytest.raises(PermissionError):
        gateway.open_child_session(
            child, run_id=child.caller.run_id + ":retry", engine="worker", capabilities=caps
        )


def test_foreign_parent_and_namespaced_identity_are_required():
    gateway, parent, caps = make()
    for name in ("different:pod1", "run-1:", "run-1"):
        with pytest.raises(ValueError):
            gateway.open_child_session(
                parent, run_id=name, engine="worker", capabilities=caps
            )
    foreign = Gateway(FakeEngine(), lambda _: None)
    with pytest.raises(PermissionError):
        foreign.open_child_session(
            parent, run_id="run-1:pod1", engine="worker", capabilities=caps
        )


def test_retrying_same_pod_name_creates_distinct_approval_identities():
    gateway, parent, caps = make()
    one = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    two = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    assert one.caller.run_id != two.caller.run_id
    assert one.caller.token != two.caller.token


def test_taint_propagates_to_child_after_initial_delegation():
    gateway, parent, caps = make()
    child = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    assert not child.tainted
    # Mimic the gateway's post-approved secret-content taint transition.
    with gateway._lock:
        gateway._sessions[next(
            key for key, rec in gateway._sessions.items()
            if rec.caller.run_id == parent.caller.run_id
        )].tainted = True
    assert child.tainted
    assert child.authorize(kind="tool_call", tool="list_issues").outcome == "allow"
    assert child.tainted
