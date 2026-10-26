"""LOCUS-479: enforce a real local Auto route in installed-facing API flows."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generated_artifacts import AUTH_HEADERS, client, main_module, store
from test_provider_unavailable import _cleanup_run


INSTALLED = [
    {"id": "gpt-oss:20b-ctx32k"},
    {"id": "gpt-oss:20b"},
    {"id": "nomic-embed-text:latest"},
]


@pytest.fixture()
def fake_installed_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.local_models, "ollama_available", lambda: True)
    monkeypatch.setattr(main_module.local_models, "installed_models", lambda: list(INSTALLED))
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")


def test_auto_selects_installed_ollama_never_unconfigured_openai(
    monkeypatch: pytest.MonkeyPatch, fake_installed_ollama: None
) -> None:
    observed: dict[str, Any] = {}

    def fake_iterations(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        observed["model"] = kwargs["model"]
        observed["chain"] = kwargs.get("auto_local_chain")
        return "LOCAL_OK", {"mode": "live", "provider": "ollama", "model": "gpt-oss:20b-ctx32k"}

    monkeypatch.setattr(main_module, "_run_agent_iterations", fake_iterations)
    response = client.post(
        "/workflow-runs",
        json={"title": "Auto local test", "prompt": "Assess distributed security architecture.",
              "model": "auto"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json()["id"])
    try:
        assert observed["model"].startswith("ollama/gpt-oss:20b")
        assert len(observed["chain"]) >= 1
        detail = store.run_details[run_id]
        assert detail["model_selection"]["mode"] == "auto"
        assert detail["model_selection"]["scope"] == "local-only"
        assert detail["runtime"]["provider"] == "ollama"
        assert not any("openai/" in m for m in detail["model_selection"]["candidates"])
    finally:
        _cleanup_run(run_id)


def test_auto_rejects_no_installed_chat_models_before_creating_run(
    monkeypatch: pytest.MonkeyPatch, fake_installed_ollama: None
) -> None:
    monkeypatch.setattr(main_module.local_models, "installed_models", lambda: [
        {"id": "nomic-embed-text:latest"},
    ])
    before = len(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"title": "Denied", "prompt": "Summarize", "model": "auto"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "auto_no_eligible_model"
    assert len(store.runs) == before


def test_auto_denies_cross_provider_policy_without_cloud_egress(
    fake_installed_ollama: None,
) -> None:
    before = len(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"title": "Denied", "prompt": "Summarize", "model": "auto",
              "runtime_policy": {"allowed_providers": ["openai"]}},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403
    assert len(store.runs) == before


def test_auto_local_fallback_after_empty_response_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    transitions: list[Any] = []

    def fake_chat(*, model: str, **_kwargs: Any) -> tuple[str, dict[str, Any]]:
        attempts.append(model)
        if len(attempts) == 1:
            return "", {"mode": "live", "provider": "ollama", "model": model}
        return "Recovered response", {"mode": "live", "provider": "ollama", "model": model}

    monkeypatch.setattr(main_module, "_run_openai_chat", fake_chat)
    monkeypatch.setattr(main_module, "_record_model_fallback", transitions.append)
    chain = [
        main_module.model_calls.ModelTier("ollama", "gpt-oss:20b-ctx32k"),
        main_module.model_calls.ModelTier("ollama", "gpt-oss:20b"),
    ]
    response, metadata = main_module._run_model_chain_chat(
        chain, system_prompt="", user_prompt="Hello", temperature=0.2
    )
    assert response == "Recovered response"
    assert attempts == ["ollama/gpt-oss:20b-ctx32k", "ollama/gpt-oss:20b"]
    assert len(transitions) == 1
    assert transitions[0].reason_code == "empty_model_output"
    assert metadata["tier_fallback_used"] is True


def test_auto_does_not_retry_calls_with_tools_after_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_chat(*, model: str, **_kwargs: Any) -> tuple[str, dict[str, Any]]:
        calls.append(model)
        return "", {"mode": "live", "provider": "ollama", "model": model}

    monkeypatch.setattr(main_module, "_run_openai_chat", fake_chat)
    chain = [
        main_module.model_calls.ModelTier("ollama", "gpt-oss:20b"),
        main_module.model_calls.ModelTier("ollama", "gpt-oss:20b-ctx32k"),
    ]
    result, _meta = main_module._run_model_chain_chat(
        chain, system_prompt="", user_prompt="Please run a tool",
        temperature=0.2, tools=[{"type": "function", "function": {"name": "test"}}],
    )
    assert result == ""
    assert len(calls) == 1
