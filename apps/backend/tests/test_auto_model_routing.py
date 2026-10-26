"""LOCUS-479: Auto routes only to real, policy-eligible local chat models."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auto_model_routing import (
    chat_capable_model,
    plan_local_auto,
    valid_response_body,
)


INSTALLED = [
    {"id": "gpt-oss-t3:latest"},
    {"id": "gpt-oss:20b"},
    {"id": "gpt-oss:20b-ctx32k"},
    {"id": "hf.co/yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF:Q4_K_M"},
    {"id": "nomic-embed-text:latest"},
]


def test_auto_skips_stale_uninstalled_preferred_model() -> None:
    plan = plan_local_auto(
        INSTALLED, preferred="llama3.2:3b", prompt="Analyze a secure distributed architecture."
    )
    assert plan is not None
    assert plan.selected.startswith("ollama/gpt-oss:20b")
    assert all("nomic-embed-text" not in model for model in plan.candidates)
    assert "ollama/llama3.2:3b" not in plan.candidates
    assert plan.task_class == "analysis"
    assert plan.considered_count == 4


def test_auto_does_not_guess_an_openai_model_without_local_inventory() -> None:
    assert plan_local_auto([], prompt="Write a Rust program.") is None


def test_auto_applies_policy_model_restriction_before_ranking() -> None:
    plan = plan_local_auto(
        INSTALLED,
        prompt="Implement this function.",
        allowed_models=["ollama/gpt-oss-t3:latest"],
    )
    assert plan is not None
    assert plan.candidates == ("ollama/gpt-oss-t3:latest",)
    assert plan_local_auto(
        INSTALLED,
        allowed_models=["openai/gpt-5.2"],
    ) is None


def test_explicit_non_chat_capability_and_embedding_models_cannot_be_auto_selected() -> None:
    assert not chat_capable_model("nomic-embed-text:latest")
    assert not chat_capable_model("new-embedding-model", ["embedding"])
    assert chat_capable_model("gpt-oss:20b", ["completion", "tools"])
    plan = plan_local_auto([
        {"id": "gpt-oss:20b", "capabilities": ["embedding"]},
        {"id": "nomic-embed-text:latest"},
    ])
    assert plan is None


def test_auto_never_implicitly_uses_unprofiled_custom_hf_finetune() -> None:
    plan = plan_local_auto([
        {"id": "hf.co/unknown-company/uncalibrated-finetune:Q4_K_M"},
    ])
    assert plan is None


def test_output_minimum_validator_detects_empty_only() -> None:
    assert not valid_response_body("")
    assert not valid_response_body(" \n  ")
    assert valid_response_body("The answer requires review.")
