"""LOCUS-350: user-browser endpoints -- principal-only tier/pairing, loopback-only relay."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

from app.control_status import PostureFacts, evaluate_controls
from app.main import app
from app.user_browser import is_human_principal, relay_request_refusal

from locus_runtime.computer_use import controller as cu
from locus_runtime.computer_use.user_browser import relay as relay_mod
from locus_runtime.computer_use.user_browser import tiers as tiers_mod
from locus_runtime.computer_use.user_browser.pairing import (
    CHROMIUM_ORIGIN,
    PAIRING_SECRET_NAME,
)

BEARER = os.environ["LOCUS_API_BEARER_TOKEN"]
PRINCIPAL = {"Authorization": f"Bearer {BEARER}", "x-locus-actor": "locus-admin"}
AGENT = {"Authorization": f"Bearer {BEARER}", "x-locus-actor": "agent:research"}


def _with_client(host: str) -> Callable[..., Any]:
    """ASGI wrapper that presents ``host`` as the peer address."""

    async def wrapped(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            scope = {**scope, "client": (host, 50123)}
        await app(scope, receive, send)

    return wrapped


client = TestClient(app)
loopback = TestClient(_with_client("127.0.0.1"))  # type: ignore[arg-type]
remote = TestClient(_with_client("192.168.1.20"))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def fresh_state(
    in_memory_keychain: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    # Secret-file fallbacks (and unpair's cleanup) stay inside the test's temp dir.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    previous = (cu._DEFAULT, cu._INSTALLED, relay_mod._HUB, tiers_mod._STORE)  # noqa: SLF001
    controller = cu.ComputerUseController("takeover")
    cu.install_controller(controller)
    hub = relay_mod.RelayHub()  # reads the (in-memory test) keychain like production
    hub.attach(controller)
    relay_mod.install_hub(hub)
    tiers_mod.install_tier_store(tiers_mod.TierStore())
    try:
        yield {"controller": controller, "hub": hub, "keychain": in_memory_keychain}
    finally:
        cu._DEFAULT, cu._INSTALLED = previous[0], previous[1]  # noqa: SLF001
        relay_mod.install_hub(previous[2])
        tiers_mod.install_tier_store(previous[3])


def _pair() -> str:
    response = client.post("/user-browser/pairing", headers=PRINCIPAL)
    assert response.status_code == 200, response.text
    from locus_tooling.native_secrets import get_secret

    key = get_secret(PAIRING_SECRET_NAME)
    assert key
    return key


def _hello(key: str, **extra_headers: str) -> Any:
    return loopback.post(
        "/user-browser/relay/hello",
        json={"origin": CHROMIUM_ORIGIN, "browser": "chrome", "extension_version": "0.1.0"},
        headers={"X-Locus-Pairing-Key": key, **extra_headers},
    )


# --------------------------------------------------------------------------- #
# Principal-only settings
# --------------------------------------------------------------------------- #
def test_status_needs_authentication_and_never_returns_the_key() -> None:
    assert client.get("/user-browser/status").status_code == 401
    key = _pair()
    body = client.get("/user-browser/status", headers=PRINCIPAL).json()
    assert body["paired"] is True and body["tier"]["tier"] == "strict"
    assert body["extension_ids"]["firefox"] == "locus-browser@lattix.io"
    assert key not in str(body)


def test_pairing_is_principal_only_and_key_lives_in_the_secret_store(
    monkeypatch: pytest.MonkeyPatch, fresh_state: dict[str, Any]
) -> None:
    assert client.post("/user-browser/pairing").status_code == 401
    # An agent token stays refused even if it were configured as an admin actor.
    monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,agent:research")
    refused = client.post("/user-browser/pairing", headers=AGENT)
    assert refused.status_code == 403
    assert not fresh_state["hub"].paired
    response = client.post("/user-browser/pairing", headers=PRINCIPAL)
    assert response.status_code == 200 and response.json()["rotated"] is False
    stored = fresh_state["keychain"].store
    assert any(name == PAIRING_SECRET_NAME for _, name in stored)
    assert all(value not in response.text for value in stored.values())


def test_tier_is_principal_only_and_widening_records_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    widen = {"tier": "trusted", "granted_sites": ["https://mail.example.com"]}
    assert client.put("/user-browser/tier", json=widen).status_code == 401
    monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,agent:research")
    agent = client.put(
        "/user-browser/tier", json={**widen, "acknowledge_risk": True}, headers=AGENT
    )
    assert agent.status_code == 403
    assert tiers_mod.get_tier_store().settings.tier == "strict"

    unacknowledged = client.put("/user-browser/tier", json=widen, headers=PRINCIPAL)
    assert unacknowledged.status_code == 422
    assert tiers_mod.get_tier_store().settings.tier == "strict"

    done = client.put(
        "/user-browser/tier", json={**widen, "acknowledge_risk": True}, headers=PRINCIPAL
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["tier"] == "trusted" and body["granted_sites"] == ["example.com"]
    assert body["consent"]["actor"] == "locus-admin" and body["consent"]["tier"] == "trusted"
    read = client.get("/user-browser/tier", headers=PRINCIPAL).json()
    assert read["effective_tier"] == "trusted" and read["history"][-1]["to_tier"] == "trusted"
    bad = client.put(
        "/user-browser/tier", json={"tier": "open", "granted_sites": "x.com"}, headers=PRINCIPAL
    )
    assert bad.status_code == 422


def test_principal_changes_refuse_cross_site_browser_requests() -> None:
    evil = {**PRINCIPAL, "Origin": "https://evil.example"}
    assert client.post("/user-browser/pairing", headers=evil).status_code == 403
    sneaky = {**PRINCIPAL, "Sec-Fetch-Site": "cross-site"}
    assert (
        client.put("/user-browser/tier", json={"tier": "strict"}, headers=sneaky).status_code == 403
    )
    ui = {**PRINCIPAL, "Origin": "http://127.0.0.1:3000", "Sec-Fetch-Site": "same-site"}
    assert client.put("/user-browser/tier", json={"tier": "strict"}, headers=ui).status_code == 200


def test_principal_check_rejects_agents_services_and_internal_callers() -> None:
    human = {"authenticated": True, "principal_type": "user"}
    assert is_human_principal(human)
    assert not is_human_principal({**human, "authenticated": False})
    assert not is_human_principal({**human, "principal_type": "agent"})
    assert not is_human_principal({**human, "agent_id": "a1"})
    assert not is_human_principal({**human, "internal_service_authenticated": True})
    assert not is_human_principal({**human, "trusted_subject_authenticated": True})
    assert not is_human_principal(None)


# --------------------------------------------------------------------------- #
# Relay: loopback only, paired only
# --------------------------------------------------------------------------- #
def test_relay_refuses_non_loopback_and_browser_callers() -> None:
    key = _pair()
    assert (
        remote.post(
            "/user-browser/relay/hello",
            json={"origin": CHROMIUM_ORIGIN},
            headers={"X-Locus-Pairing-Key": key},
        ).status_code
        == 403
    )
    # A web page on this machine sends Origin / Sec-Fetch-* headers: refused.
    assert _hello(key, Origin="https://evil.example").status_code == 403
    assert _hello(key, **{"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert relay_request_refusal("::1", {}) is None
    assert relay_request_refusal("10.0.0.1", {}) == "not_loopback"
    assert relay_request_refusal("testclient", {}) == "not_loopback"


def test_relay_refuses_unpaired_mismatched_and_unpinned_clients() -> None:
    assert _hello("anything").status_code == 401  # nothing paired yet
    key = _pair()
    assert _hello(key + "x").status_code == 401
    assert _hello("").status_code == 401
    unpinned = loopback.post(
        "/user-browser/relay/hello",
        json={"origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"},
        headers={"X-Locus-Pairing-Key": key},
    )
    assert unpinned.status_code == 401
    ok = _hello(key)
    assert ok.status_code == 200 and ok.json()["session_token"]
    bogus = loopback.post(
        "/user-browser/relay/next",
        json={"wait_s": 0},
        headers={"X-Locus-Relay-Client": ok.json()["client_id"], "X-Locus-Relay-Session": "x"},
    )
    assert bogus.status_code == 401


def test_relay_round_trip_then_unpair_revokes(fresh_state: dict[str, Any]) -> None:
    key = _pair()
    hello = _hello(key).json()
    session = {
        "X-Locus-Relay-Client": hello["client_id"],
        "X-Locus-Relay-Session": hello["session_token"],
    }
    hub: relay_mod.RelayHub = fresh_state["hub"]
    outcome: dict[str, Any] = {}

    def driver_call() -> None:
        outcome["result"] = hub.call("list_tabs", timeout_s=5)

    worker = threading.Thread(target=driver_call)
    worker.start()
    commands: list[dict[str, Any]] = []
    for _ in range(50):
        commands = loopback.post(
            "/user-browser/relay/next", json={"wait_s": 0.1}, headers=session
        ).json()["commands"]
        if commands:
            break
    assert commands and commands[0]["op"] == "list_tabs"
    delivered = loopback.post(
        "/user-browser/relay/result",
        json={"type": "result", "id": commands[0]["id"], "ok": True, "result": {"tabs": []}},
        headers=session,
    )
    assert delivered.json() == {"delivered": True}
    worker.join(timeout=5)
    assert outcome["result"] == {"tabs": []}

    assert client.delete("/user-browser/pairing", headers=PRINCIPAL).status_code == 200
    after = loopback.post("/user-browser/relay/next", json={"wait_s": 0}, headers=session)
    assert after.status_code == 401
    assert _hello(key).status_code == 401


def test_extension_panic_button_latches_computer_use(fresh_state: dict[str, Any]) -> None:
    key = _pair()
    hello = _hello(key).json()
    session = {
        "X-Locus-Relay-Client": hello["client_id"],
        "X-Locus-Relay-Session": hello["session_token"],
    }
    assert (
        loopback.post(
            "/user-browser/relay/event",
            json={"event": "panic"},
            headers={"X-Locus-Relay-Client": "x"},
        ).status_code
        == 401
    )
    response = loopback.post("/user-browser/relay/event", json={"event": "panic"}, headers=session)
    assert response.status_code == 200 and response.json()["panicked"] is True
    assert fresh_state["controller"].panicked


# --------------------------------------------------------------------------- #
# Posture (P32)
# --------------------------------------------------------------------------- #
def _facts(**overrides: object) -> PostureFacts:
    base = PostureFacts(
        auth_required=True,
        a2a_signed_messages=True,
        a2a_trusted_subject_count=1,
        a2a_replay_protection=True,
        egress_allowlist=True,
        guardrail_signals_enabled=True,
        guardrail_signal_enforcement="block_high",
        presidio_flag=False,
        presidio_state="not_loaded",
        audit_durable=False,
        sandbox_requested=True,
        sandbox_strategy="kernel-bwrap",
        policy_engine_available=True,
        biscuit_loaded=False,
        vault_addr_configured=False,
        envoy_authz_filters=False,
        nats_loaded=False,
        secret_storage_mode="keychain",
        gateway_enforcing=True,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def _state(**overrides: object) -> tuple[str, str]:
    for control in evaluate_controls(_facts(**overrides)):
        if control.id == "user_browser":
            return control.state, control.evidence
    raise AssertionError("no user_browser control")


def test_posture_shows_the_tier_and_who_accepted_it() -> None:
    assert _state(user_browser=None)[0] == "off"
    assert _state(user_browser={"paired": False})[0] == "off"
    assert _state(user_browser={"paired": True}, gateway_enforcing=False)[0] == "unverified"
    assert _state(user_browser={"paired": True, "effective_tier": "strict"})[0] == "enforced"
    state, evidence = _state(
        user_browser={
            "paired": True,
            "effective_tier": "open",
            "consent": {"actor": "alice", "recorded_at_iso": "2026-10-03T12:00:00Z"},
        }
    )
    assert state == "degraded"
    assert "open" in evidence and "alice" in evidence and "2026-10-03T12:00:00Z" in evidence


# --------------------------------------------------------------------------- #
# Desktop: widening needs the shell's out-of-band confirmation proof
# --------------------------------------------------------------------------- #
SHELL_SECRET = bytes(range(100, 132))


@pytest.fixture()
def desktop(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from locus_tooling import shell_confirmation as sc

    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    sc.install_secret(SHELL_SECRET)
    try:
        yield
    finally:
        sc.install_secret(None)


def _tier_proof(body: dict[str, Any], *, ts: int | None = None, key: bytes = SHELL_SECRET) -> str:
    import secrets as _secrets
    import time as _time

    from locus_tooling import shell_confirmation as sc

    return sc.proof_header(
        key,
        lambda nonce, stamp: sc.tier_message(
            tier=body["tier"],
            allowlisted_sites=body.get("allowlisted_sites"),
            granted_sites=body.get("granted_sites"),
            nonce=nonce,
            timestamp=stamp,
        ),
        nonce=_secrets.token_hex(16),
        timestamp=int(_time.time()) if ts is None else ts,
    )


def _put_tier(body: dict[str, Any], proof: str | None = None) -> Any:
    headers = dict(PRINCIPAL)
    if proof is not None:
        headers["X-Locus-Shell-Proof"] = proof
    return client.put("/user-browser/tier", json=body, headers=headers)


WIDEN = {
    "tier": "trusted",
    "allowlisted_sites": ["example.com"],
    "granted_sites": ["mail.example.com"],
    "acknowledge_risk": True,
}


def test_desktop_widening_without_proof_is_refused_and_audited(desktop: None) -> None:
    from app.main import store as backend_store

    refused = _put_tier(WIDEN)
    assert refused.status_code == 403, refused.text
    assert tiers_mod.get_tier_store().settings.tier == "strict"
    event = next(e for e in backend_store.audit_events if e.action == "user_browser.tier.set")
    assert event.outcome == "blocked" and event.metadata["reason"] == "missing_proof"


def test_desktop_widening_with_a_valid_proof_and_its_replay(desktop: None) -> None:
    proof = _tier_proof(WIDEN)
    done = _put_tier(WIDEN, proof)
    assert done.status_code == 200, done.text
    assert done.json()["tier"] == "trusted" and done.json()["consent"]["tier"] == "trusted"
    tiers_mod.get_tier_store().reset()
    replay = _put_tier(WIDEN, proof)
    assert replay.status_code == 403 and "replayed_proof" in replay.text


@pytest.mark.parametrize("kind", ["expired", "wrong_key", "other_request", "no_lists"])
def test_desktop_invalid_proofs_are_refused(desktop: None, kind: str) -> None:
    import time as _time

    body = dict(WIDEN)
    if kind == "expired":
        proof = _tier_proof(body, ts=int(_time.time()) - 3600)
    elif kind == "wrong_key":
        proof = _tier_proof(body, key=b"\x07" * 32)
    elif kind == "other_request":
        proof = _tier_proof({**body, "tier": "assisted"})  # confirmed something else
    else:
        body = {"tier": "assisted", "acknowledge_risk": True}  # lists omitted
        proof = _tier_proof(body)
    refused = _put_tier(body, proof)
    assert refused.status_code == 403, refused.text
    assert tiers_mod.get_tier_store().settings.tier == "strict"


def test_desktop_narrowing_needs_no_proof(desktop: None) -> None:
    assert _put_tier(WIDEN, _tier_proof(WIDEN)).status_code == 200
    fewer = {**WIDEN, "granted_sites": []}
    assert _put_tier(fewer).status_code == 200  # removing a site narrows
    assert _put_tier({"tier": "assisted", "acknowledge_risk": True}).status_code == 200
    strict = _put_tier({"tier": "strict"})
    assert strict.status_code == 200 and strict.json()["consent"] is None
    # ...and widening again needs a fresh proof.
    assert _put_tier(WIDEN).status_code == 403


def test_desktop_profile_without_a_shell_secret_refuses_widening(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-native")
    refused = _put_tier(WIDEN, _tier_proof(WIDEN))
    assert refused.status_code == 403 and "no_shell" in refused.text
    assert _put_tier({"tier": "strict"}).status_code == 200


def test_desktop_pairing_needs_a_pairing_proof(desktop: None) -> None:
    import secrets as _secrets
    import time as _time

    from locus_tooling import shell_confirmation as sc

    assert client.post("/user-browser/pairing", headers=PRINCIPAL).status_code == 403
    tier_proof = _tier_proof(WIDEN)  # a proof for another action does not pair
    assert (
        client.post(
            "/user-browser/pairing", headers={**PRINCIPAL, "X-Locus-Shell-Proof": tier_proof}
        ).status_code
        == 403
    )
    proof = sc.proof_header(
        SHELL_SECRET,
        lambda nonce, ts: sc.pairing_message(nonce=nonce, timestamp=ts),
        nonce=_secrets.token_hex(16),
        timestamp=int(_time.time()),
    )
    paired = client.post(
        "/user-browser/pairing", headers={**PRINCIPAL, "X-Locus-Shell-Proof": proof}
    )
    assert paired.status_code == 200, paired.text
    # Unpairing narrows: no proof needed.
    assert client.delete("/user-browser/pairing", headers=PRINCIPAL).status_code == 200


def test_non_desktop_profile_keeps_principal_auth_plus_acknowledgement() -> None:
    assert _put_tier({**WIDEN, "acknowledge_risk": False}).status_code == 422
    assert _put_tier(WIDEN).status_code == 200
