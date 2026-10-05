"""Does a change widen what the agents may do? (LOCUS-357)

Pure predicates used by the desktop shell-proof check
(``request_security.shell_proof_rules()``): on the desktop profile a request
that widens capability needs the Tauri shell's out-of-band confirmation;
one that only narrows (or changes nothing security-relevant) does not.

Two kinds:

* **body predicates** decide from the request body alone, before the handler
  runs (``approval_decision_approves``, ``schedule_enabled``, ...). Where the
  body leaves the outcome open they answer "widening" (fail closed).
* **state predicates** compare the stored value with the value the handler is
  about to store (``is_widening`` for platform settings, user settings, user
  skills). The handler evaluates them right before it commits.

Every field of :class:`PlatformSettings` is classified in exactly one of the
tables below; a test fails when a new field is left out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

# --------------------------------------------------------------------------- #
# Platform settings
# --------------------------------------------------------------------------- #
#: Booleans that restrict when True: turning one off widens.
RESTRICTIVE_FLAGS: frozenset[str] = frozenset(
    {
        "local_only_mode",
        "mask_secrets_in_events",
        "require_human_approval",
        "enforce_egress_allowlist",
        "enforce_local_network_only",
        "mcp_require_local_server",
        "retrieval_require_local_source_url",
        "a2a_require_signed_messages",
        "a2a_replay_protection",
        "require_human_approval_for_high_risk_tools",
        "enforce_integration_policies",
        "require_signed_integrations",
        "require_sandbox_for_third_party",
        "enforce_runtime_engine_allowlist",
        "enable_foss_guardrail_signals",
        "foss_guardrail_detect_prompt_injection",
        "foss_guardrail_detect_pii",
        "foss_guardrail_detect_command_injection",
        "foss_guardrail_detect_exfiltration",
        "emergency_read_only_mode",
        "block_new_runs",
        "block_graph_runs",
        "block_tool_calls",
        "block_retrieval_calls",
        "require_authenticated_requests",
        "require_a2a_runtime_headers",
    }
)
#: Booleans that permit when True: turning one on widens.
PERMISSIVE_FLAGS: frozenset[str] = frozenset(
    {
        "allow_local_unsigned_integrations",
        "allow_runtime_engine_override",
        # Telemetry (LOCUS-375): content in spans; spans sent off the machine.
        "telemetry_capture_content",
        "telemetry_otlp_enabled",
        "telemetry_langsmith_enabled",
    }
)
#: Allowlists: any new entry widens.
ALLOWLISTS: frozenset[str] = frozenset(
    {
        "allowed_egress_hosts",
        "allowed_mcp_server_urls",
        "allowed_retrieval_sources",
        "allow_local_network_hostnames",
        "a2a_trusted_subjects",
        "allowed_runtime_engines",
        "tenant_scoped_skills",
    }
)
#: Blocklists: any removed entry widens.
BLOCKLISTS: frozenset[str] = frozenset({"global_blocked_keywords", "high_risk_tool_patterns"})
#: Limits: raising one widens.
LIMITS: frozenset[str] = frozenset(
    {
        "collaboration_max_agents",
        "max_tool_calls_per_run",
        "max_retrieval_items",
        # Keeping captured content longer (LOCUS-375).
        "telemetry_payload_retention_days",
    }
)
#: Where data is sent, and with which credential (LOCUS-375 telemetry exporters):
#: a new non-empty value widens (a new destination or account); clearing narrows.
DESTINATIONS: frozenset[str] = frozenset(
    {
        "telemetry_otlp_endpoint",
        "telemetry_otlp_auth_secret_ref",
        "telemetry_langsmith_endpoint",
        "telemetry_langsmith_api_key_ref",
    }
)
#: Settings that cannot be ranked (a different guardrail ruleset, runtime
#: engine or strategy, a model endpoint): any change widens.
UNRANKED: frozenset[str] = frozenset(
    {
        "default_guardrail_ruleset_id",
        "default_runtime_engine",
        "default_runtime_strategy",
        "default_hybrid_runtime_routing",
        "nim_base_url",
        "ollama_base_url",
    }
)
#: Ranked guardrail enforcement: lowering it widens.
SIGNAL_ENFORCEMENT_RANK: Mapping[str, int] = {
    "off": 0,
    "audit": 1,
    "block_high": 2,
    "raise_high": 3,
}
#: Fields with no security effect (branding, UI defaults, model names).
NEUTRAL: frozenset[str] = frozenset(
    {
        "org_name",
        "org_slug",
        "support_email",
        "website",
        "console_classification_banner_enabled",
        "console_classification_banner_text",
        "console_classification_banner_background_color",
        "console_classification_banner_text_color",
        "default_kickoff_workflow",
        "preferred_review_depth",
        "idle_timeout",
        "openai_model",
        "openai_fallback_model",
        "nim_default_model",
        "ollama_default_model",
        # Write-only key fields: the handler blanks them before they reach the
        # settings; key writes are judged by ``provider_key_writes_widen``.
        "openai_api_key",
        "nim_api_key",
        # Same destination and account: a LangSmith project is a label there.
        "telemetry_langsmith_project",
    }
)
#: Handled field by field below.
SPECIAL: frozenset[str] = frozenset({"foss_guardrail_signal_enforcement", "ai_providers"})


def classified_platform_fields() -> frozenset[str]:
    return (
        RESTRICTIVE_FLAGS
        | PERMISSIVE_FLAGS
        | ALLOWLISTS
        | BLOCKLISTS
        | LIMITS
        | DESTINATIONS
        | UNRANKED
        | NEUTRAL
        | SPECIAL
    )


def _items(value: Any) -> set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable) or isinstance(value, Mapping):
        return set()
    return {str(item).strip().lower() for item in value if str(item).strip()}


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _signal_rank(value: Any) -> int:
    # Unknown values are served as "block_high" (main._evaluate_guardrail).
    return SIGNAL_ENFORCEMENT_RANK.get(str(value or "").strip().lower(), 2)


def _provider_endpoints(value: Any) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    if isinstance(value, Mapping):
        for provider, entry in value.items():
            if isinstance(entry, Mapping):
                endpoints[str(provider).strip().lower()] = str(entry.get("base_url") or "").strip()
    return endpoints


def widening_fields(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """The platform-settings fields whose change from ``old`` to ``new`` widens.

    Fields outside the tables count as widening when they change (fail closed).
    """
    widened: list[str] = []
    known = classified_platform_fields()
    for field in sorted(set(old) | set(new)):
        before, after = old.get(field), new.get(field)
        if field in NEUTRAL:
            continue
        if field in RESTRICTIVE_FLAGS:
            changed = bool(before) and not bool(after)
        elif field in PERMISSIVE_FLAGS:
            changed = bool(after) and not bool(before)
        elif field in ALLOWLISTS:
            changed = bool(_items(after) - _items(before))
        elif field in BLOCKLISTS:
            changed = bool(_items(before) - _items(after))
        elif field in LIMITS:
            changed = _int(after) > _int(before)
        elif field in DESTINATIONS:
            new_value = str(after or "").strip()
            changed = bool(new_value) and new_value != str(before or "").strip()
        elif field == "foss_guardrail_signal_enforcement":
            changed = _signal_rank(after) < _signal_rank(before)
        elif field == "ai_providers":
            # A new or changed model endpoint sends prompts somewhere new; a new
            # default model for a configured provider does not.
            old_endpoints = _provider_endpoints(before)
            changed = any(
                url and old_endpoints.get(provider) != url
                for provider, url in _provider_endpoints(after).items()
            )
        elif field in UNRANKED or field not in known:
            changed = before != after
        else:  # pragma: no cover - every table is handled above
            changed = before != after
        if changed:
            widened.append(field)
    return widened


#: Every telemetry setting (LOCUS-375); a widening change to one also needs
#: ``confirm_security_change`` on every profile (desktop: the shell proof too).
TELEMETRY_FIELDS: frozenset[str] = frozenset(
    field for field in classified_platform_fields() if field.startswith("telemetry_")
)


def telemetry_widening_fields(old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    """The telemetry settings whose change widens: content capture on, an exporter
    enabled, a new endpoint or credential, a longer payload retention. Disabling an
    exporter, clearing an endpoint or shortening retention narrows."""
    return [field for field in widening_fields(old, new) if field in TELEMETRY_FIELDS]


def is_widening(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """Whether replacing platform settings ``old`` with ``new`` widens anything:
    an allowlist grows, a blocklist shrinks, a restrictive flag is turned off or
    a permissive one on, a limit is raised, guardrail enforcement is lowered,
    or an unranked setting (guardrail ruleset, runtime, model endpoint) changes."""
    return bool(widening_fields(old, new))


def provider_key_writes_widen(key_writes: Mapping[str, str], clear_sentinel: str) -> bool:
    """Storing a provider key widens; clearing one narrows."""
    return any(str(value) != clear_sentinel for value in key_writes.values())


# --------------------------------------------------------------------------- #
# Per-user settings (PUT /user/settings)
# --------------------------------------------------------------------------- #
#: The chat modes, least to most capable. ``default_mode`` is the mode new
#: chats start in; raising it widens what the agent does without the human
#: switching modes. The other fields (default working folder, preferred model,
#: reasoning effort) are UI defaults: every run states its own workspace and
#: model, and the gateway checks each action, so they do not widen.
CHAT_MODE_RANK: Mapping[str, int] = {"chat": 0, "plan": 1, "execute": 2}


def user_settings_widening(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    def rank(settings: Mapping[str, Any]) -> int:
        return CHAT_MODE_RANK.get(str(settings.get("default_mode") or "execute"), 2)

    return rank(new) > rank(old)


# --------------------------------------------------------------------------- #
# Per-user skills (PUT /skills/user): adding a skill installs it.
# --------------------------------------------------------------------------- #
def user_skills_widening(old: Iterable[str], new: Iterable[str]) -> bool:
    return bool({str(s) for s in new} - {str(s) for s in old})


# --------------------------------------------------------------------------- #
# Body predicates (decided before the handler runs)
# --------------------------------------------------------------------------- #
def approval_decision_approves(body: Mapping[str, Any]) -> bool:
    """``POST /approvals``: approving widens; requesting changes does not."""
    return str(body.get("decision") or "").strip().lower() == "approved"


def schedule_enabled(body: Mapping[str, Any]) -> bool:
    """``POST /workflow-definitions/{id}/schedules``: an enabled schedule (the
    default) widens; a schedule created disabled does not."""
    return bool(body.get("enabled", True))


def schedule_toggle_enables(body: Mapping[str, Any]) -> bool:
    """``POST /schedules/{id}/toggle``: turning on widens. Without an explicit
    ``enabled`` the outcome depends on the stored state, so it counts as on."""
    return bool(body["enabled"]) if "enabled" in body else True


def skill_save_enables(body: Mapping[str, Any]) -> bool:
    """``POST /skills``: unless the body disables the skill, the saved skill may
    be enabled (a new skill defaults to enabled), which widens."""
    return str(body.get("status") or "").strip() != "disabled"


BODY_PREDICATES: Mapping[str, Callable[[Mapping[str, Any]], bool]] = {
    "approval_decision_approves": approval_decision_approves,
    "schedule_enabled": schedule_enabled,
    "schedule_toggle_enables": schedule_toggle_enables,
    "skill_save_enables": skill_save_enables,
}

#: Evaluated by the handler right before it commits (they need stored state).
STATE_PREDICATES: frozenset[str] = frozenset(
    {
        "platform_settings_widening",
        "user_settings_widening",
        "user_skills_widening",
        "browser_tier_widening",
    }
)


__all__ = [
    "BODY_PREDICATES",
    "CHAT_MODE_RANK",
    "STATE_PREDICATES",
    "TELEMETRY_FIELDS",
    "approval_decision_approves",
    "classified_platform_fields",
    "is_widening",
    "provider_key_writes_widen",
    "schedule_enabled",
    "schedule_toggle_enables",
    "skill_save_enables",
    "telemetry_widening_fields",
    "user_settings_widening",
    "user_skills_widening",
    "widening_fields",
]
