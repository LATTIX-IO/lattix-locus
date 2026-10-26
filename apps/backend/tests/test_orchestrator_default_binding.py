"""LOCUS-484: configured orchestrator stays bound across requests and replies."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generated_artifacts import AUTH_HEADERS, client, main_module, store
from test_provider_unavailable import _cleanup_run


@pytest.fixture()
def orchestrator_runtime(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")
    monkeypatch.setattr(
        main_module,
        "_user_settings_for",
        lambda _actor: {
            "orchestrator_model": "hosted/astra",
            "preferred_model": "hosted/astra",
            "worker_routing": "auto",
            "default_mode": "execute",
        },
    )

    def unavailable_user_runtime(**_kwargs: Any) -> None:
        raise main_module.HTTPException(status_code=412, detail="No user runtime credential")

    def fake_agent(*_args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        seen.append(str(kwargs["model"]))
        return "Synthetic test fixture only", {
            "mode": "live", "provider": "hosted", "model": "astra",
        }

    monkeypatch.setattr(main_module, "_resolve_request_chat_runtime", unavailable_user_runtime)
    monkeypatch.setattr(main_module, "_run_agent_iterations", fake_agent)
    return seen


def test_explicit_user_default_binds_the_orchestrator_and_followups(
    orchestrator_runtime: list[str],
) -> None:
    response = client.post(
        "/workflow-runs",
        json={"session_kind": "chat", "prompt": "Design a distributed control plane."},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    try:
        details = store.run_details[run_id]
        assert details["orchestrator_binding"] == {
            "model": "hosted/astra",
            "source": "user_default",
            "selection_role": "orchestrator",
        }
        assert details["worker_routing"] == "auto"
        assert orchestrator_runtime == ["hosted/astra"]

        continued = client.post(
            f"/workflow-runs/{run_id}/messages",
            json={"message": "Continue that same architecture design."},
            headers=AUTH_HEADERS,
        )
        assert continued.status_code == 200, continued.text
        assert orchestrator_runtime == ["hosted/astra", "hosted/astra"]
        assert store.run_details[run_id]["orchestrator_binding"]["model"] == "hosted/astra"
    finally:
        _cleanup_run(run_id)


def test_explicit_run_override_wins_over_user_default(
    orchestrator_runtime: list[str],
) -> None:
    response = client.post(
        "/workflow-runs",
        json={
            "session_kind": "chat",
            "prompt": "Design an API service.",
            "model": "ollama/sol",
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    try:
        assert store.run_details[run_id]["orchestrator_binding"]["model"] == "ollama/sol"
        assert store.run_details[run_id]["orchestrator_binding"]["source"] == "request_override"
        assert orchestrator_runtime == ["ollama/sol"]
    finally:
        _cleanup_run(run_id)


def test_unavailable_auto_orchestrator_does_not_fall_through_to_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "_user_settings_for",
        lambda _actor: {"orchestrator_model": "auto", "worker_routing": "auto"},
    )
    before = set(store.runs)
    response = client.post(
        "/workflow-runs",
        json={"session_kind": "chat", "prompt": "Explain this architecture."},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412, response.text
    assert response.json()["detail"]["code"] == "orchestrator_auto_unavailable"
    assert set(store.runs) == before
