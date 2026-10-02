"""The agent loop — model-agnostic, append-only, replayable.

One linear message list grows across the whole run (no per-iteration prompt
rebuilds — that both wastes the model's context and destroys prefix caching).
Termination is the ``submit`` tool, not text markers. Budgets are hard, with
submit-or-zero semantics (DeepSWE compact filtering): a run that exhausts its
budget without submitting earns zero credit and is tagged so it can be excluded
from training data.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from frontier_runtime.harness.enforcement import (
    ReaskPolicy,
    constraint_kwargs,
    reask_tool_message,
    schema_by_name,
    validate_tool_call,
)
from frontier_runtime.harness.llm import ChatClient, ChatResponse, ToolCall
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.trajectory import TrajectoryRecorder


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\S+", str(text or ""))
    return max(0, int(round(len(words) / 0.75))) if words else 0


class LoopOutcome(str, Enum):
    SUBMITTED = "submitted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    ERROR = "error"


@dataclass
class LoopBudgets:
    max_steps: int = 30
    max_seconds: float = 1800.0
    max_context_tokens: int = 96_000
    max_repair_retries: int = 2


@dataclass
class LoopResult:
    outcome: LoopOutcome
    text: str
    submission: dict[str, Any] | None
    steps: int
    messages: list[dict[str, Any]]
    telemetry: dict[str, Any]
    trajectory: TrajectoryRecorder
    elapsed_seconds: float
    tokens_estimate: int


@dataclass
class AgentLoop:
    client: ChatClient
    toolset: CodingToolset
    profile: ModelCapabilityProfile
    system_prompt: str
    user_prompt: str
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    reask_policy: ReaskPolicy = field(default_factory=ReaskPolicy)
    recorder: TrajectoryRecorder | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    agent_id: str = "swe-agent"
    task_meta: dict[str, Any] = field(default_factory=dict)
    provider_max_retries: int = 3
    provider_retry_backoff: float = 1.5  # seconds, exponential

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def _sampler(self) -> dict[str, Any]:
        return {"temperature": self.profile.temperature, "top_p": self.profile.top_p}

    def run(self) -> LoopResult:
        rec = self.recorder or TrajectoryRecorder(run_id=self.task_meta.get("run_id", "local"))
        tools = self.toolset.schemas()
        schemas = schema_by_name(tools)
        start = time.time()

        rec.header(
            agent_id=self.agent_id,
            model=getattr(self.client, "model", "unknown"),
            provider=getattr(self.client, "provider", "unknown"),
            sampler=self._sampler(),
            budgets=self.budgets.__dict__,
            system_prompt=self.system_prompt,
            task=self.task_meta,
            harness={"version": "0.1.0", "protocol": self.profile.tool_protocol},
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]
        rec.message(messages[0], step=0)
        rec.message(messages[1], step=0)

        outcome = LoopOutcome.ERROR
        step = 0
        reasks_used = 0
        tokens_estimate = 0
        forced_final = False

        while step < self.budgets.max_steps:
            elapsed = time.time() - start
            tokens_estimate = sum(_estimate_tokens(str(m.get("content") or "")) for m in messages)

            over_budget = (
                elapsed >= self.budgets.max_seconds
                or tokens_estimate >= self.budgets.max_context_tokens
            )
            if over_budget and not forced_final:
                forced_final = True
                messages.append(
                    {
                        "role": "user",
                        "content": "Budget limit reached. Call `submit` now with your best "
                        "current answer; no further tool calls are available.",
                    }
                )
                rec.annotation(
                    "budget_forced_submit", step=step, elapsed=elapsed, tokens=tokens_estimate
                )
            elif over_budget and forced_final:
                outcome = LoopOutcome.BUDGET_EXHAUSTED
                break

            extra = constraint_kwargs(getattr(self.client, "provider", ""), self.profile, tools)
            if forced_final:
                extra = {
                    **extra,
                    "tool_choice": {"type": "function", "function": {"name": "submit"}},
                }
            # Local inference servers (Ollama/vLLM/llama.cpp) under load throw
            # transient errors/timeouts. Retry with backoff before giving up so
            # long-horizon runs survive a contended endpoint.
            resp = None
            for attempt in range(self.provider_max_retries + 1):
                try:
                    resp = self.client.complete(
                        messages,
                        tools=tools,
                        temperature=self.profile.temperature,
                        top_p=self.profile.top_p,
                        extra=extra or None,
                    )
                    break
                except Exception as exc:  # noqa: BLE001 - transient provider failure
                    rec.annotation(
                        "provider_error", step=step, attempt=attempt, error=str(exc)[:200]
                    )
                    self._emit("provider_error", attempt=attempt, error=str(exc))
                    if attempt < self.provider_max_retries:
                        time.sleep(self.provider_retry_backoff * (2**attempt))
            if resp is None:
                outcome = LoopOutcome.PROVIDER_UNAVAILABLE
                break

            step += 1
            assistant_msg = self._assistant_message(resp)
            messages.append(assistant_msg)
            rec.message(assistant_msg, step=step, usage=resp.usage or None)
            self._emit(
                "model_step", step=step, has_tools=bool(resp.tool_calls), text=resp.text[:200]
            )

            if not resp.tool_calls:
                # No tool call. In bash-only mode, try to parse an action.
                if self.profile.tool_protocol == "bash-only":
                    handled = self._handle_bash_only(resp.text, messages, rec, step)
                    if handled == "submit":
                        outcome = LoopOutcome.SUBMITTED
                        break
                    if handled == "action":
                        continue
                # Otherwise nudge to use tools / submit.
                if forced_final:
                    outcome = LoopOutcome.BUDGET_EXHAUSTED
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue working using the tools. When the task is complete "
                        "and tests pass, call `submit`.",
                    }
                )
                rec.message(messages[-1], step=step)
                continue

            submitted = self._dispatch_tool_calls(
                resp.tool_calls, messages, schemas, rec, step, reasks_used
            )
            reasks_used = submitted["reasks_used"]
            if submitted["submitted"]:
                outcome = LoopOutcome.SUBMITTED
                break

        elapsed = time.time() - start
        if outcome == LoopOutcome.ERROR and self.toolset.submitted:
            outcome = LoopOutcome.SUBMITTED
        if (
            step >= self.budgets.max_steps
            and not self.toolset.submitted
            and outcome
            in (
                LoopOutcome.ERROR,
                LoopOutcome.BUDGET_EXHAUSTED,
            )
        ):
            outcome = LoopOutcome.BUDGET_EXHAUSTED

        submission = self.toolset.submission if outcome == LoopOutcome.SUBMITTED else None
        final_text = (submission or {}).get("answer", "") if submission else ""

        rec.outcome(
            outcome.value,
            submission=submission,
            steps=step,
            budgets_used={
                "seconds": round(elapsed, 2),
                "tokens_estimate": tokens_estimate,
                "steps": step,
            },
        )
        self._emit("outcome", outcome=outcome.value, steps=step)

        return LoopResult(
            outcome=outcome,
            text=final_text,
            submission=submission,
            steps=step,
            messages=messages,
            telemetry=self.toolset.telemetry.snapshot(),
            trajectory=rec,
            elapsed_seconds=elapsed,
            tokens_estimate=tokens_estimate,
        )

    # -- helpers ------------------------------------------------------------
    def _assistant_message(self, resp: ChatResponse) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": resp.text or None}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments
                        if isinstance(tc.arguments, str)
                        else _to_json(tc.arguments),
                    },
                }
                for tc in resp.tool_calls
            ]
        return msg

    def _dispatch_tool_calls(
        self,
        tool_calls: list[ToolCall],
        messages: list[dict[str, Any]],
        schemas: dict[str, dict[str, Any]],
        rec: TrajectoryRecorder,
        step: int,
        reasks_used: int,
    ) -> dict[str, Any]:
        submitted = False
        for tc in tool_calls:
            name, raw_args = _normalize_tool_name(tc.name, tc.arguments)
            args, reason = validate_tool_call(name, raw_args, schemas)
            if args is None:
                self.toolset.telemetry.tool_calls_malformed += 1
                if reasks_used < self.reask_policy.max_reasks_per_run:
                    reasks_used += 1
                    self.toolset.telemetry.reasks += 1
                    tool_msg = reask_tool_message(tc.id, tc.name, reason)
                    messages.append(tool_msg)
                    rec.message(tool_msg, step=step, tool={"name": tc.name, "reask": True})
                    rec.annotation("reask", step=step, tool=tc.name, reason=reason)
                    self._emit("reask", tool=tc.name, reason=reason)
                    continue
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": f"[dropped invalid call: {reason}]",
                }
                messages.append(tool_msg)
                rec.message(tool_msg, step=step, tool={"name": tc.name, "dropped": True})
                continue

            self.toolset.telemetry.tool_calls_total += 1
            t0 = time.time()
            try:
                result = self.toolset.dispatch(name, args)
            except Exception as exc:  # noqa: BLE001 - tool errors are fed back, not fatal
                result = f"[tool error] {exc}"
            tool_msg = {"role": "tool", "tool_call_id": tc.id, "content": result}
            messages.append(tool_msg)
            rec.message(
                tool_msg,
                step=step,
                tool={"name": name, "wall_ms": int((time.time() - t0) * 1000)},
            )
            self._emit("tool", name=name, args=args)
            if name == "submit":
                submitted = True
                break
        return {"submitted": submitted, "reasks_used": reasks_used}

    def _handle_bash_only(
        self, text: str, messages: list[dict[str, Any]], rec: TrajectoryRecorder, step: int
    ) -> str:
        """Parse a fenced command block (mini-SWE-agent style) and run it."""
        if re.search(r"(?im)^\s*submit\s*$", text):
            # treat a bare 'submit' line as submission
            self.toolset.dispatch("submit", {"answer": text})
            return "submit"
        match = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", text, re.DOTALL)
        if not match:
            return "none"
        command = match.group(1).strip()
        result = self.toolset.dispatch("execute_bash", {"command": command})
        obs = {"role": "user", "content": f"Observation:\n{result}"}
        messages.append(obs)
        rec.message(obs, step=step, tool={"name": "execute_bash", "bash_only": True})
        return "action"


_EDITOR_SUBCOMMANDS = {"view", "create", "str_replace", "insert"}
_TOOL_NAME_ALIASES = {
    "bash": "execute_bash",
    "shell": "execute_bash",
    "run_bash": "execute_bash",
    "run_command": "execute_bash",
    "grep": "search",
    "find": "search",
    "edit": "str_replace_editor",
    "edit_file": "str_replace_editor",
    "view_file": "str_replace_editor",
    "open_file": "str_replace_editor",
    "finish": "submit",
    "done": "submit",
}


def _normalize_tool_name(name: str, raw_args: Any) -> tuple[str, Any]:
    """Map model tool-name priors onto our actual tools.

    gpt-oss has been observed calling the editor sub-command directly as a
    top-level tool (e.g. ``view``); other models use ``bash``/``edit``/``finish``.
    Rewrite the name (folding an editor sub-command into the ``command`` arg)
    rather than rejecting the call and burning a re-ask.
    """
    if name in _EDITOR_SUBCOMMANDS:
        import json as _json

        args = raw_args
        if isinstance(raw_args, str):
            try:
                args = _json.loads(raw_args) if raw_args.strip() else {}
            except (ValueError, TypeError):
                return name, raw_args  # let validation report the JSON error
        if isinstance(args, dict) and "command" not in args:
            args = {**args, "command": name}
        return "str_replace_editor", args
    if name in _TOOL_NAME_ALIASES:
        return _TOOL_NAME_ALIASES[name], raw_args
    return name, raw_args


def _to_json(value: Any) -> str:
    import json

    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return "{}"
