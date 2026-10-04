"""The verified loop's guarantees as Deep Agents / LangChain agent middleware (LOCUS-361).

Every class here is built against the loaded third-party stack
(:class:`~locus_runtime.harness.deep_agents.library.LangChain`) and delegates
the decisions to :class:`~locus_runtime.harness.runtime_controller.RunController`,
i.e. to the verified loop's own code. Nothing here decides policy itself.

* :class:`GatedChatModel` -- the only chat model the graph has. Every call is a
  gated Locus model turn (``RunController.model_turn`` -> ``GatedChatClient`` ->
  gateway ``model_call``) with the loop's budget guards, retries and
  accounting. It also drops any tool outside the run's mediated set from the
  request, whatever a middleware added. No provider SDK client is ever built.
* ``LocusGatewayMiddleware`` -- in every agent stack (main and sub-agents):
  ``before_model`` applies the envelope guards (user stop, steps, tokens, time,
  cost, context); ``wrap_model_call`` offers only mediated tools;
  ``wrap_tool_call`` runs Locus tools through the controller (so through the
  gateway session and the action ledger), lets the pure control tools through,
  records ``write_todos`` as the run plan and refuses everything else (Deep
  Agents' built-in file tools included). A gateway ``ask`` becomes a LangGraph
  ``interrupt``.
* ``LocusTurnMiddleware`` -- main agent only: a model turn without a tool call
  gets the verified loop's nudge (or its end state) and loops back to the model.
* ``LocusCompactionMiddleware`` -- compacts old tool output in what the model
  sees (:mod:`.compaction`).

``submit`` is a Locus tool: it runs the verify gate (done criteria + acceptance
judge) through the controller, and only a passing gate ends the run ``done``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from locus_runtime.harness.deep_agents.compaction import CompactionPolicy, compact, saved_chars
from locus_runtime.harness.deep_agents.library import LangChain
from locus_runtime.harness.deep_agents.prompts import TASK_PARAMETER_DESCRIPTIONS
from locus_runtime.harness.loop import _normalize_tool_name
from locus_runtime.harness.runtime_controller import blocker_for_asks

#: The harness-profile key the gated model reports as its provider.
PROFILE_KEY = "locus"
PLAN_TOOL_DA = "write_todos"
SUBAGENT_TOOL = "task"
ASK_INTERRUPT_KIND = "locus_gateway_ask"
_RAW_ARGS = "__raw_arguments__"


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "".join(parts)


def tool_call_args(raw: Any) -> dict[str, Any]:
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


def tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or (tool.get("function") or {}).get("name") or "")
    return str(getattr(tool, "name", "") or "")


def slim_schema(schema: dict[str, Any], descriptions: dict[str, str]) -> dict[str, Any]:
    """An OpenAI tool schema with shorter parameter descriptions (same names and types)."""
    function = dict(schema.get("function") or {})
    parameters = dict(function.get("parameters") or {})
    properties = {
        key: {**value, "description": descriptions[key]} if key in descriptions else value
        for key, value in dict(parameters.get("properties") or {}).items()
    }
    parameters["properties"] = properties
    function["parameters"] = parameters
    return {"type": "function", "function": function}


def _new_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class HarnessClasses:
    """The Locus classes built against one loaded stack."""

    model: Any
    tool: Any
    gateway: Any
    turn: Any
    compaction: Any


def build_classes(lc: LangChain) -> HarnessClasses:
    chat_model_base: Any = lc.BaseChatModel
    tool_base: Any = lc.BaseTool
    middleware_base: Any = lc.AgentMiddleware

    class GatedChatModel(chat_model_base):  # type: ignore[misc]
        """LangChain chat model whose every call is a gated Locus model turn."""

        controller: Any = None
        #: Tool names the model may be offered (the mediated set).
        allowed_tools: Any = frozenset()

        @property
        def _llm_type(self) -> str:
            return "locus-gated"

        def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> Any:
            # Deep Agents resolves its harness profile from ``ls_provider``.
            return {"ls_provider": PROFILE_KEY, "ls_model_type": "chat"}

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
            allowed = self.allowed_tools
            tools = [t for t in kwargs.get("tools") or [] if tool_name(t) in allowed]
            payload = lc.convert_to_openai_messages(messages)
            resp = self.controller.model_turn(list(payload), tools)
            calls = [
                {
                    "name": tc.name,
                    "args": tool_call_args(tc.arguments),
                    "id": tc.id or _new_call_id(),
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
            return str(self.controller.tool_call(_new_call_id(), self.name, kwargs))

    class LocusGatewayMiddleware(middleware_base):  # type: ignore[misc]
        """Envelope guards before each model call; mediation of every tool call."""

        def __init__(self, controller: Any, gated: frozenset[str], passthrough: frozenset[str]):
            super().__init__()
            self.controller = controller
            self.gated = gated
            self.passthrough = passthrough

        @property
        def name(self) -> str:
            return "LocusGatewayMiddleware"

        def before_model(self, state: Any, runtime: Any) -> Any:
            self.controller.guard_model_call()  # raises the end state when a budget is out
            return None

        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            # Offer the model only mediated tools (hides Deep Agents' mandatory,
            # state-only ``read_file`` and anything a profile might add).
            allowed = self.gated | self.passthrough
            tools = [self._slim(t) for t in request.tools if tool_name(t) in allowed]
            return handler(request.override(tools=tools))

        @staticmethod
        def _slim(tool: Any) -> Any:
            # Token diet: upstream's ``task`` parameter descriptions are long;
            # the tool (and its execution) is unchanged, only the schema shown.
            if tool_name(tool) != SUBAGENT_TOOL:
                return tool
            return slim_schema(lc.convert_to_openai_tool(tool), TASK_PARAMETER_DESCRIPTIONS)

        def wrap_tool_call(self, request: Any, handler: Any) -> Any:
            call = request.tool_call
            name = str(call.get("name") or "")
            args = call.get("args") or {}
            call_id = str(call.get("id") or _new_call_id())
            normalized, _ = _normalize_tool_name(name, args)
            if normalized in self.gated:
                raw = args.get(_RAW_ARGS) if isinstance(args, dict) and _RAW_ARGS in args else args
                content = self._gated_call(call_id, name, normalized, raw)
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

        def _gated_call(self, call_id: str, name: str, normalized: str, raw: Any) -> str:
            controller = self.controller
            content = str(controller.tool_call(call_id, name, raw))
            asks = controller.asks_for(call_id)
            if not asks:
                return content
            # LangGraph HITL: the graph pauses (and is checkpointed) here; the
            # runtime approves through the gateway ledger and resumes, or ends
            # the run blocked. On resume the call re-runs and, approved, executes.
            # Parallel calls interrupt separately, each with its own asks.
            lc.interrupt(
                {
                    "kind": ASK_INTERRUPT_KIND,
                    "tool": normalized,
                    "call_id": call_id,
                    "asks": [a.model_dump(mode="json") for a in asks],
                }
            )
            # ``interrupt`` returned: this call was resumed with a decision for an
            # earlier ask and the re-run raised a new one. Decide it here, once.
            controller.resolve_asks(controller.claim_asks(call_id))
            content = str(controller.tool_call(call_id, name, raw))
            again = controller.claim_asks(call_id)
            if again:
                controller.end_blocked(blocker_for_asks(again))
            return content

    class LocusTurnMiddleware(middleware_base):  # type: ignore[misc]
        """A turn without a tool call: the verified loop's nudge, then the model again."""

        def __init__(self, controller: Any):
            super().__init__()
            self.controller = controller

        @property
        def name(self) -> str:
            return "LocusTurnMiddleware"

        def after_model(self, state: Any, runtime: Any) -> Any:
            messages = state.get("messages") or []
            last = messages[-1] if messages else None
            if last is None or getattr(last, "tool_calls", None):
                return None
            nudge = self.controller.handle_text(content_text(getattr(last, "content", "")))
            return {"messages": [lc.HumanMessage(content=nudge)], "jump_to": "model"}

    # LangChain's ``hook_config`` marks the hook in place (the graph adds the
    # after-model -> model edge only for hooks that declare it).
    lc.hook_config(can_jump_to=["model"])(LocusTurnMiddleware.after_model)

    class LocusCompactionMiddleware(middleware_base):  # type: ignore[misc]
        """Compacts old tool output in the model's view of the conversation."""

        def __init__(self, controller: Any, policy: CompactionPolicy):
            super().__init__()
            self.controller = controller
            self.policy = policy

        @property
        def name(self) -> str:
            return "LocusCompactionMiddleware"

        def wrap_model_call(self, request: Any, handler: Any) -> Any:
            messages = list(request.messages)
            view = [
                (str(getattr(m, "type", "")), content_text(getattr(m, "content", "")))
                for m in messages
            ]
            replacements = compact(view, self.policy)
            if not replacements:
                return handler(request)
            for index, text in replacements.items():
                messages[index] = messages[index].model_copy(update={"content": text})
            self.controller.note_compaction(len(replacements), saved_chars(view, replacements))
            return handler(request.override(messages=messages))

    return HarnessClasses(
        model=GatedChatModel,
        tool=GatedTool,
        gateway=LocusGatewayMiddleware,
        turn=LocusTurnMiddleware,
        compaction=LocusCompactionMiddleware,
    )
