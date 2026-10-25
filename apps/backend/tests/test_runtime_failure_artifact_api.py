"""LOCUS-477: a failed live task must produce a real, safe diagnostic artifact.

The original user incident had a generic unexpected exception, but GET
/workflow-runs/{id} silently manufactured a provider/auth failure artifact.
The artifact body was empty; the event claimed evidence that never existed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generated_artifacts import AUTH_HEADERS, client, main_module, store
from test_provider_unavailable import _cleanup_run, _create_run_and_collect


def _explode_secretly(*_args: object, **_kwargs: object) -> tuple[str, dict]:
    raise RuntimeError("secret: sk-test-NEVER-LEAK provider key from upstream")


def _artifact_from_run(run_id: str) -> dict:
    details = client.get(f"/workflow-runs/{run_id}", headers=AUTH_HEADERS)
    assert details.status_code == 200
    artifacts = details.json()["artifacts"]
    report = next(a for a in artifacts if a["name"] == "Agent Execution Failure Report")
    response = client.get(f"/artifacts/{report['id']}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    return {"report": report, "content": response.json()["content"], "detail": details.json()}


def test_unexpected_error_creates_real_sanitized_failure_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_resolve_user_scoped_runtime", lambda: None)
    monkeypatch.setattr(main_module, "_run_agent_iterations", _explode_secretly)
    run_id = _create_run_and_collect(monkeypatch)
    try:
        assert store.runs[run_id].status == "Failed"
        output = _artifact_from_run(run_id)
        content = output["content"]
        report = json.loads(content)
        assert report["schema_version"] == "locus/runtime-failure/v1"
        assert report["error"]["code"] == "execution_unhandled_exception"
        assert report["error"]["exception_type"] == "RuntimeError"
        assert "test_runtime_failure_artifact_api.py" in report["error"]["failure_site"]
        assert "sk-test-" not in content
        assert "NEVER-LEAK" not in content
        assert "provider key" not in content
        assert "Captured provider/auth" not in content

        assert output["detail"]["error"]["code"] == "execution_unhandled_exception"
        events = client.get(f"/workflow-runs/{run_id}/events", headers=AUTH_HEADERS)
        assert events.status_code == 200
        event_payload = events.json()
        error = next(e for e in event_payload if e["type"] == "error")
        artifact_created = next(e for e in event_payload if e["type"] == "artifact_created")
        assert error["metadata"]["exception_type"] == "RuntimeError"
        assert error["metadata"]["error_code"] == "execution_unhandled_exception"
        assert artifact_created["summary"].startswith("Bounded failure facts")
    finally:
        ids = {a["id"] for a in store.run_details.get(run_id, {}).get("artifacts", [])}
        store.artifacts[:] = [a for a in store.artifacts if a.id not in ids]
        _cleanup_run(run_id)


def test_legacy_failure_report_admits_root_cause_was_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_resolve_user_scoped_runtime", lambda: None)
    monkeypatch.setattr(main_module, "_run_agent_iterations", _explode_secretly)
    run_id = _create_run_and_collect(monkeypatch)
    try:
        # Simulate the uninstrumented 0.10.5 run with no real failure report.
        original_artifacts = store.run_details[run_id]["artifacts"]
        store.run_details[run_id]["artifacts"] = []
        store.run_details[run_id].pop("error", None)
        store.run_events[run_id] = [
            event for event in store.run_events[run_id]
            if event.type != "artifact_created"
        ]
        response = client.get(f"/workflow-runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        report = next(
            a for a in response.json()["artifacts"]
            if a["name"] == "Agent Execution Failure Report"
        )
        artifact_response = client.get(f"/artifacts/{report['id']}", headers=AUTH_HEADERS)
        assert artifact_response.status_code == 200
        body = json.loads(artifact_response.json()["content"])
        assert body["error"]["code"] == "historical_failure_details_unavailable"
        assert "provider/auth cause has not been established" in body["error"]["message"]
        events = client.get(f"/workflow-runs/{run_id}/events", headers=AUTH_HEADERS)
        assert "Captured provider/auth" not in json.dumps(events.json())
    finally:
        ids = {a["id"] for a in store.run_details.get(run_id, {}).get("artifacts", [])}
        ids.update(a["id"] for a in original_artifacts)
        store.artifacts[:] = [a for a in store.artifacts if a.id not in ids]
        _cleanup_run(run_id)


def test_existing_0105_placeholder_is_relabelled_not_misrepresented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "_resolve_user_scoped_runtime", lambda: None)
    monkeypatch.setattr(main_module, "_run_agent_iterations", _explode_secretly)
    run_id = _create_run_and_collect(monkeypatch)
    try:
        # Reconstruct exactly the owner's 0.10.5 state: generic Failed run,
        # placeholder artifact with no content, and false provider/auth event.
        artifacts = store.run_details[run_id]["artifacts"]
        assert len(artifacts) == 1
        artifacts[0].pop("content", None)
        store.run_details[run_id].pop("error", None)
        for event in store.run_events[run_id]:
            if event.type == "artifact_created":
                event.title = "Failure artifact created"
                event.summary = "Captured provider/auth configuration failure details."

        response = client.get(f"/workflow-runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["artifacts"][0]["content"]
        detail = _artifact_from_run(run_id)
        report = json.loads(detail["content"])
        assert report["error"]["code"] == "historical_failure_details_unavailable"
        events = client.get(f"/workflow-runs/{run_id}/events", headers=AUTH_HEADERS)
        assert "Captured provider/auth" not in json.dumps(events.json())
        assert "Historical failure details were not captured." in json.dumps(events.json())
    finally:
        ids = {a["id"] for a in store.run_details.get(run_id, {}).get("artifacts", [])}
        store.artifacts[:] = [a for a in store.artifacts if a.id not in ids]
        _cleanup_run(run_id)
