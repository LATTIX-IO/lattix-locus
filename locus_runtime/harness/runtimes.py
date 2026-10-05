"""Agent runtime implementations and the single selection point (LOCUS-348, D-27).

Callers build a :class:`~locus_runtime.harness.runtime_contract.RuntimeRequest`
and get the runtime from :func:`create_runtime`; nothing else constructs a
runtime. ``LOCUS_AGENT_RUNTIME`` overrides the default (``deep-agents``).

* ``verified-loop`` -- :class:`VerifiedLoopRuntime`, an adapter over the
  existing :class:`~locus_runtime.harness.verified_loop.VerifiedLoop`. The loop
  itself is unchanged; the adapter adds the port's ask rule (an unapproved
  gateway ask ends the run ``blocked``) through the loop's event hook.
* ``deep-agents`` -- :class:`~locus_runtime.harness.deep_agents.runtime.DeepAgentsRuntime`
  (LangChain Deep Agents on LangGraph, extended by
  :mod:`locus_runtime.harness.deep_agents`; LOCUS-361). Imported lazily: the
  third-party stack (and the vendor SDKs it carries) loads only when this
  runtime is created.

The default is ``deep-agents`` since 2026-10-04: the extended runtime matched or
beat the verified loop on the RSI scorecard (D-27; ``compare()`` said ``promote``,
see ``docs/development/runtime-bakeoff-2026-10.md``). ``verified-loop`` stays
selectable as the fallback (``LOCUS_AGENT_RUNTIME=verified-loop``). The default
never falls back silently: when the Deep Agents stack is missing or differs from
its audited pins, :func:`create_runtime` raises ``RuntimeUnavailable``.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from typing import Any

from locus_runtime.harness.runtime_contract import (
    PORT_VERSION,
    AgentRuntime,
    RuntimeRequest,
    RuntimeResult,
    RuntimeUnavailable,
)
from locus_runtime.harness.runtime_controller import AskCursor, approve, blocker_for_asks
from locus_runtime.harness.verified_loop import VerifiedLoop

VERIFIED_LOOP = "verified-loop"
DEEP_AGENTS = "deep-agents"
RUNTIME_NAMES: tuple[str, ...] = (VERIFIED_LOOP, DEEP_AGENTS)
RUNTIME_ENV = "LOCUS_AGENT_RUNTIME"
#: The runtime :func:`create_runtime` builds when neither a name nor
#: ``LOCUS_AGENT_RUNTIME`` is given (D-27, confirmed 2026-10-04).
DEFAULT_RUNTIME = DEEP_AGENTS

#: ``request.options`` keys forwarded to :class:`VerifiedLoop` unchanged.
_VERIFIED_LOOP_OPTIONS = frozenset(
    {
        "plan_mode",
        "judge_pricing",
        "budget_sink",
        "reask_policy",
        "max_identical_failures",
        "provider_max_retries",
        "provider_retry_backoff",
    }
)


class VerifiedLoopRuntime:
    """The Locus verified loop behind the runtime port (no change to the loop)."""

    name = VERIFIED_LOOP
    port_version = PORT_VERSION

    def run(self, request: RuntimeRequest) -> RuntimeResult:
        toolset = request.toolset
        cursor = AskCursor.of(toolset)
        interrupts: list[Any] = []
        holder: dict[str, VerifiedLoop] = {}
        user_on_event = request.on_event

        def on_event(kind: str, data: dict[str, Any]) -> None:
            if user_on_event is not None:
                user_on_event(kind, data)
            if kind != "tool":
                return
            asks = cursor.take(toolset)
            if not asks:
                return
            interrupts.extend(asks)
            if all(approve(toolset, a, request.approver) for a in asks):
                return  # approved once; the agent may retry the exact action
            holder["loop"]._block(blocker_for_asks(asks))  # noqa: SLF001 - port ask rule

        options = {k: v for k, v in request.options.items() if k in _VERIFIED_LOOP_OPTIONS}
        loop = VerifiedLoop(
            client=request.client,
            toolset=toolset,
            profile=request.profile,
            envelope=request.envelope,
            system_prompt=request.system_prompt,
            user_prompt=request.user_prompt,
            run_id=request.run_id,
            judge_client=request.judge_client,
            pricing=request.pricing,
            checkpoint_path=request.checkpoint_path,
            recorder=request.recorder,
            on_event=on_event,
            should_stop=request.should_stop,
            agent_id=request.agent_id,
            task_meta=dict(request.task_meta),
            **options,
        )
        holder["loop"] = loop
        result = loop.run()
        offered = [t["function"]["name"] for t in loop._tool_schemas()]  # noqa: SLF001
        return RuntimeResult.from_run(
            self.name, result, offered_tools=offered, interrupts=interrupts
        )


def _deep_agents_factory() -> AgentRuntime:
    try:
        module = importlib.import_module("locus_runtime.harness.deep_agents.runtime")
        runtime: AgentRuntime = module.DeepAgentsRuntime()
    except ImportError as exc:
        raise RuntimeUnavailable(
            "the deep-agents runtime needs the pinned 'deepagents' stack "
            f"(langgraph/langchain 1.x, audited versions): {exc}; "
            f"set {RUNTIME_ENV}={VERIFIED_LOOP} to use the fallback runtime"
        ) from exc
    return runtime


_FACTORIES: dict[str, Callable[[], AgentRuntime]] = {
    VERIFIED_LOOP: VerifiedLoopRuntime,
    DEEP_AGENTS: _deep_agents_factory,
}


def default_runtime_name() -> str:
    name = str(os.getenv(RUNTIME_ENV) or "").strip().lower()
    return name or DEFAULT_RUNTIME


def create_runtime(name: str | None = None) -> AgentRuntime:
    """The one place a runtime is selected and built (raises on unknown names)."""
    key = str(name or default_runtime_name()).strip().lower()
    factory = _FACTORIES.get(key)
    if factory is None:
        raise ValueError(f"unknown agent runtime {key!r}; expected one of {RUNTIME_NAMES}")
    runtime = factory()
    if runtime.port_version != PORT_VERSION:
        raise RuntimeUnavailable(
            f"runtime {key!r} implements port {runtime.port_version}, expected {PORT_VERSION}"
        )
    return runtime


def runtime_available(name: str) -> bool:
    """Whether :func:`create_runtime` can build ``name`` in this environment."""
    try:
        create_runtime(name)
    except (RuntimeUnavailable, ValueError):
        return False
    return True
