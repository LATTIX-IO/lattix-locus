"""Conversation history management with progressive compaction.

Enables multi-turn conversations for Locus agent nodes, replacing
the single-turn (system + user) pattern with full conversation history,
token-aware compaction, and session continuity.

Feature flag: LOCUS_CONVERSATION_ENABLED (default: false)
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    role: str  # "system" | "user" | "assistant"
    content: str
    turn_index: int
    token_estimate: int
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)
    compacted: bool = False


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\S+", str(text or ""))
    if not words:
        return 0
    return max(1, int(round(len(words) / 0.75)))


class ConversationManager:
    """Manages multi-turn conversation history with progressive compaction.

    Compaction stages (triggered when usage exceeds compaction_threshold):
      1. Trim tool results from turns older than the last 3 (free, no LLM)
      2. Summarize old turns into a compact summary turn (~200 tokens)
      3. Hard truncate to system + summary + last 4 turns
    """

    def __init__(
        self,
        session_id: str,
        run_id: str,
        *,
        max_tokens: int = 8000,
        compaction_threshold: float = 0.75,
    ) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.max_tokens = max(500, int(max_tokens))
        self.compaction_threshold = max(0.1, min(1.0, float(compaction_threshold)))
        self._turns: list[ConversationTurn] = []
        self._next_index: int = 0
        self._summary: str | None = None

    @property
    def total_tokens(self) -> int:
        return sum(turn.token_estimate for turn in self._turns)

    @property
    def turns(self) -> list[ConversationTurn]:
        return list(self._turns)

    def add_turn(
        self,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurn:
        token_estimate = _estimate_tokens(content)
        turn = ConversationTurn(
            role=role,
            content=content,
            turn_index=self._next_index,
            token_estimate=token_estimate,
            timestamp=time.time(),
            metadata=metadata or {},
        )
        self._turns.append(turn)
        self._next_index += 1

        if self.total_tokens > self.max_tokens * self.compaction_threshold:
            self.compact()

        return turn

    def get_messages(self) -> list[dict[str, str]]:
        """Return OpenAI-format messages list from current turns."""
        messages: list[dict[str, str]] = []
        if self._summary:
            messages.append(
                {
                    "role": "system",
                    "content": f"[Conversation summary from earlier turns]\n{self._summary}",
                }
            )
        for turn in self._turns:
            if turn.compacted:
                continue
            messages.append({"role": turn.role, "content": turn.content})
        return messages

    def get_last_reasoning(self) -> str | None:
        """Return the last reasoning summary from assistant turns (for WS3)."""
        for turn in reversed(self._turns):
            if turn.role == "assistant" and turn.metadata.get("reasoning_summaries"):
                summaries = turn.metadata["reasoning_summaries"]
                if isinstance(summaries, list) and summaries:
                    return summaries[-1] if isinstance(summaries[-1], str) else str(summaries[-1])
        return None

    def compact(self) -> None:
        """Progressive 3-stage compaction."""
        if len(self._turns) <= 4:
            return

        # Stage 1: Trim tool results from turns older than last 3
        cutoff = len(self._turns) - 3
        for i in range(cutoff):
            turn = self._turns[i]
            if turn.role == "assistant" and "tool_calls" in turn.metadata:
                trimmed_meta = dict(turn.metadata)
                trimmed_meta.pop("tool_calls", None)
                trimmed_meta.pop("tool_results", None)
                self._turns[i] = ConversationTurn(
                    role=turn.role,
                    content=turn.content,
                    turn_index=turn.turn_index,
                    token_estimate=turn.token_estimate,
                    timestamp=turn.timestamp,
                    metadata=trimmed_meta,
                    compacted=turn.compacted,
                )

        if self.total_tokens <= self.max_tokens:
            return

        # Stage 2: Summarize old turns into bullet points (rule-based, no LLM)
        old_turns = self._turns[:cutoff]
        last_reasoning = None
        for turn in reversed(old_turns):
            if turn.role == "assistant" and turn.metadata.get("reasoning_summaries"):
                summaries = turn.metadata["reasoning_summaries"]
                if isinstance(summaries, list) and summaries:
                    last_reasoning = (
                        summaries[-1] if isinstance(summaries[-1], str) else str(summaries[-1])
                    )
                    break

        summary_parts: list[str] = []
        for turn in old_turns:
            if turn.compacted:
                continue
            prefix = turn.role.upper()
            snippet = turn.content[:200].replace("\n", " ").strip()
            if snippet:
                summary_parts.append(f"- [{prefix}] {snippet}")

        if summary_parts:
            new_summary = "\n".join(summary_parts[-8:])
            if last_reasoning:
                new_summary += f"\n\n[Last reasoning]: {last_reasoning[:300]}"
            if self._summary:
                self._summary = f"{self._summary}\n{new_summary}"
            else:
                self._summary = new_summary

        for i in range(cutoff):
            self._turns[i] = ConversationTurn(
                role=self._turns[i].role,
                content="",
                turn_index=self._turns[i].turn_index,
                token_estimate=0,
                timestamp=self._turns[i].timestamp,
                metadata=self._turns[i].metadata,
                compacted=True,
            )

        if self.total_tokens <= self.max_tokens:
            return

        # Stage 3: Hard truncate — keep only system (first) + summary + last 4 turns
        active_turns = [t for t in self._turns if not t.compacted]
        if len(active_turns) > 4:
            keep_set = set()
            # Keep the system turn if present
            for t in self._turns:
                if not t.compacted and t.role == "system":
                    keep_set.add(t.turn_index)
                    break
            # Keep last 4 non-compacted
            for t in active_turns[-4:]:
                keep_set.add(t.turn_index)
            for i, t in enumerate(self._turns):
                if not t.compacted and t.turn_index not in keep_set:
                    self._turns[i] = ConversationTurn(
                        role=t.role,
                        content="",
                        turn_index=t.turn_index,
                        token_estimate=0,
                        timestamp=t.timestamp,
                        metadata=t.metadata,
                        compacted=True,
                    )

    def serialize(self) -> str:
        """Serialize to JSON for Redis persistence."""
        return json.dumps(
            {
                "session_id": self.session_id,
                "run_id": self.run_id,
                "max_tokens": self.max_tokens,
                "compaction_threshold": self.compaction_threshold,
                "next_index": self._next_index,
                "summary": self._summary,
                "turns": [asdict(t) for t in self._turns],
            }
        )

    @classmethod
    def deserialize(cls, data: str) -> ConversationManager:
        """Reconstruct from serialized JSON."""
        parsed = json.loads(data)
        manager = cls(
            session_id=parsed["session_id"],
            run_id=parsed["run_id"],
            max_tokens=parsed.get("max_tokens", 8000),
            compaction_threshold=parsed.get("compaction_threshold", 0.75),
        )
        manager._next_index = parsed.get("next_index", 0)
        manager._summary = parsed.get("summary")
        for turn_data in parsed.get("turns", []):
            manager._turns.append(
                ConversationTurn(
                    role=turn_data["role"],
                    content=turn_data["content"],
                    turn_index=turn_data["turn_index"],
                    token_estimate=turn_data["token_estimate"],
                    timestamp=turn_data["timestamp"],
                    metadata=turn_data.get("metadata", {}),
                    compacted=turn_data.get("compacted", False),
                )
            )
        return manager
