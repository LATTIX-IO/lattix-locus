"""Computer-use risk classification and gateway decisions (LOCUS-341).

Policy is a FakeEngine here (allow all); the Rego side (app lists, secret
fields, navigation scheme) is tested against real OPA in
tests/policy/test_computer_use_opa.py and policies/tests/computer_use_test.rego.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.gateway import (
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    RiskClass,
    UiFacts,
    classify_ui,
    computer_use_input,
    policy_inputs,
)
from tests.gateway_support import FakeEngine


def _ui(control: str = "click", **kwargs: Any) -> UiFacts:
    kwargs.setdefault("surface", "browser")
    return UiFacts.create(control=control, **kwargs)


@pytest.mark.parametrize(
    ("kind", "ui", "expected"),
    [
        ("ui_observe", _ui("observe", surface="desktop"), RiskClass.R0),
        ("browser_read", _ui("read"), RiskClass.R0),
        ("browser_read", _ui("screenshot"), RiskClass.R1),
        ("browser_navigate", _ui("navigate", url_scheme="https"), RiskClass.R2),
        ("browser_act", _ui("click", role="link", name="Docs"), RiskClass.R2),
        ("ui_click", _ui("click", surface="desktop", role="Button", name="Bold"), RiskClass.R2),
        ("browser_act", _ui("fill", role="textbox", name="Search"), RiskClass.R2),
        (
            "ui_type",
            _ui("type", surface="desktop", role="Document", name="Text editor"),
            RiskClass.R2,
        ),
        ("browser_act", _ui("click", role="button", name="Pay now"), RiskClass.R3),
        ("browser_act", _ui("click", role="button", name="Delete"), RiskClass.R3),
        ("browser_act", _ui("click", role="button", name="Send message"), RiskClass.R3),
        ("browser_act", _ui("click", role="button", name="Confirm transfer"), RiskClass.R3),
        ("browser_act", _ui("click", role="button", name="Buy"), RiskClass.R3),
        ("ui_click", _ui("click", surface="desktop", role="Button", name="Submit"), RiskClass.R3),
        (
            "browser_act",
            _ui("click", role="button", name="Continue", submits_form=True, form_sensitive=True),
            RiskClass.R3,
        ),
        (
            "browser_act",
            _ui(
                "press",
                role="textbox",
                name="Note",
                key="Enter",
                submits_form=True,
                form_text="Send",
            ),
            RiskClass.R3,
        ),
        ("browser_act", _ui("press", role="textbox", name="Note", key="Tab"), RiskClass.R2),
        ("browser_act", _ui("fill", input_type="password", name="Pass"), RiskClass.R4),
        ("browser_act", _ui("fill", autocomplete="cc-number", name="Number"), RiskClass.R4),
        ("browser_act", _ui("fill", field_id="cvv"), RiskClass.R4),
        ("browser_act", _ui("fill", name="Social Security Number"), RiskClass.R4),
        ("browser_act", _ui("fill", name="One-time code"), RiskClass.R4),
        ("browser_act", _ui("fill", label="Credit card"), RiskClass.R4),
        ("browser_act", _ui("press", input_type="password", key="Enter"), RiskClass.R4),
        ("browser_act", _ui("select", autocomplete="cc-exp-month"), RiskClass.R4),
        ("ui_type", _ui("type", surface="desktop", is_password=True), RiskClass.R4),
        ("ui_key", _ui("key", surface="desktop", is_password=True, key="a"), RiskClass.R4),
        ("ui_key", _ui("key", surface="desktop", key="win+r"), RiskClass.R3),
        ("ui_key", _ui("key", surface="desktop", key="ctrl+s"), RiskClass.R2),
        ("browser_act", None, RiskClass.R4),
        ("browser_act", _ui("drag"), RiskClass.R4),
        ("ui_click", _ui("observe", surface="desktop"), RiskClass.R4),
    ],
)
def test_classify_ui(kind: str, ui: UiFacts | None, expected: RiskClass) -> None:
    assert classify_ui(kind, ui) == expected


def test_clicking_a_password_field_is_not_typing() -> None:
    assert classify_ui("browser_act", _ui("click", input_type="password")) == RiskClass.R2


def test_screen_text_only_raises_risk() -> None:
    # A page can mislabel a destructive button; it then gets the default class,
    # never less (documented limitation: label text is untrusted).
    assert classify_ui("browser_act", _ui("click", name="OK")) == RiskClass.R2
    assert classify_ui("browser_act", _ui("click", name="OK, delete it")) == RiskClass.R3


def test_ui_facts_are_bounded_and_summarised_without_values() -> None:
    facts = UiFacts.create(surface="browser", control="fill", name="x" * 1000, is_password=1)
    assert len(facts.name) == 300 and facts.is_password is True
    summary = facts.as_summary()
    assert summary["ui_control"] == "fill" and "ui_name" in summary


# --------------------------------------------------------------------------- #
# Gateway
# --------------------------------------------------------------------------- #
class _AllGrants:
    """A grant verifier that covers everything (worst case for the taint gate)."""

    ready = True

    def covers(self, action: gw.GatewayAction, capabilities: Capabilities) -> bool:  # noqa: ARG002
        return True


def _gateway(**kwargs: Any) -> tuple[Gateway, list[GatewayAuditRecord]]:
    audit: list[GatewayAuditRecord] = []
    return Gateway(FakeEngine(), audit.append, **kwargs), audit


def _session(gateway: Gateway, **caps: Any) -> gw.GatewaySession:
    base = Capabilities(
        allowed_tools=frozenset(gw.COMPUTER_USE_KINDS | {"network_egress"}),
        allowed_egress_hosts=("127.0.0.1",),
        allowed_apps=("notepad.exe",),
    )
    for key, value in caps.items():
        base = replace(base, **{key: value})
    return gateway.open_session(
        run_id="run-cu", principal="alice", engine="test", capabilities=base
    )


def _act(session: gw.GatewaySession, ui: UiFacts, **args: Any) -> gw.GatewayDecision:
    return session.authorize(kind="browser_act", tool="browser_act", ui=ui, target="t", args=args)


def test_password_typing_is_denied_even_with_grant_and_approval() -> None:
    gateway, audit = _gateway(grants=_AllGrants())
    session = _session(gateway)
    ui = _ui("fill", input_type="password", name="Password")
    first = _act(session, ui, text="hunter2")
    assert first.outcome == "deny" and gw.REASON_R4_PROHIBITED in first.reasons
    gateway.approvals.approve("run-cu", first.fingerprint, "alice")
    again = _act(session, ui, text="hunter2")
    assert again.outcome == "deny"
    # The typed value never reaches the audit record.
    assert all("hunter2" not in str(record.as_metadata()) for record in audit)


def test_pay_now_click_asks_and_a_grant_does_not_cover_it() -> None:
    gateway, _ = _gateway(grants=_AllGrants())
    session = _session(gateway)
    decision = _act(session, _ui("click", role="button", name="Pay now"))
    assert decision.outcome == "ask" and decision.risk == RiskClass.R3
    assert gw.REASON_TAINT_NO_GRANT in decision.reasons
    assert gw.REASON_GRANT not in decision.reasons


def test_human_approval_of_the_exact_click_allows_it_once() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    pay = _ui("click", role="button", name="Pay now")
    asked = _act(session, pay)
    gateway.approvals.approve("run-cu", asked.fingerprint, "alice")
    # A different element is not covered by that approval.
    assert _act(session, _ui("click", role="button", name="Delete")).outcome == "ask"
    allowed = _act(session, pay)
    assert allowed.outcome == "allow" and gw.REASON_APPROVED in allowed.reasons
    assert _act(session, pay).outcome == "ask"  # single use


def test_plain_click_is_allowed_in_tiered_and_asks_when_supervised() -> None:
    gateway, _ = _gateway()
    assert _act(_session(gateway), _ui("click", name="Docs")).outcome == "allow"
    supervised = _session(gateway, autonomy_tier="supervised")
    assert _act(supervised, _ui("click", name="Docs")).outcome == "ask"


def test_caller_cannot_lower_the_class_by_dropping_facts() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    action = session.action(kind="browser_act", tool="browser_act", ui=_ui("click", name="Pay"))
    forged = replace(action, risk=RiskClass.R0, tainted=False)
    decision = gateway.authorize(forged)
    assert decision.outcome == "ask" and decision.risk == RiskClass.R3


def test_computer_use_kinds_are_always_tainted() -> None:
    gateway, _ = _gateway()
    action = _session(gateway).action(kind="ui_click", tool="desktop_click", ui=_ui("click"))
    assert action.tainted is True


def test_policy_inputs_include_computer_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_COMPUTER_USE_DENIED_APPS", "Calc.exe, mspaint.exe")
    gateway, _ = _gateway()
    session = _session(gateway, denied_apps=("wordpad.exe",))
    ui = UiFacts.create(surface="browser", control="type", app="Notepad.exe", is_password=True)
    action = session.action(kind="ui_type", tool="desktop_type", ui=ui)
    plans = dict(policy_inputs(action, session.capabilities))
    payload = plans["computer_use"]
    # The surface comes from the kind, not the tool's facts.
    assert payload["surface"] == "desktop"
    assert payload["app"] == "notepad.exe"
    assert payload["allowed_apps"] == ["notepad.exe"]
    assert payload["denied_apps"] == ["calc.exe", "mspaint.exe", "wordpad.exe"]
    assert payload["sensitive_field"] is True
    assert plans["agent_policy"]["tool"] == "ui_type"


def test_navigate_checks_network_egress_and_scheme() -> None:
    gateway, _ = _gateway()
    session = _session(gateway)
    ui = UiFacts.create(surface="browser", control="navigate", url_scheme="https")
    action = session.action(
        kind="browser_navigate", tool="browser_navigate", ui=ui, egress_host="example.com"
    )
    plans = dict(policy_inputs(action, session.capabilities))
    assert plans["network_egress"]["target"] == "example.com"
    assert computer_use_input(action, session.capabilities)["url_scheme"] == "https"
    assert action.risk == RiskClass.R2
