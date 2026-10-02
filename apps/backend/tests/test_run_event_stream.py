"""Focused tests for the SSE run-event bridge (resource-efficiency plan 2.2)."""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main_module
from app.main import WorkflowRunEvent, WorkflowRunSummary, app, store

client = TestClient(app)

HEADERS = {"x-frontier-actor": "tester"}


def _seed_terminal_run(run_id: str, event_ids: list[str]) -> None:
    store.runs[run_id] = WorkflowRunSummary(
        id=run_id,
        title="Stream test run",
        status="Done",
        updatedAt="just now",
        progressLabel="Complete",
    )
    store.run_events[run_id] = [
        WorkflowRunEvent(
            id=event_id,
            type="step_started",
            title=f"Event {index}",
            summary=f"Stream test event {index}",
            createdAt=main_module._now_iso(),
            metadata={},
        )
        for index, event_id in enumerate(event_ids)
    ]
    store.run_details[run_id] = {
        "artifacts": [],
        "status": "Done",
        "graph": {"nodes": [], "links": []},
        "agent_traces": [],
        "approvals": {"required": False, "pending": False},
    }


def _cleanup_run(run_id: str) -> None:
    store.runs.pop(run_id, None)
    store.run_events.pop(run_id, None)
    store.run_details.pop(run_id, None)


def test_stream_emits_events_and_closes_on_terminal_run() -> None:
    run_id = str(uuid4())
    event_ids = [f"evt-{uuid4()}", f"evt-{uuid4()}"]
    _seed_terminal_run(run_id, event_ids)
    try:
        with client.stream(
            "GET", f"/workflow-runs/{run_id}/events/stream", headers=HEADERS
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(response.iter_text())
    finally:
        _cleanup_run(run_id)

    assert body.count("event: run_event") == 2
    assert event_ids[0] in body
    assert event_ids[1] in body
    assert "event: run_status" in body
    assert '"status": "Done"' in body
    assert "event: stream_end" in body
    assert '"reason": "terminal"' in body


def test_stream_after_cursor_skips_already_seen_events() -> None:
    run_id = str(uuid4())
    event_ids = [f"evt-{uuid4()}", f"evt-{uuid4()}"]
    _seed_terminal_run(run_id, event_ids)
    try:
        with client.stream(
            "GET",
            f"/workflow-runs/{run_id}/events/stream",
            params={"after": event_ids[0]},
            headers=HEADERS,
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
    finally:
        _cleanup_run(run_id)

    assert body.count("event: run_event") == 1
    assert event_ids[0] not in body.replace(f"after={event_ids[0]}", "")
    assert event_ids[1] in body
    assert "event: stream_end" in body
