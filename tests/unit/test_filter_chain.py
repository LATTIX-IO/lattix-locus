import asyncio

from locus_runtime.envelope import Envelope
from locus_runtime.guardrails import FilterContext, default_filter_chain
from locus_runtime.security import CapabilityMinter, build_default_keypair


def test_filter_chain_allows_valid_envelope() -> None:
    token = CapabilityMinter(build_default_keypair()).mint_agent_token(
        agent_id="research",
        allowed_tools=["execute_step"],
        allowed_read_paths=[],
        allowed_write_paths=[],
        max_tool_calls=1,
    )
    envelope = Envelope(
        source_agent="backend",
        target_agent="research",
        action="execute_step",
        payload={"task": "research foo"},
        capability_token=token.decode("utf-8"),
    )
    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))
    assert result.action in {"pass", "modify"}


def test_filter_chain_blocks_targeted_envelope_without_capability_token() -> None:
    envelope = Envelope(
        source_agent="backend",
        target_agent="research",
        action="execute_step",
        payload={"task": "research foo"},
    )

    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))

    assert result.action == "block"
    assert result.reason == "capability token required"


def test_filter_chain_blocks_capability_scoped_action_without_target_agent() -> None:
    envelope = Envelope(
        source_agent="backend", action="execute_step", payload={"task": "research foo"}
    )

    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))

    assert result.action == "block"
    assert result.reason == "target agent required for capability-scoped action"


def test_filter_chain_blocks_capability_token_when_tool_budget_exceeded() -> None:
    token = CapabilityMinter(build_default_keypair()).mint_agent_token(
        agent_id="research",
        allowed_tools=["execute_step"],
        allowed_read_paths=[],
        allowed_write_paths=[],
        max_tool_calls=1,
    )
    envelope = Envelope(
        source_agent="backend",
        target_agent="research",
        action="execute_step",
        payload={"task": "research foo"},
        metadata={"tool_call_count": 2},
        capability_token=token.decode("utf-8"),
    )

    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))

    assert result.action == "block"
    assert result.reason == "invalid capability token"


def test_filter_chain_blocks_capability_token_when_read_path_outside_scope(tmp_path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside_file = outside_root / "secret.txt"
    outside_file.write_text("nope", encoding="utf-8")

    token = CapabilityMinter(build_default_keypair()).mint_agent_token(
        agent_id="research",
        allowed_tools=["read_file"],
        allowed_read_paths=[str(allowed_root)],
        allowed_write_paths=[],
        max_tool_calls=2,
    )
    envelope = Envelope(
        source_agent="backend",
        target_agent="research",
        action="read_file",
        payload={"task": "read secret"},
        metadata={"resource_path": str(outside_file)},
        capability_token=token.decode("utf-8"),
    )

    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))

    assert result.action == "block"
    assert result.reason == "invalid capability token"


def test_filter_chain_blocks_capability_token_without_target_agent_even_if_present() -> None:
    token = CapabilityMinter(build_default_keypair()).mint_agent_token(
        agent_id="research",
        allowed_tools=["execute_step"],
        allowed_read_paths=[],
        allowed_write_paths=[],
        max_tool_calls=1,
    )
    envelope = Envelope(
        source_agent="backend",
        action="execute_step",
        payload={"task": "research foo"},
        capability_token=token.decode("utf-8"),
    )

    result = asyncio.run(default_filter_chain().run(envelope, FilterContext()))

    assert result.action == "block"
    assert result.reason == "target agent required for capability-scoped action"
