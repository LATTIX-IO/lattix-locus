"""Per-model capability profiles.

Different models need different harness behaviour to perform well:

* **edit_format** — search-replace (capable), whole-file (weak), apply_patch
  (gpt-oss, trained on it).
* **tool_protocol** — native-fc (OpenAI tool calling), harmony (gpt-oss native
  channels), xml (Cline/Roo-style), bash-only (mini-SWE-agent, parsed from
  plain completions; works with *any* model).
* **max_effective_context** — DeepSWE found a 32K plateau for 32B models;
  feeding more clean context than the model uses wastes prefill.
* sampler defaults + structured-output backend for grammar-constrained calls.

Resolution: agent/explicit overrides > platform settings > builtin patterns >
``locus-default``. First matching fnmatch pattern wins.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import os
from dataclasses import dataclass
from typing import Any, Literal

EditFormat = Literal["search-replace", "whole-file", "apply_patch"]
ToolProtocol = Literal["native-fc", "harmony", "xml", "bash-only"]
StructuredOutput = Literal["none", "json_schema", "xgrammar", "gbnf"]


@dataclass(frozen=True)
class ModelCapabilityProfile:
    profile_id: str = "locus-default"
    edit_format: EditFormat = "search-replace"
    tool_protocol: ToolProtocol = "native-fc"
    max_effective_context: int = 32_768
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str = ""  # "", "low", "medium", "high"
    structured_output: StructuredOutput = "none"
    tool_defs_in_system: bool = False

    def with_overrides(self, overrides: dict[str, Any] | None) -> "ModelCapabilityProfile":
        if not overrides:
            return self
        valid = {f.name for f in dataclasses.fields(self)}
        clean = {k: v for k, v in overrides.items() if k in valid and v is not None}
        if not clean:
            return self
        return dataclasses.replace(self, **clean)


def _env_context(name: str, default: int) -> int:
    """Context size from the environment (bounded), else ``default``."""
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return max(4_096, min(value, 2_000_000))


BUILTIN_PROFILES: dict[str, ModelCapabilityProfile] = {
    "locus-default": ModelCapabilityProfile(
        profile_id="locus-default",
        edit_format="search-replace",
        tool_protocol="native-fc",
        max_effective_context=128_000,
    ),
    "gpt-oss-harmony": ModelCapabilityProfile(
        profile_id="gpt-oss-harmony",
        edit_format="apply_patch",
        tool_protocol="harmony",
        max_effective_context=131_072,
        temperature=1.0,
        top_p=1.0,
        reasoning_effort="medium",
        structured_output="none",
        tool_defs_in_system=True,
    ),
    # gpt-oss when harmony serving is unavailable: native FC with grammar
    # constraints is the next-best path and still works on vLLM/llama.cpp.
    "gpt-oss-native": ModelCapabilityProfile(
        profile_id="gpt-oss-native",
        edit_format="search-replace",
        tool_protocol="native-fc",
        max_effective_context=131_072,
        temperature=1.0,
        top_p=1.0,
        reasoning_effort="medium",
        structured_output="xgrammar",
    ),
    "local-32b-class": ModelCapabilityProfile(
        profile_id="local-32b-class",
        edit_format="search-replace",
        tool_protocol="native-fc",
        max_effective_context=32_768,
        temperature=0.2,
        top_p=0.95,
        structured_output="json_schema",
    ),
    "local-weak": ModelCapabilityProfile(
        profile_id="local-weak",
        edit_format="whole-file",
        tool_protocol="native-fc",
        max_effective_context=16_384,
        temperature=0.0,
        structured_output="json_schema",
    ),
    # Hosted NVIDIA NIM (D-21): OpenAI tool calling on the API catalog. The
    # effective context is configurable (LOCUS_NIM_CONTEXT_TOKENS) because it
    # differs per catalog model; 128K matches the Llama 3.x 70B instruct models.
    "nim-hosted": ModelCapabilityProfile(
        profile_id="nim-hosted",
        edit_format="search-replace",
        tool_protocol="native-fc",
        max_effective_context=_env_context("LOCUS_NIM_CONTEXT_TOKENS", 131_072),
        temperature=0.2,
        top_p=0.95,
        structured_output="json_schema",
    ),
    # gpt-oss served by NIM speaks OpenAI tools, not raw harmony channels.
    "nim-gpt-oss": ModelCapabilityProfile(
        profile_id="nim-gpt-oss",
        edit_format="apply_patch",
        tool_protocol="native-fc",
        max_effective_context=_env_context("LOCUS_NIM_CONTEXT_TOKENS", 131_072),
        temperature=1.0,
        top_p=1.0,
        reasoning_effort="medium",
        structured_output="json_schema",
    ),
    # bash-only mini-SWE-agent scaffold: no tool-calling API at all; works with
    # any model/endpoint that can complete text. Most robust fallback.
    "bash-only": ModelCapabilityProfile(
        profile_id="bash-only",
        edit_format="whole-file",
        tool_protocol="bash-only",
        max_effective_context=32_768,
        temperature=0.0,
    ),
}

# (provider/bare_model glob, profile_id) — first match wins.
PROFILE_PATTERNS: list[tuple[str, str]] = [
    ("nim/*gpt-oss*", "nim-gpt-oss"),
    ("nim/*", "nim-hosted"),
    ("*/gpt-oss*", "gpt-oss-harmony"),
    ("vllm/*", "local-32b-class"),
    ("llamacpp/*", "local-32b-class"),
    ("lmstudio/*", "local-32b-class"),
    ("ollama/*", "local-weak"),
]


def resolve_profile(
    provider: str,
    bare_model: str,
    *,
    overrides: dict[str, Any] | None = None,
    profile_id: str | None = None,
    platform_patterns: list[tuple[str, str]] | None = None,
) -> ModelCapabilityProfile:
    """Resolve the capability profile for a (provider, model) pair.

    ``profile_id`` forces a builtin profile; ``overrides`` apply field-level
    tweaks on top of whatever profile is resolved.
    """
    if profile_id and profile_id in BUILTIN_PROFILES:
        return BUILTIN_PROFILES[profile_id].with_overrides(overrides)

    key = f"{provider}/{bare_model}".lower()
    patterns = list(platform_patterns or []) + PROFILE_PATTERNS
    for pattern, pid in patterns:
        if fnmatch.fnmatch(key, pattern.lower()) and pid in BUILTIN_PROFILES:
            return BUILTIN_PROFILES[pid].with_overrides(overrides)

    return BUILTIN_PROFILES["locus-default"].with_overrides(overrides)
