"""LangChain Deep Agents behind the agent runtime port (LOCUS-348, D-27).

This is the only module that imports ``deepagents`` / ``langchain`` /
``langgraph`` 1.x (an optional dependency set; see
``docs/development/runtime-bakeoff-2026-10.md``). Every import is resolved at
runtime so the rest of ``locus_runtime`` neither needs nor type-checks against
them.

Security shape (P6, non-negotiable):

* **Tools.** Deep Agents' built-in filesystem/shell tools (``ls``,
  ``read_file``, ``write_file``, ``edit_file``, ``glob``, ``grep``,
  ``execute``) are removed: ``FilesystemMiddleware(tools=[])`` over an
  in-memory ``StateBackend`` replaces the default one in the main agent and
  in the sub-agent. The model is offered only the run's Locus tools (each a
  wrapper over ``CodingToolset.dispatch`` through :class:`RunController`, so
  over the run's gateway session) plus pure control tools: ``write_todos``
  (planning state), ``task`` (sub-agent) and ``submit``/``report_blocker``.
  A gateway middleware sits in every agent stack and refuses any other tool
  name, so nothing the model asks for runs unmediated.
* **Model.** The chat model is :class:`_GatedChatModel`, a LangChain
  ``BaseChatModel`` whose ``_generate`` calls ``RunController.model_turn``,
  i.e. the run's gated ``ChatClient`` with the verified loop's budget guards,
  retries and accounting. No provider SDK client is ever built by
  LangChain (no ``init_chat_model``), and LangSmith tracing is forced off for
  the run (it would be an unmediated egress channel from the host process).
* **Asks.** A gateway ``ask`` (or an out-of-workspace escalation) raises a
  LangGraph ``interrupt``. Without an approver the run ends ``blocked``;
  with one, the approval is recorded in the gateway's single-use ledger and
  the graph resumes, re-running the exact action.
* **Sub-agents** (Deep Agents' ``task`` tool) get the same gated tools and
  session minus ``submit``/``report_blocker``; their model calls use the same
  gated model, so they count against the same budget.
* **Done** means what it means for the verified loop: ``submit`` runs the
  same verify gate through :class:`RunController`.
"""

from __future__ import annotations

import importlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from locus_runtime.harness.loop import _normalize_tool_name
from locus_runtime.harness.runtime_contract import PORT_VERSION, RuntimeRequest, RuntimeResult
from locus_runtime.harness.runtime_controller import RunController
from locus_runtime.harness.verified_loop import (
    BLOCKER_TOOL,
    PLAN_TOOL,
    SUBMIT_TOOL,
    Blocker,
)

NAME = "deep-agents"
PLAN_TOOL_DA = "write_todos"
SUBAGENT_TOOL = "task"
_RAW_ARGS = "__raw_arguments__"

SUBAGENT_PROMPT = (
    "You are a sub-agent working inside the same repository and run as the main agent. "
    "Use the tools to do the delegated piece of work, then reply with a short, factual "
    "report of what you found or changed. You cannot submit the run."
)


def _load(module: str) -> Any:
    return importlib.import_module(module)


@dataclass(frozen=True)
class _LangChain:
    """The third-party symbols this runtime uses (resolved once)."""

    BaseChatModel: Any
    AIMessage: Any
    ToolMessage: Any
    ChatGeneration: Any
    ChatResult: Any
    BaseTool: Any
    convert_to_openai_messages: Any
    convert_to_openai_tool: Any
    AgentMiddleware: Any
    TodoListMiddleware: Any
    create_deep_agent: Any
    FilesystemMiddleware: Any
    StateBackend: Any
    InMemorySaver: Any
    Command: Any
    interrupt: Any
    tracing_context: Any
    versions: dict[str, str]

    @classmethod
    def load(cls) -> _LangChain:
        deepagents = _load("deepagents")
        middleware = _load("langchain.agents.middleware")
        messages = _load("langchain_core.messages")
        outputs = _load("langchain_core.outputs")
        types = _load("langgraph.types")
        metadata = _load("importlib.metadata")
        versions = {
            name: str(metadata.version(name))
            for name in ("deepagents", "langchain", "langchain-core", "langgraph")
        }
        return cls(
            BaseChatModel=_load("langchain_core.language_models").BaseChatModel,
            AIMessage=messages.AIMessage,
            ToolMessage=messages.ToolMessage,
            ChatGeneration=outputs.ChatGeneration,
            ChatResult=outputs.ChatResult,
            BaseTool=_load("langchain_core.tools").BaseTool,
            convert_to_openai_messages=messages.convert_to_openai_messages,
            convert_to_openai_tool=_load(
                "langchain_core.utils.function_calling"
            ).convert_to_openai_tool,
            AgentMiddleware=middleware.AgentMiddleware,
            TodoListMiddleware=middleware.TodoListMiddleware,
            create_deep_agent=deepagents.create_deep_agent,
            FilesystemMiddleware=_load("deepagents.middleware.filesystem").FilesystemMiddleware,
            StateBackend=_load("deepagents.backends").StateBackend,
            InMemorySaver=_load("langgraph.checkpoint.memory").InMemorySaver,
            Command=types.Command,
            interrupt=types.interrupt,
            tracing_context=_load("langsmith").tracing_context,
            versions=versions,
        )


# --------------------------------------------------------------------------- #
# LangChain adapters (classes built against the loaded library)
# --------------------------------------------------------------------------- #
def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def _tool_call_args(raw: Any) -> dict[str, Any]:
    """LangChain needs dict args; unparseable ones are carried raw to the validator."""
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {_RAW_ARGS: str(raw)}
    return parsed if isinstance(parsed, dict) else {_RAW_ARGS: str(raw)}


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or (tool.get("function") or {}).get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _build_classes(lc: _LangChain) -> tuple[Any, Any, Any]:
    chat_model_base: Any = lc.BaseChatModel
    tool_base: Any = lc.BaseTool
    middleware_base: Any = lc.AgentMiddleware

    class GatedChatModel(chat_model_base):  # type: ignore[misc]
        """LangChain chat model whose every call is a gated Locus model turn."""

        controller: Any = None

        @property
        def _llm_type(self) -> str:
            return "locus-gated"

        def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
            formatted = [lc.convert_to_openai_tool(t) for t in tools]
            return self.bind(tools=formatted)

        def _generate(
            self,
            messages: Any,
            stop: Any = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> Any:
            tools = list(kwargs.get("tools") or [])
            payload = lc.convert_to_openai_messages(messages)
            resp = self.controller.model_turn(list(payload), tools)
            calls = [
                {
                    "name": tc.name,
                    "args": _tool_call_args(tc.arguments),
                    "id": tc.id or f"call_{uuid.uuid4().hex[:12]}",
                    "type": "tool_call",
                }
                for tc in resp.tool_calls
            ]
            usage = resp.usage or {}
            prompt = int(usage.get("prompt_tokens") or 0)
            completion = int(usage.get("completion_tokens") or 0)
            message = lc.AIMessage(
                content=resp.text or "",
                tool_calls=calls,
                usage_metadata={
                    "input_tokens": prompt,
                    "output_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            )
            return lc.ChatResult(generations=[lc.ChatGeneration(message=message)])

    class GatedTool(tool_base):  # type: ignore[misc]
        """A Locus tool: schema for the model, execution through the controller."""

        controller: Any = None

        def _run(self, *args: Any, **kwargs: Any) -> str:
            # Reached only if a framework path skips the gateway middleware; it
            # still executes through the controller (so through the gateway).
            kwargs.pop("run_manager", None)
            kwargs.pop("config", None)
            call_id = f"call_{uuid.uuid4().hex[:12]}"
            return str(self.controller.tool_call(call_id, self.name, kwargs))

    class GatewayMiddleware(middleware_base):  # type: ignore[misc]
        """Outer guard of every tool call: Locus tools through the controller,
        pure control tools through Deep Agents, everything else refused."""

        def __init__(self, controller: Any, gated: frozenset[str], passthrough: frozenset[str]):
            super().__init__()
            self.controller = controller
            self.gated = gated
            self.passthrough = passthrough

        @property
        def name(self) -> str:
            return "LocusGatewayMiddleware"

        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            # Offer the model only mediated tools (hides Deep Agents' mandatory,
            # state-only ``read_file`` and anything a profile might add).
            allowed = self.gated | self.passthrough
            tools = [t for t in request.tools if _tool_name(t) in allowed]
            return handler(request.override(tools=tools))

        def wrap_tool_call(self, request: Any, handler: Any) -> Any:
            call = request.tool_call
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
            normalized, _ = _normalize_tool_name(name, args)
            if normalized in self.gated:
                raw = args.get(_RAW_ARGS) if isinstance(args, dict) and _RAW_ARGS in args else args
                content = self.controller.tool_call(call_id, name, raw)
                if self.controller.has_pending_asks():
                    # LangGraph HITL: pause here; the runtime approves or ends blocked.
                    lc.interrupt({"kind": "locus_gateway_ask", "tool": normalized})
                return lc.ToolMessage(content=content, tool_call_id=call_id, name=normalized)
            if name in self.passthrough:
                if name == PLAN_TOOL_DA and isinstance(args, dict):
                    self.controller.record_todos(args.get("todos"))
                return handler(request)
            return lc.ToolMessage(
                content=self.controller.refuse_tool(name),
                tool_call_id=call_id,
                name=name,
                status="error",
            )

    return GatedChatModel, GatedTool, GatewayMiddleware


# --------------------------------------------------------------------------- #
# The runtime
# --------------------------------------------------------------------------- #
class DeepAgentsRuntime:
    """Deep Agents (LangGraph) as the agent loop, inside the Locus run envelope."""

    name = NAME
    port_version = PORT_VERSION

    def __init__(self) -> None:
        self._lc = _LangChain.load()  # ImportError when the optional deps are missing
        self._model_cls, self._tool_cls, self._middleware_cls = _build_classes(self._lc)

    @property
    def versions(self) -> dict[str, str]:
        return dict(self._lc.versions)

    def run(self, request: RuntimeRequest) -> RuntimeResult:
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
        )
        use_subagents = bool(request.options.get("subagents", True))
        result = controller.drive(lambda c: self._drive(c, use_subagents))
        return RuntimeResult.from_run(
            self.name,
            result,
            offered_tools=sorted(controller.offered_tools),
            interrupts=list(controller.interrupts),
        )

    # -- graph ------------------------------------------------------------------
    def _tools(self, controller: RunController) -> list[Any]:
        tools = []
        for schema in controller._tool_schemas():  # noqa: SLF001 - envelope-filtered schemas
            fn = schema["function"]
            if fn["name"] == PLAN_TOOL:
                continue  # Deep Agents plans with write_todos
            tools.append(
                self._tool_cls(
                    name=fn["name"],
                    description=str(fn.get("description") or ""),
                    args_schema=fn.get("parameters") or {"type": "object", "properties": {}},
                    controller=controller,
                )
            )
        return tools

    def _build(self, controller: RunController, use_subagents: bool) -> Any:
        lc = self._lc
        model = self._model_cls(controller=controller)
        tools = self._tools(controller)
        names = frozenset(t.name for t in tools)
        work_tools = [t for t in tools if t.name not in {SUBMIT_TOOL, BLOCKER_TOOL}]
        work_names = frozenset(t.name for t in work_tools)
        backend = lc.StateBackend()

        def no_fs() -> Any:
            # Replaces Deep Agents' default FilesystemMiddleware. Only the mandatory
            # ``read_file`` stays registered, over in-memory graph state (no host
            # IO); the gateway middleware hides it from the model and refuses it.
            # No eviction of tool output into that state.
            return lc.FilesystemMiddleware(
                backend=backend,
                tools=["read_file"],
                tool_token_limit_before_evict=None,
                human_message_token_limit_before_evict=None,
            )

        passthrough = frozenset({PLAN_TOOL_DA, SUBAGENT_TOOL} if use_subagents else {PLAN_TOOL_DA})
        subagents = [
            {
                "name": "general-purpose",
                "description": "Delegate a self-contained piece of the work (investigate, "
                "edit, run tests) to a sub-agent with the same tools and permissions.",
                "system_prompt": SUBAGENT_PROMPT,
                "tools": work_tools,
                "middleware": [
                    no_fs(),
                    self._middleware_cls(controller, work_names, frozenset()),
                ],
            }
        ]
        return lc.create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=controller.system_prompt_text(),
            middleware=[
                no_fs(),
                lc.TodoListMiddleware(),
                self._middleware_cls(controller, names, passthrough),
            ],
            subagents=subagents if use_subagents else [],
            backend=backend,
            checkpointer=lc.InMemorySaver(),
            name="locus-deep-agent",
        )

    def _drive(self, controller: RunController, use_subagents: bool) -> None:
        lc = self._lc
        agent = self._build(controller, use_subagents)
        config = {
            "configurable": {"thread_id": controller.run_id},
            "recursion_limit": 100_000,  # the envelope budget is the limit
        }
        payload: Any = {"messages": [{"role": "user", "content": controller.initial_message()}]}
        with lc.tracing_context(enabled=False):
            while True:
                controller.check_user_stop()
                out = agent.invoke(payload, config=config)
                asks = controller.take_pending_asks()
                interrupted = isinstance(out, dict) and bool(out.get("__interrupt__"))
                if asks:
                    controller.resolve_asks(asks)  # ends the run blocked unless approved
                    payload = lc.Command(resume={"approved": True})
                    continue
                if interrupted:
                    controller.end_blocked(
                        Blocker(
                            kind="configuration",
                            detail="the agent graph paused for an interrupt with no gateway ask",
                            unblock="inspect the run trajectory; rerun without that interrupt",
                        )
                    )
                messages = out.get("messages") if isinstance(out, dict) else None
                last = messages[-1] if messages else None
                text = _content_text(getattr(last, "content", "")) if last is not None else ""
                nudge = controller.handle_text(text)
                payload = {"messages": [{"role": "user", "content": nudge}]}
