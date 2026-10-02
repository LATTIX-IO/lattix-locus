"""Session auto-notes for continuity between agent turns.

Generates lightweight session notes after each agent turn, capturing
decisions, files modified, and tools used. Provides continuity so
subsequent turns don't start from scratch.

Feature flag: LOCUS_SESSION_NOTES_ENABLED (default: false)
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class SessionNote:
    session_id: str
    run_id: str
    turn_index: int
    summary: str
    decisions: list[str]
    files_modified: list[str]
    tools_used: list[str]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_context_string(self) -> str:
        parts = [f"[Turn {self.turn_index}] {self.summary}"]
        if self.decisions:
            parts.append("Decisions: " + "; ".join(self.decisions[:3]))
        if self.files_modified:
            parts.append("Files: " + ", ".join(self.files_modified[:5]))
        if self.tools_used:
            parts.append("Tools: " + ", ".join(self.tools_used[:5]))
        return " | ".join(parts)


def _extract_decisions(text: str) -> list[str]:
    """Extract decision-like statements from assistant output."""
    decisions: list[str] = []
    patterns = [
        re.compile(
            r"(?:decided|choosing|selected|using|going with|opted for)\s+(.{10,80})", re.IGNORECASE
        ),
        re.compile(r"(?:will|should|need to)\s+(.{10,80})", re.IGNORECASE),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            candidate = match.group(1).strip().rstrip(".")
            if candidate and candidate not in decisions:
                decisions.append(candidate)
            if len(decisions) >= 3:
                return decisions
    return decisions


def _extract_files_modified(text: str, tool_calls: list[dict[str, Any]] | None = None) -> list[str]:
    """Extract file paths from tool calls or text content."""
    files: list[str] = []
    if tool_calls:
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            args = call.get("arguments") or call.get("args") or {}
            if isinstance(args, dict):
                for key in ("file_path", "path", "filename"):
                    val = str(args.get(key) or "").strip()
                    if val and val not in files:
                        files.append(val)

    file_pattern = re.compile(r"[`'\"]([^\s`'\"]+\.[a-zA-Z0-9]{1,10})[`'\"]")
    for match in file_pattern.finditer(text):
        candidate = match.group(1)
        if candidate not in files:
            files.append(candidate)
    return files[:10]


def _extract_tools_used(tool_calls: list[dict[str, Any]] | None = None) -> list[str]:
    """Extract unique tool names from tool calls."""
    if not tool_calls:
        return []
    tools: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name") or call.get("function", {}).get("name") or "").strip()
        if name and name not in tools:
            tools.append(name)
    return tools[:10]


def _summarize_rule_based(node_title: str, user_input: str, assistant_output: str) -> str:
    """Generate a 1-2 sentence summary using rules (no LLM cost)."""
    user_snippet = user_input[:100].replace("\n", " ").strip()
    response_snippet = assistant_output[:150].replace("\n", " ").strip()
    return f'Node \'{node_title}\' processed: "{user_snippet}" -> "{response_snippet}"'


def generate_session_note(
    node_title: str,
    user_input: str,
    assistant_output: str,
    tool_calls: list[dict[str, Any]] | None = None,
    model_meta: dict[str, Any] | None = None,
    *,
    session_id: str = "",
    run_id: str = "",
    turn_index: int = 0,
) -> SessionNote:
    """Generate a session note from a completed agent turn.

    Default mode is rule-based (zero LLM cost). LLM-assisted mode
    is available via LOCUS_SESSION_NOTES_LLM_ENABLED but not
    implemented in this module — callers can override the summary.
    """
    summary = _summarize_rule_based(node_title, user_input, assistant_output)
    decisions = _extract_decisions(assistant_output)
    files_modified = _extract_files_modified(assistant_output, tool_calls)
    tools_used = _extract_tools_used(tool_calls)

    return SessionNote(
        session_id=session_id,
        run_id=run_id,
        turn_index=turn_index,
        summary=summary[:500],
        decisions=decisions,
        files_modified=files_modified,
        tools_used=tools_used,
        timestamp=time.time(),
    )
