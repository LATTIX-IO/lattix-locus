"""LangSmith (LangChain's hosted tracing) is never on in Locus (LOCUS-361, P10/P12).

LangChain, LangGraph and Deep Agents carry the ``langsmith`` SDK in process. If
``LANGSMITH_TRACING`` / ``LANGCHAIN_TRACING_V2`` is ``true`` in the environment,
every prompt, tool argument and tool output would be shipped to LangSmith from
the host process: outside the gateway and outside the sandbox. Locus therefore
scrubs those variables at process start (backend and desktop entry points) and
again when the agent runtime loads, and pins the switches to ``false``.

The opt-in OTLP export to a LangSmith endpoint (LOCUS-375, ``telemetry``) is a
separate, principal-configured channel; it resolves its key through native
secrets (``auth_secret_ref``), never through these variables.

Standard library only: entry points import this before anything else.
"""

from __future__ import annotations

import os
import sys
from collections.abc import MutableMapping

#: Variables removed from the environment (matched case-insensitively).
SCRUB_PREFIXES: tuple[str, ...] = ("LANGSMITH_", "LANGCHAIN_TRACING")
SCRUB_NAMES: frozenset[str] = frozenset(
    {"LANGCHAIN_API_KEY", "LANGCHAIN_ENDPOINT", "LANGCHAIN_PROJECT", "LANGCHAIN_HANDLER"}
)
#: Switches pinned off after the scrub (read by langsmith and langchain-core).
FORCED_OFF: dict[str, str] = {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}


def _is_tracing_var(name: str) -> bool:
    upper = name.upper()
    return upper in SCRUB_NAMES or upper.startswith(SCRUB_PREFIXES)


def force_langsmith_off(environ: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Remove LangSmith/LangChain tracing variables and pin tracing off.

    Returns the names removed (never their values). Idempotent. When the
    ``langsmith`` SDK is already loaded, its cached environment lookups are
    cleared and its global tracing switch is set off as well.
    """
    env = os.environ if environ is None else environ
    removed = sorted(
        name
        for name in list(env)
        if _is_tracing_var(name) and str(env.get(name)) != FORCED_OFF.get(name.upper())
    )
    for name in removed:
        del env[name]
    for name, value in FORCED_OFF.items():
        env[name] = value
    if environ is None:
        _disable_loaded_sdk()
    return tuple(removed)


def _disable_loaded_sdk() -> None:
    """Turn tracing off in an already imported ``langsmith`` (never imports it)."""
    utils = sys.modules.get("langsmith.utils")
    cache_clear = getattr(getattr(utils, "get_env_var", None), "cache_clear", None)
    if callable(cache_clear):
        cache_clear()
    run_trees = sys.modules.get("langsmith.run_trees")
    configure = getattr(run_trees, "configure", None)
    if callable(configure):
        configure(enabled=False)


def tracing_env_active(environ: MutableMapping[str, str] | None = None) -> bool:
    """Whether the environment would switch LangSmith tracing on."""
    env = os.environ if environ is None else environ
    for name, value in env.items():
        upper = name.upper()
        if upper.startswith(("LANGSMITH_TRACING", "LANGCHAIN_TRACING")):
            if str(value).strip().lower() not in {"", "0", "false", "no", "off"}:
                return True
    return False
