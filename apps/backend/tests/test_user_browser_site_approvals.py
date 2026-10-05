"""Approve user-browser asks site by site (desktop UX batch, LOCUS-350).

The principal starts every tier with empty site lists and approves sites as the
agent needs them: an ask on a site carries that site, and "Always allow on
<site>" adds it to the tier's list with a full-list PUT /user-browser/tier
(still shell-confirmed on the desktop). Denying an ask grants nothing.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import app, store
from app.request_security import CapabilityEffect, classify_shell_proof
from app.user_browser import escalation_site_offer

from locus_runtime.computer_use.user_browser import tiers as tiers_mod
from locus_runtime.gateway import (
    GatewayAction,
    GatewayCaller,
    GatewayDecision,
    RiskClass,
    UiFacts,
)

BEARER = os.environ["LOCUS_API_BEARER_TOKEN"]
PRINCIPAL = {"Authorization": f"Bearer {BEARER}", "x-locus-actor": "locus-admin"}
client = TestClient(app)
SHELL_SECRET = bytes(range(100, 132))


@pytest.fixture(autouse=True)
def fresh_tiers() -> Iterator[None]:
    previous = tiers_mod._STORE  # noqa: SLF001
    tiers_mod.install_tier_store(tiers_mod.TierStore())
    try:
        yield
    finally:
        tiers_mod.install_tier_store(previous)


@pytest.fixture()
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from locus_tooling import shell_confirmation as sc

    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    sc.install_secret(SHELL_SECRET)
    try:
        yield
    finally:
        sc.install_secret(None)


def _tier_proof(body: dict[str, Any]) -> str:
    import secrets as _secrets
    import time as _time

    from locus_tooling import shell_confirmation as sc

    return sc.proof_header(
        SHELL_SECRET,
        lambda nonce, stamp: sc.tier_message(
            tier=body["tier"],
            allowlisted_sites=body.get("allowlisted_sites"),
            granted_sites=body.get("granted_sites"),
            nonce=nonce,
            timestamp=stamp,
        ),
        nonce=_secrets.token_hex(16),
        timestamp=int(_time.time()),
    )


def _put_tier(body: dict[str, Any], proof: str | None = None) -> Any:
    headers = dict(PRINCIPAL)
    if proof is not None:
        headers["X-Locus-Shell-Proof"] = proof
    return client.put("/user-browser/tier", json=body, headers=headers)


def _settings(tier: str, allow: tuple[str, ...] = (), grant: tuple[str, ...] = ()) -> Any:
    store_ = tiers_mod.TierStore()
    if tier != "strict":
        store_.update(
            tier=tier,
            allowlisted_sites=list(allow),
            granted_sites=list(grant),
            actor="locus-admin",
            principal_type="user",
            acknowledge_risk=True,
        )
    return store_.settings


# --- empty lists and single-site additions -------------------------------------
@pytest.mark.parametrize("tier", ["strict", "assisted", "trusted", "open"])
def test_every_tier_accepts_empty_site_lists(tier: str) -> None:
    body = {"tier": tier, "allowlisted_sites": [], "granted_sites": [], "acknowledge_risk": True}
    done = _put_tier(body)
    assert done.status_code == 200, done.text
    saved = done.json()
    assert saved["tier"] == tier and saved["effective_tier"] == tier
    assert saved["allowlisted_sites"] == [] and saved["granted_sites"] == []


@pytest.mark.parametrize("tier", ["assisted", "trusted", "open"])
def test_desktop_widening_with_empty_lists_is_confirmed_by_the_shell(
    desktop: None, tier: str
) -> None:
    body = {"tier": tier, "allowlisted_sites": [], "granted_sites": [], "acknowledge_risk": True}
    assert _put_tier(body).status_code == 403  # widening needs the proof...
    done = _put_tier(body, _tier_proof(body))  # ...which covers the empty lists
    assert done.status_code == 200, done.text
    assert done.json()["effective_tier"] == tier


def test_adding_one_site_is_a_full_list_put_that_the_shell_confirms(desktop: None) -> None:
    start = {
        "tier": "trusted",
        "allowlisted_sites": [],
        "granted_sites": [],
        "acknowledge_risk": True,
    }
    assert _put_tier(start, _tier_proof(start)).status_code == 200
    one_more = {**start, "granted_sites": ["https://mail.example.com/inbox"]}
    refused = _put_tier(one_more)
    assert refused.status_code == 403  # a new site widens
    added = _put_tier(one_more, _tier_proof(one_more))
    assert added.status_code == 200, added.text
    assert added.json()["granted_sites"] == ["example.com"]
    two = {**start, "granted_sites": ["example.com", "docs.example.org"]}
    assert _put_tier(two, _tier_proof(two)).json()["granted_sites"] == [
        "example.com",
        "example.org",
    ]
    # Taking a site off again narrows: no proof.
    assert _put_tier({**start, "granted_sites": ["example.org"]}).status_code == 200


# --- the site an ask offers ------------------------------------------------------
def test_assisted_offers_the_allowlist_and_trusted_the_grant_list() -> None:
    assisted = escalation_site_offer(
        "user_browser_navigate", "news.example.com", _settings("assisted")
    )
    assert assisted == {
        "site": "example.com",
        "browser_tier": "assisted",
        "site_list": "allowlisted_sites",
    }
    trusted = escalation_site_offer("user_browser_act", "example.com", _settings("trusted"))
    assert trusted["site_list"] == "granted_sites"


def test_no_list_offer_when_the_tier_has_none_or_the_site_is_listed() -> None:
    for tier in ("strict", "open"):
        offer = escalation_site_offer("user_browser_act", "example.com", _settings(tier))
        assert offer["site"] == "example.com" and offer["site_list"] is None
    granted = _settings("trusted", grant=("example.com",))
    assert (
        escalation_site_offer("user_browser_act", "app.example.com", granted)["site_list"] is None
    )
    # A widened tier without consent is effectively strict: nothing to add to.
    unconsented = tiers_mod.TierSettings(tier="trusted")
    assert (
        escalation_site_offer("user_browser_act", "example.com", unconsented)["site_list"] is None
    )


def test_only_user_browser_actions_with_a_site_carry_an_offer() -> None:
    assert escalation_site_offer("shell_command", "example.com", _settings("trusted")) == {}
    assert escalation_site_offer("user_browser_act", "", _settings("trusted")) == {}


def _ask_for(run_id: str, site: str) -> None:
    action = GatewayAction.create(
        caller=GatewayCaller(run_id=run_id, principal="locus-admin", engine="native"),
        kind="user_browser_act",
        tool="user_browser.click",
        target=f"https://{site}/compose",
        ui=UiFacts.create(surface="browser", control="click", site=site, tab_shared=True),
    )
    decision = GatewayDecision(
        outcome="ask",
        reasons=("user_browser:needs_approval",),
        audit_id="audit-1",
        policy_version="test",
        risk=RiskClass.R2,
        action_kind=action.kind,
        tool=action.tool,
        target=action.target,
        fingerprint=f"fp-{run_id}",
    )
    main_module._gateway_decision_listener(action, decision)  # noqa: SLF001


def test_a_user_browser_ask_carries_its_site_to_the_approval() -> None:
    tiers_mod.get_tier_store().update(
        tier="trusted",
        allowlisted_sites=[],
        granted_sites=[],
        actor="locus-admin",
        principal_type="user",
        acknowledge_risk=True,
    )
    store.run_details["run-site-offer"] = {"access": {"actor": "locus-admin"}}
    try:
        _ask_for("run-site-offer", "mail.example.com")
        escalation = store.run_details["run-site-offer"]["escalations"][0]
        assert escalation["kind"] == "gateway" and escalation["status"] == "pending"
        assert escalation["site"] == "example.com"
        assert escalation["browser_tier"] == "trusted"
        assert escalation["site_list"] == "granted_sites"
        listed = client.get("/workflow-runs/run-site-offer/escalations", headers=PRINCIPAL)
        assert listed.json()["escalations"][0]["site_list"] == "granted_sites"
    finally:
        store.run_details.pop("run-site-offer", None)


# --- deny ---------------------------------------------------------------------------
def test_deny_records_the_decision_grants_nothing_and_needs_no_proof(desktop: None) -> None:
    rule = classify_shell_proof("POST", "/workflow-runs/r/escalations/e/deny")
    assert rule is not None and rule.effect == CapabilityEffect.NARROWING
    store.runs["run-deny"] = main_module.WorkflowRunSummary(
        id="run-deny", title="deny", status="Running", updatedAt="now", progressLabel="running"
    )
    store.run_details["run-deny"] = {"access": {"actor": "locus-admin"}}
    try:
        _ask_for("run-deny", "example.com")
        escalation_id = store.run_details["run-deny"]["escalations"][0]["id"]
        denied = client.post(
            f"/workflow-runs/run-deny/escalations/{escalation_id}/deny", headers=PRINCIPAL
        )
        assert denied.status_code == 200, denied.text
        body = denied.json()["escalation"]
        assert body["status"] == "denied" and body["denied_by"] == "locus-admin"
        assert "grant_id" not in body
        again = client.post(
            f"/workflow-runs/run-deny/escalations/{escalation_id}/deny", headers=PRINCIPAL
        )
        assert again.status_code == 409
        missing = client.post("/workflow-runs/run-deny/escalations/nope/deny", headers=PRINCIPAL)
        assert missing.status_code == 404
        event = next(
            e for e in reversed(store.audit_events) if e.action == "workflow.run.escalations.deny"
        )
        assert event.metadata["site"] == "example.com"
    finally:
        store.run_details.pop("run-deny", None)
        store.runs.pop("run-deny", None)
