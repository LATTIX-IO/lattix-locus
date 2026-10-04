"""LOCUS-357: pure predicates deciding whether a change widens capability."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"

from app import capability_widening as cw

DEFAULTS: dict[str, Any] = {
    "require_human_approval": False,
    "require_human_approval_for_high_risk_tools": True,
    "enforce_egress_allowlist": True,
    "allowed_egress_hosts": ["localhost", "127.0.0.1"],
    "allowed_mcp_server_urls": ["http://localhost:7071/mcp"],
    "global_blocked_keywords": ["secret"],
    "high_risk_tool_patterns": ["delete", "send"],
    "max_tool_calls_per_run": 20,
    "allow_runtime_engine_override": False,
    "foss_guardrail_signal_enforcement": "block_high",
    "default_guardrail_ruleset_id": "gr-1",
    "default_runtime_engine": "native",
    "ai_providers": {"openai": {"base_url": "", "default_model": "gpt-a"}},
    "org_name": "Lattix Locus",
    "openai_model": "gpt-a",
}


def _with(**changes: Any) -> dict[str, Any]:
    return {**DEFAULTS, **changes}


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        (
            {"allowed_egress_hosts": ["localhost", "127.0.0.1", "evil.example"]},
            "allowed_egress_hosts",
        ),
        ({"allowed_mcp_server_urls": ["http://localhost:7071/mcp", "https://x/mcp"]}, None),
        ({"require_human_approval_for_high_risk_tools": False}, None),
        ({"enforce_egress_allowlist": False}, None),
        ({"global_blocked_keywords": []}, "global_blocked_keywords"),
        ({"high_risk_tool_patterns": ["delete"]}, None),
        ({"max_tool_calls_per_run": 21}, "max_tool_calls_per_run"),
        ({"allow_runtime_engine_override": True}, None),
        ({"foss_guardrail_signal_enforcement": "audit"}, None),
        ({"foss_guardrail_signal_enforcement": "off"}, None),
        ({"default_guardrail_ruleset_id": "gr-2"}, None),
        ({"default_guardrail_ruleset_id": None}, None),
        ({"default_runtime_engine": "langgraph"}, None),
        ({"ai_providers": {"openai": {"base_url": "https://proxy.example/v1"}}}, "ai_providers"),
        ({"ai_providers": {**DEFAULTS["ai_providers"], "nim": {"base_url": "https://n/v1"}}}, None),
        ({"brand_new_setting": True}, "brand_new_setting"),  # unknown fields fail closed
    ],
)
def test_widening_changes(changes: dict[str, Any], field: str | None) -> None:
    new = _with(**changes)
    assert cw.is_widening(DEFAULTS, new)
    if field is not None:
        assert field in cw.widening_fields(DEFAULTS, new)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"allowed_egress_hosts": ["localhost"]},
        {"allowed_egress_hosts": ["LOCALHOST", "127.0.0.1"]},  # normalised, same set
        {"require_human_approval": True},
        {"global_blocked_keywords": ["secret", "token"]},
        {"high_risk_tool_patterns": ["delete", "send", "pay"]},
        {"max_tool_calls_per_run": 5},
        {"foss_guardrail_signal_enforcement": "raise_high"},
        {"ai_providers": {"openai": {"base_url": "", "default_model": "gpt-b"}}},
        {"org_name": "Renamed", "openai_model": "gpt-b"},
        {"openai_api_key": ""},
    ],
)
def test_narrowing_or_neutral_changes(changes: dict[str, Any]) -> None:
    assert not cw.is_widening(DEFAULTS, _with(**changes)), cw.widening_fields(
        DEFAULTS, _with(**changes)
    )


def test_mixed_change_is_widening() -> None:
    new = _with(allowed_egress_hosts=["localhost"], max_tool_calls_per_run=99)
    assert cw.widening_fields(DEFAULTS, new) == ["max_tool_calls_per_run"]


def test_unknown_signal_enforcement_counts_as_block_high() -> None:
    assert not cw.is_widening(DEFAULTS, _with(foss_guardrail_signal_enforcement="bogus"))
    assert cw.is_widening(
        _with(foss_guardrail_signal_enforcement="raise_high"),
        _with(foss_guardrail_signal_enforcement="bogus"),
    )


def test_every_platform_setting_is_classified_exactly_once() -> None:
    from app.main import PlatformSettings

    tables = [
        cw.RESTRICTIVE_FLAGS,
        cw.PERMISSIVE_FLAGS,
        cw.ALLOWLISTS,
        cw.BLOCKLISTS,
        cw.LIMITS,
        cw.UNRANKED,
        cw.NEUTRAL,
        cw.SPECIAL,
    ]
    fields = set(PlatformSettings.model_fields)
    assert fields == cw.classified_platform_fields(), fields ^ cw.classified_platform_fields()
    assert sum(len(t) for t in tables) == len(fields), "a field is in two tables"


def test_provider_key_writes() -> None:
    assert cw.provider_key_writes_widen({"openai": "sk-x"}, "__clear__")
    assert not cw.provider_key_writes_widen({"openai": "__clear__"}, "__clear__")
    assert not cw.provider_key_writes_widen({}, "__clear__")


def test_user_settings_only_a_higher_default_mode_widens() -> None:
    base = {"default_mode": "chat", "preferred_model": "a", "default_working_folder": "x"}
    assert cw.user_settings_widening(base, {**base, "default_mode": "plan"})
    assert cw.user_settings_widening(base, {**base, "default_mode": "execute"})
    assert not cw.user_settings_widening({**base, "default_mode": "execute"}, base)
    assert not cw.user_settings_widening(
        base, {**base, "preferred_model": "b", "default_working_folder": "/projects/y"}
    )
    assert not cw.user_settings_widening({}, {"default_mode": "execute"})  # default is execute


def test_user_skills_adding_widens() -> None:
    assert cw.user_skills_widening(["/a"], ["/a", "/b"])
    assert not cw.user_skills_widening(["/a", "/b"], ["/a"])
    assert not cw.user_skills_widening([], [])


@pytest.mark.parametrize(
    ("name", "body", "widening"),
    [
        ("approval_decision_approves", {"decision": "approved"}, True),
        ("approval_decision_approves", {"decision": " Approved "}, True),
        ("approval_decision_approves", {"decision": "changes_requested"}, False),
        ("approval_decision_approves", {}, False),
        ("schedule_enabled", {}, True),
        ("schedule_enabled", {"enabled": True}, True),
        ("schedule_enabled", {"enabled": False}, False),
        ("schedule_toggle_enables", {}, True),
        ("schedule_toggle_enables", {"enabled": True}, True),
        ("schedule_toggle_enables", {"enabled": False}, False),
        ("skill_save_enables", {}, True),
        ("skill_save_enables", {"status": "enabled"}, True),
        ("skill_save_enables", {"status": "bogus"}, True),
        ("skill_save_enables", {"status": "disabled"}, False),
    ],
)
def test_body_predicates(name: str, body: dict[str, Any], widening: bool) -> None:
    assert cw.BODY_PREDICATES[name](body) is widening
