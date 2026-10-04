"""LangChain Deep Agents as the Locus agent runtime (LOCUS-361, D-27; port per D-28).

``DeepAgentsRuntime`` builds a Deep Agents graph (``create_deep_agent``) whose
model, tools, middleware and checkpointer are the Locus ones from this package,
and drives it inside the run envelope through
:class:`~locus_runtime.harness.runtime_controller.RunController`.

Security shape (P6, non-negotiable):

* **Model.** The graph's only chat model is the gated one (every turn a gateway
  ``model_call``); no provider SDK client is built, LangSmith is forced off at
  load and per run.
* **Tools.** The model is offered the run's Locus tools (each through the
  controller, so through the run's gateway session) plus pure control tools:
  ``write_todos`` (the plan), ``task`` (sub-agent; ``options["subagents"]``)
  and ``submit`` / ``report_blocker``. Deep Agents' built-in file tools are
  replaced by a state-only ``read_file`` that is hidden and refused.
* **Asks.** A gateway ``ask`` is a LangGraph ``interrupt``: without an approver
  the run ends ``blocked``; with one, the approval goes into the gateway's
  single-use ledger and the graph resumes, re-running exactly that action.
* **Durability.** With ``request.checkpoint_path`` (an SQLite file, normally
  :func:`~locus_runtime.harness.run_store.default_run_db_path`), the LangGraph
  checkpoints, the controller state and the action ledger live in that one WAL
  database, and ``run()`` with the same ``run_id`` resumes the run: a pending
  ask is decided again, a finished call is replayed and a call interrupted by a
  crash is never re-run. Without it the run is in memory only.

Options (``request.options``): ``subagents`` (default ``True``), ``compaction``
(a :class:`CompactionPolicy`, or ``False`` to disable), ``provider_retry_backoff``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from locus_runtime.harness.deep_agents.compaction import CompactionPolicy
from locus_runtime.harness.deep_agents.library import LangChain
from locus_runtime.harness.deep_agents.middleware import (
    ASK_INTERRUPT_KIND,
    PLAN_TOOL_DA,
    PROFILE_KEY,
    SUBAGENT_TOOL,
    build_classes,
    content_text,
)
from locus_runtime.harness.deep_agents.prompts import (
    SUBAGENT_DESCRIPTION,
    SUBAGENT_PROMPT,
    TASK_TOOL_DESCRIPTION,
    TODO_SYSTEM_PROMPT,
    TODO_TOOL_DESCRIPTION,
)
from locus_runtime.harness.run_store import RunStore, open_sqlite
from locus_runtime.harness.runtime_contract import (
    PORT_VERSION,
    ApprovalRequest,
    RuntimeRequest,
    RuntimeResult,
)
from locus_runtime.harness.runtime_controller import RunController
from locus_runtime.harness.verified_loop import BLOCKER_TOOL, PLAN_TOOL, SUBMIT_TOOL, Blocker

NAME = "deep-agents"


def asks_from_interrupts(interrupts: Any) -> list[ApprovalRequest]:
    """The approval requests carried by pending LangGraph interrupts (they survive a restart)."""
    asks: list[ApprovalRequest] = []
    for item in interrupts or []:
        value = getattr(item, "value", item)
        if isinstance(value, dict) and value.get("kind") == ASK_INTERRUPT_KIND:
            asks.extend(ApprovalRequest(**a) for a in value.get("asks") or [])
    return asks


class DeepAgentsRuntime:
    """Deep Agents (LangGraph) as the agent loop, inside the Locus run envelope."""

    name = NAME
    port_version = PORT_VERSION

    def __init__(self) -> None:
        self._lc = LangChain.load()  # ImportError when missing or not the audited versions
        self._cls = build_classes(self._lc)
        # The Deep Agents extension point for prompt/tool trimming: a harness
        # profile for the gated model's provider key. Summarization is replaced
        # by Locus compaction (no extra model calls); the ``task`` description
        # is trimmed. Re-registering is idempotent.
        self._lc.register_harness_profile(
            PROFILE_KEY,
            self._lc.HarnessProfile(
                tool_description_overrides={SUBAGENT_TOOL: TASK_TOOL_DESCRIPTION},
                excluded_middleware=frozenset({"SummarizationMiddleware"}),
            ),
        )

    @property
    def versions(self) -> dict[str, str]:
        return dict(self._lc.versions)

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        path = request.checkpoint_path
        store = RunStore(path) if path is not None else None
        try:
            controller = RunController(
                client=request.client,
                toolset=request.toolset,
                profile=request.profile,
                envelope=request.envelope,
                system_prompt=request.system_prompt,
                user_prompt=request.user_prompt,
                run_id=request.run_id,
                judge_client=request.judge_client,
                pricing=request.pricing,
                plan_mode="optional",
                recorder=request.recorder,
                on_event=request.on_event,
                should_stop=request.should_stop,
                agent_id=request.agent_id,
                task_meta=dict(request.task_meta),
                plan_tool=PLAN_TOOL_DA,
                approver=request.approver,
                runtime_name=self.name,
                provider_retry_backoff=float(request.options.get("provider_retry_backoff", 1.5)),
                store=store,
                unique_call_ids=True,
            )
            resumed = controller.restore()
            use_subagents = bool(request.options.get("subagents", True))
            policy = _compaction_policy(request.options.get("compaction"))
            with _checkpointer(self._lc, path) as saver:
                result = controller.drive(
                    lambda c: self._drive(c, saver, use_subagents, policy, resumed)
                )
        finally:
            if store is not None:
                store.close()
        out = RuntimeResult.from_run(
            self.name,
            result,
            offered_tools=sorted(controller.offered_tools),
            interrupts=list(controller.interrupts),
        )
        out.telemetry.update(controller.compaction_stats())
        return out

    # -- graph ------------------------------------------------------------------
    def _tools(self, controller: RunController) -> list[Any]:
        tools = []
        for schema in controller._tool_schemas():  # noqa: SLF001 - envelope-filtered schemas
            fn = schema["function"]
            if fn["name"] == PLAN_TOOL:
                continue  # Deep Agents plans with write_todos
            tools.append(
                self._cls.tool(
                    name=fn["name"],
                    description=str(fn.get("description") or ""),
                    args_schema=fn.get("parameters") or {"type": "object", "properties": {}},
                    controller=controller,
                )
            )
        return tools

    def _build(
        self,
        controller: RunController,
        saver: Any,
        use_subagents: bool,
        policy: CompactionPolicy | None,
    ) -> Any:
        lc, cls = self._lc, self._cls
        tools = self._tools(controller)
        names = frozenset(t.name for t in tools)
        work_tools = [t for t in tools if t.name not in {SUBMIT_TOOL, BLOCKER_TOOL}]
        work_names = frozenset(t.name for t in work_tools)
        passthrough = frozenset({PLAN_TOOL_DA, SUBAGENT_TOOL} if use_subagents else {PLAN_TOOL_DA})
        model = cls.model(controller=controller, allowed_tools=names | passthrough)
        backend = lc.StateBackend()

        def no_fs() -> Any:
            # Replaces Deep Agents' default FilesystemMiddleware (it cannot be
            # excluded). Only the mandatory ``read_file`` stays registered, over
            # in-memory graph state (no host IO); the gateway middleware hides it
            # from the model and refuses it. No eviction of tool output into it.
            return lc.FilesystemMiddleware(
                backend=backend,
                tools=["read_file"],
                tool_token_limit_before_evict=None,
                human_message_token_limit_before_evict=None,
            )

        def compaction() -> list[Any]:
            return [cls.compaction(controller, policy)] if policy is not None else []

        # The explicit ``general-purpose`` spec replaces Deep Agents' default one,
        # so a sub-agent never runs without the Locus middleware. With sub-agents
        # off, ``task`` is neither offered nor allowed.
        subagent = {
            "name": "general-purpose",
            "description": SUBAGENT_DESCRIPTION,
            "system_prompt": SUBAGENT_PROMPT,
            "tools": work_tools,
            "middleware": [
                no_fs(),
                cls.gateway(controller, work_names, frozenset()),
                *compaction(),
            ],
        }
        return lc.create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=controller.system_prompt_text(),
            middleware=[
                no_fs(),
                lc.TodoListMiddleware(
                    system_prompt=TODO_SYSTEM_PROMPT, tool_description=TODO_TOOL_DESCRIPTION
                ),
                cls.gateway(controller, names, passthrough),
                cls.turn(controller),
                *compaction(),
            ],
            subagents=[subagent],
            backend=backend,
            checkpointer=saver,
            name="locus-deep-agent",
        )

    def _drive(
        self,
        controller: RunController,
        saver: Any,
        use_subagents: bool,
        policy: CompactionPolicy | None,
        resumed: bool,
    ) -> None:
        lc = self._lc
        agent = self._build(controller, saver, use_subagents, policy)
        config = {
            "configurable": {"thread_id": controller.run_id},
            "recursion_limit": 100_000,  # the envelope budget is the limit
        }
        with lc.tracing_context(enabled=False):
            payload = self._start(controller, agent, config, resumed)
            while True:
                controller.check_user_stop()
                out = agent.invoke(payload, config=config)
                interrupts = list(out.get("__interrupt__") or []) if isinstance(out, dict) else []
                asks = controller.take_pending_asks() or asks_from_interrupts(interrupts)
                if interrupts or asks:
                    payload = self._decide(controller, asks, interrupts)
                    continue
                # The turn middleware nudges text turns inside the graph; the graph
                # ending on its own is a fallback path (same nudge, new invoke).
                payload = {"messages": [{"role": "user", "content": self._nudge(controller, out)}]}

    def _start(self, controller: RunController, agent: Any, config: Any, resumed: bool) -> Any:
        fresh = {"messages": [{"role": "user", "content": controller.initial_message()}]}
        if not resumed:
            return fresh
        snapshot = agent.get_state(config)
        if not (snapshot.values or {}).get("messages"):
            return fresh  # the process died before the graph's first checkpoint
        if snapshot.interrupts:
            # A decision was pending when the process stopped: ask again (an
            # approval is never carried across a restart), then resume.
            pending = list(snapshot.interrupts)
            return self._decide(controller, asks_from_interrupts(pending), pending)
        if snapshot.next:
            return None  # continue from the last checkpoint
        return {"messages": [{"role": "user", "content": self._nudge(controller, snapshot.values)}]}

    def _decide(
        self, controller: RunController, asks: list[ApprovalRequest], interrupts: list[Any]
    ) -> Any:
        if not asks:
            controller.end_blocked(
                Blocker(
                    kind="configuration",
                    detail="the agent graph paused for an interrupt with no gateway ask",
                    unblock="inspect the run trajectory; rerun without that interrupt",
                )
            )
        controller.resolve_asks(asks)  # ends the run blocked unless approved
        decision = {"approved": True}
        ids = [str(getattr(i, "id", "") or "") for i in interrupts]
        if len(ids) > 1 and all(ids):
            # LangGraph needs one resume value per pending interrupt (parallel calls).
            return self._lc.Command(resume={i: decision for i in ids})
        return self._lc.Command(resume=decision)

    @staticmethod
    def _nudge(controller: RunController, values: Any) -> str:
        messages = values.get("messages") if isinstance(values, dict) else None
        last = messages[-1] if messages else None
        text = content_text(getattr(last, "content", "")) if last is not None else ""
        return controller.handle_text(text)


def _compaction_policy(option: Any) -> CompactionPolicy | None:
    if option is False:
        return None
    if isinstance(option, CompactionPolicy):
        return option
    return CompactionPolicy()


@contextmanager
def _checkpointer(lc: LangChain, path: Path | None) -> Iterator[Any]:
    """SQLite (WAL) checkpointer in the run database, or in memory without one."""
    if path is None:
        yield lc.InMemorySaver()
        return
    conn: sqlite3.Connection = open_sqlite(Path(path))
    try:
        saver = lc.SqliteSaver(conn)
        saver.setup()
        yield saver
    finally:
        conn.close()
