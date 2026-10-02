"""Focused tests for the SSE run-event bridge (resource-efficiency plan 2.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main_module
from app.main import WorkflowRunEvent, WorkflowRunSummary, app, store

client = TestClient(app)

HEADERS = {"x-locus-actor": "tester"}


def _seed_terminal_run(run_id: str, event_ids: list[str], status: str = "Done") -> None:
    store.runs[run_id] = WorkflowRunSummary(
        id=run_id,
        title="Stream test run",
        status=status,
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
        "status": status,
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
    assert body.count("event: end") == 1
    assert body.rstrip().endswith('"terminal": true}')
    assert '"reason": "terminal"' in body
    assert "event: stream_closed" not in body


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
    assert body.count("event: end") == 1


def _frames(body: str) -> list[tuple[str, dict[str, object]]]:
    frames: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if name and data:
            frames.append((name, json.loads(data)))
    return frames


def test_stream_end_frame_carries_failed_terminal_status() -> None:
    run_id = str(uuid4())
    _seed_terminal_run(run_id, [f"evt-{uuid4()}"], status="Failed")
    try:
        with client.stream(
            "GET", f"/workflow-runs/{run_id}/events/stream", headers=HEADERS
        ) as response:
            body = "".join(response.iter_text())
    finally:
        _cleanup_run(run_id)

    frames = _frames(body)
    assert [name for name, _ in frames].count("end") == 1
    assert frames[-1] == (
        "end",
        {"run_id": run_id, "reason": "terminal", "status": "Failed", "terminal": True},
    )


def test_stream_timeout_cap_emits_non_terminal_stream_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = str(uuid4())
    event_ids = [f"evt-{uuid4()}"]
    _seed_terminal_run(run_id, event_ids, status="Running")
    monkeypatch.setattr(main_module, "_RUN_STREAM_MAX_SECONDS", 0)
    try:
        with client.stream(
            "GET", f"/workflow-runs/{run_id}/events/stream", headers=HEADERS
        ) as response:
            body = "".join(response.iter_text())
    finally:
        _cleanup_run(run_id)

    frames = _frames(body)
    names = [name for name, _ in frames]
    assert "end" not in names
    assert names[-1] == "stream_closed"
    assert frames[-1][1] == {
        "run_id": run_id,
        "reason": "timeout",
        "reconnect": True,
        "after": event_ids[0],
    }


def test_stream_pending_approval_is_not_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = str(uuid4())
    _seed_terminal_run(run_id, [], status="Done")
    store.run_details[run_id]["approvals"] = {"required": True, "pending": True}
    monkeypatch.setattr(main_module, "_RUN_STREAM_MAX_SECONDS", 0)
    try:
        with client.stream(
            "GET", f"/workflow-runs/{run_id}/events/stream", headers=HEADERS
        ) as response:
            body = "".join(response.iter_text())
    finally:
        _cleanup_run(run_id)

    names = [name for name, _ in _frames(body)]
    assert names[-1] == "stream_closed"
    assert "end" not in names
