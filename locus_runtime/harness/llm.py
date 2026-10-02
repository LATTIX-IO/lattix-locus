"""Model client abstraction for the harness.

The loop talks to a ``ChatClient`` — anything that, given messages + tool
schemas + sampler settings, returns a ``ChatResponse`` (assistant text and/or
tool calls + token usage). This keeps the loop model-agnostic and makes it
trivially testable with a scripted client (no network, no provider SDK).

``OpenAIChatClient`` is the production implementation over any
OpenAI-compatible endpoint (vLLM, llama.cpp llama-server, LM Studio, Ollama,
OpenAI). It lazily imports the ``openai`` SDK so importing this module never
requires it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Any  # raw JSON string from the model, or a dict


@dataclass
class ChatResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


class ChatClient(Protocol):
    provider: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResponse: ...


class OpenAIChatClient:
    """OpenAI-compatible chat client (vLLM / llama.cpp / LM Studio / Ollama / OpenAI)."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str = "not-needed",
        provider: str = "openai-compatible",
        default_max_tokens: int = 4096,
        request_timeout: float = 600.0,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key or "not-needed"
        self.provider = provider
        self.default_max_tokens = default_max_tokens
        self.request_timeout = request_timeout
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - env dependent
                raise RuntimeError(
                    "The 'openai' package is required for OpenAIChatClient. "
                    "Install it or inject a ChatClient stub."
                ) from exc
            self._client = OpenAI(
                base_url=self.base_url, api_key=self.api_key, timeout=self.request_timeout
            )
        return self._client

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if reasoning_effort:
            # reasoning models (gpt-oss/Harmony) take the effort level; send it in
            # the request body so OpenAI-compatible servers that don't support it
            # ignore it rather than erroring.
            body = dict(kwargs.get("extra_body") or {})
            body["reasoning_effort"] = reasoning_effort
            kwargs["extra_body"] = body
        if extra:
            kwargs.update(extra)

        completion = client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            tool_calls.append(
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            )
        usage = {}
        if getattr(completion, "usage", None):
            usage = {
                "prompt_tokens": completion.usage.prompt_tokens,
                "completion_tokens": completion.usage.completion_tokens,
            }
        return ChatResponse(
            text=msg.content or "", tool_calls=tool_calls, usage=usage, raw=completion
        )


@dataclass
class ScriptedChatClient:
    """Deterministic client for tests: replays a fixed list of ChatResponses.

    Each ``complete`` call pops the next scripted response. If a callable is
    provided instead, it is invoked with the current messages — enabling
    state-dependent fakes (e.g. 'emit a fix only after viewing the file').
    """

    provider: str = "scripted"
    model: str = "scripted-model"
    responses: list[Any] = field(default_factory=list)
    _idx: int = 0
    calls: list[list[dict[str, Any]]] = field(default_factory=list)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ChatResponse:
        self.calls.append([dict(m) for m in messages])
        if self._idx >= len(self.responses):
            return ChatResponse(text="(no scripted response remaining)")
        item = self.responses[self._idx]
        self._idx += 1
        if callable(item):
            item = item(messages)
        if isinstance(item, ChatResponse):
            return item
        if isinstance(item, str):
            return ChatResponse(text=item)
        raise TypeError(f"Unsupported scripted response: {type(item)}")
