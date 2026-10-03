"""The verified run loop (LOCUS-337; 11 §2, P1-P5, P16, P17).

``intake -> envelope -> plan -> (act via gateway -> observe -> revise)* -> verify
-> done | blocked | stopped``

Differences from :class:`~locus_runtime.harness.loop.AgentLoop`:

* **Envelope.** Every run carries a :class:`RunEnvelope` (goal, done criteria,
  capabilities, budget, autonomy tier). The agent sees the goal and the
  criteria; tools outside the envelope are not offered.
* **Plan phase.** The first model call asks for a plan artifact through the
  ``update_plan`` tool: steps, plus how each done criterion will be verified.
  Plans are versioned and recorded in the trajectory; the agent revises them
  with ``update_plan`` at any time. ``plan_mode="required"`` (the default)
  refuses other actions until a plan exists; ``"optional"`` records a plan if
  one is given (used under :class:`SweAgent` so weak models and legacy scripted
  flows still work).
* **Verify gate.** ``submit`` runs every done criterion (see
  :mod:`locus_runtime.harness.verification`). A failing check rejects the
  submit and its findings come back as the tool observation; the loop goes on
  until verified, blocked or out of budget.
* **Exactly three end states** (P3): ``done`` (all criteria verified; evidence
  attached), ``blocked`` (specific blocker + what would unblock it) or
  ``stopped`` (budget, user or policy). There is no unverified "finished".
* **Budgets.** Tokens and cost accumulate from model usage (worker and judge),
  actions count executed agent tool calls; any limit at 100% stops the run, 80%
  emits a ``budget_warning`` event (11 §5). Before every action the current
  :class:`~locus_runtime.gateway.BudgetFigures` are reported to the gateway
  session, so ``budget_policy`` is evaluated on each action; a budget_policy
  deny stops the run (policy).
* **Checkpoint/resume.** After every step the loop state is written atomically
  to a checkpoint file; :meth:`VerifiedLoop.resume` rebuilds the loop in a new
  process and continues. Resuming a finished run returns its result without
  calling the model again (idempotent).

Checkpoint granularity is one step: a step interrupted mid-way is replayed from
its model call on resume, so tool actions are at-least-once per step. File
edits are exact-match (a replayed ``str_replace`` fails harmlessly).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from locus_runtime.gateway import BudgetFigures, GatewaySession
from locus_runtime.harness.enforcement import (
    ReaskPolicy,
    constraint_kwargs,
    reask_tool_message,
    schema_by_name,
    validate_tool_call,
)
from locus_runtime.harness.llm import ChatClient, ChatResponse, ToolCall
from locus_runtime.harness.loop import LoopOutcome, _normalize_tool_name, _to_json
from locus_runtime.harness.model_profiles import ModelCapabilityProfile
from locus_runtime.harness.run_envelope import RunEnvelope
from locus_runtime.harness.tools import CodingTelemetry, CodingToolset
from locus_runtime.harness.trajectory import TrajectoryRecorder
from locus_runtime.harness.verification import (
    AcceptanceJudge,
    CheckResult,
    VerificationReport,
    budget_policy_denied,
    verify,
)

CHECKPOINT_VERSION = 1
CHECKPOINT_KIND = "locus.run_checkpoint"
HARNESS_VERSION = "0.2.0"

PLAN_TOOL = "update_plan"
BLOCKER_TOOL = "report_blocker"
SUBMIT_TOOL = "submit"
_LOOP_TOOLS = frozenset({PLAN_TOOL, BLOCKER_TOOL, SUBMIT_TOOL})

PlanMode = Literal["required", "optional"]
BUDGET_WARNING_FRACTION = 0.8


class EndState(str, Enum):
    DONE = "done"
    BLOCKED = "blocked"
    STOPPED = "stopped"


@dataclass
class Blocker:
    """Why a run cannot continue, and what would unblock it (P3)."""

    kind: str  # gateway | missing_tool | agent | ambiguous_criteria | provider | judge | no_progress | configuration
    detail: str
    unblock: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class StopReason:
    kind: Literal["budget", "user", "policy"]
    detail: str
    dimension: str = ""


# --------------------------------------------------------------------------- #
# Budget accounting
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ModelPricing:
    """USD per 1k tokens; zero for local engines. Provider-reported ``cost_usd`` wins."""

    prompt_per_1k_usd: float = 0.0
    completion_per_1k_usd: float = 0.0

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.prompt_per_1k_usd + completion_tokens * self.completion_per_1k_usd
        ) / 1000.0


def _estimate_tokens(text: str) -> int:
    words = re.findall(r"\S+", str(text or ""))
    return int(round(len(words) / 0.75)) if words else 0


def _messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += _estimate_tokens(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            total += _estimate_tokens(str((tc.get("function") or {}).get("arguments") or ""))
    return total


@dataclass
class BudgetUsage:
    steps: int = 0
    actions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    elapsed_seconds: float = 0.0
    model_calls: int = 0
    judge_calls: int = 0
    verifier_runs: int = 0
    tokens_estimated: bool = False

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def figures(self, envelope: RunEnvelope) -> BudgetFigures:
        b = envelope.budget
        return BudgetFigures(
            tokens_used=float(self.tokens),
            max_tokens=float(b.max_tokens),
            duration_used_seconds=round(self.elapsed_seconds, 3),
            max_duration_seconds=float(b.max_seconds),
            cost_used_usd=round(self.cost_usd, 6),
            max_cost_usd=float(b.max_cost_usd),
        )

    def fractions(self, envelope: RunEnvelope) -> dict[str, float]:
        b = envelope.budget
        return {
            "steps": self.steps / b.max_steps,
            "seconds": self.elapsed_seconds / b.max_seconds,
            "tokens": self.tokens / b.max_tokens,
            "cost_usd": self.cost_usd / b.max_cost_usd,
            "actions": self.actions / b.max_actions,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tokens"] = self.tokens
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BudgetUsage:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


# --------------------------------------------------------------------------- #
# Loop-level tools
# --------------------------------------------------------------------------- #
def loop_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": PLAN_TOOL,
                "description": "Record or revise your plan: the steps you will take and, for "
                "each done criterion id, how you will verify it. Call this first, and again "
                "whenever the plan changes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {"type": "array", "items": {"type": "string"}},
                        "verification": {
                            "type": "array",
                            "description": "One entry per done criterion.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "criterion_id": {"type": "string"},
                                    "method": {"type": "string"},
                                },
                            },
                        },
                        "note": {"type": "string", "description": "Why the plan changed."},
                    },
                    "required": ["steps"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": BLOCKER_TOOL,
                "description": "Stop and report that the run cannot be completed: a done "
                "criterion is ambiguous or contradictory, a required tool or access is "
                "missing, or the gateway denied something essential. Say exactly what would "
                "unblock it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "ambiguous_criteria",
                                "missing_tool",
                                "missing_access",
                                "other",
                            ],
                        },
                        "blocker": {"type": "string"},
                        "unblock": {"type": "string"},
                    },
                    "required": ["blocker", "unblock"],
                },
            },
        },
    ]


def _plan_steps(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    steps: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            item = item.get("description") or item.get("step") or item.get("text") or ""
        text = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", str(item)).strip()
        if text:
            steps.append(text[:500])
    return steps[:50]


def _plan_verification(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        return {str(k): str(v)[:500] for k, v in raw.items()}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            cid = str(item.get("criterion_id") or item.get("criterion") or item.get("id") or "")
            method = str(item.get("method") or item.get("how") or item.get("check") or "")
            if cid:
                out[cid] = method[:500]
    return out


# --------------------------------------------------------------------------- #
# State, result, checkpoint
# --------------------------------------------------------------------------- #
@dataclass
class LoopState:
    run_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    plan_history: list[dict[str, Any]] = field(default_factory=list)
    usage: BudgetUsage = field(default_factory=BudgetUsage)
    verification: list[dict[str, Any]] = field(default_factory=list)
    failure_fingerprints: list[str] = field(default_factory=list)
    reasks_used: int = 0
    budget_warnings: list[str] = field(default_factory=list)
    started: bool = False
    final: dict[str, Any] | None = None  # serialized RunResult summary once ended

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "messages": self.messages,
            "plan": self.plan,
            "plan_history": self.plan_history,
            "usage": self.usage.to_dict(),
            "verification": self.verification,
            "failure_fingerprints": self.failure_fingerprints,
            "reasks_used": self.reasks_used,
            "budget_warnings": self.budget_warnings,
            "started": self.started,
            "final": self.final,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopState:
        return cls(
            run_id=str(data["run_id"]),
            messages=list(data.get("messages") or []),
            plan=data.get("plan"),
            plan_history=list(data.get("plan_history") or []),
            usage=BudgetUsage.from_dict(data.get("usage") or {}),
            verification=list(data.get("verification") or []),
            failure_fingerprints=list(data.get("failure_fingerprints") or []),
            reasks_used=int(data.get("reasks_used") or 0),
            budget_warnings=list(data.get("budget_warnings") or []),
            started=bool(data.get("started")),
            final=data.get("final"),
        )


@dataclass
class RunResult:
    run_id: str
    end_state: EndState
    envelope: RunEnvelope
    plan: dict[str, Any] | None
    plan_history: list[dict[str, Any]]
    verification: list[dict[str, Any]]
    usage: BudgetUsage
    evidence: dict[str, Any] | None = None  # set iff done
    blocker: Blocker | None = None  # set iff blocked
    stop: StopReason | None = None  # set iff stopped
    submission: dict[str, Any] | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)
    trajectory: TrajectoryRecorder | None = None
    checkpoint_path: str = ""

    @property
    def done(self) -> bool:
        return self.end_state == EndState.DONE

    @property
    def steps(self) -> int:
        return self.usage.steps

    @property
    def legacy_outcome(self) -> LoopOutcome:
        """The :class:`LoopOutcome` older callers (evals, TeamFlow) understand."""
        if self.end_state == EndState.DONE:
            return LoopOutcome.SUBMITTED
        if self.end_state == EndState.STOPPED and self.stop and self.stop.kind == "budget":
            return LoopOutcome.BUDGET_EXHAUSTED
        if self.end_state == EndState.BLOCKED and self.blocker and self.blocker.kind == "provider":
            return LoopOutcome.PROVIDER_UNAVAILABLE
        return LoopOutcome.ERROR

    def summary(self) -> dict[str, Any]:
        """JSON summary (stored in the checkpoint; what a runner posts back)."""
        return {
            "run_id": self.run_id,
            "end_state": self.end_state.value,
            "evidence": self.evidence,
            "blocker": asdict(self.blocker) if self.blocker else None,
            "stop": asdict(self.stop) if self.stop else None,
            "submission": self.submission,
            "usage": self.usage.to_dict(),
            "plan_version": (self.plan or {}).get("version", 0),
            "verification_attempts": len(self.verification),
            "telemetry": self.telemetry,
        }


def write_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    """Atomic write (temp file + replace) so a crash never leaves a torn checkpoint."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_checkpoint(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("kind") != CHECKPOINT_KIND:
        raise ValueError(f"{path} is not a Locus run checkpoint")
    if int(data.get("version") or 0) != CHECKPOINT_VERSION:
        raise ValueError(f"unsupported checkpoint version {data.get('version')}")
    return data


def gateway_session_of(toolset: CodingToolset) -> GatewaySession | None:
    executor = getattr(toolset.workspace, "executor", None)
    session = getattr(executor, "gateway_session", None)
    return session if isinstance(session, GatewaySession) else None


class _RunEnded(BaseException):
    """Internal control flow: the run reached an end state.

    A BaseException so broad ``except Exception`` handlers (tools, judge) never
    swallow an end state raised from inside them."""


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #
@dataclass
class VerifiedLoop:
    client: ChatClient
    toolset: CodingToolset
    profile: ModelCapabilityProfile
    envelope: RunEnvelope
    system_prompt: str = ""
    user_prompt: str = ""
    run_id: str = "local"
    judge_client: ChatClient | None = None  # default: ``client`` with a separate prompt
    pricing: ModelPricing = field(default_factory=ModelPricing)
    judge_pricing: ModelPricing | None = None
    plan_mode: PlanMode = "required"
    checkpoint_path: Path | None = None
    recorder: TrajectoryRecorder | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    #: Receives BudgetFigures before each action. Default: the executor's gateway session.
    budget_sink: Callable[[BudgetFigures], Any] | None = None
    should_stop: Callable[[], bool] | None = None
    reask_policy: ReaskPolicy = field(default_factory=ReaskPolicy)
    max_identical_failures: int = 3
    agent_id: str = "verified-loop"
    task_meta: dict[str, Any] = field(default_factory=dict)
    provider_max_retries: int = 3
    provider_retry_backoff: float = 1.5
    state: LoopState | None = None

    def __post_init__(self) -> None:
        if self.state is None:
            self.state = LoopState(run_id=self.run_id)
        self._stop_event = threading.Event()
        self._stop_detail = ""
        self._t0 = 0.0
        self._ended: RunResult | None = None
        self._pending_calls: list[str] = []
        if self.budget_sink is None:
            session = gateway_session_of(self.toolset)
            if session is not None:
                self.budget_sink = session.report_budget

    # -- control ---------------------------------------------------------------
    def request_stop(self, detail: str = "stopped by user") -> None:
        """Stop the run at the next step or action boundary (P5)."""
        self._stop_detail = detail
        self._stop_event.set()

    # -- resume -------------------------------------------------------------------
    @classmethod
    def resume(
        cls,
        checkpoint: str | Path,
        *,
        client: ChatClient,
        toolset: CodingToolset,
        profile: ModelCapabilityProfile,
        **kwargs: Any,
    ) -> VerifiedLoop:
        """Rebuild a loop from its checkpoint (e.g. after a process restart).

        The envelope, messages, plan, budgets used and verifier results come from
        the checkpoint; the caller supplies the live objects (model client, toolset
        bound to the same workspace). Call :meth:`run` to continue.
        """
        path = Path(checkpoint)
        data = read_checkpoint(path)
        state = LoopState.from_dict(data["state"])
        _restore_telemetry(toolset, data.get("toolset") or {})
        recorder = kwargs.pop("recorder", None)
        trajectory_path = data.get("trajectory_path")
        if recorder is None and trajectory_path:
            recorder = TrajectoryRecorder(
                run_id=state.run_id, file_path=Path(trajectory_path), append=True
            )
        params: dict[str, Any] = {
            "agent_id": str(data.get("agent_id") or "verified-loop"),
            "task_meta": dict(data.get("task_meta") or {}),
            "plan_mode": data.get("plan_mode") or "required",
            **kwargs,
        }
        return cls(
            client=client,
            toolset=toolset,
            profile=profile,
            envelope=RunEnvelope.from_dict(data["envelope"]),
            run_id=state.run_id,
            checkpoint_path=path,
            recorder=recorder,
            state=state,
            **params,
        )

    # -- main ---------------------------------------------------------------------
    def run(self) -> RunResult:
        st = self._st
        rec = self.recorder or TrajectoryRecorder(run_id=self.run_id)
        self.recorder = rec
        if st.final is not None:
            return self._result_from_final(st.final)

        self._t0 = time.time() - st.usage.elapsed_seconds
        tools = self._tool_schemas()
        schemas = schema_by_name(tools)
        if not st.started:
            rec.header(
                agent_id=self.agent_id,
                model=getattr(self.client, "model", "unknown"),
                provider=getattr(self.client, "provider", "unknown"),
                sampler={"temperature": self.profile.temperature, "top_p": self.profile.top_p},
                budgets=asdict(self.envelope.budget),
                system_prompt=self.system_prompt,
                task={**self.task_meta, "envelope": self.envelope.to_dict()},
                harness={
                    "version": HARNESS_VERSION,
                    "loop": "verified",
                    "protocol": self.profile.tool_protocol,
                    "plan_mode": self.plan_mode,
                },
            )
            st.messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self._initial_user_message()},
            ]
            rec.message(st.messages[0], step=0)
            rec.message(st.messages[1], step=0)
            st.started = True
            self._checkpoint()
        else:
            rec.annotation(
                "resumed", step=st.usage.steps, from_checkpoint=str(self.checkpoint_path)
            )
            self._emit("resumed", step=st.usage.steps)

        try:
            if self.envelope.acceptance_criteria and self._judge() is None:
                self._block(
                    Blocker(
                        kind="configuration",
                        detail="the envelope has free-text criteria but no acceptance judge",
                        unblock="configure a judge model for the run",
                    )
                )
            while True:
                self._guard_before_model_call()
                resp = self._call_model(st.messages, tools)
                st.usage.steps += 1
                step = st.usage.steps
                assistant = _assistant_message(resp)
                st.messages.append(assistant)
                rec.message(assistant, step=step, usage=resp.usage or None)
                self._emit(
                    "model_step", step=step, has_tools=bool(resp.tool_calls), text=resp.text[:200]
                )
                if resp.tool_calls:
                    self._dispatch(resp.tool_calls, schemas, step)
                else:
                    self._handle_text(resp.text, step)
                self._checkpoint()
        except _RunEnded:
            pass
        assert self._ended is not None
        return self._ended

    # -- model calls ---------------------------------------------------------------
    @property
    def _st(self) -> LoopState:
        assert self.state is not None
        return self.state

    def _call_model(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        extra = constraint_kwargs(getattr(self.client, "provider", ""), self.profile, tools)
        if self._plan_phase() and self.plan_mode == "required" and tools:
            extra = {**extra, "tool_choice": {"type": "function", "function": {"name": PLAN_TOOL}}}
        last_error = ""
        for attempt in range(self.provider_max_retries + 1):
            try:
                resp = self.client.complete(
                    messages,
                    tools=tools or None,
                    temperature=self.profile.temperature,
                    top_p=self.profile.top_p,
                    extra=extra or None,
                )
            except Exception as exc:  # noqa: BLE001 - transient provider failure
                last_error = str(exc)[:300]
                self._rec.annotation(
                    "provider_error", step=self._st.usage.steps, attempt=attempt, error=last_error
                )
                self._emit("provider_error", attempt=attempt, error=last_error)
                if attempt < self.provider_max_retries and self.provider_retry_backoff > 0:
                    time.sleep(self.provider_retry_backoff * (2**attempt))
                continue
            self._account(messages, resp, self.pricing)
            return resp
        # P16: an unavailable engine fails loudly; nothing is simulated.
        self._block(
            Blocker(
                kind="provider",
                detail=f"model provider unavailable after {self.provider_max_retries + 1} "
                f"attempts: {last_error}",
                unblock="restore the model endpoint or credentials, then resume the run",
            )
        )
        raise AssertionError("unreachable")

    def _judge(self) -> AcceptanceJudge | None:
        client = self.judge_client or self.client
        if client is None:
            return None
        pricing = self.judge_pricing or self.pricing

        def complete(messages: list[dict[str, Any]]) -> ChatResponse:
            self._guard_budget()
            resp = client.complete(messages, tools=None, temperature=0.0)
            self._account(messages, resp, pricing)
            self._st.usage.judge_calls += 1
            return resp

        return AcceptanceJudge(complete=complete)

    def _account(
        self, messages: list[dict[str, Any]], resp: ChatResponse, pricing: ModelPricing
    ) -> None:
        usage = self._st.usage
        usage.model_calls += 1
        reported = resp.usage or {}
        prompt = reported.get("prompt_tokens")
        completion = reported.get("completion_tokens")
        if prompt is None or completion is None:
            usage.tokens_estimated = True
            prompt = _messages_tokens(messages) if prompt is None else prompt
            completion = (
                _estimate_tokens(resp.text)
                + sum(_estimate_tokens(_to_json(tc.arguments)) for tc in resp.tool_calls)
                if completion is None
                else completion
            )
        usage.prompt_tokens += int(prompt)
        usage.completion_tokens += int(completion)
        cost = reported.get("cost_usd")
        usage.cost_usd += (
            float(cost) if cost is not None else pricing.cost(int(prompt), int(completion))
        )
        self._tick()

    # -- budget / stop guards ----------------------------------------------------------
    def _tick(self) -> None:
        self._st.usage.elapsed_seconds = time.time() - self._t0
        for dim, fraction in self._st.usage.fractions(self.envelope).items():
            if fraction >= BUDGET_WARNING_FRACTION and dim not in self._st.budget_warnings:
                self._st.budget_warnings.append(dim)
                self._rec.annotation(
                    "budget_warning",
                    step=self._st.usage.steps,
                    dimension=dim,
                    fraction=round(fraction, 3),
                )
                self._emit("budget_warning", dimension=dim, fraction=round(fraction, 3))

    def _guard_budget(self) -> None:
        self._tick()
        usage, b = self._st.usage, self.envelope.budget
        exhausted = [
            ("seconds", usage.elapsed_seconds >= b.max_seconds),
            ("tokens", usage.tokens >= b.max_tokens),
            ("cost_usd", usage.cost_usd >= b.max_cost_usd),
        ]
        for dim, hit in exhausted:
            if hit:
                self._stop(
                    StopReason(kind="budget", dimension=dim, detail=f"{dim} budget exhausted")
                )

    def _guard_user(self) -> None:
        if self._stop_event.is_set() or (self.should_stop is not None and self.should_stop()):
            self._stop(StopReason(kind="user", detail=self._stop_detail or "stopped by user"))

    def _guard_before_model_call(self) -> None:
        self._guard_user()
        self._guard_budget()
        b = self.envelope.budget
        if self._st.usage.steps >= b.max_steps:
            self._stop(StopReason(kind="budget", dimension="steps", detail="step budget exhausted"))
        if b.max_context_tokens and _messages_tokens(self._st.messages) >= b.max_context_tokens:
            self._stop(
                StopReason(
                    kind="budget", dimension="context_tokens", detail="context budget exhausted"
                )
            )

    def _guard_before_action(self) -> None:
        self._guard_user()
        self._guard_budget()
        if self._st.usage.actions >= self.envelope.budget.max_actions:
            self._stop(
                StopReason(kind="budget", dimension="actions", detail="action budget exhausted")
            )
        self._report_budget()

    def _report_budget(self) -> None:
        """Expose current BudgetFigures to the gateway before the action (budget_policy)."""
        if self.budget_sink is None:
            return
        figures = self._st.usage.figures(self.envelope)
        try:
            self.budget_sink(figures)
        except Exception as exc:  # noqa: BLE001 - unreportable budget: fail closed
            self._stop(
                StopReason(
                    kind="policy",
                    dimension="budget_report",
                    detail=f"could not report budget to the gateway: {exc}",
                )
            )

    # -- dispatch ----------------------------------------------------------------------
    def _plan_phase(self) -> bool:
        return self._st.plan is None

    def _dispatch(self, tool_calls: list[ToolCall], schemas: dict[str, Any], step: int) -> None:
        st = self._st
        self._pending_calls = [tc.id for tc in tool_calls]
        for tc in tool_calls:
            name, raw_args = _normalize_tool_name(tc.name, tc.arguments)
            args, reason = validate_tool_call(name, raw_args, schemas)
            if args is None:
                self.toolset.telemetry.tool_calls_malformed += 1
                if st.reasks_used < self.reask_policy.max_reasks_per_run:
                    st.reasks_used += 1
                    self.toolset.telemetry.reasks += 1
                    self._tool_reply(
                        reask_tool_message(tc.id, tc.name, reason), step, tc.name, reask=True
                    )
                    self._rec.annotation("reask", step=step, tool=tc.name, reason=reason)
                    continue
                self._tool_reply(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"[dropped invalid call: {reason}]",
                    },
                    step,
                    tc.name,
                    dropped=True,
                )
                continue
            self._dispatch_one(tc, name, args, step)
        self._pending_calls = []

    def _dispatch_one(self, tc: ToolCall, name: str, args: dict[str, Any], step: int) -> None:
        if name == PLAN_TOOL:
            self._tool_reply(
                self._record_plan(args, step, source="tool"), step, name, call_id=tc.id
            )
            return
        if name == BLOCKER_TOOL:
            self._tool_reply(
                "Blocker recorded; the run ends as blocked.", step, name, call_id=tc.id
            )
            self._block(
                Blocker(
                    kind=str(args.get("kind") or "agent")
                    if args.get("kind") != "other"
                    else "agent",
                    detail=str(args.get("blocker") or "")[:1000],
                    unblock=str(args.get("unblock") or "")[:1000],
                    evidence={"reported_by": "agent", "step": step},
                )
            )
        if self._plan_phase() and self.plan_mode == "required":
            self._tool_reply(
                f"[not executed] Record your plan first: call {PLAN_TOOL} with your steps and how "
                "each done criterion will be verified.",
                step,
                name,
                call_id=tc.id,
            )
            return
        if self._plan_phase() and not self._st.plan_history:
            self._rec.annotation("plan_skipped", step=step, first_tool=name)
            self._st.plan_history.append({"version": 0, "skipped": True, "step": step})
        if name == SUBMIT_TOOL:
            self._submit(tc.id, args, step)
            return
        self._guard_before_action()
        self._run_tool(tc.id, name, args, step)

    def _run_tool(self, call_id: str, name: str, args: dict[str, Any], step: int) -> None:
        self.toolset.telemetry.tool_calls_total += 1
        self._st.usage.actions += 1
        blocks_before = len(self.toolset.gateway_blocks)
        t0 = time.time()
        try:
            result = self.toolset.dispatch(name, args)
        except Exception as exc:  # noqa: BLE001 - tool errors are observations
            result = f"[tool error] {exc}"
        self._tool_reply(
            result, step, name, call_id=call_id, wall_ms=int((time.time() - t0) * 1000)
        )
        self._emit("tool", name=name, args=args)
        self._check_budget_policy(self.toolset.gateway_blocks[blocks_before:])

    def _check_budget_policy(self, blocks: list[dict[str, Any]]) -> None:
        for block in blocks:
            if budget_policy_denied([str(r) for r in block.get("reasons") or []]):
                self._stop(
                    StopReason(
                        kind="policy",
                        dimension="budget_policy",
                        detail=f"gateway budget_policy denied {block.get('action_kind')} "
                        f"(audit {block.get('audit_id') or 'n/a'})",
                    )
                )

    def _tool_reply(
        self,
        content: Any,
        step: int,
        tool: str,
        *,
        call_id: str = "",
        reask: bool = False,
        dropped: bool = False,
        wall_ms: int | None = None,
    ) -> None:
        msg = (
            content
            if isinstance(content, dict)
            else {"role": "tool", "tool_call_id": call_id, "content": str(content)}
        )
        self._st.messages.append(msg)
        meta: dict[str, Any] = {"name": tool}
        if reask:
            meta["reask"] = True
        if dropped:
            meta["dropped"] = True
        if wall_ms is not None:
            meta["wall_ms"] = wall_ms
        self._rec.message(msg, step=step, tool=meta)

    def _handle_text(self, text: str, step: int) -> None:
        if self.profile.tool_protocol == "bash-only":
            if re.search(r"(?im)^\s*submit\s*$", text):
                self._submit("", {"answer": text}, step, as_observation=True)
                return
            match = re.search(r"```(?:bash|sh)?\s*\n(.*?)```", text, re.DOTALL)
            if match:
                if self._plan_phase() and not self._st.plan_history:
                    self._record_plan(
                        {"steps": [], "note": "bash-only protocol"}, step, source="text"
                    )
                self._guard_before_action()
                self._st.usage.actions += 1
                blocks_before = len(self.toolset.gateway_blocks)
                result = self.toolset.dispatch("execute_bash", {"command": match.group(1).strip()})
                self._observe(f"Observation:\n{result}", step, tool="execute_bash")
                self._check_budget_policy(self.toolset.gateway_blocks[blocks_before:])
                return
        if self._plan_phase() and text.strip():
            plan_args = _plan_from_text(text)
            if plan_args["steps"]:
                note = self._record_plan(plan_args, step, source="text")
                self._observe(f"{note} Now carry out the plan using the tools.", step)
                return
        nudge = (
            f"Record your plan with {PLAN_TOOL} first."
            if self._plan_phase() and self.plan_mode == "required"
            else "Continue working using the tools. When every done criterion is met, call "
            "`submit`; it runs the verifiers. If you cannot proceed, call report_blocker."
        )
        self._observe(nudge, step)

    def _observe(self, content: str, step: int, tool: str = "") -> None:
        msg = {"role": "user", "content": content}
        self._st.messages.append(msg)
        self._rec.message(msg, step=step, tool={"name": tool, "bash_only": True} if tool else None)

    # -- plan --------------------------------------------------------------------------
    def _record_plan(self, args: dict[str, Any], step: int, *, source: str) -> str:
        st = self._st
        steps = _plan_steps(args.get("steps"))
        verification = _plan_verification(args.get("verification"))
        version = 1 + max((int(p.get("version") or 0) for p in st.plan_history), default=0)
        criterion_ids = [c.id for c in self.envelope.done_criteria]
        missing = [cid for cid in criterion_ids if cid not in verification]
        plan = {
            "version": version,
            "steps": steps,
            "verification": verification,
            "unplanned_criteria": missing,
            "note": str(args.get("note") or "")[:500],
            "source": source,
            "step": step,
        }
        st.plan = plan
        st.plan_history.append(plan)
        self._rec.annotation("plan", step=step, plan=plan)
        self._emit("plan", version=version, steps=len(steps))
        reply = f"Plan v{version} recorded ({len(steps)} steps)."
        if missing:
            reply += (
                " No verification method given for: "
                + ", ".join(missing)
                + ". Every done criterion is checked when you submit."
            )
        return reply

    # -- verify gate -------------------------------------------------------------------
    def _submit(
        self, call_id: str, args: dict[str, Any], step: int, *, as_observation: bool = False
    ) -> None:
        st = self._st
        self._guard_before_action()
        out = self.toolset.dispatch(SUBMIT_TOOL, args)
        if not self.toolset.submitted:
            # The toolset itself refused (e.g. empty diff despite applied edits).
            self._reply_or_observe(out, step, call_id, as_observation)
            return
        submission = dict(self.toolset.submission or {})
        attempt = len(st.verification) + 1
        blocks_before = len(self.toolset.gateway_blocks)
        self._report_budget()
        st.usage.verifier_runs += 1
        report = verify(
            self.envelope,
            executor=self.toolset.workspace.executor,
            judge=self._judge(),
            diff=str(submission.get("patch") or ""),
            answer=str(submission.get("answer") or ""),
            attempt=attempt,
        )
        st.verification.append(report.to_dict())
        self._rec.annotation(
            "verification",
            step=step,
            attempt=attempt,
            passed=report.passed,
            results=[r.to_dict() for r in report.results],
        )
        self._emit(
            "verification",
            attempt=attempt,
            passed=report.passed,
            failed=[r.id for r in report.results if not r.passed],
        )
        self._check_budget_policy(self.toolset.gateway_blocks[blocks_before:])
        self._check_budget_policy([r.evidence.get("gateway") or {} for r in report.blocked])
        if report.passed:
            self._reply_or_observe(
                f"Verified: all {len(report.results)} done criteria passed. The run is done.",
                step,
                call_id,
                as_observation,
            )
            self._finish(EndState.DONE, evidence=_evidence(report, st), submission=submission)
        self.toolset.submitted = False
        self.toolset.submission = None
        if report.blocked:
            first = report.blocked[0]
            self._reply_or_observe(report.feedback(), step, call_id, as_observation)
            self._block(
                Blocker(
                    kind=_blocker_kind(first),
                    detail=first.blocker or first.detail,
                    unblock=first.unblock or "resolve the blocked done criterion",
                    evidence={"check": first.to_dict(), "attempt": attempt},
                )
            )
        fingerprint = report.failure_fingerprint()
        st.failure_fingerprints.append(fingerprint)
        repeats = sum(1 for f in st.failure_fingerprints if f == fingerprint)
        self._reply_or_observe(report.feedback(), step, call_id, as_observation)
        if repeats >= self.max_identical_failures:
            self._block(
                Blocker(
                    kind="no_progress",
                    detail=f"verification failed identically {repeats} times: "
                    + "; ".join(f"{r.id}: {r.detail}" for r in report.results if not r.passed),
                    unblock="a human should review the failing criteria (they may be wrong or "
                    "need access the run does not have) and edit the envelope or plan",
                    evidence={"attempt": attempt, "fingerprint": fingerprint},
                )
            )

    def _reply_or_observe(
        self, content: str, step: int, call_id: str, as_observation: bool
    ) -> None:
        if as_observation:
            self._observe(content, step)
        else:
            self._tool_reply(content, step, SUBMIT_TOOL, call_id=call_id)

    # -- end states --------------------------------------------------------------------
    def _block(self, blocker: Blocker) -> None:
        self._finish(EndState.BLOCKED, blocker=blocker)

    def _stop(self, reason: StopReason) -> None:
        self._finish(EndState.STOPPED, stop=reason)

    def _finish(
        self,
        state: EndState,
        *,
        evidence: dict[str, Any] | None = None,
        blocker: Blocker | None = None,
        stop: StopReason | None = None,
        submission: dict[str, Any] | None = None,
    ) -> None:
        st = self._st
        st.usage.elapsed_seconds = time.time() - self._t0 if self._t0 else st.usage.elapsed_seconds
        self._close_pending_calls()
        result = RunResult(
            run_id=self.run_id,
            end_state=state,
            envelope=self.envelope,
            plan=st.plan,
            plan_history=list(st.plan_history),
            verification=list(st.verification),
            usage=st.usage,
            evidence=evidence,
            blocker=blocker,
            stop=stop,
            submission=submission,
            messages=st.messages,
            telemetry=self.toolset.telemetry.snapshot(),
            trajectory=self._rec,
            checkpoint_path=str(self.checkpoint_path or ""),
        )
        st.final = result.summary()
        self._rec.annotation(
            "end_state",
            step=st.usage.steps,
            end_state=state.value,
            blocker=asdict(blocker) if blocker else None,
            stop=asdict(stop) if stop else None,
        )
        self._rec.outcome(
            state.value,
            submission=submission,
            steps=st.usage.steps,
            budgets_used=st.usage.to_dict(),
        )
        self._emit("end", end_state=state.value, steps=st.usage.steps)
        self._checkpoint()
        self._ended = result
        raise _RunEnded

    def _close_pending_calls(self) -> None:
        """Every tool call of the last assistant message gets a reply (valid transcript)."""
        replied = {m.get("tool_call_id") for m in self._st.messages if m.get("role") == "tool"}
        for call_id in self._pending_calls:
            if call_id not in replied:
                msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "[not executed: run ended]",
                }
                self._st.messages.append(msg)
                self._rec.message(
                    msg, step=self._st.usage.steps, tool={"name": "", "skipped": True}
                )
        self._pending_calls = []

    def _result_from_final(self, final: dict[str, Any]) -> RunResult:
        st = self._st
        blocker = final.get("blocker")
        stop = final.get("stop")
        return RunResult(
            run_id=self.run_id,
            end_state=EndState(final["end_state"]),
            envelope=self.envelope,
            plan=st.plan,
            plan_history=list(st.plan_history),
            verification=list(st.verification),
            usage=st.usage,
            evidence=final.get("evidence"),
            blocker=Blocker(**blocker) if blocker else None,
            stop=StopReason(**stop) if stop else None,
            submission=final.get("submission"),
            messages=st.messages,
            telemetry=dict(final.get("telemetry") or {}),
            trajectory=self._rec,
            checkpoint_path=str(self.checkpoint_path or ""),
        )

    # -- checkpoint --------------------------------------------------------------------
    def _checkpoint(self) -> None:
        if self.checkpoint_path is None:
            return
        if self._t0:
            self._st.usage.elapsed_seconds = time.time() - self._t0
        rec_path = self._rec.file_path
        write_checkpoint(
            Path(self.checkpoint_path),
            {
                "kind": CHECKPOINT_KIND,
                "version": CHECKPOINT_VERSION,
                "harness_version": HARNESS_VERSION,
                "saved_at": time.time(),
                "agent_id": self.agent_id,
                "task_meta": self.task_meta,
                "plan_mode": self.plan_mode,
                "status": (self._st.final or {}).get("end_state", "running"),
                "envelope": self.envelope.to_dict(),
                "state": self._st.to_dict(),
                "toolset": {
                    "telemetry": asdict(self.toolset.telemetry),
                    "edit_format": self.toolset.edit_format,
                },
                "trajectory_path": str(rec_path) if rec_path is not None else "",
            },
        )

    # -- helpers -----------------------------------------------------------------------
    @property
    def _rec(self) -> TrajectoryRecorder:
        if self.recorder is None:
            self.recorder = TrajectoryRecorder(run_id=self.run_id)
        return self.recorder

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def _tool_schemas(self) -> list[dict[str, Any]]:
        if self.profile.tool_protocol == "bash-only":
            return []
        allowed = set(self.envelope.capabilities.tools) | {SUBMIT_TOOL}
        base = [t for t in self.toolset.schemas() if t["function"]["name"] in allowed]
        return [*base, *loop_tool_schemas()]

    def _initial_user_message(self) -> str:
        env = self.envelope
        b = env.budget
        plan_line = (
            f"Before acting, call `{PLAN_TOOL}` with your steps and, for each done criterion "
            "id, how you will verify it. Revise it with the same tool when the plan changes."
            if self.profile.tool_protocol != "bash-only"
            else "Start by stating your plan (numbered steps) in plain text."
        )
        return (
            f"{self.user_prompt.strip()}\n\n"
            "<envelope>\n"
            f"Goal: {env.goal}\n"
            f"Done criteria (all are verified when you submit):\n{env.describe_criteria()}\n"
            f"Budget: {b.max_steps} steps, {b.max_actions} actions, {int(b.max_seconds)}s, "
            f"{b.max_tokens} tokens, ${b.max_cost_usd:.2f}. Autonomy tier: {env.autonomy_tier}.\n"
            "</envelope>\n\n"
            f"{plan_line} When every criterion is met, call `submit`: it runs the verifiers and "
            "a failing check rejects the submit with its findings. If a criterion is ambiguous "
            "or you cannot proceed (missing tool or access, denied action), call "
            f"`{BLOCKER_TOOL}` with the blocker and what would unblock it."
        )


# --------------------------------------------------------------------------- #
# Module helpers
# --------------------------------------------------------------------------- #
def _assistant_message(resp: ChatResponse) -> dict[str, Any]:
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


def _plan_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and parsed.get("steps"):
            return {
                "steps": parsed.get("steps"),
                "verification": parsed.get("verification"),
                "note": parsed.get("note") or "",
            }
    steps = [line for line in raw.splitlines() if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)]
    return {"steps": steps, "verification": None, "note": ""}


def _blocker_kind(check: CheckResult) -> str:
    if check.evidence.get("gateway"):
        return "gateway"
    if check.kind == "acceptance":
        return "judge" if "judge" in check.detail else "configuration"
    if "exit 127" in check.detail or "missing" in check.blocker:
        return "missing_tool"
    return "verification"


def _evidence(report: VerificationReport, st: LoopState) -> dict[str, Any]:
    """Evidence attached to a ``done`` run (P3, P17)."""
    commands, files, judge = [], [], []
    for r in report.results:
        if r.kind == "command":
            commands.append(
                {
                    "id": r.id,
                    "command": r.evidence.get("command"),
                    "exit_code": r.evidence.get("exit_code"),
                    "expected_exit_code": r.evidence.get("expected_exit_code"),
                    "duration_seconds": r.evidence.get("duration_seconds"),
                    "output_tail": r.evidence.get("output_tail"),
                }
            )
        elif r.kind == "file":
            files.append({"id": r.id, "label": r.label, "status": r.status})
        else:
            judge.append({"id": r.id, "criterion": r.label, "pass": r.passed, "reason": r.detail})
    return {
        "verified_at": report.created_at,
        "attempt": report.attempt,
        "commands": commands,
        "files": files,
        "judge": judge,
        "diff": report.diff,
        "answer": report.answer,
        "plan_version": (st.plan or {}).get("version", 0),
        "verification_attempts": len(st.verification),
    }


def _restore_telemetry(toolset: CodingToolset, data: dict[str, Any]) -> None:
    telemetry = data.get("telemetry") or {}
    names = {f.name for f in fields(CodingTelemetry)}
    for key, value in telemetry.items():
        if key in names:
            setattr(toolset.telemetry, key, value)
    if data.get("edit_format"):
        toolset.edit_format = str(data["edit_format"])
