"""The third-party stack behind the ``deep-agents`` runtime, loaded once (LOCUS-361).

This is the only module that imports ``deepagents``, ``langchain``,
``langchain_core``, ``langgraph`` and ``langsmith`` for the agent runtime (they
pull the Anthropic and Google SDKs in process; nothing outside this package may
import them). Imports are resolved at runtime so the rest of ``locus_runtime``
neither needs nor type-checks against them.

Before anything is imported, LangSmith tracing is forced off
(:func:`locus_runtime.hosted_tracing.force_langsmith_off`); after import, its
global switch is set off as well. The installed versions must equal the audited
pins (:data:`AUDITED_VERSIONS`): an unreviewed upgrade fails closed instead of
running a middleware stack nobody re-audited.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from locus_runtime.hosted_tracing import force_langsmith_off

#: Versions whose middleware stack and in-process dependencies were audited
#: (docs/development/deep-agents-harness.md). Change together with pyproject.toml.
AUDITED_VERSIONS: dict[str, str] = {
    "deepagents": "0.7.21",
    "langchain": "1.4.3",
    "langchain-core": "1.6.6",
    "langgraph": "1.2.12",
    "langgraph-checkpoint": "4.2.0",
    "langgraph-checkpoint-sqlite": "3.1.1",
}


class UnauditedVersion(ImportError):
    """The installed stack is not the audited one (an ImportError: runtime unavailable)."""


def _load(module: str) -> Any:
    return importlib.import_module(module)


def installed_versions() -> dict[str, str]:
    metadata = _load("importlib.metadata")
    out: dict[str, str] = {}
    for name in AUDITED_VERSIONS:
        try:
            out[name] = str(metadata.version(name))
        except metadata.PackageNotFoundError:
            out[name] = "not installed"
    return out


def check_audited(versions: dict[str, str]) -> None:
    drift = {
        name: (versions.get(name, "not installed"), pinned)
        for name, pinned in AUDITED_VERSIONS.items()
        if versions.get(name) != pinned
    }
    if drift:
        detail = ", ".join(
            f"{n} {got} (audited {want})" for n, (got, want) in sorted(drift.items())
        )
        raise UnauditedVersion(
            f"the deep-agents runtime refuses an unaudited stack: {detail}; re-audit per "
            "docs/development/deep-agents-harness.md and update AUDITED_VERSIONS"
        )


@dataclass(frozen=True)
class LangChain:
    """The third-party symbols the runtime uses (resolved once per runtime)."""

    BaseChatModel: Any
    AIMessage: Any
    HumanMessage: Any
    ToolMessage: Any
    ChatGeneration: Any
    ChatResult: Any
    BaseTool: Any
    convert_to_openai_messages: Any
    convert_to_openai_tool: Any
    AgentMiddleware: Any
    TodoListMiddleware: Any
    hook_config: Any
    create_deep_agent: Any
    FilesystemMiddleware: Any
    StateBackend: Any
    HarnessProfile: Any
    GeneralPurposeSubagentProfile: Any
    register_harness_profile: Any
    InMemorySaver: Any
    SqliteSaver: Any
    Command: Any
    interrupt: Any
    tracing_context: Any
    versions: dict[str, str]

    @classmethod
    def load(cls) -> LangChain:
        force_langsmith_off()  # before langsmith reads (and caches) the environment
        versions = installed_versions()
        check_audited(versions)
        deepagents = _load("deepagents")
        middleware = _load("langchain.agents.middleware")
        messages = _load("langchain_core.messages")
        outputs = _load("langchain_core.outputs")
        types = _load("langgraph.types")
        langsmith = _load("langsmith")
        force_langsmith_off()  # now also the loaded SDK's caches and global switch
        return cls(
            BaseChatModel=_load("langchain_core.language_models").BaseChatModel,
            AIMessage=messages.AIMessage,
            HumanMessage=messages.HumanMessage,
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
            hook_config=_load("langchain.agents.middleware.types").hook_config,
            create_deep_agent=deepagents.create_deep_agent,
            FilesystemMiddleware=deepagents.FilesystemMiddleware,
            StateBackend=_load("deepagents.backends").StateBackend,
            HarnessProfile=deepagents.HarnessProfile,
            GeneralPurposeSubagentProfile=deepagents.GeneralPurposeSubagentProfile,
            register_harness_profile=deepagents.register_harness_profile,
            InMemorySaver=_load("langgraph.checkpoint.memory").InMemorySaver,
            SqliteSaver=_load("langgraph.checkpoint.sqlite").SqliteSaver,
            Command=types.Command,
            interrupt=types.interrupt,
            tracing_context=langsmith.tracing_context,
            versions=versions,
        )
