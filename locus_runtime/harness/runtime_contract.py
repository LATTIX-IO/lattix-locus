"""The agent runtime port (LOCUS-348, D-27; port shape per D-28).

An *agent runtime* drives one run of the agent loop inside a run envelope:
it decides what to ask the model, which tool to call next and when to stop.
Everything else is fixed by the platform and identical across runtimes:

* the **envelope** (goal, done criteria, capabilities, budget, tier; P2),
* the **toolset** (``CodingToolset`` over the run's gated executor; P6),
* the **model client** (a gated ``ChatClient``; every turn a gateway
  ``model_call``),
* the **verify gate** (``verification.verify`` + the acceptance judge), and
* the **three end states** (done / blocked / stopped; P3).

A runtime implementation receives a :class:`RuntimeRequest` and returns a
:class:`RuntimeResult`. Callers select an implementation through
:func:`locus_runtime.harness.runtimes.create_runtime`; they never construct
one directly. ``PORT_VERSION`` changes when the request/result shape changes
incompatibly.

Contract obligations every implementation must meet (enforced by
``tests/harness/test_runtime_contract.py``):

1. Every model call goes through ``request.client`` and every side effect
   through ``request.toolset`` (so through the run's gateway session). A
   runtime offers the model no tool outside the envelope's tools plus the
   loop-level control tools (:data:`CONTROL_TOOLS`).
2. ``done`` only when every done criterion passed the verify gate.
3. Budgets are hard stops (``stopped`` with kind ``budget``).
4. A gateway ``ask`` is never silently approved: without an approver the
   action does not run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, PlainValidator

from locus_runtime.harness.model_profiles import ModelCapabilityProfile
from locus_runtime.harness.run_envelope import RunEnvelope
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.trajectory import TrajectoryRecorder
from locus_runtime.harness.verified_loop import ModelPricing, RunResult

PORT_VERSION = "1.0"

#: Loop-level tools with no side effect of their own (planning, blocker,
#: submit -> verify gate). Runtimes may offer these besides the envelope tools.
CONTROL_TOOLS: frozenset[str] = frozenset(
    {"update_plan", "report_blocker", "submit", "write_todos", "task"}
)

EndStateName = Literal["done", "blocked", "stopped"]

_T = TypeVar("_T")


def _is(cls: type[_T]) -> tuple[PlainValidator, PlainSerializer]:
    """Live object field: an isinstance check, never copied or introspected."""

    def check(value: Any) -> Any:
        if not isinstance(value, cls):
            raise ValueError(f"expected {cls.__name__}, got {type(value).__name__}")
        return value

    return PlainValidator(check), PlainSerializer(lambda v: repr(v), return_type=str)


Envelope = Annotated[RunEnvelope, *_is(RunEnvelope)]
Toolset = Annotated[CodingToolset, *_is(CodingToolset)]
Profile = Annotated[ModelCapabilityProfile, *_is(ModelCapabilityProfile)]
Pricing = Annotated[ModelPricing, *_is(ModelPricing)]
Recorder = Annotated[TrajectoryRecorder, *_is(TrajectoryRecorder)]
Run = Annotated[RunResult, *_is(RunResult)]


class ApprovalRequest(BaseModel):
    """A decision point a runtime surfaced instead of acting (gateway ``ask``, P4)."""

    model_config = ConfigDict(frozen=True)

    source: Literal["gateway", "workspace_bounds"]
    tool: str
    action_kind: str = ""
    target: str = ""
    audit_id: str = ""
    fingerprint: str = ""
    risk: str = ""
    reasons: tuple[str, ...] = ()


#: Returns True to approve the exact action (single use), False to refuse.
Approver = Callable[[ApprovalRequest], bool]


class RuntimeRequest(BaseModel):
    """Everything one run needs. Live objects are passed by reference, never copied."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    envelope: Envelope
    toolset: Toolset
    #: A ``ChatClient`` (protocol): production passes ``GatedChatClient``.
    client: Any
    profile: Profile
    system_prompt: str = ""
    user_prompt: str = ""
    run_id: str = "local"
    judge_client: Any = None
    pricing: Pricing = Field(default_factory=ModelPricing)
    recorder: Recorder | None = None
    checkpoint_path: Path | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    should_stop: Callable[[], bool] | None = None
    #: Interactive approvals. ``None`` = non-interactive: an ask ends the run blocked
    #: (Deep Agents) or comes back to the agent as a refusal (verified loop).
    approver: Approver | None = None
    agent_id: str = "agent-runtime"
    task_meta: dict[str, Any] = Field(default_factory=dict)
    #: Implementation-specific knobs (e.g. ``plan_mode`` for the verified loop).
    options: dict[str, Any] = Field(default_factory=dict)


class RuntimeResult(BaseModel):
    """The outcome of one run, in the shape every runtime reports."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    port_version: str = PORT_VERSION
    runtime: str
    run_id: str
    end_state: EndStateName
    verified: bool
    blocker: dict[str, Any] | None = None
    stop: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    telemetry: dict[str, Any] = Field(default_factory=dict)
    plan_version: int = 0
    verification_attempts: int = 0
    #: Names of every tool the runtime offered the model during the run.
    offered_tools: list[str] = Field(default_factory=list)
    #: Approval requests raised (each one interrupted the run).
    interrupts: list[ApprovalRequest] = Field(default_factory=list)
    #: The full run record (messages, trajectory); not serialized.
    run: Run | None = Field(default=None, exclude=True)

    @property
    def done(self) -> bool:
        return self.end_state == "done"

    @classmethod
    def from_run(
        cls,
        runtime: str,
        run: RunResult,
        *,
        offered_tools: list[str] | None = None,
        interrupts: list[ApprovalRequest] | None = None,
    ) -> RuntimeResult:
        summary = run.summary()
        verified = bool(run.done and run.verification and run.verification[-1].get("passed"))
        return cls(
            runtime=runtime,
            run_id=run.run_id,
            end_state=run.end_state.value,
            verified=verified,
            blocker=summary["blocker"],
            stop=summary["stop"],
            evidence=run.evidence,
            usage=run.usage.to_dict(),
            telemetry=dict(run.telemetry),
            plan_version=int(summary["plan_version"] or 0),
            verification_attempts=int(summary["verification_attempts"]),
            offered_tools=sorted(set(offered_tools or [])),
            interrupts=list(interrupts or []),
            run=run,
        )


class RuntimeUnavailable(RuntimeError):
    """The selected runtime cannot run here (e.g. its optional dependency is missing)."""


@runtime_checkable
class AgentRuntime(Protocol):
    """The port: one agent loop implementation."""

    name: str
    port_version: str

    def run(self, request: RuntimeRequest) -> RuntimeResult: ...
