"""Focused tests for sectioned state persistence (resource-efficiency plan 1.1).

These deliberately avoid a real Postgres: the SQL surface is exercised against a
minimal fake connection, and the change-detection logic against a recording stub.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))


def _load_real_platform_services() -> Any:
    """Import the real module by path, bypassing any fake registered in sys.modules."""
    path = BACKEND_ROOT / "app" / "platform_services.py"
    spec = importlib.util.spec_from_file_location("real_platform_services", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __init__(self, table: dict[str, str]) -> None:
        self._table = table
        self._rows: list[tuple[str, str]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("create table"):
            return
        if normalized.startswith("insert into frontier_state_store"):
            assert params is not None
            self._table[str(params[0])] = str(params[1])
            return
        if normalized.startswith("delete from frontier_state_store"):
            assert params is not None
            self._table.pop(str(params[0]), None)
            return
        if normalized.startswith("select state_key, payload"):
            self._rows = list(self._table.items())
            return
        raise AssertionError(f"unexpected SQL in fake cursor: {normalized[:80]}")

    def fetchall(self) -> list[tuple[str, str]]:
        return self._rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


class _FakeConnection:
    def __init__(self, table: dict[str, str]) -> None:
        self._table = table

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._table)


def _make_store(module: Any, table: dict[str, str]) -> Any:
    store = module.PostgresStateStore("postgresql://fake")
    store.enabled = True
    store._initialized = True

    @contextmanager
    def _fake_connect() -> Any:
        yield _FakeConnection(table)

    store._connect = _fake_connect  # type: ignore[method-assign]
    return store


def test_sectioned_save_merges_over_legacy_global_row() -> None:
    module = _load_real_platform_services()
    table: dict[str, str] = {"global": '{"runs": [1], "inbox": [2], "artifacts": [3]}'}
    store = _make_store(module, table)

    store.save_state_sections({"runs": "[9, 9]"})

    merged = store.load_state()
    assert merged is not None
    assert merged["runs"] == [9, 9]  # section row wins
    assert merged["inbox"] == [2]  # legacy global fills unwritten sections
    assert merged["artifacts"] == [3]
    assert "global" in table  # partial write keeps the legacy row


def test_replace_all_write_removes_legacy_global_row() -> None:
    module = _load_real_platform_services()
    table: dict[str, str] = {"global": '{"runs": [1], "inbox": [2]}'}
    store = _make_store(module, table)

    store.save_state_sections({"runs": "[5]", "inbox": "[6]"}, replace_all=True)

    assert "global" not in table
    merged = store.load_state()
    assert merged == {"runs": [5], "inbox": [6]}


def test_safe_json_loads_handles_predecoded_jsonb_values() -> None:
    """psycopg returns JSONB as decoded Python objects — including lists.

    Regression: `payload in {None, ""}` raised TypeError for list payloads and
    crashed startup state loading on Postgres-backed deployments.
    """
    module = _load_real_platform_services()
    assert module._safe_json_loads([1, 2]) == [1, 2]
    assert module._safe_json_loads({"a": 1}) == {"a": 1}
    assert module._safe_json_loads(None) is None
    assert module._safe_json_loads("") is None
    assert module._safe_json_loads('{"a": 1}') == {"a": 1}
    assert module._safe_json_loads("[3]") == [3]


def test_postgres_load_state_accepts_predecoded_rows() -> None:
    module = _load_real_platform_services()
    table: dict[str, str] = {}
    store = _make_store(module, table)
    # Simulate psycopg returning decoded JSONB objects rather than strings.
    table["section:runs"] = [{"id": "r1"}]  # type: ignore[assignment]
    table["section:inbox"] = []  # type: ignore[assignment]
    merged = store.load_state()
    assert merged == {"runs": [{"id": "r1"}], "inbox": []}


def test_sqlite_state_store_sections_merge_and_replace_all(tmp_path) -> None:
    module = _load_real_platform_services()
    store = module.SQLiteStateStore(str(tmp_path / "state.db"))

    store.save_state({"runs": [1], "inbox": [2]})  # legacy full-snapshot write
    store.save_state_sections({"runs": "[9]"})

    merged = store.load_state()
    assert merged is not None
    assert merged["runs"] == [9]  # section row wins
    assert merged["inbox"] == [2]  # legacy global fills unwritten sections

    store.save_state_sections({"runs": "[5]", "inbox": "[6]"}, replace_all=True)
    assert store.load_state() == {"runs": [5], "inbox": [6]}
    assert store.healthcheck() is True


def test_sqlite_audit_log_appends_and_loads_newest_first(tmp_path) -> None:
    module = _load_real_platform_services()
    log = module.SQLiteAuditLog(str(tmp_path / "audit.db"))
    for index in range(3):
        log.append(
            {
                "id": f"audit-{index}",
                "action": "test.action",
                "actor": "tester",
                "outcome": "allowed",
                "created_at": f"2026-06-10T00:00:0{index}Z",
                "metadata": {"sequence": index},
            }
        )
    # Duplicate ids are ignored (append-only, tamper-evident).
    log.append(
        {
            "id": "audit-0",
            "action": "test.action.other",
            "actor": "tester",
            "outcome": "blocked",
            "created_at": "later",
            "metadata": {},
        }
    )

    recent = log.load_recent(limit=2)
    assert [item["id"] for item in recent] == ["audit-2", "audit-1"]
    assert recent[0]["metadata"] == {"sequence": 2}
    assert len(log.load_recent()) == 3
    assert log.load_recent()[2]["action"] == "test.action"


def test_persist_store_state_writes_only_changed_sections(monkeypatch) -> None:
    import app.main as main_module

    class _RecordingStore:
        def __init__(self) -> None:
            self.enabled = True
            self.calls: list[tuple[dict[str, str], bool]] = []

        def save_state_sections(
            self, encoded_sections: dict[str, str], *, replace_all: bool = False
        ) -> None:
            self.calls.append((dict(encoded_sections), replace_all))

    recording = _RecordingStore()
    monkeypatch.setattr(main_module, "_POSTGRES_STATE", recording)
    monkeypatch.setattr(main_module, "_PERSIST_SECTION_CACHE", {}, raising=True)

    # First persist: cold cache, every section is "changed" => full snapshot write.
    main_module._persist_store_state()
    assert len(recording.calls) == 1
    first_sections, first_replace_all = recording.calls[0]
    assert first_replace_all is True
    assert "runs" in first_sections

    # Second persist with no store mutations: nothing should be written.
    main_module._persist_store_state()
    assert len(recording.calls) == 1

    # Mutate one section: only that section (plus any sections sharing state) rewrites.
    run_id = "persistence-test-run"
    main_module.store.runs[run_id] = main_module.WorkflowRunSummary(
        id=run_id,
        title="Persistence test",
        status="Running",
        updatedAt="just now",
        progressLabel="Step 1/1",
    )
    try:
        main_module._persist_store_state()
        assert len(recording.calls) == 2
        changed_sections, second_replace_all = recording.calls[1]
        assert second_replace_all is False
        assert set(changed_sections) == {"runs"}
    finally:
        main_module.store.runs.pop(run_id, None)
        main_module._persist_store_state()
