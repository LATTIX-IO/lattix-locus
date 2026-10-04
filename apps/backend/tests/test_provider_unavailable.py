"""LOCUS-309: no simulated/echo output when a model provider is missing or failing.

Production paths must raise a typed ``ProviderUnavailableError`` (stable codes
``provider_not_configured`` / ``provider_call_failed``) and runs must end Failed
with that error visible in their events — never ``[simulated:...]`` echo text.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_generated_artifacts import AUTH_HEADERS, _sample_graph, client, main_module, store

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PROMPT = "Summarize the quarterly incident review for the board."


@pytest.fixture(autouse=True)
def _default_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCUS_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("LOCUS_SECURE_LOCAL_MODE", raising=False)


@pytest.fixture()
def no_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No usable provider: no OpenAI key (env or settings) and no user providers."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main_module, "_openai_api_key", lambda: "")
    monkeypatch.setattr(main_module, "_user_provider_configs", lambda _principal: {})


class _FailingOpenAIClient:
    """Fake provider client whose every call raises (provider-error path)."""

    def __init__(self, message: str) -> None:
        def _raise(**_kwargs: object) -> None:
            raise RuntimeError(message)

        self.responses = SimpleNamespace(create=_raise)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_raise))


def test_no_simulated_or_fallback_echo_text_in_backend_app() -> None:
    offenders = [
        f"{path.name}: {marker}"
        for path in sorted(APP_DIR.glob("*.py"))
        for marker in ("[simulated:", "[fallback:")
        if marker in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_missing_key_raises_provider_not_configured(no_provider_credentials: None) -> None:
    with pytest.raises(main_module.ProviderUnavailableError) as caught:
        main_module._run_openai_chat(
            system_prompt="You are helpful.",
            user_prompt=PROMPT,
            model="gpt-test-model",
            temperature=0.2,
        )
    exc = caught.value
    assert exc.code == "provider_not_configured"
    assert exc.status_code == 412
    assert exc.provider == "openai"
    assert exc.model == "gpt-test-model"
    assert "openai" in exc.message and "gpt-test-model" in exc.message
    assert PROMPT not in exc.message
    assert exc.detail["code"] == "provider_not_configured"


def test_provider_exception_raises_provider_call_failed_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz"
    monkeypatch.setattr(
        main_module,
        "_get_chat_client",
        lambda _provider: (_FailingOpenAIClient(f"upstream 500 for key {secret}"), ""),
    )
    with pytest.raises(main_module.ProviderUnavailableError) as caught:
        main_module._run_openai_chat(
            system_prompt="You are helpful.",
            user_prompt=PROMPT,
            model="gpt-test-model",
            temperature=0.2,
        )
    exc = caught.value
    assert exc.code == "provider_call_failed"
    assert exc.status_code == 424
    assert exc.provider == "openai"
    assert exc.model == "gpt-test-model"
    assert "upstream 500" in exc.reason
    assert secret not in exc.message
    assert PROMPT not in exc.message


def test_streaming_provider_error_is_typed_and_redacts_url_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: object) -> None:
        raise RuntimeError(
            "Client error '403 Forbidden' for url "
            "'https://generativelanguage.googleapis.com/v1beta/models/x?alt=sse&key=AIzaSECRET'"
        )

    monkeypatch.setattr(main_module, "_stream_gemini_chat", _boom)
    with pytest.raises(main_module.ProviderUnavailableError) as caught:
        main_module._run_openai_chat(
            system_prompt="",
            user_prompt=PROMPT,
            model="gemini-2.0-flash",
            temperature=0.2,
            runtime={
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "base_url": "https://generativelanguage.googleapis.com/v1beta",
                "api_key": "AIzaSECRET",
            },
        )
    exc = caught.value
    assert exc.code == "provider_call_failed"
    assert exc.provider == "gemini"
    assert "AIzaSECRET" not in exc.message
    assert "403" in exc.message


def test_runtime_providers_reports_not_configured(no_provider_credentials: None) -> None:
    response = client.get("/runtime/providers", headers=AUTH_HEADERS)
    assert response.status_code == 200
    openai_status = next(
        item for item in response.json()["providers"] if item["provider"] == "openai"
    )
    assert openai_status["configured"] is False
    assert openai_status["mode"] == "not_configured"


def _create_run_and_collect(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")
    monkeypatch.setattr(
        main_module, "_generate_workflow_run_title", lambda **_: ("Board summary", "generated")
    )
    monkeypatch.setattr(main_module, "_resolve_agent_chat_model", lambda _agent: "gpt-test-model")
    response = client.post(
        "/workflow-runs",
        json={"title": "Board summary", "prompt": PROMPT},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    return str(response.json()["id"])


def _cleanup_run(run_id: str) -> None:
    for bucket in (
        store.runs,
        store.run_details,
        store.run_events,
        store.run_streams,
        store.run_stream_complete,
    ):
        bucket.pop(run_id, None)


def _assert_no_echo(run_id: str) -> None:
    for event in store.run_events.get(run_id, []):
        text = f"{event.summary} {event.content or ''}"
        assert "[simulated:" not in text and "[fallback:" not in text
        if event.type == "agent_message":
            assert PROMPT not in text


def test_run_without_provider_fails_with_provider_not_configured(
    monkeypatch: pytest.MonkeyPatch, no_provider_credentials: None
) -> None:
    run_id = _create_run_and_collect(monkeypatch)
    try:
        assert store.runs[run_id].status == "Failed"
        errors = [event for event in store.run_events[run_id] if event.type == "error"]
        assert errors, "run must record an explicit error event"
        error = errors[-1]
        assert error.metadata["error_code"] == "provider_not_configured"
        assert error.metadata["error"]["provider"] == "openai"
        assert error.metadata["error"]["model"] == "gpt-test-model"
        assert "openai" in error.summary and "gpt-test-model" in error.summary
        assert not any(event.type == "agent_message" for event in store.run_events[run_id])
        detail = store.run_details[run_id]
        assert detail["status"] == "Failed"
        assert detail["error"]["code"] == "provider_not_configured"
        _assert_no_echo(run_id)
    finally:
        _cleanup_run(run_id)


def test_run_with_failing_provider_fails_with_provider_call_failed(
    monkeypatch: pytest.MonkeyPatch, no_provider_credentials: None
) -> None:
    monkeypatch.setattr(
        main_module,
        "_get_chat_client",
        lambda _provider: (_FailingOpenAIClient("connection reset by upstream"), ""),
    )
    run_id = _create_run_and_collect(monkeypatch)
    try:
        assert store.runs[run_id].status == "Failed"
        error = [event for event in store.run_events[run_id] if event.type == "error"][-1]
        assert error.metadata["error_code"] == "provider_call_failed"
        assert "connection reset by upstream" in error.summary
        assert store.run_details[run_id]["error"]["code"] == "provider_call_failed"
        _assert_no_echo(run_id)
    finally:
        _cleanup_run(run_id)


def test_run_with_fake_provider_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured-provider behavior is unchanged (explicit test double)."""

    def _fake_chat(*, system_prompt, user_prompt, model, temperature, **_kwargs):
        return "Board summary ready.", {"provider": "openai", "model": model, "mode": "live"}

    monkeypatch.setattr(main_module, "_run_openai_chat", _fake_chat)
    run_id = _create_run_and_collect(monkeypatch)
    try:
        assert store.runs[run_id].status in {"Done", "Needs Review"}
        assert not [event for event in store.run_events[run_id] if event.type == "error"]
    finally:
        _cleanup_run(run_id)


def test_graph_run_without_provider_returns_typed_412(no_provider_credentials: None) -> None:
    graph = _sample_graph()
    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/1.0",
            "nodes": graph["nodes"],
            "links": graph["links"],
            "input": {"message": PROMPT},
        },
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 412
    detail = response.json()["detail"]
    assert detail["code"] == "provider_not_configured"
    assert detail["provider"] == "openai"
    assert PROMPT not in str(detail)
