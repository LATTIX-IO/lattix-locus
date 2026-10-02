"""Focused tests for the cron matcher and schedule trigger loop (Phase D)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.cron import cron_matches, is_valid_cron, parse_cron
from app.main import WorkflowDefinition, app, store

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}
UTC = timezone.utc


def test_cron_validation() -> None:
    assert is_valid_cron("* * * * *")
    assert is_valid_cron("*/15 9-17 * * 1-5")
    assert is_valid_cron("0 0 1 1 *")
    assert not is_valid_cron("* * * *")  # 4 fields
    assert not is_valid_cron("60 * * * *")  # minute out of range
    assert not is_valid_cron("not a cron")


def test_cron_field_parsing() -> None:
    minute, hour, _, _, _ = parse_cron("*/15 9-11 * * *")
    assert minute == {0, 15, 30, 45}
    assert hour == {9, 10, 11}


def test_cron_matches_minute_and_hour() -> None:
    expr = "30 14 * * *"  # 14:30 daily
    assert cron_matches(expr, datetime(2026, 6, 11, 14, 30, tzinfo=UTC))
    assert not cron_matches(expr, datetime(2026, 6, 11, 14, 31, tzinfo=UTC))
    assert not cron_matches(expr, datetime(2026, 6, 11, 15, 30, tzinfo=UTC))


def test_cron_weekday_semantics() -> None:
    # 2026-06-11 is a Thursday (cron dow 4).
    weekday_9am = "0 9 * * 1-5"
    assert cron_matches(weekday_9am, datetime(2026, 6, 11, 9, 0, tzinfo=UTC))
    # 2026-06-13 is a Saturday (cron dow 6) — outside 1-5.
    assert not cron_matches(weekday_9am, datetime(2026, 6, 13, 9, 0, tzinfo=UTC))


def test_schedule_crud_and_validation() -> None:
    workflow_id = str(uuid4())
    store.workflow_definitions[workflow_id] = WorkflowDefinition(
        id=workflow_id,
        name=f"Sched Flow {workflow_id[:8]}",
        description="schedule test",
        version=1,
        status="published",
    )
    schedule_id = None
    try:
        bad = client.post(
            f"/workflow-definitions/{workflow_id}/schedules",
            json={"cron": "every minute", "label": "bad"},
            headers=ADMIN_HEADERS,
        )
        assert bad.status_code == 400

        created = client.post(
            f"/workflow-definitions/{workflow_id}/schedules",
            json={"cron": "*/5 * * * *", "label": "every 5 min"},
            headers=ADMIN_HEADERS,
        )
        assert created.status_code == 200
        schedule_id = created.json()["id"]
        assert created.json()["cron"] == "*/5 * * * *"
        assert created.json()["enabled"] is True

        listing = client.get(
            f"/workflow-definitions/{workflow_id}/schedules", headers=ADMIN_HEADERS
        )
        assert any(item["id"] == schedule_id for item in listing.json())

        toggled = client.post(
            f"/schedules/{schedule_id}/toggle",
            json={"enabled": False},
            headers=ADMIN_HEADERS,
        )
        assert toggled.json()["enabled"] is False

        deleted = client.delete(f"/schedules/{schedule_id}", headers=ADMIN_HEADERS)
        assert deleted.status_code == 200
        schedule_id = None
    finally:
        if schedule_id:
            store.workflow_schedules.pop(schedule_id, None)
        store.workflow_definitions.pop(workflow_id, None)


def test_scheduler_tick_fires_due_schedule_once(monkeypatch) -> None:
    workflow_id = str(uuid4())
    store.workflow_definitions[workflow_id] = WorkflowDefinition(
        id=workflow_id,
        name=f"Tick Flow {workflow_id[:8]}",
        description="tick test",
        version=1,
        status="published",
    )
    schedule_id = str(uuid4())
    store.workflow_schedules[schedule_id] = {
        "id": schedule_id,
        "workflow_id": workflow_id,
        "actor": "frontier-admin",
        "label": "tick",
        "cron": "* * * * *",  # always matches
        "enabled": True,
        "created_at": main_module._now_iso(),
        "last_fired_minute": "",
    }

    fired_payloads: list[dict] = []

    def _fake_create_run(request, payload):
        fired_payloads.append(payload)
        return {"id": "fake-run", "status": "started"}

    monkeypatch.setattr(main_module, "create_workflow_run", _fake_create_run)
    moment = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    try:
        assert main_module._scheduler_tick(moment) == 1
        # Same minute again: dedupe prevents a second fire.
        assert main_module._scheduler_tick(moment) == 0
        # Next minute: fires again.
        assert main_module._scheduler_tick(moment.replace(minute=1)) == 1
        assert len(fired_payloads) == 2
        assert fired_payloads[0]["context"]["source"] == "schedule_trigger"
    finally:
        store.workflow_schedules.pop(schedule_id, None)
        store.workflow_definitions.pop(workflow_id, None)


def test_scheduler_tick_skips_disabled_and_unpublished(monkeypatch) -> None:
    workflow_id = str(uuid4())
    store.workflow_definitions[workflow_id] = WorkflowDefinition(
        id=workflow_id,
        name="Disabled Flow",
        description="x",
        version=1,
        status="draft",  # not published
    )
    schedule_id = str(uuid4())
    store.workflow_schedules[schedule_id] = {
        "id": schedule_id,
        "workflow_id": workflow_id,
        "actor": "frontier-admin",
        "label": "disabled",
        "cron": "* * * * *",
        "enabled": True,
        "created_at": main_module._now_iso(),
        "last_fired_minute": "",
    }
    calls: list = []
    monkeypatch.setattr(main_module, "create_workflow_run", lambda r, p: calls.append(p))
    try:
        # Cron matches and schedule enabled, but workflow is draft -> no run.
        main_module._scheduler_tick(datetime(2026, 6, 11, 12, 0, tzinfo=UTC))
        assert calls == []
    finally:
        store.workflow_schedules.pop(schedule_id, None)
        store.workflow_definitions.pop(workflow_id, None)
