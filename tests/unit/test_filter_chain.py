"""Filter chain after the HMAC capability-token retirement (LOCUS-334).

Capability-scoped envelopes fail closed: side effects are authorized by the
gateway with server-side Biscuit grants, never by a token carried in a message.
"""

import asyncio

import pytest

from locus_runtime.envelope import Envelope
from locus_runtime.guardrails import (
    CAPABILITY_SCOPED_REASON,
    FilterContext,
    default_filter_chain,
)


def _run(envelope: Envelope):
    return asyncio.run(default_filter_chain().run(envelope, FilterContext()))


def test_filter_chain_passes_unscoped_envelope() -> None:
    envelope = Envelope(source_agent="backend", action="summarize", payload={"text": "hi"})
    assert _run(envelope).action == "pass"


@pytest.mark.parametrize(
    "envelope",
    [
        Envelope(
            source_agent="backend", target_agent="research", action="execute_step", payload={}
        ),
        Envelope(source_agent="backend", action="execute_step", payload={}),
        Envelope(source_agent="backend", action="read_file", payload={}, metadata={"path": "/x"}),
        Envelope(
            source_agent="backend", action="summarize", payload={}, metadata={"tool_call_count": 2}
        ),
    ],
)
def test_filter_chain_blocks_capability_scoped_envelopes(envelope: Envelope) -> None:
    result = _run(envelope)
    assert result.action == "block"
    assert result.reason == CAPABILITY_SCOPED_REASON


def test_envelope_carried_token_is_never_accepted() -> None:
    # Whatever the token claims (the retired HMAC format, a Biscuit, garbage),
    # a message cannot carry its own capability.
    for token in ("eyJhZ2VudF9pZCI6InJlc2VhcmNoIn0.c2ln", "En0KEwoEZ3JhbnQ", "x"):
        envelope = Envelope(
            source_agent="backend",
            target_agent="research",
            action="summarize",
            payload={},
            capability_token=token,
        )
        result = _run(envelope)
        assert result.action == "block"
        assert result.reason == CAPABILITY_SCOPED_REASON
