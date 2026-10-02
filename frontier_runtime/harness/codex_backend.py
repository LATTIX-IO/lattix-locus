"""Drive OpenAI **Codex** as a subprocess coding backend.

xFrontier keeps orchestration / memory / security / diff capture; Codex does the
file work in its own OS sandbox. We invoke ``codex exec --json`` headlessly in a
bound git worktree, pointed at local gpt-oss via Codex's built-in ``--oss``
(Ollama) provider, and translate its JSONL ``ThreadEvent`` stream into
xFrontier run-events. The produced diff is captured by ``workspace.changed_files()``.

Wire schema (grounded in codex-rs/exec/src/exec_events.rs): each stdout line is a
``ThreadEvent`` ``{"type": "...", ...}`` where type is one of ``thread.started``,
``turn.started``, ``turn.completed`` (usage), ``turn.failed`` (error), ``error``,
``item.started`` / ``item.updated`` / ``item.completed`` (carry a ``ThreadItem``
``{id, type, ...}`` whose type is ``agent_message{text}``, ``reasoning{text}``,
``command_execution{command,aggregated_output,exit_code,status}``,
``file_change{changes:[{path,kind}],status}``, ``mcp_tool_call{...}``, ``error{message}``).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class CodexResult:
    answer: str = ""
    reasoning: str = ""
    files: list[str] = field(default_factory=list)
    outcome: str = "completed"  # completed | failed | unavailable | timeout
    usage: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int | None = None


def map_thread_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Codex ``ThreadEvent`` JSON object to a normalized step
    ``{kind, ...}``, or None if it isn't worth surfacing. Pure + unit-testable.

    Only terminal ``item.completed`` items carry full payloads; ``item.started``/
    ``item.updated`` are interim and ignored (avoids duplicate/empty surfaces).
    """
    if not isinstance(ev, dict):
        return None
    etype = str(ev.get("type") or "")
    if etype == "item.completed":
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        itype = str(item.get("type") or "")
        if itype == "agent_message":
            return {"kind": "agent_message", "text": str(item.get("text") or "")}
        if itype == "reasoning":
            return {"kind": "reasoning", "text": str(item.get("text") or "")}
        if itype == "command_execution":
            return {
                "kind": "command",
                "command": str(item.get("command") or ""),
                "exit_code": item.get("exit_code"),
                "status": str(item.get("status") or ""),
                "output": str(item.get("aggregated_output") or "")[:2000],
            }
        if itype == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            return {
                "kind": "file_change",
                "status": str(item.get("status") or ""),
                "files": [
                    str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")
                ],
                "kinds": [str(c.get("kind")) for c in changes if isinstance(c, dict)],
            }
        if itype == "mcp_tool_call":
            return {
                "kind": "mcp_tool",
                "server": item.get("server"),
                "tool": item.get("tool"),
                "status": item.get("status"),
            }
        if itype == "error":
            return {"kind": "error", "message": str(item.get("message") or "")}
        return None
    if etype == "turn.completed":
        return {
            "kind": "usage",
            "usage": ev.get("usage") if isinstance(ev.get("usage"), dict) else {},
        }
    if etype == "turn.failed":
        err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
        return {"kind": "error", "message": str(err.get("message") or "turn failed")}
    if etype == "error":
        return {"kind": "error", "message": str(ev.get("message") or "stream error")}
    return None  # thread.started / turn.started / item.started / item.updated


def _build_command(
    *,
    codex_bin: str,
    cwd: str,
    model: str,
    sandbox: str,
    last_message_file: str,
    config_overrides: dict[str, str],
) -> list[str]:
    args = [
        codex_bin,
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--cd",
        str(cwd),
        "--sandbox",
        sandbox,
        "--oss",
        "-m",
        model,
        "-o",
        last_message_file,
    ]
    for key, value in (config_overrides or {}).items():
        args += ["-c", f"{key}={value}"]
    args += ["-"]  # read the prompt from stdin
    return args


def run_codex(
    *,
    prompt: str,
    cwd: str,
    model: str = "gpt-oss:20b",
    sandbox: str = "workspace-write",
    config_overrides: dict[str, str] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    timeout: int = 900,
    codex_bin: str | None = None,
) -> CodexResult:
    """Run ``codex exec --json`` in ``cwd`` and stream its events.

    Degrades to ``outcome="unavailable"`` if the codex binary is missing, so the
    caller can fall back to the native backend rather than crash.
    """
    binary = codex_bin or os.getenv("CODEX_BIN", "codex")
    model = model.split("/", 1)[-1] if "/" in model else model  # strip provider prefix
    overrides = dict(config_overrides or {})
    # Point Codex's built-in OSS provider at our Ollama endpoint when it isn't the
    # default localhost (e.g. a sidecar). Finalize the exact key at live smoke.
    base = os.getenv("CODEX_OLLAMA_BASE_URL") or os.getenv("OLLAMA_BASE_URL")
    if base and "localhost" not in base and "127.0.0.1" not in base:
        overrides.setdefault("model_providers.oss.base_url", f"{base.rstrip('/')}/v1")

    result = CodexResult()
    last_msg_path = ""
    try:
        fd, last_msg_path = tempfile.mkstemp(prefix="codex-last-", suffix=".txt")
        os.close(fd)
        cmd = _build_command(
            codex_bin=binary,
            cwd=cwd,
            model=model,
            sandbox=sandbox,
            last_message_file=last_msg_path,
            config_overrides=overrides,
        )
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            result.outcome = "unavailable"
            return result

        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

        deadline = time.time() + timeout
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                result.outcome = "timeout"
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            mapped = map_thread_event(ev)
            if not mapped:
                continue
            result.events.append(mapped)
            if on_event:
                try:
                    on_event(mapped["kind"], mapped)
                except Exception:  # noqa: BLE001
                    pass
            kind = mapped["kind"]
            if kind == "agent_message" and mapped.get("text"):
                result.answer = mapped["text"]
            elif kind == "reasoning" and mapped.get("text"):
                result.reasoning = (result.reasoning + "\n" + mapped["text"]).strip()
            elif kind == "file_change":
                result.files.extend(mapped.get("files") or [])
            elif kind == "usage":
                result.usage = mapped.get("usage") or {}
            elif kind == "error" and result.outcome == "completed":
                result.outcome = "failed"

        try:
            result.exit_code = proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        # Prefer the explicit last-message file for the final answer.
        try:
            text = Path(last_msg_path).read_text(encoding="utf-8").strip()
            if text:
                result.answer = text
        except Exception:  # noqa: BLE001
            pass
        if result.exit_code not in (0, None) and result.outcome == "completed":
            result.outcome = "failed"
        return result
    finally:
        if last_msg_path:
            try:
                os.unlink(last_msg_path)
            except OSError:
                pass
