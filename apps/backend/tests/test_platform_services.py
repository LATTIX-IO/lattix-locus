from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_PLATFORM_SERVICES_PATH = Path(__file__).resolve().parents[1] / "app" / "platform_services.py"
_PLATFORM_SERVICES_SPEC = importlib.util.spec_from_file_location(
    "platform_services_real_for_tests", _PLATFORM_SERVICES_PATH
)
assert _PLATFORM_SERVICES_SPEC is not None
assert _PLATFORM_SERVICES_SPEC.loader is not None
platform_services = importlib.util.module_from_spec(_PLATFORM_SERVICES_SPEC)
_PLATFORM_SERVICES_SPEC.loader.exec_module(platform_services)

Neo4jRunGraph = platform_services.Neo4jRunGraph
PostgresStateStore = platform_services.PostgresStateStore


class _FakeTransaction:
    def __init__(self, *, fail_on_run: int | None = None) -> None:
        self.fail_on_run = fail_on_run
        self.run_calls: list[tuple[str, dict[str, object]]] = []
        self.commit_called = False
        self.rollback_called = False

    def run(self, query: str, parameters: dict[str, object] | None = None) -> None:
        self.run_calls.append((query, parameters or {}))
        if self.fail_on_run is not None and len(self.run_calls) == self.fail_on_run:
            raise RuntimeError("neo4j write failed")

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True


class _FakeSession:
    def __init__(self, tx: _FakeTransaction) -> None:
        self.tx = tx
        self.begin_transaction_called = False

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def begin_transaction(self) -> _FakeTransaction:
        self.begin_transaction_called = True
        return self.tx

    def run(self, *_args, **_kwargs) -> None:
        raise AssertionError("project_causal_assembly should not call session.run directly")


class _FakeDriver:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def session(self) -> _FakeSession:
        return self._session


class _FakePostgresConnection:
    def __enter__(self) -> _FakePostgresConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakePsycopg:
    def __init__(self) -> None:
        self.connect_calls: list[tuple[str, dict[str, object]]] = []

    def connect(self, dsn: str, **kwargs: object) -> _FakePostgresConnection:
        self.connect_calls.append((dsn, dict(kwargs)))
        return _FakePostgresConnection()


def _sample_projection() -> dict[str, object]:
    return {
        "assembly": {
            "assembly_id": "assembly:test-1",
            "id": "causal-assembly:assembly:test-1",
            "updated_at": 123.0,
            "column_count": 1,
            "belief_snapshot_count": 1,
            "belief_count": 1,
            "confidence_sample_count": 1,
            "outcome_count": 1,
        },
        "columns": [
            {
                "id": "column-1",
                "assembly_id": "assembly:test-1",
                "column_id": "goal",
                "kind": "goal",
                "confidence": 0.9,
                "last_updated": 123.0,
                "evidence_refs": ["doc-1"],
                "adaptation_metrics_json": "{}",
                "belief_count": 1,
            }
        ],
        "belief_snapshots": [
            {
                "id": "snapshot-1",
                "assembly_id": "assembly:test-1",
                "column_id": "goal",
                "column_node_id": "column-1",
                "recorded_at": 123.0,
                "confidence": 0.9,
                "evidence_refs": ["doc-1"],
                "cause_json": "{}",
                "belief_count": 1,
            }
        ],
        "beliefs": [
            {
                "id": "belief-1",
                "assembly_id": "assembly:test-1",
                "column_id": "goal",
                "snapshot_id": "snapshot-1",
                "belief_key": "goal",
                "value_json": '"reduce risk"',
                "confidence": 0.9,
                "evidence_refs": ["doc-1"],
                "rationale": "derived",
                "metadata_json": "{}",
            }
        ],
        "confidence_samples": [
            {
                "id": "confidence-1",
                "assembly_id": "assembly:test-1",
                "column_id": "goal",
                "column_node_id": "column-1",
                "recorded_at": 123.0,
                "confidence": 0.9,
                "adaptation_metrics_json": "{}",
                "cause_json": "{}",
            }
        ],
        "outcomes": [
            {
                "id": "outcome-1",
                "assembly_id": "assembly:test-1",
                "outcome": "committed",
                "recorded_at": 123.0,
                "metadata_json": "{}",
                "decision": "approve",
                "commitment_confidence": 0.92,
                "is_ready": True,
                "blockers": [],
                "next_actions": ["publish"],
                "supporting_columns": ["column-1"],
                "dissenting_columns": [],
            }
        ],
        "support_edges": [{"outcome_id": "outcome-1", "column_id": "column-1"}],
        "dissent_edges": [],
    }


def test_project_causal_assembly_uses_single_explicit_transaction() -> None:
    tx = _FakeTransaction()
    session = _FakeSession(tx)
    graph = Neo4jRunGraph("bolt://example", "neo4j", "password")
    graph.enabled = True
    graph._driver = _FakeDriver(session)

    result = graph.project_causal_assembly(projection=_sample_projection())

    assert result is True
    assert session.begin_transaction_called is True
    assert len(tx.run_calls) == 8
    assert tx.commit_called is True
    assert tx.rollback_called is False


def test_postgres_connect_uses_bounded_connect_timeout(monkeypatch) -> None:
    fake_psycopg = _FakePsycopg()
    monkeypatch.setattr(platform_services, "psycopg", fake_psycopg)

    store = PostgresStateStore("postgresql://frontier:test@db.example/frontier")

    with store._connect():
        pass

    assert fake_psycopg.connect_calls == [
        (
            "postgresql://frontier:test@db.example/frontier",
            {"autocommit": True, "connect_timeout": 5},
        )
    ]


def test_project_causal_assembly_rolls_back_and_returns_false_on_failure() -> None:
    tx = _FakeTransaction(fail_on_run=3)
    session = _FakeSession(tx)
    graph = Neo4jRunGraph("bolt://example", "neo4j", "password")
    graph.enabled = True
    graph._driver = _FakeDriver(session)

    result = graph.project_causal_assembly(projection=_sample_projection())

    assert result is False
    assert session.begin_transaction_called is True
    assert tx.commit_called is False
    assert tx.rollback_called is True
