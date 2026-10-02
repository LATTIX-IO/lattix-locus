from locus_runtime.security import CapabilityMinter, CapabilityVerifier, build_default_keypair


def test_capability_token_round_trip() -> None:
    keypair = build_default_keypair()
    minter = CapabilityMinter(keypair)
    verifier = CapabilityVerifier(keypair)
    token = minter.mint_agent_token("research", ["execute_step"], [], [], 1)
    assert verifier.verify(token, "execute_step", "research") is True


def test_capability_token_rejects_non_allowlisted_action() -> None:
    keypair = build_default_keypair()
    minter = CapabilityMinter(keypair)
    verifier = CapabilityVerifier(keypair)
    token = minter.mint_agent_token("research", ["search"], [], [], 1)
    assert verifier.verify(token, "execute_step", "research") is False


def test_capability_token_enforces_tool_call_budget() -> None:
    keypair = build_default_keypair()
    minter = CapabilityMinter(keypair)
    verifier = CapabilityVerifier(keypair)
    token = minter.mint_agent_token("research", ["execute_step"], [], [], 1)

    assert verifier.verify(token, "execute_step", "research", tool_call_count=1) is True
    assert verifier.verify(token, "execute_step", "research", tool_call_count=2) is False


def test_capability_token_enforces_canonical_read_paths(tmp_path) -> None:
    keypair = build_default_keypair()
    minter = CapabilityMinter(keypair)
    verifier = CapabilityVerifier(keypair)

    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    allowed_file = allowed_root / "artifact.txt"
    allowed_file.write_text("ok", encoding="utf-8")

    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside_file = outside_root / "secret.txt"
    outside_file.write_text("nope", encoding="utf-8")

    token = minter.mint_agent_token("research", ["read_file"], [str(allowed_root)], [], 2)

    assert verifier.verify(token, "read_file", "research", resource_path=str(allowed_file)) is True
    assert verifier.verify(token, "read_file", "research", resource_path=str(outside_file)) is False
