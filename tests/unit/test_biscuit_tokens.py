"""LOCUS-334: Biscuit capability grants -- mint, verify, attenuate, expire, rotate, revoke.

Replaces the retired HMAC ``CapabilityMinter``/``CapabilityVerifier`` tests.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from locus_runtime import grants as gr
from locus_runtime.gateway import GatewayAction, GatewayCaller, RiskClass

NOW = 1_800_000_000.0
CALLER = GatewayCaller(run_id="run-1", principal="alice", engine="harness", token="t")


def _send(to: str, *, run_id: str = "run-1", principal: str = "alice", **extra: object):
    caller = GatewayCaller(run_id=run_id, principal=principal, engine="harness", token="t")
    return GatewayAction.create(
        caller=caller,
        kind="tool_call",
        tool="send_email",
        target="smtp.example.com",
        args={"to": to, "subject": "hi", "body": "text", "account": "work", **extra},
        egress_host="smtp.example.com",
    )


def _verifier(
    authority: gr.GrantAuthority | None = None, now: float = NOW
) -> tuple[gr.BiscuitGrantVerifier, gr.GrantStore, list[float]]:
    clock = [now]
    store = gr.GrantStore()
    verifier = gr.BiscuitGrantVerifier(
        authority or gr.GrantAuthority.generate(), store, clock=lambda: clock[0]
    )
    return verifier, store, clock


def _mint(verifier: gr.BiscuitGrantVerifier, action: GatewayAction, **kwargs: object):
    scope = kwargs.pop("scope", "standing")
    record = verifier.authority.mint(
        gr.tightest_pattern(action),
        principal=action.caller.principal,
        scope=scope,  # type: ignore[arg-type]
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )
    verifier.store.add(record)
    return record


def _covers(verifier: gr.BiscuitGrantVerifier, action: GatewayAction) -> bool:
    return verifier.match(action, None) is not None  # type: ignore[arg-type]


def test_mint_and_verify_round_trip() -> None:
    verifier, _, _ = _verifier()
    action = _send("bob@example.com")
    record = _mint(verifier, action)
    assert record.token and record.revocation_ids
    assert record.expires_at == pytest.approx(NOW + timedelta(days=30).total_seconds())
    match = verifier.match(action, None)  # type: ignore[arg-type]
    assert match is not None and match.grant_id == record.grant_id
    assert verifier.ready and verifier.can_mint


def test_tightest_pattern_pins_domain_args_and_target() -> None:
    pattern = gr.tightest_pattern(_send("Bob@Example.com", amount="12.50"))
    assert pattern.recipients == "@example.com"
    assert pattern.amount_ceiling_cents == 1250
    assert pattern.target == "smtp.example.com" and pattern.target_mode == "exact"
    assert pattern.args == '{"account":"work"}'


def test_standing_grant_does_not_match_broader_actions() -> None:
    verifier, _, _ = _verifier()
    _mint(verifier, _send("bob@example.com"))
    assert _covers(verifier, _send("carol@example.com"))
    # Other domain, an extra recipient, other pinned args, an amount, other target/principal.
    assert not _covers(verifier, _send("bob@evil.com"))
    assert not _covers(verifier, _send("bob@example.com, x@evil.com"))
    assert not _covers(verifier, _send("bob@example.com", account="personal"))
    assert not _covers(verifier, _send("bob@example.com", amount=1))
    other_target = GatewayAction.create(
        caller=CALLER,
        kind="tool_call",
        tool="send_email",
        target="smtp.evil.com",
        args={"to": "bob@example.com", "account": "work"},
    )
    assert not _covers(verifier, other_target)
    assert not _covers(verifier, _send("bob@example.com", principal="mallory"))


def test_amount_ceiling() -> None:
    verifier, _, _ = _verifier()
    _mint(verifier, _send("bob@example.com", amount=100))
    assert _covers(verifier, _send("bob@example.com", amount="99.99"))
    assert _covers(verifier, _send("bob@example.com", amount=100))
    assert not _covers(verifier, _send("bob@example.com", amount="100.01"))
    assert not _covers(verifier, _send("bob@example.com", amount="lots"))
    assert not _covers(verifier, _send("bob@example.com"))


def test_folder_prefix_pattern_rejects_traversal() -> None:
    verifier, _, _ = _verifier()

    def write(path: str) -> GatewayAction:
        return GatewayAction.create(
            caller=CALLER, kind="file_write", tool="write_file", target=path
        )

    _mint(verifier, write("/home/a/notes/today.md"))
    assert _covers(verifier, write("/home/a/notes/tomorrow.md"))
    assert not _covers(verifier, write("/home/a/notes-other/x.md"))
    assert not _covers(verifier, write("/home/a/notes/../.ssh/config"))
    assert not _covers(verifier, write("/home/a/x.md"))


def test_expiry() -> None:
    verifier, _, clock = _verifier()
    action = _send("bob@example.com")
    record = _mint(verifier, action, ttl=timedelta(hours=1))
    assert _covers(verifier, action)
    clock[0] = NOW + 3601
    assert not _covers(verifier, action)
    # The Datalog expiry check holds on its own, whatever the stored metadata says.
    assert not verifier.token_covers(record.token, action)


def test_pinned_standing_grant_has_no_expiry_but_run_grants_cap_at_24h() -> None:
    verifier, _, clock = _verifier()
    action = _send("bob@example.com")
    pinned = _mint(verifier, action, pinned=True)
    assert pinned.expires_at is None
    clock[0] = NOW + timedelta(days=400).total_seconds()
    assert _covers(verifier, action)

    run_grant = verifier.authority.mint(
        gr.tightest_pattern(action),
        principal="alice",
        scope="run",
        run_id="run-1",
        ttl=timedelta(days=10),
        now=NOW,
    )
    assert run_grant.expires_at == pytest.approx(NOW + 86400)
    with pytest.raises(gr.GrantError):
        verifier.authority.mint(
            gr.tightest_pattern(action), principal="alice", scope="run", run_id="r", pinned=True
        )


def test_run_grant_is_bound_to_its_run() -> None:
    verifier, _, _ = _verifier()
    action = _send("bob@example.com")
    record = _mint(verifier, action, scope="run", run_id="run-1")
    assert _covers(verifier, action)
    assert not _covers(verifier, _send("bob@example.com", run_id="run-2"))
    # Even without the store's run prefilter, the token's own check binds the run.
    assert not verifier.token_covers(record.token, _send("bob@example.com", run_id="run-2"))


def test_wrong_key_is_rejected() -> None:
    verifier, _, _ = _verifier()
    action = _send("bob@example.com")
    foreign = gr.GrantAuthority.generate().mint(
        gr.tightest_pattern(action), principal="alice", scope="standing", now=NOW
    )
    assert not verifier.token_covers(foreign.token, action)
    verifier.store.add(foreign)
    assert not _covers(verifier, action)
    assert not verifier.token_covers("not-a-token", action)
    assert not verifier.token_covers(foreign.token[:-8] + "A" * 8, action)


def test_key_rotation_accepts_listed_previous_keys() -> None:
    old = gr.GrantAuthority.from_secret(gr.generate_authority_secret())
    action = _send("bob@example.com")
    old_grant = old.mint(gr.tightest_pattern(action), principal="alice", scope="standing", now=NOW)

    new_secret = gr.generate_authority_secret()
    rotated = gr.GrantAuthority.from_secret(new_secret, old.public_key_hex)
    assert rotated.key_id != old.key_id
    assert set(rotated.accepted_key_ids) == {rotated.key_id, old.key_id}
    verifier, _, _ = _verifier(rotated)
    assert verifier.token_covers(old_grant.token, action)
    new_grant = rotated.mint(
        gr.tightest_pattern(action), principal="alice", scope="standing", now=NOW
    )
    assert new_grant.key_id == rotated.key_id
    assert verifier.token_covers(new_grant.token, action)

    # Once the old key leaves the accepted list, its grants stop verifying.
    unrotated, _, _ = _verifier(gr.GrantAuthority.from_secret(new_secret))
    assert not unrotated.token_covers(old_grant.token, action)


def test_revocation() -> None:
    verifier, store, _ = _verifier()
    action = _send("bob@example.com")
    record = _mint(verifier, action)
    assert _covers(verifier, action)
    store.revoke(record.grant_id, by="alice")
    assert not _covers(verifier, action)
    assert not verifier.token_covers(record.token, action)
    revoked = store.get(record.grant_id)
    assert revoked is not None and revoked.status(NOW) == "revoked"
    store.revoke(record.grant_id)  # idempotent


def test_attenuation_narrows_and_cannot_widen() -> None:
    import biscuit_auth as biscuit

    verifier, store, _ = _verifier()
    action = _send("bob@example.com")
    parent = _mint(verifier, action)

    child = gr.attenuate(verifier.authority, parent, run_id="sub-run-7")
    assert child.parent_id == parent.grant_id
    assert not verifier.token_covers(child.token, action)  # another run
    assert verifier.token_covers(child.token, _send("bob@example.com", run_id="sub-run-7"))
    assert not verifier.token_covers(child.token, _send("bob@evil.com", run_id="sub-run-7"))

    # A hostile attenuation block that adds facts cannot widen the grant: the
    # authorizer's allow policy only trusts the authority block.
    widened = (
        verifier.authority.parse(parent.token)
        .append(
            biscuit.BlockBuilder(
                'grant_id("x"); grant_tool("delete_repo"); action("tool_call", "delete_repo");'
                " check if true;"
            )
        )
        .to_base64()
    )
    other_tool = GatewayAction.create(
        caller=CALLER, kind="tool_call", tool="delete_repo", target="smtp.example.com"
    )
    assert not verifier.token_covers(widened, other_tool)
    assert not verifier.token_covers(widened, _send("bob@evil.com"))

    with pytest.raises(gr.GrantError):
        gr.attenuate(verifier.authority, parent)  # must add a restriction

    # Revoking the parent revokes the attenuated child.
    store.revoke(parent.grant_id)
    assert not verifier.token_covers(child.token, _send("bob@example.com", run_id="sub-run-7"))


def test_r4_is_never_grantable() -> None:
    action = GatewayAction.create(
        caller=CALLER, kind="tool_call", tool="export_credentials", target="vault"
    )
    assert action.risk == RiskClass.R4
    with pytest.raises(gr.GrantError):
        gr.tightest_pattern(action)


def test_pattern_from_dict_rejects_unknown_or_unpinned_fields() -> None:
    base = {"kind": "tool_call", "tool": "x", "target_mode": "exact", "target": "h"}
    with pytest.raises(gr.GrantError):
        gr.GrantPattern.from_dict({**base, "args": "{}", "wildcard": True})
    with pytest.raises(gr.GrantError):
        gr.GrantPattern.from_dict(base)  # tool calls must pin their arguments
    with pytest.raises(gr.GrantError):
        gr.GrantPattern.from_dict(
            {"kind": "file_write", "tool": "w", "target_mode": "prefix", "target": "/a/../"}
        )


def test_load_grant_authority_fails_closed() -> None:
    from locus_tooling.native_secrets import SecretStorageUnavailable

    def unavailable(_name: str) -> str:
        raise SecretStorageUnavailable("no keychain")

    assert gr.load_grant_authority(unavailable) is None
    assert gr.load_grant_authority(lambda _name: None) is None
    assert gr.load_grant_authority(lambda _name: "change-me") is None
    secret = gr.generate_authority_secret()
    authority = gr.load_grant_authority(
        lambda name: secret if name == gr.GRANT_KEY_SECRET else None
    )
    assert authority is not None and authority.can_mint
    hex_secret = gr._decode_key_bytes(secret).hex()  # noqa: SLF001
    assert gr.GrantAuthority.from_secret(hex_secret).key_id == authority.key_id
