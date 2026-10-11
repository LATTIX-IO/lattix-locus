"""LOCUS-432: delegated child sessions are registered and parent-attenuated."""
import dataclasses
import pytest
from locus_runtime.gateway import Capabilities, Gateway, RiskClass
from tests.gateway_support import FakeEngine


def make():
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
        max_risk_class=RiskClass.R2,
    )
    parent = gateway.open_session(run_id="run-1", principal="alice", engine="parent", capabilities=caps)
    child_caps = dataclasses.replace(caps, allowed_tools=frozenset({"list_issues"}), read_roots=("/work/reports",), max_actions=3, max_risk_class=RiskClass.R0)
    return gateway, parent, child_caps


def test_child_uses_parent_principal_and_cannot_act_above_ceiling():
    gateway, parent, caps = make()
    child = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    assert child.caller.principal == "alice"
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
    {"max_risk_class": RiskClass.R3},
    {"max_risk_class": None},
    {"data_classification": "restricted"},
    {"runtime_profile": "evals"},
])
def test_reject_expansion(change):
    gateway, parent, caps = make()
    with pytest.raises(PermissionError):
        gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=dataclasses.replace(caps, **change))


def test_revoked_parent_invalidates_child():
    gateway, parent, caps = make()
    child = gateway.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
    parent.close()
    assert child.authorize(kind="tool_call", tool="list_issues").outcome == "deny"


def test_foreign_parent_and_renamed_run_rejected():
    gateway, parent, caps = make()
    with pytest.raises(ValueError):
        gateway.open_child_session(parent, run_id="different:pod1", engine="worker", capabilities=caps)
    foreign = Gateway(FakeEngine(), lambda _: None)
    with pytest.raises(PermissionError):
        foreign.open_child_session(parent, run_id="run-1:pod1", engine="worker", capabilities=caps)
