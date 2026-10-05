"""LOCUS-362 (bake-off finding 6): every assistant turn the loop sends back is one an
OpenAI-compatible provider accepts, reproducing the two Ollama HTTP 400s.

``OllamaStrictClient`` validates each request the way Ollama's OpenAI-compatible
endpoint does and raises the same 400 errors the bake-off hit. Before the fix the
verified loop sent ``content: null`` for a turn with no text and no tool calls,
and echoed unparseable tool-call arguments verbatim; both blocked the run as a
provider failure after the retries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.harness.enforcement import (
    INVALID_ARGUMENTS_PLACEHOLDER,
    assistant_message,
    provider_safe_arguments,
)
from locus_runtime.harness.llm import ChatResponse, ToolCall
from locus_runtime.harness.loop import AgentLoop
from locus_runtime.harness.verified_loop import EndState
from tests.harness.conftest import requires_bash, requires_git
from tests.harness.test_swe_agent_e2e import _make_repo
from tests.harness.test_verified_loop import (
    PROFILE,
    _fix,
    _loop,
    _plan,
    _submit,
    _toolset,
)


class OllamaHTTP400(RuntimeError):
    pass


def _ollama_validate(messages: list[dict[str, Any]]) -> None:
    """Ollama's OpenAI-compat message conversion (openai.go), reduced to the two checks."""
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, str | list) and not msg.get("tool_calls"):
            raise OllamaHTTP400(f"invalid message content type: {type(content).__name__}")
        for call in msg.get("tool_calls") or []:
            raw = call["function"]["arguments"]
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise OllamaHTTP400("invalid tool call arguments") from exc
            if not isinstance(parsed, dict):
                raise OllamaHTTP400("invalid tool call arguments")


@dataclass
class OllamaStrictClient:
    """Scripted client that rejects a request Ollama would reject (HTTP 400)."""

    responses: list[ChatResponse]
    provider: str = "ollama"
    model: str = "gpt-oss:20b"
    rejected: list[str] = field(default_factory=list)
    calls: int = 0

    def complete(self, messages: list[dict[str, Any]], **_: Any) -> ChatResponse:
        try:
            _ollama_validate(messages)
        except OllamaHTTP400 as exc:
            self.rejected.append(str(exc))
            raise
        self.calls += 1
        if not self.responses:
            return ChatResponse(text="(no scripted response remaining)")
        return self.responses.pop(0)


def _malformed_view(call_id: str = "bad") -> ChatResponse:
    # Truncated JSON, as gpt-oss emitted it in the bake-off.
    raw = '{"command": "view", "path": "mathlib/core.py"'
    return ChatResponse(
        text="", tool_calls=[ToolCall(id=call_id, name="str_replace_editor", arguments=raw)]
    )


# --------------------------------------------------------------------------- #
# Unit: the message builder
# --------------------------------------------------------------------------- #
def test_turn_without_text_or_tool_calls_has_string_content() -> None:
    msg, invalid = assistant_message(ChatResponse(text=""))
    assert msg == {"role": "assistant", "content": ""}
    assert invalid == {}
    _ollama_validate([msg])


def test_malformed_arguments_become_a_placeholder_and_are_never_echoed() -> None:
    raw = '{"command": "view", "path": "secret-plan'
    msg, invalid = assistant_message(
        ChatResponse(text="", tool_calls=[ToolCall(id="c1", name="search", arguments=raw)])
    )
    assert msg["tool_calls"][0]["function"]["arguments"] == INVALID_ARGUMENTS_PLACEHOLDER
    assert "not valid JSON" in invalid["c1"]
    assert "secret-plan" not in json.dumps(msg) and "secret-plan" not in invalid["c1"]
    _ollama_validate([msg])


def test_argument_shapes() -> None:
    assert provider_safe_arguments(None) == ("{}", "")
    assert provider_safe_arguments("  ") == ("{}", "")
    assert provider_safe_arguments({"a": 1}) == ('{"a": 1}', "")
    assert provider_safe_arguments('{"a":1}') == ('{"a": 1}', "")
    assert provider_safe_arguments("[1, 2]") == ("{}", "arguments must be a JSON object")
    assert provider_safe_arguments('"x"')[1] == "arguments must be a JSON object"
    # Python's json accepts NaN; Go's (Ollama) does not.
    assert provider_safe_arguments('{"a": NaN}')[0] == "{}"
    assert provider_safe_arguments({"a": float("nan")})[0] == "{}"


# --------------------------------------------------------------------------- #
# The verified loop against a strict provider
# --------------------------------------------------------------------------- #
@requires_bash
@requires_git
def test_empty_turn_does_not_send_null_content(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    client = OllamaStrictClient([_plan(), ChatResponse(text=""), _fix(), _submit()])
    loop = _loop(tmp_path, [], provider_max_retries=1)
    loop.client = client

    result = loop.run()

    assert client.rejected == []
    assert result.end_state is EndState.DONE, result.blocker
    empty_turns = [
        m for m in result.messages if m.get("role") == "assistant" and not m.get("tool_calls")
    ]
    assert empty_turns and all(m["content"] == "" for m in empty_turns)


@requires_bash
@requires_git
def test_malformed_tool_arguments_are_sanitized_and_reported(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    client = OllamaStrictClient([_plan(), _malformed_view(), _fix(), _submit()])
    loop = _loop(tmp_path, [], provider_max_retries=1)
    loop.client = client

    result = loop.run()

    assert client.rejected == []
    assert result.end_state is EndState.DONE, result.blocker
    sent = [
        call
        for m in result.messages
        if m.get("role") == "assistant"
        for call in m.get("tool_calls") or []
        if call["id"] == "bad"
    ]
    assert sent and sent[0]["function"]["arguments"] == "{}"
    reply = next(
        m["content"]
        for m in result.messages
        if m.get("role") == "tool" and m.get("tool_call_id") == "bad"
    )
    assert "not valid JSON" in reply and "replaced with {}" in reply
    assert '"path": "mathlib/core.py"' not in reply
    # Recorded as an error observation in the trajectory, without the raw text.
    assert result.trajectory is not None
    notes = [r for r in result.trajectory.records if r.get("note") == "malformed_tool_arguments"]
    assert notes and notes[0]["call_id"] == "bad" and "mathlib" not in json.dumps(notes[0])
    assert loop.toolset.telemetry.tool_calls_malformed == 1


def test_without_the_fix_ollama_would_have_rejected_both_turns() -> None:
    # Guard against the strict fake being too lenient: the pre-fix shapes fail.
    with pytest.raises(OllamaHTTP400, match="content type"):
        _ollama_validate([{"role": "assistant", "content": None}])
    with pytest.raises(OllamaHTTP400, match="tool call arguments"):
        _ollama_validate(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "x", "function": {"name": "s", "arguments": "{"}}],
                }
            ]
        )


@requires_bash
@requires_git
def test_agent_loop_sends_provider_safe_turns(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    client = OllamaStrictClient([ChatResponse(text=""), _malformed_view(), _fix(), _submit()])
    loop = AgentLoop(
        client=client,
        toolset=_toolset(tmp_path),
        profile=PROFILE,
        system_prompt="You fix bugs.",
        user_prompt="Fix add() in mathlib/core.py.",
        provider_retry_backoff=0,
    )
    result = loop.run()
    assert client.rejected == []
    assert result.submission is not None
    assert all(
        isinstance(m.get("content"), str) for m in result.messages if m.get("role") == "assistant"
    )
