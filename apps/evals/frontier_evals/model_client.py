"""Build the ChatClient that drives the agent for an evaluation.

* live: an OpenAI-compatible client (vLLM/llama.cpp/LM Studio) for the model
  under test (e.g. gpt-oss-20b).
* plumbing: a deterministic *reference solver* that applies a task's known fix
  recipe through real tool calls — used to validate the eval pipeline end to
  end and to produce a known resolve rate for the automated test. It is NOT a
  model; it exercises the harness + grading, not model capability.
"""

from __future__ import annotations

import json
from typing import Any

from frontier_runtime.harness.llm import (
    ChatResponse,
    OpenAIChatClient,
    ScriptedChatClient,
    ToolCall,
)

from frontier_evals.config import EvalConfig


def build_live_client(config: EvalConfig) -> OpenAIChatClient:
    if not config.api_base_url or not config.model:
        raise RuntimeError(
            "live mode requires FRONTIER_EVALS_API_BASE_URL and FRONTIER_EVALS_MODEL "
            "(point them at a vLLM/llama.cpp endpoint serving the model under test)."
        )
    return OpenAIChatClient(
        model=config.model,
        base_url=config.api_base_url,
        api_key=config.api_key,
        provider=config.provider,
    )


def _tool(call_id: str, name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def build_reference_solver(fix_edits: list[dict[str, str]]) -> ScriptedChatClient:
    """A scripted client that applies the given edits, runs tests, then submits."""
    responses: list[ChatResponse] = []
    for idx, edit in enumerate(fix_edits):
        responses.append(
            ChatResponse(
                tool_calls=[
                    _tool(
                        f"e{idx}",
                        "str_replace_editor",
                        command="str_replace",
                        path=edit["path"],
                        old_str=edit["old"],
                        new_str=edit["new"],
                    )
                ]
            )
        )
    responses.append(ChatResponse(tool_calls=[_tool("t", "run_tests")]))
    responses.append(
        ChatResponse(tool_calls=[_tool("s", "submit", answer="Applied reference fix.")])
    )
    return ScriptedChatClient(provider="reference", model="reference-solver", responses=responses)


def build_noop_solver() -> ScriptedChatClient:
    """A client that submits without making any change (resolves nothing)."""
    return ScriptedChatClient(
        provider="noop",
        model="noop-solver",
        responses=[ChatResponse(tool_calls=[_tool("s", "submit", answer="no change")])],
    )
