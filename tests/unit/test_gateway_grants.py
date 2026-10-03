"""LOCUS-334: the gateway honours Biscuit grants -- R3 ask → allow, never R4, never over a deny."""

from __future__ import annotations

import dataclasses

from locus_runtime import gateway as gw
from locus_runtime import grants as gr
from locus_runtime.gateway import (
    REASON_APPROVAL_REQUIRED,
    REASON_GRANT,
    REASON_GRANT_ERROR,
    REASON_R4_PROHIBITED,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
    RiskClass,
)
from tests.gateway_support import FakeEngine, installed

ROOT = "/work/repo"
SEND = {"to": "bob@example.com", "subject": "status", "body": "all good"}


def _caps(**overrides: object) -> Capabilities:
    base = Capabilities(
        allowed_tools=frozenset({"send_email", "export_credentials", "write_file"}),
        read_roots=(ROOT,),
        write_roots=(ROOT,),
        allowed_egress_hosts=("smtp.example.com",),
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def _setup(
    engine: FakeEngine | None = None,
) -> tuple[Gateway, gr.BiscuitGrantVerifier, list[GatewayAuditRecord]]:
    audit: list[GatewayAuditRecord] = []
    verifier = gr.BiscuitGrantVerifier(gr.GrantAuthority.generate(), gr.GrantStore())
    return Gateway(engine or FakeEngine(), audit.append, grants=verifier), verifier, audit


def _session(gateway: Gateway, run_id: str = "run-1", **caps: object) -> GatewaySession:
    return gateway.open_session(
        run_id=run_id, principal="alice", engine="harness", capabilities=_caps(**caps)
    )


def _send(session: GatewaySession, **args: object) -> gw.GatewayDecision:
    return session.authorize(
        kind="tool_call",
        tool="send_email",
        target="smtp.example.com",
        args={**SEND, **args},
        egress_host="smtp.example.com",
    )


def _grant_for(
    verifier: gr.BiscuitGrantVerifier, session: GatewaySession, **kwargs: object
) -> gr.GrantRecord:
    action = session.action(
        kind="tool_call",
        tool="send_email",
        target="smtp.example.com",
        args=SEND,
        egress_host="smtp.example.com",
    )
    scope = kwargs.pop("scope", "standing")
    record = verifier.authority.mint(
        gr.tightest_pattern(action),
        principal="alice",
        scope=scope,  # type: ignore[arg-type]
        run_id=session.caller.run_id if scope == "run" else "",
        **kwargs,  # type: ignore[arg-type]
    )
    return verifier.store.add(record)


def test_r3_asks_without_grant_and_allows_with_covering_grant() -> None:
    gateway, verifier, audit = _setup()
    session = _session(gateway)
    first = _send(session)
    assert first.risk == RiskClass.R3
    assert first.outcome == "ask" and REASON_APPROVAL_REQUIRED in first.reasons

    record = _grant_for(verifier, session)
    allowed = _send(session, body="different text, same pattern")
    assert allowed.outcome == "allow" and allowed.allowed
    assert REASON_GRANT in allowed.reasons
    assert f"gateway.grant:{record.grant_id}" in allowed.reasons
    # Attributable (P11): the audit record names the grant.
    assert f"gateway.grant:{record.grant_id}" in audit[-1].reasons

    # A broader action (another recipient domain) is not covered: still asks.
    assert _send(session, to="eve@evil.com").outcome == "ask"


def test_standing_grant_spans_runs_but_run_grant_does_not() -> None:
    gateway, verifier, _ = _setup()
    run_one = _session(gateway, "run-1")
    _grant_for(verifier, run_one, scope="run")
    assert _send(run_one).outcome == "allow"
    assert _send(_session(gateway, "run-2")).outcome == "ask"

    _grant_for(verifier, run_one, scope="standing")
    assert _send(_session(gateway, "run-3")).outcome == "allow"


def test_policy_deny_wins_over_a_grant() -> None:
    gateway, verifier, _ = _setup(FakeEngine({"network_egress": False}))
    session = _session(gateway)
    _grant_for(verifier, session)
    decision = _send(session)
    assert decision.outcome == "deny"
    assert REASON_GRANT not in decision.reasons


def test_grant_never_authorizes_r4() -> None:
    gateway, verifier, _ = _setup()
    session = _session(gateway)
    # Hand-build a pattern for an R4 tool (tightest_pattern refuses R4) and mint it.
    pattern = gr.GrantPattern(
        "tool_call", "export_credentials", "exact", "vault", args='{"name":"prod"}'
    )
    verifier.store.add(verifier.authority.mint(pattern, principal="alice", scope="standing"))
    decision = session.authorize(
        kind="tool_call", tool="export_credentials", target="vault", args={"name": "prod"}
    )
    assert decision.risk == RiskClass.R4
    assert decision.outcome == "deny"
    assert REASON_R4_PROHIBITED in decision.reasons and REASON_GRANT not in decision.reasons


def test_revoked_and_expired_grants_fall_back_to_ask() -> None:
    gateway, verifier, _ = _setup()
    session = _session(gateway)
    record = _grant_for(verifier, session)
    assert _send(session).outcome == "allow"
    verifier.store.revoke(record.grant_id)
    assert _send(session).outcome == "ask"

    clock = [1_800_000_000.0]
    expiring = gr.BiscuitGrantVerifier(verifier.authority, gr.GrantStore(), clock=lambda: clock[0])
    gateway2 = Gateway(FakeEngine(), lambda _r: None, grants=expiring)
    session2 = _session(gateway2)
    _grant_for(expiring, session2, now=clock[0])
    assert _send(session2).outcome == "allow"
    clock[0] += gr.STANDING_TTL.total_seconds() + 1
    assert _send(session2).outcome == "ask"


def test_untrusted_input_cannot_inject_a_grant() -> None:
    gateway, verifier, _ = _setup()
    session = _session(gateway)
    # A perfectly valid token for this exact action, minted by the real authority,
    # but delivered through the action's own arguments: the gateway never reads it.
    smuggled = verifier.authority.mint(
        gr.tightest_pattern(
            session.action(
                kind="tool_call",
                tool="send_email",
                target="smtp.example.com",
                args=SEND,
                egress_host="smtp.example.com",
            )
        ),
        principal="alice",
        scope="standing",
    )
    for key in ("grant", "capability_token", "biscuit", "authorization"):
        decision = _send(session, **{key: smuggled.token})
        assert decision.outcome == "ask", key
    assert verifier.store.records() == []
    # Another principal's grant does not cover alice either.
    verifier.store.add(dataclasses.replace(smuggled, principal="mallory"))
    assert _send(session).outcome == "ask"


def test_supervised_r2_is_also_coverable() -> None:
    gateway, verifier, _ = _setup()
    session = _session(gateway, autonomy_tier="supervised")
    decision = session.authorize(kind="file_write", tool="write_file", target="/tmp/out/a.txt")
    assert decision.risk == RiskClass.R2 and decision.outcome == "ask"
    action = session.action(kind="file_write", tool="write_file", target="/tmp/out/a.txt")
    verifier.store.add(
        verifier.authority.mint(gr.tightest_pattern(action), principal="alice", scope="standing")
    )
    assert (
        session.authorize(kind="file_write", tool="write_file", target="/tmp/out/b.txt").outcome
        == "allow"
    )
    assert (
        session.authorize(kind="file_write", tool="write_file", target="/tmp/other.txt").outcome
        == "ask"
    )


def test_broken_verifier_fails_closed_to_ask() -> None:
    class Exploding:
        ready = True

        def match(self, action: object, caps: object) -> None:
            raise RuntimeError("boom")

        def covers(self, action: object, caps: object) -> bool:
            raise RuntimeError("boom")

    gateway = Gateway(FakeEngine(), lambda _r: None, grants=Exploding())  # type: ignore[arg-type]
    decision = _send(_session(gateway))
    assert decision.outcome == "ask"
    assert REASON_GRANT_ERROR in decision.reasons


def test_grants_enforcing_posture_fact() -> None:
    gateway, _, _ = _setup()
    with installed(gateway):
        assert gw.grants_enforcing() is True
    with installed(Gateway(FakeEngine(), lambda _r: None)):
        assert gw.grants_enforcing() is False  # NoGrants
    unhealthy, _, _ = _setup(FakeEngine(running=False))
    with installed(unhealthy):
        assert gw.grants_enforcing() is False
    verify_only = gr.BiscuitGrantVerifier(gr.GrantAuthority(None), gr.GrantStore())
    assert verify_only.ready is False
    with installed(Gateway(FakeEngine(), lambda _r: None, grants=verify_only)):
        assert gw.grants_enforcing() is False
