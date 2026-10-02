"""Tool-call reliability: validation, bounded re-ask, grammar constraints.

Weak/local models lose 10-30% of attempts to malformed tool calls. Three
mitigations, in order of cost:

1. **Grammar-constrained decoding** (cheapest, when the backend supports it):
   ``constraint_kwargs`` produces vLLM (XGrammar) / llama.cpp (GBNF) /
   json_schema request kwargs so the *envelope* (tool name + arg schema) is
   structurally guaranteed. Prose is never constrained.
2. **Envelope validation**: ``validate_tool_call`` checks the tool exists, args
   are a JSON object, and required properties are present with roughly-correct
   types — replacing the silent ``{}`` coercion that hides bugs.
3. **Bounded re-ask**: on a malformed call, feed the reason back and let the
   model retry up to ``max_reasks`` times without consuming the tool budget.

Validation is intentionally dependency-free (no jsonschema) so the harness runs
anywhere, including CI without extra installs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from frontier_runtime.harness.model_profiles import ModelCapabilityProfile

_PY_TYPES = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def schema_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tool in tools:
        fn = tool.get("function") or {}
        name = fn.get("name")
        if name:
            out[name] = fn.get("parameters") or {}
    return out


def validate_tool_call(
    name: str, raw_arguments: Any, schemas: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any] | None, str]:
    """Validate a tool call's envelope. Returns (args, "") or (None, reason)."""
    import json

    if name not in schemas:
        return None, f"unknown tool '{name}'"

    if isinstance(raw_arguments, dict):
        args = raw_arguments
    elif raw_arguments in (None, ""):
        args = {}
    else:
        try:
            args = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            return None, f"arguments are not valid JSON: {exc}"
        if not isinstance(args, dict):
            return None, "arguments must be a JSON object"

    schema = schemas[name]
    required = schema.get("required") or []
    for key in required:
        if key not in args or args[key] in (None, ""):
            return None, f"missing required argument '{key}'"

    props = schema.get("properties") or {}
    for key, value in args.items():
        spec = props.get(key)
        if not spec:
            continue
        expected = spec.get("type")
        py = _PY_TYPES.get(expected) if isinstance(expected, str) else None
        if py is not None and value is not None and not isinstance(value, py):
            # tolerate int<->float and stringified ints from weak models
            if expected == "integer" and isinstance(value, str) and value.lstrip("-").isdigit():
                args[key] = int(value)
                continue
            if expected == "number" and isinstance(value, bool):
                return None, f"argument '{key}' should be {expected}"
            if not isinstance(value, py):
                return None, f"argument '{key}' should be {expected}, got {type(value).__name__}"
    return args, ""


@dataclass
class ReaskPolicy:
    max_reasks: int = 2
    max_reasks_per_run: int = 8


def reask_tool_message(tool_call_id: str, name: str, reason: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": (
            f"[invalid tool call to '{name}': {reason}] Re-issue the call with corrected arguments."
        ),
    }


def constraint_kwargs(
    provider: str, profile: ModelCapabilityProfile, tools: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Request kwargs that enforce structurally-valid tool calls on the backend.

    Isolated here because vLLM's structured-output API keys have churned across
    releases; override via the per-provider ``structured_output`` profile field.
    """
    if not tools or profile.structured_output == "none":
        return {}
    mode = profile.structured_output
    if mode == "xgrammar":
        # vLLM: guided decoding backend selects XGrammar; tools=... already
        # carries the JSON schema vLLM compiles a grammar from.
        return {"extra_body": {"guided_decoding_backend": "xgrammar"}}
    if mode == "gbnf":
        # llama.cpp llama-server compiles json_schema -> GBNF internally.
        return {"extra_body": {"guided_decoding_backend": "outlines"}}
    if mode == "json_schema":
        # Generic OpenAI-compatible structured outputs; backend-dependent.
        return {}
    return {}
