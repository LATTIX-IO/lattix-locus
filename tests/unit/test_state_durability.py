import asyncio
import hashlib
from pathlib import Path
from threading import Thread

import locus_runtime.security as security_module
from locus_runtime.events import AgentEvent, get_event_bus, reset_event_bus
from locus_runtime.persistence import (
    load_state,
    mutate_state,
    reset_shared_state_backend,
    save_state,
)
from locus_runtime.security import (
    decode_token,
    mint_token,
    reset_token_caches,
    token_identity_from_claims,
    verify_token,
)


def test_event_bus_fallback_persists_events_across_singleton_reset(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    reset_shared_state_backend()
    reset_event_bus()

    bus = get_event_bus()
    published = asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))

    reset_shared_state_backend()
    reset_event_bus()
    reloaded = get_event_bus().fallback.list_events()

    assert reloaded
    assert reloaded[-1].id == published.id
    assert reloaded[-1].event_hash == published.event_hash


def test_event_bus_rate_limit_records_observability(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    monkeypatch.setenv("LOCUS_EVENT_BUS_RATE_LIMIT_COUNT", "1")
    monkeypatch.setenv("LOCUS_EVENT_BUS_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_shared_state_backend()
    reset_event_bus()

    bus = get_event_bus()
    asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))

    try:
        asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))
    except PermissionError as exc:
        assert "rate limit exceeded" in str(exc)
    else:  # pragma: no cover - safety net
        raise AssertionError("expected event bus rate limiting to block second publish")

    metrics = load_state().get("event_bus_metrics", {})
    assert metrics.get("published") == 1
    assert metrics.get("rate_limited") == 1
    assert metrics.get("last_rate_limited_source") == "tester"


def test_replay_cache_persists_across_singleton_reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    reset_shared_state_backend()
    reset_token_caches()

    token = mint_token("orchestrator", ttl_seconds=60)
    verify_token(token, require_nonce=False)

    reset_shared_state_backend()
    reset_token_caches()

    try:
        verify_token(token, require_nonce=False)
    except ValueError as exc:
        assert "replay detected" in str(exc)
    else:  # pragma: no cover - safety net
        raise AssertionError("expected replay detection after cache reset")


def test_decode_token_preserves_identity_claims_without_consuming_replay_cache(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    reset_shared_state_backend()
    reset_token_caches()

    token = mint_token(
        "member-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "member-user",
            "tenant_id": "acme",
            "internal_service": False,
        },
    )

    claims = decode_token(token)
    identity = token_identity_from_claims(claims)

    assert identity.actor == "member-user"
    assert identity.subject == "member-user"
    assert identity.tenant_id == "acme"

    verify_token(token, require_nonce=False)


def test_mutate_state_serializes_concurrent_updates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    reset_shared_state_backend()

    def _worker() -> None:
        for _ in range(25):

            def _mutate(snapshot: dict[str, object]) -> None:
                snapshot["counter"] = int(snapshot.get("counter", 0)) + 1

            mutate_state(_mutate)

    threads = [Thread(target=_worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    state = load_state()
    assert state["counter"] == 200


def test_replay_cache_prunes_expired_entries(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "state.db"))
    monkeypatch.setenv("LOCUS_RUNTIME_REPLAY_TTL_SECONDS", "1")
    reset_shared_state_backend()
    reset_token_caches()

    token = mint_token("orchestrator", ttl_seconds=600)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    save_state(
        {
            "approvals": [],
            "events": [],
            "replay_tokens": [{"token_hash": token_hash, "expires_at": 1}],
        }
    )

    monkeypatch.setattr(security_module.time, "time", lambda: 10.0)

    claims = verify_token(token, require_nonce=False)

    assert claims["sub"] == "orchestrator"
    persisted = load_state()["replay_tokens"]
    assert len(persisted) == 1
    assert persisted[0]["token_hash"] == token_hash
    assert float(persisted[0]["expires_at"]) == 11.0
