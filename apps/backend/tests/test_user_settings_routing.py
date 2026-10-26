"""LOCUS-484: per-user orchestrator/worker defaults survive legacy partial PUTs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generated_artifacts import AUTH_HEADERS, client, main_module


@pytest.fixture(autouse=True)
def isolated_user_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_module.store, "user_settings", {})


def _put(settings: dict[str, str]) -> dict[str, str]:
    response = client.put("/user/settings", headers=AUTH_HEADERS, json=settings)
    assert response.status_code == 200, response.text
    return response.json()


def _get() -> dict[str, str]:
    response = client.get("/user/settings", headers=AUTH_HEADERS)
    assert response.status_code == 200, response.text
    return response.json()


def test_legacy_bare_openai_default_stays_primary_orchestrator() -> None:
    state = _put({"preferred_model": "gpt-5.2", "default_working_folder": "/projects/work"})
    assert state["orchestrator_model"] == "openai/gpt-5.2"
    assert state["worker_routing"] == "auto"
    assert _get()["preferred_model"] == "openai/gpt-5.2"

    # An older caller updates only its default chat mode. Persisted
    # orchestrator, worker policy and folder must not be silently reset.
    next_state = _put({"preferred_reasoning_effort": "high"})
    assert next_state["orchestrator_model"] == "openai/gpt-5.2"
    assert next_state["preferred_reasoning_effort"] == "high"
    assert next_state["default_working_folder"] == "/projects/work"


def test_new_worker_policy_is_independent_from_old_orchestrator_alias() -> None:
    _put({"orchestrator_model": "ollama/sol", "worker_routing": "manual"})
    state = _put({"preferred_model": "gpt-5.2"})
    assert state["orchestrator_model"] == "openai/gpt-5.2"
    assert state["worker_routing"] == "manual"
    assert _get()["preferred_model"] == "openai/gpt-5.2"

    # Explicit new settings take precedence over a legacy alias in the
    # same request; this does not grant a provider any new egress permission.
    changed = _put({
        "preferred_model": "gpt-5.2",
        "orchestrator_model": "ollama/terra",
        "worker_routing": "auto",
    })
    assert changed["orchestrator_model"] == "ollama/terra"
    assert changed["worker_routing"] == "auto"


def test_explicit_no_orchestrator_does_not_pick_openai() -> None:
    _put({"orchestrator_model": "ollama/sol"})
    state = _put({"orchestrator_model": ""})
    assert state["orchestrator_model"] == ""
    assert state["preferred_model"] == ""
