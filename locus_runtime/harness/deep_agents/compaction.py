"""Context compaction for model requests (LOCUS-361 token diet). Pure; no third-party code.

Tool output dominates a coding agent's context, and an old ``view`` or test log
is rarely needed verbatim many turns later. Before each model call the harness
shrinks what the *model sees* (the graph state, the trajectory and the audit
keep everything):

* every tool output is hard-capped at ``max_chars``;
* all but the newest ``keep_recent`` tool outputs are cut to ``old_chars``;
* when the whole request is over ``context_chars``, older outputs are cut
  further, to ``pressure_chars``.

A cut keeps the head and the tail and says how much was omitted, so the agent
can re-run the tool when it needs the full text. The decision is deterministic
(no model call, unlike summarization), so it costs no tokens and cannot leak
content to another prompt.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class CompactionPolicy:
    keep_recent: int = 4
    max_chars: int = 12_000
    old_chars: int = 1_500
    context_chars: int = 80_000
    pressure_chars: int = 400

    def __post_init__(self) -> None:
        if self.keep_recent < 0 or min(self.max_chars, self.old_chars, self.pressure_chars) < 80:
            raise ValueError("compaction limits are too small to keep a useful head and tail")


def clip(text: str, limit: int) -> str:
    """``text`` cut to about ``limit`` characters (head + tail + an omission note)."""
    if len(text) <= limit:
        return text
    head = (limit * 2) // 3
    tail = max(limit - head, 0)
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n[... {omitted} characters of this tool output omitted to save "
        f"context; run the tool again if you need them ...]\n{text[len(text) - tail :]}"
    )


def compact(messages: Sequence[tuple[str, str]], policy: CompactionPolicy) -> dict[int, str]:
    """Replacement contents for tool messages, keyed by index.

    ``messages`` is ``(role, text)`` per message in request order; ``role`` is
    ``"tool"`` for tool output. Messages not in the result are left unchanged.
    """
    tool_indexes = [i for i, (role, _text) in enumerate(messages) if role == "tool"]
    recent = set(tool_indexes[-policy.keep_recent :]) if policy.keep_recent else set()
    total = sum(len(text) for _role, text in messages)
    old_limit = policy.pressure_chars if total > policy.context_chars else policy.old_chars
    out: dict[int, str] = {}
    for index in tool_indexes:
        text = messages[index][1]
        limit = policy.max_chars if index in recent else old_limit
        clipped = clip(text, limit)
        if clipped != text:
            out[index] = clipped
    return out


def saved_chars(messages: Sequence[tuple[str, str]], replacements: dict[int, str]) -> int:
    return sum(len(messages[i][1]) - len(text) for i, text in replacements.items())
