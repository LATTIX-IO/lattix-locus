"""Shared run semantics for agent runtimes that drive their own loop (LOCUS-348).

:class:`RunController` is the verified loop with the *driving* taken out: an
external agent loop (Deep Agents / LangGraph) asks it for each model turn and
each tool call, and it applies exactly the code the verified loop applies --
budget guards and accounting, ``budget_policy`` reporting, tool-call
validation and re-asks, plan recording, the ``submit`` verify gate (same
``verify`` + acceptance judge) and the three end states. That is what makes
"done" mean the same thing under every runtime.

An end state is raised as the verified loop's private ``_RunEnded``
(a ``BaseException``), so it unwinds through any framework code between the
controller and :meth:`RunController.drive` without being swallowed by an
``except Exception``.

With a :class:`~locus_runtime.harness.run_store.RunStore` the controller is
durable (LOCUS-361): its state is saved after every model turn and tool call,
every tool call is written to an action ledger before it runs, and a new
controller for the same run id resumes from the store. A call the ledger shows
as finished is replayed, never re-run; a call that was running when the process
died is reported to the agent and never re-run (at most once).

This module also holds the ask handling shared by every runtime adapter:
gateway ``ask`` decisions and out-of-workspace escalations become
:class:`~locus_runtime.harness.runtime_contract.ApprovalRequest` objects; with
no approver they end the run ``blocked`` (P3/P4: never silently approved).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from locus_runtime import telemetry
from locus_runtime.harness.llm import ChatResponse, ToolCall
from locus_runtime.harness.run_store import RunStore
from locus_runtime.harness.runtime_contract import ApprovalRequest, Approver
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.verified_loop import (
    CHECKPOINT_KIND,
    CHECKPOINT_VERSION,
    HARNESS_VERSION,
    PLAN_TOOL,
    Blocker,
    LoopState,
    RunResult,
    VerifiedLoop,
    _assistant_message,
    _restore_telemetry,
    _RunEnded,
    gateway_session_of,
)
from locus_runtime.harness.enforcement import schema_by_name

#: What the agent is told about a call that was running when the process died.
NOT_REEXECUTED = (
    "[not re-executed] The run was interrupted while this action was running, so it may "
    "or may not have taken effect. Check the workspace state before repeating it."
)


# --------------------------------------------------------------------------- #
# Asks (shared by every adapter)
# --------------------------------------------------------------------------- #
@dataclass
class AskCursor:
    """Remembers how many gateway blocks / escalations a toolset had."""

    blocks: int = 0
    escalations: int = 0

    @classmethod
    def of(cls, toolset: CodingToolset) -> AskCursor:
        return cls(len(toolset.gateway_blocks), len(toolset.escalations))

    def take(self, toolset: CodingToolset) -> list[ApprovalRequest]:
        """Approval requests raised since the cursor; advances it."""
        asks: list[ApprovalRequest] = []
        for block in toolset.gateway_blocks[self.blocks :]:
            if block.get("outcome") == "ask":
                asks.append(
                    ApprovalRequest(
                        source="gateway",
                        tool=str(block.get("tool") or ""),
                        action_kind=str(block.get("action_kind") or ""),
                        target=str(block.get("target") or ""),
                        audit_id=str(block.get("audit_id") or ""),
                        fingerprint=str(block.get("fingerprint") or ""),
                        risk=str(block.get("risk") or ""),
                        reasons=tuple(str(r) for r in block.get("reasons") or []),
                    )
                )
        for esc in toolset.escalations[self.escalations :]:
            if str(esc.get("policy") or "ask") == "ask":
                asks.append(
                    ApprovalRequest(
                        source="workspace_bounds",
                        tool="str_replace_editor",
                        action_kind="file_access",
                        target=str(esc.get("path") or ""),
                        reasons=("workspace.out_of_bounds",),
                    )
                )
        self.blocks = len(toolset.gateway_blocks)
        self.escalations = len(toolset.escalations)
        return asks


def blocker_for_asks(asks: list[ApprovalRequest]) -> Blocker:
    first = asks[0]
    if first.source == "gateway":
        detail = (
            f"gateway requires approval for {first.action_kind} by {first.tool} "
            f"({first.risk}; {', '.join(first.reasons) or 'no reason given'})"
        )
        unblock = f"approve gateway request {first.audit_id or 'n/a'} for this run, or grant it"
    else:
        detail = f"{first.tool} asked to access {first.target}, outside the run workspace"
        unblock = "grant the path to the run workspace (envelope capabilities), then resume"
    return Blocker(
        kind="gateway",
        detail=detail[:1000],
        unblock=unblock,
        evidence={"asks": [a.model_dump() for a in asks], "non_interactive": True},
    )


def approve(toolset: CodingToolset, ask: ApprovalRequest, approver: Approver | None) -> bool:
    """Ask the approver; record an approval in the gateway's single-use ledger.

    Only gateway asks with a fingerprint can be approved this way; a workspace
    escalation needs a capability change, so it is never approved here.
    """
    if approver is None or ask.source != "gateway" or not ask.fingerprint:
        return False
    session = gateway_session_of(toolset)
    if session is None:
        return False
    if not approver(ask):
        return False
    session.gateway.approvals.approve(session.caller.run_id, ask.fingerprint, "runtime-approver")
    return True


# --------------------------------------------------------------------------- #
# The controller
# --------------------------------------------------------------------------- #
@dataclass
class RunController(VerifiedLoop):
    """Verified-loop semantics, driven step by step by an external agent loop."""

    plan_tool: str = PLAN_TOOL
    approver: Approver | None = None
    runtime_name: str = "external"
    offered_tools: set[str] = field(default_factory=set)
    interrupts: list[ApprovalRequest] = field(default_factory=list)
    refused_tools: list[str] = field(default_factory=list)
    #: Durable state + action ledger (``None``: in-memory only).
    store: RunStore | None = None
    #: Give every model tool call a run-unique id (providers may reuse ids such
    #: as ``call_0``; the action ledger is keyed by id).
    unique_call_ids: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self._lock = threading.RLock()
        self._asks = AskCursor.of(self.toolset)
        self._pending_asks: list[ApprovalRequest] = []
        self._trace_context: telemetry.RunContext | None = None
        self._restored_envelope: dict[str, Any] | None = None
        self._compactions = {"context_compactions": 0, "compacted_outputs": 0, "chars_saved": 0}
        #: Asks raised by each tool call id (parallel calls interrupt separately).
        self._call_asks: dict[str, list[ApprovalRequest]] = {}

    # -- durability ----------------------------------------------------------------
    def restore(self) -> bool:
        """Load this run's saved state from the store; True when there was one."""
        if self.store is None:
            return False
        data = self.store.load_state(self.run_id)
        if data is None:
            return False
        if data.get("kind") != CHECKPOINT_KIND or int(data.get("version") or 0) != (
            CHECKPOINT_VERSION
        ):
            raise ValueError(f"run {self.run_id!r}: not a Locus run checkpoint")
        self.state = LoopState.from_dict(data["state"])
        _restore_telemetry(self.toolset, data.get("toolset") or {})
        self.offered_tools = {str(t) for t in data.get("offered_tools") or []}
        self.refused_tools = [str(t) for t in data.get("refused_tools") or []]
        self.interrupts = [ApprovalRequest(**a) for a in data.get("interrupts") or []]
        self._restored_envelope = dict(data.get("envelope") or {})
        return True

    def _checkpoint(self) -> None:
        """Persist the controller state (replaces the verified loop's JSON file)."""
        if self.store is None:
            return
        st = self._st
        if self._t0:
            st.usage.elapsed_seconds = time.time() - self._t0
        status = str((st.final or {}).get("end_state") or "running")
        self.store.save_state(
            self.run_id,
            self.runtime_name,
            status,
            {
                "kind": CHECKPOINT_KIND,
                "version": CHECKPOINT_VERSION,
                "harness_version": HARNESS_VERSION,
                "saved_at": time.time(),
                "agent_id": self.agent_id,
                "task_meta": self.task_meta,
                "plan_mode": self.plan_mode,
                "runtime": self.runtime_name,
                "status": status,
                "envelope": self.envelope.to_dict(),
                "state": st.to_dict(),
                "toolset": {
                    "telemetry": asdict(self.toolset.telemetry),
                    "edit_format": self.toolset.edit_format,
                },
                "offered_tools": sorted(self.offered_tools),
                "refused_tools": list(self.refused_tools),
                "interrupts": [a.model_dump(mode="json") for a in self.interrupts],
            },
        )

    # -- lifecycle ---------------------------------------------------------------
    def drive(self, body: Callable[[RunController], None]) -> RunResult:
        """Start the run, let ``body`` drive it, return the end state.

        ``body`` returns only by raising an end state (through the controller);
        returning normally is a runtime defect and ends the run blocked."""
        st = self._st
        if st.final is not None:
            return self._result_from_final(st.final)
        with telemetry.agent_run(
            run_id=self.run_id,
            agent=self.agent_id,
            runtime=self.runtime_name,
            provider=str(getattr(self.client, "provider", "") or ""),
            model=str(getattr(self.client, "model", "") or ""),
        ) as span:
            # Framework threads re-enter the run's trace (model_turn / tool_call).
            self._trace_context = telemetry.capture_context()
            result = self._drive_run(body)
            telemetry.record_run_result(span, result)
            return result

    def _drive_run(self, body: Callable[[RunController], None]) -> RunResult:
        try:
            self._begin()
            body(self)
            self._block(
                Blocker(
                    kind="agent",
                    detail=f"the {self.runtime_name} runtime returned without an end state",
                    unblock="report a runtime defect; rerun the task",
                )
            )
        except _RunEnded:
            pass
        except Exception as exc:  # noqa: BLE001 - a framework crash still ends honestly (P3)
            try:
                self._block(
                    Blocker(
                        kind="runtime_error",
                        detail=f"the {self.runtime_name} runtime failed: "
                        f"{type(exc).__name__}: {exc}"[:1000],
                        unblock="inspect the run trajectory and the runtime error; rerun",
                    )
                )
            except _RunEnded:
                pass
        assert self._ended is not None
        return self._ended

    def _begin(self) -> None:
        st = self._st
        rec = self._rec
        self._t0 = time.time() - st.usage.elapsed_seconds
        if st.started:
            self._resumed()
            return
        system_prompt = self._system_prompt_with_skills()
        rec.header(
            agent_id=self.agent_id,
            model=getattr(self.client, "model", "unknown"),
            provider=getattr(self.client, "provider", "unknown"),
            sampler={"temperature": self.profile.temperature, "top_p": self.profile.top_p},
            budgets=asdict(self.envelope.budget),
            system_prompt=system_prompt,
            task={**self.task_meta, "envelope": self.envelope.to_dict()},
            harness={
                "version": HARNESS_VERSION,
                "loop": self.runtime_name,
                "protocol": self.profile.tool_protocol,
                "plan_mode": self.plan_mode,
            },
        )
        st.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self.initial_message()},
        ]
        rec.message(st.messages[0], step=0)
        rec.message(st.messages[1], step=0)
        st.started = True
        if self.envelope.acceptance_criteria and self._judge() is None:
            self._block(
                Blocker(
                    kind="configuration",
                    detail="the envelope has free-text criteria but no acceptance judge",
                    unblock="configure a judge model for the run",
                )
            )
        self._checkpoint()

    def _resumed(self) -> None:
        self._rec.annotation("resumed", step=self._st.usage.steps, runtime=self.runtime_name)
        self._emit("resumed", step=self._st.usage.steps)
        if (
            self._restored_envelope is not None
            and self._restored_envelope != self.envelope.to_dict()
        ):
            self._block(
                Blocker(
                    kind="configuration",
                    detail="the run was resumed with a different envelope than it started with",
                    unblock="resume with the original envelope, or start a new run",
                )
            )

    @property
    def resumed(self) -> bool:
        """Whether this controller continues a run that had already started."""
        return self._st.started

    def system_prompt_text(self) -> str:
        return self._system_prompt_with_skills()

    def initial_message(self) -> str:
        """The verified loop's envelope message, naming this runtime's plan tool."""
        text = self._initial_user_message()
        if self.plan_tool != PLAN_TOOL:
            text = text.replace(f"`{PLAN_TOOL}`", f"`{self.plan_tool}`")
        return text

    # -- model turns -------------------------------------------------------------
    def model_turn(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatResponse:
        """One gated model call: guards, retries, accounting, transcript."""
        with self._lock, telemetry.resume_context(self._trace_context):
            st = self._st
            st.messages = [dict(m) for m in messages]
            for tool in tools:
                name = str((tool.get("function") or {}).get("name") or "")
                if name:
                    self.offered_tools.add(name)
            self._guard_before_model_call()
            resp = self._call_model(st.messages, tools)
            st.usage.steps += 1
            if self.unique_call_ids:
                for index, call in enumerate(resp.tool_calls):
                    call.id = f"call_s{st.usage.steps}_{index}_{uuid.uuid4().hex[:8]}"
            assistant = _assistant_message(resp)
            st.messages.append(assistant)
            self._rec.message(assistant, step=st.usage.steps, usage=resp.usage or None)
            self._emit(
                "model_step",
                step=st.usage.steps,
                has_tools=bool(resp.tool_calls),
                text=resp.text[:200],
            )
            self._checkpoint()
            return resp

    def guard_model_call(self) -> None:
        """The verified loop's pre-model-call guards (user stop, steps, tokens,
        time, cost, context): raises the end state when one is exhausted."""
        with self._lock:
            self._guard_before_model_call()

    def handle_text(self, text: str) -> str:
        """A turn that ended without a tool call: the verified loop's nudge."""
        with self._lock:
            before = len(self._st.messages)
            self._handle_text(text, self._st.usage.steps)
            added = self._st.messages[before:]
            return str(added[-1].get("content") or "") if added else ""

    # -- tool calls ----------------------------------------------------------------
    def tool_call(self, call_id: str, name: str, arguments: Any) -> str:
        """Validate and run one Locus tool exactly as the verified loop does.

        With a store, the call goes through the action ledger: a finished call is
        replayed and a call interrupted by a crash is never run again."""
        with self._lock, telemetry.resume_context(self._trace_context):
            store = self.store
            entry = store.action(self.run_id, call_id) if store is not None else None
            if entry is not None and entry.status in ("done", "interrupted"):
                self._call_asks[call_id] = []
                self._rec.annotation(
                    "action_replayed", step=self._st.usage.steps, tool=name, call_id=call_id
                )
                return entry.content
            if store is not None and entry is not None and entry.status == "started":
                self._call_asks[call_id] = []
                store.mark_action(self.run_id, call_id, name, "interrupted", NOT_REEXECUTED)
                self._rec.annotation(
                    "action_not_reexecuted", step=self._st.usage.steps, tool=name, call_id=call_id
                )
                self._emit("action_not_reexecuted", tool=name, call_id=call_id)
                return NOT_REEXECUTED
            if store is not None:
                store.mark_action(self.run_id, call_id, name, "started")
            schemas = schema_by_name(self._tool_schemas())
            before = len(self._st.messages)
            self._dispatch(
                [ToolCall(id=call_id, name=name, arguments=arguments)],
                schemas,
                self._st.usage.steps,
            )
            asks = self._asks.take(self.toolset)
            self._call_asks[call_id] = list(asks)
            if asks:
                self._pending_asks.extend(asks)
                self.interrupts.extend(asks)
            replies = [
                m
                for m in self._st.messages[before:]
                if m.get("role") == "tool" and m.get("tool_call_id") == call_id
            ]
            content = str(replies[-1].get("content") or "") if replies else ""
            if store is not None:
                # An ask did not run the action: it may run once, after approval.
                store.mark_action(self.run_id, call_id, name, "asked" if asks else "done", content)
            self._checkpoint()
            return content

    def record_todos(self, todos: Any) -> str:
        """Record a ``write_todos`` plan as a versioned plan (P17)."""
        steps: list[str] = []
        for item in todos if isinstance(todos, list) else []:
            if isinstance(item, dict):
                steps.append(str(item.get("content") or item.get("step") or ""))
            else:
                steps.append(str(item))
        with self._lock:
            reply = self._record_plan(
                {"steps": [s for s in steps if s.strip()]},
                self._st.usage.steps,
                source="write_todos",
            )
            self._checkpoint()
            return reply

    def refuse_tool(self, name: str) -> str:
        """A tool outside the mediated set was requested: never executed."""
        with self._lock, telemetry.resume_context(self._trace_context):
            telemetry.tool_refused(name)
            self.refused_tools.append(name)
            self._rec.annotation("refused_tool", step=self._st.usage.steps, tool=name)
        return f"[not executed] '{name}' is not an available tool in this run."

    def note_compaction(self, outputs: int, chars: int) -> None:
        """Count a model request whose older tool output was compacted."""
        with self._lock:
            self._compactions["context_compactions"] += 1
            self._compactions["compacted_outputs"] += outputs
            self._compactions["chars_saved"] += chars

    def compaction_stats(self) -> dict[str, int]:
        with self._lock:
            return dict(self._compactions)

    # -- asks ----------------------------------------------------------------------
    def has_pending_asks(self) -> bool:
        with self._lock:
            return bool(self._pending_asks)

    def end_blocked(self, blocker: Blocker) -> None:
        with self._lock:
            self._block(blocker)

    def asks_for(self, call_id: str) -> list[ApprovalRequest]:
        """The asks the last run of tool call ``call_id`` raised."""
        with self._lock:
            return list(self._call_asks.get(call_id) or [])

    def claim_asks(self, call_id: str) -> list[ApprovalRequest]:
        """Take the asks of ``call_id`` out of the pending set (decided in place)."""
        with self._lock:
            asks = self._call_asks.pop(call_id, None) or []
            self._pending_asks = [a for a in self._pending_asks if a not in asks]
            return asks

    def take_pending_asks(self) -> list[ApprovalRequest]:
        with self._lock:
            asks, self._pending_asks = self._pending_asks, []
            return asks

    def resolve_asks(self, asks: list[ApprovalRequest]) -> None:
        """Approve every ask through the approver, or end the run blocked."""
        if asks and all(approve(self.toolset, a, self.approver) for a in asks):
            self._rec.annotation("approved", step=self._st.usage.steps, count=len(asks))
            return
        with self._lock:
            self._block(blocker_for_asks(asks))

    def check_user_stop(self) -> None:
        with self._lock:
            self._guard_user()
            self._guard_budget()
