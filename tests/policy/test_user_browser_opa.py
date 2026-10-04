"""User-browser tiers through the real gateway and real OPA (LOCUS-350, D-25).

The tier x action matrix and the floor, evaluated end to end: the gateway
builds the ``user_browser`` input from process state (tier store, relay
pairing, panic latch) and applies the risk class on top of the Rego decision.
No browser is needed; the tool facts are given as ``UiFacts``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.computer_use import controller as cu
from locus_runtime.computer_use.operations import USER_BROWSER_TOOLS, computer_use_operations
from locus_runtime.computer_use.user_browser import relay as relay_mod
from locus_runtime.computer_use.user_browser import tiers as tiers_mod
from locus_runtime.computer_use.user_browser.pairing import CHROMIUM_ORIGIN
from locus_runtime.gateway import Capabilities, Gateway, GatewayAuditRecord, RiskClass, UiFacts
from locus_runtime.policy_engine import OpaSidecarEngine

KEY = "opa-test-pairing-key"


class _AllGrants:
    """Worst case for the taint gate: a verifier that covers everything."""

    ready = True

    def covers(self, action: gw.GatewayAction, capabilities: Capabilities) -> bool:  # noqa: ARG002
        return True


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    previous = (cu._DEFAULT, cu._INSTALLED, relay_mod._HUB, tiers_mod._STORE)  # noqa: SLF001
    controller = cu.ComputerUseController("takeover")
    cu.install_controller(controller)
    state: dict[str, Any] = {"key": KEY}
    hub = relay_mod.RelayHub(key_loader=lambda: state["key"])
    hub.hello(origin=CHROMIUM_ORIGIN, presented_key=KEY, browser="chrome")
    relay_mod.install_hub(hub)
    store = tiers_mod.TierStore()
    tiers_mod.install_tier_store(store)
    state.update(controller=controller, hub=hub, store=store)
    try:
        yield state
    finally:
        cu._DEFAULT, cu._INSTALLED = previous[0], previous[1]  # noqa: SLF001
        relay_mod.install_hub(previous[2])
        tiers_mod.install_tier_store(previous[3])


@pytest.fixture()
def session(opa_engine: OpaSidecarEngine) -> tuple[Gateway, gw.GatewaySession, list[Any]]:
    audit: list[GatewayAuditRecord] = []
    gateway = Gateway(opa_engine, audit.append, grants=_AllGrants())
    caps = Capabilities(allowed_tools=frozenset(computer_use_operations(USER_BROWSER_TOOLS)))
    return (
        gateway,
        gateway.open_session(run_id="run-ub", principal="alice", engine="test", capabilities=caps),
        audit,
    )


def _tier(store: tiers_mod.TierStore, tier: str, **lists: Any) -> None:
    store.update(
        tier=tier,
        allowlisted_sites=lists.get("allowlisted", ["example.com"]),
        granted_sites=lists.get("granted", ["example.com"]),
        actor="alice",
        principal_type="user",
        acknowledge_risk=True,
    )


ACTIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "observe": ("user_browser_read", {"control": "observe"}),
    "screenshot": ("user_browser_read", {"control": "screenshot"}),
    "navigate": ("user_browser_navigate", {"control": "navigate", "url_scheme": "https"}),
    "click": ("user_browser_act", {"control": "click", "role": "button", "name": "Next"}),
    "fill": ("user_browser_act", {"control": "fill", "role": "textbox", "name": "Note"}),
    "pay": ("user_browser_act", {"control": "click", "role": "button", "name": "Pay now"}),
    "security": (
        "user_browser_act",
        {"control": "click", "role": "button", "name": "Account security"},
    ),
}

# (tier, action) -> outcome, on example.com, a shared tab, listed in both lists.
MATRIX: dict[str, dict[str, str]] = {
    "strict": {
        "observe": "allow",
        "screenshot": "allow",
        "navigate": "ask",
        "click": "ask",
        "fill": "ask",
        "pay": "ask",
        "security": "ask",
    },
    "assisted": {
        "observe": "allow",
        "screenshot": "allow",
        "navigate": "allow",
        "click": "ask",
        "fill": "ask",
        "pay": "ask",
        "security": "ask",
    },
    "trusted": {
        "observe": "allow",
        "screenshot": "allow",
        "navigate": "allow",
        "click": "allow",
        "fill": "allow",
        "pay": "ask",
        "security": "ask",
    },
    "open": {
        "observe": "allow",
        "screenshot": "allow",
        "navigate": "allow",
        "click": "allow",
        "fill": "allow",
        "pay": "allow",
        "security": "allow",
    },
}


def _authorize(
    sess: gw.GatewaySession, action: str, *, site: str = "example.com", **facts: Any
) -> gw.GatewayDecision:
    kind, base = ACTIONS[action]
    fields = {"tab_shared": True, **base, **facts}
    ui = UiFacts.create(surface="browser", app="locus-user-browser", site=site, **fields)
    return sess.authorize(kind=kind, tool=f"user_browser.{action}", target=site, ui=ui)


@pytest.mark.parametrize("tier", list(MATRIX))
@pytest.mark.parametrize("action", list(ACTIONS))
def test_tier_by_action_matrix(
    env: dict[str, Any],
    session: tuple[Gateway, gw.GatewaySession, list[Any]],
    tier: str,
    action: str,
) -> None:
    _, sess, _ = session
    if tier != "strict":
        _tier(env["store"], tier)
    decision = _authorize(sess, action)
    assert decision.outcome == MATRIX[tier][action], decision.describe()
    if decision.outcome == "ask":
        # A standing grant (here: one covering everything) never turns it into allow.
        assert gw.REASON_GRANT not in decision.reasons


def test_irreversible_classification_in_the_user_profile(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, _ = session
    assert _authorize(sess, "pay").risk == RiskClass.R3
    assert _authorize(sess, "security").risk == RiskClass.R3  # account / security settings
    assert _authorize(sess, "click").risk == RiskClass.R2


def test_open_tier_records_consent_reason_and_floor_still_holds(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, audit = session
    _tier(env["store"], "open")
    paid = _authorize(sess, "pay")
    assert paid.outcome == "allow" and gw.REASON_OPEN_TIER in paid.reasons
    assert audit[-1].outcome == "allow" and audit[-1].risk_class == "R3"
    for name in ("Password", "Card number", "One-time code"):
        typed = _authorize(sess, "fill", name=name)
        assert typed.outcome == "deny" and typed.risk == RiskClass.R4, name
    assert _authorize(sess, "fill", input_type="password", name="x").outcome == "deny"


def test_widened_tier_without_consent_behaves_as_strict(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, _ = session
    store: tiers_mod.TierStore = env["store"]
    _tier(store, "open")
    # A consent record for another tier does not count (e.g. a tampered store).
    store._settings = tiers_mod.TierSettings(  # noqa: SLF001
        tier="open",
        consent=tiers_mod.ConsentRecord("trusted", "alice", "user", 0.0, "x"),
    )
    assert _authorize(sess, "click").outcome == "ask"
    assert _authorize(sess, "pay").outcome == "ask"


def test_panic_and_unpairing_deny_in_every_tier(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, _ = session
    _tier(env["store"], "open")
    env["controller"].panic("test")
    panicked = _authorize(sess, "observe")
    assert panicked.outcome == "deny"
    assert any("panic" in reason for reason in panicked.reasons)
    env["controller"].reset("test")
    env["key"] = None  # unpaired: the stored key is gone
    unpaired = _authorize(sess, "observe")
    assert unpaired.outcome == "deny"
    assert any("not_paired" in reason for reason in unpaired.reasons)


def test_unshared_tabs_and_unlisted_sites(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, _ = session
    assert _authorize(sess, "observe", tab_shared=False).outcome == "deny"
    _tier(env["store"], "assisted")
    assert _authorize(sess, "observe", tab_shared=False).outcome == "allow"  # allowlisted
    assert _authorize(sess, "observe", site="other.org", tab_shared=False).outcome == "deny"
    assert _authorize(sess, "navigate", site="other.org").outcome == "ask"


def test_run_must_list_the_operation(env: dict[str, Any], opa_engine: OpaSidecarEngine) -> None:
    gateway = Gateway(opa_engine, lambda record: None)
    sess = gateway.open_session(
        run_id="run-ro",
        principal="alice",
        engine="test",
        capabilities=Capabilities(allowed_tools=frozenset({"user_browser_read"})),
    )
    _tier(env["store"], "open")
    assert _authorize(sess, "observe").outcome == "allow"
    assert _authorize(sess, "click").outcome == "deny"  # agent_policy: not in the envelope


def test_navigation_scheme_floor(
    env: dict[str, Any], session: tuple[Gateway, gw.GatewaySession, list[Any]]
) -> None:
    _, sess, _ = session
    _tier(env["store"], "open")
    assert _authorize(sess, "navigate", url_scheme="javascript").outcome == "deny"
    assert _authorize(sess, "navigate", url_scheme="file", site="").outcome == "deny"
