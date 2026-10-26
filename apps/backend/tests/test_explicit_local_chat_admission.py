"""LOCUS-480: explicit local chat routing must never inherit hosted preferences."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generated_artifacts import AUTH_HEADERS, client, main_module, store
from test_provider_unavailable import _cleanup_run

MODEL = "gpt-oss:20b"


@pytest.fixture()
def local_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.local_models, "ollama_available", lambda: True)
    monkeypatch.setattr(
        main_module.local_models,
        "installed_models",
        lambda: [{"id": MODEL}, {"id": "nomic-embed-text:latest"}],
    )
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")


def test_local_followup_never_uses_saved_hosted_runtime(
    monkeypatch: pytest.MonkeyPatch, local_ollama: None
) -> None:
    observed: dict[str, Any] = {"runtime_resolver_calls": 0}

    def hosted_resolver(**_kwargs: Any) -> None:
        observed["runtime_resolver_calls"] += 1
        raise AssertionError("Explicit local inference must not resolve a hosted account")

    def local_iteration(*_args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        observed["model"] = kwargs["model"]
        return "Local routing accepted", {"mode": "live", "provider": "ollama", "model": MODEL}

    monkeypatch.setattr(main_module, "_resolve_request_chat_runtime", hosted_resolver)
    monkeypatch.setattr(main_module, "_run_agent_iterations", local_iteration)
    response = client.post(
        "/workflow-runs",
        json={
            "session_kind": "chat",
            "prompt": "Review the architecture using only this local model.",
            "model": f"ollama/{MODEL}",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    try:
        assert observed["model"] == f"ollama/{MODEL}"
        assert observed["runtime_resolver_calls"] == 0
        assert store.run_details[run_id]["runtime"]["mode"] == "live"
        assert store.runs[run_id].status != "Failed"
    finally:
        _cleanup_run(run_id)


def test_missing_local_model_is_rejected_before_run_creation(
    monkeypatch: pytest.MonkeyPatch, local_ollama: None
) -> None:
    before = set(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"prompt": "Continue locally", "model": "ollama/not-installed:latest"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "local_model_not_installed"
    assert set(store.runs) == before


def test_conflicting_hosted_override_is_rejected_before_run_creation(
    local_ollama: None,
) -> None:
    before = set(store.runs)
    response = client.post(
        "/workflow-runs",
        json={
            "prompt": "Only Ollama may process this task.",
            "model": f"ollama/{MODEL}",
            "runtime": {"provider": "openai", "model": "gpt-test"},
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400, response.text
    assert set(store.runs) == before


def test_local_provenance_denial_cannot_fallback_to_hosted(
    monkeypatch: pytest.MonkeyPatch, local_ollama: None
) -> None:
    monkeypatch.setattr(
        main_module.model_calls,
        "provenance_denial",
        lambda *_args: "Provenance attestation is required",
    )
    before = set(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"prompt": "Continue locally", "model": f"ollama/{MODEL}"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "model_call_denied"
    assert set(store.runs) == before


def test_unavailable_ollama_does_not_send_to_another_provider(
    monkeypatch: pytest.MonkeyPatch, local_ollama: None
) -> None:
    monkeypatch.setattr(main_module.local_models, "ollama_available", lambda: False)
    before = set(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"prompt": "Continue locally", "model": f"ollama/{MODEL}"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "local_runtime_unavailable"
    assert set(store.runs) == before
