"""The fixed R2E-Gym/DeepSWE-shaped coding tool set.

Five tools, deliberately small — weak/local models perform better with a few
rigid, in-distribution tools than with a large free-form surface:

* ``execute_bash``      — run a shell command in the workspace
* ``search``            — ripgrep-style search (rg if present, python fallback)
* ``str_replace_editor``— view / create / str_replace / insert (exact-match edits)
* ``run_tests``         — run the workspace test command, verbatim output
* ``submit``            — finish; returns the workspace diff and ends the loop

Edits are exact-match: ``str_replace`` requires ``old_str`` to occur exactly
once (0 → not-found, >1 → ambiguous), the single biggest source of wasted
attempts on weak models — so we measure well-formed-edit rate as telemetry and
can auto-downgrade to whole-file edits.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any

from frontier_runtime.harness.workspace import Workspace

TOOL_OUTPUT_MAX_BYTES = 50_000
TOOL_OUTPUT_MAX_LINES = 2_000
BASH_TIMEOUT_CEILING = 600  # hard cap on a single execute_bash call (seconds)


def truncate_output(
    text: str, *, max_bytes: int = TOOL_OUTPUT_MAX_BYTES, max_lines: int = TOOL_OUTPUT_MAX_LINES
) -> tuple[str, bool]:
    """Head-60% / tail-40% elision; never splits a line mid-way."""
    if text is None:
        return "", False
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        head = int(max_lines * 0.6)
        tail = max_lines - head
        elided = len(lines) - max_lines
        lines = lines[:head] + [f"[... {elided} lines elided ...]"] + lines[-tail:]
        text = "\n".join(lines)
        truncated = True
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > max_bytes:
        head_b = int(max_bytes * 0.6)
        tail_b = max_bytes - head_b
        head_s = encoded[:head_b].decode("utf-8", "ignore")
        tail_s = encoded[-tail_b:].decode("utf-8", "ignore")
        text = f"{head_s}\n[... {len(encoded) - max_bytes} bytes elided ...]\n{tail_s}"
        truncated = True
    return text, truncated


@dataclass
class CodingTelemetry:
    tool_calls_total: int = 0
    tool_calls_malformed: int = 0
    edits_attempted: int = 0
    edits_well_formed: int = 0
    edits_applied: int = 0
    reasks: int = 0
    edit_format_downgrades: int = 0
    bash_calls: int = 0
    test_runs: int = 0

    def well_formed_edit_rate(self) -> float:
        return 1.0 if self.edits_attempted == 0 else self.edits_well_formed / self.edits_attempted

    def well_formed_call_rate(self) -> float:
        total = self.tool_calls_total + self.tool_calls_malformed
        return 1.0 if total == 0 else self.tool_calls_total / total

    def snapshot(self) -> dict[str, Any]:
        return {
            "tool_calls_total": self.tool_calls_total,
            "tool_calls_malformed": self.tool_calls_malformed,
            "edits_attempted": self.edits_attempted,
            "edits_well_formed": self.edits_well_formed,
            "edits_applied": self.edits_applied,
            "reasks": self.reasks,
            "edit_format_downgrades": self.edit_format_downgrades,
            "bash_calls": self.bash_calls,
            "test_runs": self.test_runs,
            "well_formed_edit_rate": round(self.well_formed_edit_rate(), 4),
            "well_formed_call_rate": round(self.well_formed_call_rate(), 4),
        }


def tool_schemas(edit_format: str = "search-replace") -> list[dict[str, Any]]:
    editor_desc = (
        "View, create, or edit a file. Commands: 'view' (show file with line numbers; "
        "optional view_range [start,end]); 'create' (write file_text to a new file); "
        "'str_replace' (replace the UNIQUE occurrence of old_str with new_str — old_str "
        "must match exactly once including whitespace); 'insert' (insert new_str after "
        "insert_line)."
    )
    if edit_format == "whole-file":
        editor_desc += " NOTE: prefer 'create' to rewrite the entire file when edits are difficult."
    return [
        {
            "type": "function",
            "function": {
                "name": "execute_bash",
                "description": "Run a shell command in the repository working directory.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run."},
                        "timeout": {"type": "integer", "description": "Seconds (default 60)."},
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search file contents for a query string (ripgrep semantics).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "description": "Dir or file (default '.')."},
                        "max_results": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "str_replace_editor",
                "description": editor_desc,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": ["view", "create", "str_replace", "insert"],
                        },
                        "path": {"type": "string"},
                        "old_str": {"type": "string"},
                        "new_str": {"type": "string"},
                        "file_text": {"type": "string"},
                        "insert_line": {"type": "integer"},
                        "view_range": {"type": "array", "items": {"type": "integer"}},
                    },
                    # 'command' is intentionally NOT required: weak models often
                    # omit it (or use line_start/line_end), and we infer it from
                    # the other arguments rather than burning a re-ask step.
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_tests",
                "description": "Run the repository test command and return verbatim output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "test_command": {"type": "string", "description": "Override command."}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit",
                "description": "Submit your final answer once the work is complete and tests pass. "
                "Call exactly once.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "regression_tests": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional test commands that verify the fix.",
                        },
                    },
                },
            },
        },
    ]


CODING_TOOL_NAMES = {"execute_bash", "search", "str_replace_editor", "run_tests", "submit"}


@dataclass
class CodingToolset:
    workspace: Workspace
    edit_format: str = "search-replace"
    bash_timeout: int = 60
    test_timeout: int = 600
    telemetry: CodingTelemetry = field(default_factory=CodingTelemetry)
    downgrade_after: int = 2

    max_empty_submit_retries: int = 2
    # Out-of-bounds policy: how to handle file ops outside the bound workspace.
    #   "ask"   — deny + raise an escalation (human must grant the path)
    #   "deny"  — deny silently with a message
    #   "allow" — permit (the executor must already allow the path)
    out_of_bounds: str = "ask"
    on_escalation: Any = None  # Callable[[dict], None] | None
    # When False the agent is read+exec only: str_replace_editor allows just
    # `view` (no create/str_replace/insert). Used by analyzer agents (security,
    # QA, performance) that read code + run tests/scanners but never mutate it.
    allow_edits: bool = True

    submitted: bool = False
    submission: dict[str, Any] | None = None
    escalations: list[dict[str, Any]] = field(default_factory=list)
    _consecutive_edit_failures: int = 0
    _failed_submits: int = 0

    def schemas(self) -> list[dict[str, Any]]:
        return tool_schemas(self.edit_format)

    # -- dispatch -----------------------------------------------------------
    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "submit":
            return self._submit(arguments)
        if name == "execute_bash":
            return self._bash(arguments)
        if name == "search":
            return self._search(arguments)
        if name == "str_replace_editor":
            return self._editor(arguments)
        if name == "run_tests":
            return self._run_tests(arguments)
        return f"[error] unknown tool: {name}"

    # -- individual tools ---------------------------------------------------
    def _bash(self, args: dict[str, Any]) -> str:
        cmd = str(args.get("command") or "").strip()
        if not cmd:
            return "[error] execute_bash requires a 'command'."
        self.telemetry.bash_calls += 1
        # Clamp model-supplied timeouts: gpt-oss has been observed passing
        # timeout=10000 (2.7h), which would let a hung command stall the run.
        requested = int(args.get("timeout") or self.bash_timeout)
        timeout = max(1, min(requested, BASH_TIMEOUT_CEILING))
        res = self.workspace.executor.run_shell(cmd, timeout=timeout)
        out, _ = truncate_output(res.combined())
        return out

    def _search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query") or "")
        if not query:
            return "[error] search requires a 'query'."
        path = str(args.get("path") or ".")
        max_results = int(args.get("max_results") or 50)
        ex = self.workspace.executor
        probe = ex.run_shell("command -v rg >/dev/null 2>&1 && echo yes || echo no", timeout=15)
        if probe.stdout.strip().endswith("yes"):
            cmd = (
                f"rg --line-number --no-heading --color=never --max-count={max_results} "
                f"-- {shlex.quote(query)} {shlex.quote(path)}"
            )
        else:
            cmd = (
                f"grep -rn --max-count={max_results} -- {shlex.quote(query)} {shlex.quote(path)} "
                f"2>/dev/null || true"
            )
        res = ex.run_shell(cmd, timeout=60)
        out, _ = truncate_output(res.stdout or "(no matches)")
        return out

    def _run_tests(self, args: dict[str, Any]) -> str:
        self.telemetry.test_runs += 1
        cmd = str(args.get("test_command") or "")
        res = self.workspace.run_tests(cmd, timeout=self.test_timeout)
        if res is None:
            return "[error] no test command configured for this workspace."
        out, _ = truncate_output(res.combined())
        return out

    def _submit(self, args: dict[str, Any]) -> str:
        diff = self.workspace.diff()
        # Safety net: if we applied edits but git produced no diff, a transient
        # stat-cache miss likely ate it. Refresh and retry once.
        if not diff.strip() and self.telemetry.edits_applied > 0:
            try:
                if self.workspace.has_uncommitted_changes():
                    diff = self.workspace.diff()
            except Exception:  # noqa: BLE001
                pass
        # If we still have an empty diff despite applied edits, REJECT the submit
        # (don't terminate) so the model can verify/re-apply rather than losing a
        # real fix to a flaky diff. Genuine no-op tasks (edits_applied == 0) submit.
        if not diff.strip() and self.telemetry.edits_applied > 0:
            self._failed_submits += 1
            if self._failed_submits <= self.max_empty_submit_retries:
                return (
                    "Submit rejected: the workspace diff is EMPTY despite "
                    f"{self.telemetry.edits_applied} applied edit(s). Your changes may not have "
                    "persisted. Run execute_bash 'git --no-pager diff' to check, re-apply the edit "
                    "with str_replace_editor if it is missing, then call submit again."
                )
            # give up rejecting after N tries; accept whatever we have
        self.submission = {
            "answer": str(args.get("answer") or ""),
            "patch": diff,
            "regression_tests": args.get("regression_tests") or [],
        }
        self.submitted = True
        out, _ = truncate_output(diff or "(no changes)")
        return f"Submission recorded. Workspace diff:\n{out}"

    # -- editor (the edit-reliability hot path) -----------------------------
    def _normalize_editor_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Adapt to the editor API priors different models bring.

        Observed with gpt-oss: a 'view' issued as {path, line_start, line_end}
        with no 'command'. Rather than burn a re-ask, infer the command and map
        common aliases (line_start/line_end -> view_range, content/text ->
        file_text). Defaults to the read-only 'view' when ambiguous.
        """
        args = dict(args)
        # alias mapping
        if "view_range" not in args and ("line_start" in args or "line_end" in args):
            start = args.get("line_start", 1)
            end = args.get("line_end", start)
            args["view_range"] = [start, end]
        if "file_text" not in args:
            for alias in ("content", "text", "new_file_text"):
                if alias in args:
                    args["file_text"] = args[alias]
                    break
        # command inference when missing/blank
        if not str(args.get("command") or "").strip():
            if args.get("old_str") is not None:
                args["command"] = "str_replace"
            elif args.get("file_text") is not None:
                args["command"] = "create"
            elif args.get("insert_line") is not None and args.get("new_str") is not None:
                args["command"] = "insert"
            else:
                args["command"] = "view"
        return args

    def _check_bounds(self, path: str) -> str | None:
        """Enforce the workspace boundary. Returns an error/escalation message to
        send back to the agent if ``path`` is out of bounds, else None."""
        ex = self.workspace.executor
        allows = getattr(ex, "allows", None)
        if allows is None or self.out_of_bounds == "allow" or allows(path):
            return None
        request = {"path": path, "workspace_root": ex.workdir(), "policy": self.out_of_bounds}
        self.escalations.append(request)
        if callable(self.on_escalation):
            self.on_escalation(request)
        if self.out_of_bounds == "deny":
            return (
                f"[denied] '{path}' is outside your bound workspace ({ex.workdir()}). "
                "Work only within the workspace you were assigned."
            )
        return (
            f"[permission required] '{path}' is outside your bound workspace ({ex.workdir()}). "
            "You may only work in the assigned workspace. To touch this path, the change must be "
            "approved by the human and the path granted to the workspace. Proceed within bounds, "
            "or stop and request access."
        )

    def _editor(self, args: dict[str, Any]) -> str:
        args = self._normalize_editor_args(args)
        command = str(args.get("command") or "")
        path = str(args.get("path") or "")
        if not path:
            return "[error] str_replace_editor requires 'path'."
        if not self.allow_edits and command != "view":
            return (
                "[denied] this agent is read-only (analysis mode): only "
                "str_replace_editor 'view' is allowed. Report findings via submit; "
                "do not modify files."
            )
        bounds_error = self._check_bounds(path)
        if bounds_error is not None:
            return bounds_error
        ex = self.workspace.executor

        if command == "view":
            content = ex.read_file(path)
            if content is None:
                return f"[error] file not found: {path}"
            view_range = args.get("view_range")
            lines = content.splitlines()
            start, end = 1, len(lines)
            if isinstance(view_range, list) and len(view_range) == 2:
                start = max(1, int(view_range[0]))
                end = min(len(lines), int(view_range[1]))
            numbered = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(start, end + 1))
            out, _ = truncate_output(numbered)
            return out

        if command == "create":
            file_text = args.get("file_text")
            if file_text is None:
                return "[error] 'create' requires 'file_text'."
            self.telemetry.edits_attempted += 1
            ex.write_file(path, str(file_text))
            self.telemetry.edits_well_formed += 1
            self.telemetry.edits_applied += 1
            self._consecutive_edit_failures = 0
            return f"File written: {path} ({len(str(file_text).splitlines())} lines)."

        if command == "str_replace":
            return self._str_replace(args, path)

        if command == "insert":
            return self._insert(args, path)

        return f"[error] unknown editor command: {command}"

    def _str_replace(self, args: dict[str, Any], path: str) -> str:
        self.telemetry.edits_attempted += 1
        old_str = args.get("old_str")
        new_str = args.get("new_str", "")
        if old_str is None:
            self._note_edit_failure()
            return "[error] 'str_replace' requires 'old_str'."
        content = self.workspace.executor.read_file(path)
        if content is None:
            self._note_edit_failure()
            return f"[error] file not found: {path}"
        count = content.count(old_str)
        if count == 0:
            self._note_edit_failure()
            hint = self._nearest_hint(content, str(old_str))
            return f"[error] old_str not found in {path}. {hint}{self._downgrade_hint()}"
        if count > 1:
            self._note_edit_failure()
            return (
                f"[error] old_str is ambiguous ({count} occurrences) in {path}; "
                f"include more surrounding context so it matches exactly once."
                f"{self._downgrade_hint()}"
            )
        updated = content.replace(old_str, str(new_str), 1)
        self.workspace.executor.write_file(path, updated)
        self.telemetry.edits_well_formed += 1
        self.telemetry.edits_applied += 1
        self._consecutive_edit_failures = 0
        return f"Edit applied to {path}."

    def _insert(self, args: dict[str, Any], path: str) -> str:
        self.telemetry.edits_attempted += 1
        new_str = args.get("new_str")
        insert_line = args.get("insert_line")
        if new_str is None or insert_line is None:
            self._note_edit_failure()
            return "[error] 'insert' requires 'new_str' and 'insert_line'."
        content = self.workspace.executor.read_file(path)
        if content is None:
            self._note_edit_failure()
            return f"[error] file not found: {path}"
        lines = content.splitlines()
        idx = max(0, min(len(lines), int(insert_line)))
        lines[idx:idx] = str(new_str).splitlines()
        self.workspace.executor.write_file(path, "\n".join(lines) + "\n")
        self.telemetry.edits_well_formed += 1
        self.telemetry.edits_applied += 1
        self._consecutive_edit_failures = 0
        return f"Inserted {len(str(new_str).splitlines())} line(s) into {path}."

    # -- edit-format auto-downgrade ----------------------------------------
    def _note_edit_failure(self) -> None:
        self._consecutive_edit_failures += 1
        if (
            self.edit_format == "search-replace"
            and self._consecutive_edit_failures >= self.downgrade_after
        ):
            self.edit_format = "whole-file"
            self.telemetry.edit_format_downgrades += 1

    def _downgrade_hint(self) -> str:
        if self.edit_format == "whole-file":
            return (
                " Edits are failing repeatedly — use str_replace_editor with command 'create' "
                "to rewrite the whole file instead."
            )
        return ""

    @staticmethod
    def _nearest_hint(content: str, old_str: str) -> str:
        first = old_str.strip().splitlines()[0] if old_str.strip() else ""
        if first and first in content:
            return f"A line containing '{first[:60]}' exists — re-check exact whitespace."
        return "No close match found; view the file first."


def parse_arguments(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse tool-call arguments (which arrive as a JSON string from the model)."""
    if isinstance(raw, dict):
        return raw, ""
    if raw is None or raw == "":
        return {}, ""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"arguments are not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "arguments must be a JSON object"
    return parsed, ""
