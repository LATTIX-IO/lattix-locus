"""Unit tests for harness building blocks (no network, no model)."""

from __future__ import annotations

from frontier_runtime.harness.enforcement import (
    ReaskPolicy,
    constraint_kwargs,
    schema_by_name,
    validate_tool_call,
)
from frontier_runtime.harness.model_profiles import BUILTIN_PROFILES, resolve_profile
from frontier_runtime.harness.tools import CodingTelemetry, tool_schemas, truncate_output
from frontier_runtime.harness.trajectory import TrajectoryRecorder


# -- trajectory -------------------------------------------------------------
def test_trajectory_roundtrip_reconstructs_messages():
    rec = TrajectoryRecorder(run_id="r1")
    rec.header(
        agent_id="a",
        model="m",
        provider="p",
        sampler={"temperature": 0.2},
        budgets={"max_steps": 5},
        system_prompt="sys",
        task={"instance_id": "i1"},
    )
    rec.message({"role": "system", "content": "sys"}, step=0)
    rec.message({"role": "user", "content": "do it"}, step=0)
    rec.message({"role": "assistant", "content": "ok"}, step=1, usage={"completion_tokens": 3})
    rec.outcome("submitted", submission={"patch": "diff"}, steps=1, budgets_used={"steps": 1})

    parsed = TrajectoryRecorder.parse(rec.to_jsonl())
    assert parsed[0]["kind"] == "meta"
    seqs = [r["seq"] for r in parsed]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    msgs = rec.messages()
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]
    assert rec.final_outcome()["status"] == "submitted"


def test_trajectory_writes_file(tmp_path):
    path = tmp_path / "t.jsonl"
    rec = TrajectoryRecorder(run_id="r", file_path=path)
    rec.message({"role": "user", "content": "hi"}, step=0)
    assert path.exists()
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 1


# -- model profiles ---------------------------------------------------------
def test_profile_pattern_resolution():
    assert resolve_profile("vllm", "gpt-oss-20b").profile_id == "gpt-oss-harmony"
    assert resolve_profile("vllm", "qwen3-coder").profile_id == "local-32b-class"
    assert resolve_profile("ollama", "llama3").profile_id == "local-weak"
    assert resolve_profile("anthropic", "claude").profile_id == "frontier-default"


def test_profile_forced_and_overrides():
    p = resolve_profile(
        "vllm", "x", profile_id="local-weak", overrides={"edit_format": "search-replace"}
    )
    assert p.profile_id == "local-weak"
    assert p.edit_format == "search-replace"  # override wins
    # frozen dataclass: override produces a new instance
    assert BUILTIN_PROFILES["local-weak"].edit_format == "whole-file"


# -- truncation -------------------------------------------------------------
def test_truncate_by_lines():
    text = "\n".join(str(i) for i in range(5000))
    out, truncated = truncate_output(text, max_lines=100, max_bytes=10_000_000)
    assert truncated
    assert "lines elided" in out
    assert len(out.splitlines()) <= 102


def test_truncate_passthrough():
    out, truncated = truncate_output("small")
    assert not truncated and out == "small"


# -- telemetry --------------------------------------------------------------
def test_telemetry_rates_with_zero_denominator():
    t = CodingTelemetry()
    assert t.well_formed_edit_rate() == 1.0
    assert t.well_formed_call_rate() == 1.0
    t.edits_attempted = 4
    t.edits_well_formed = 3
    assert t.well_formed_edit_rate() == 0.75
    t.tool_calls_total = 2
    t.tool_calls_malformed = 2
    assert t.well_formed_call_rate() == 0.5


# -- enforcement ------------------------------------------------------------
def test_validate_tool_call_accepts_and_rejects():
    schemas = schema_by_name(tool_schemas())
    args, reason = validate_tool_call("execute_bash", '{"command": "ls"}', schemas)
    assert args == {"command": "ls"} and reason == ""

    args, reason = validate_tool_call("execute_bash", "{not json", schemas)
    assert args is None and "JSON" in reason

    args, reason = validate_tool_call("execute_bash", "{}", schemas)
    assert args is None and "command" in reason  # required missing

    args, reason = validate_tool_call("nope", "{}", schemas)
    assert args is None and "unknown tool" in reason


def test_validate_coerces_stringified_int():
    schemas = schema_by_name(tool_schemas())
    args, reason = validate_tool_call("execute_bash", '{"command": "ls", "timeout": "30"}', schemas)
    assert reason == "" and args["timeout"] == 30


def test_constraint_kwargs_by_backend():
    from frontier_runtime.harness.model_profiles import ModelCapabilityProfile

    tools = tool_schemas()
    xg = ModelCapabilityProfile(structured_output="xgrammar")
    assert (
        constraint_kwargs("vllm", xg, tools)["extra_body"]["guided_decoding_backend"] == "xgrammar"
    )
    none = ModelCapabilityProfile(structured_output="none")
    assert constraint_kwargs("vllm", none, tools) == {}


def test_reask_policy_defaults():
    p = ReaskPolicy()
    assert p.max_reasks == 2 and p.max_reasks_per_run == 8
