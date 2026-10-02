"""World-models on Postgres (no Java/Neo4j): PostgresWorldGraph is a drop-in for
Neo4jRunGraph. DB-backed paths need a live Postgres (CI/e2e); here we verify the
disabled path + interface parity offline."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.platform_services import Neo4jRunGraph, PostgresWorldGraph  # noqa: E402


def test_disabled_without_dsn():
    g = PostgresWorldGraph("")
    assert g.enabled is False
    assert g.healthcheck() is False
    # No-ops must not raise when disabled.
    g.record_run(run_id="r1", title="t", agent="a", workflow="w")
    g.project_memory_summary(projection={"owner": {"id": "owner:x"}, "memory": {"id": "m1"}})
    assert g.query_memory_context(bucket_id="x", memory_scope="session") == {
        "memories": [],
        "topics": [],
        "relations": [],
    }


def test_interface_parity_with_neo4j():
    # Same public surface so it drops into the _NEO4J_GRAPH alias unchanged.
    for name in (
        "enabled",
        "healthcheck",
        "record_run",
        "project_memory_summary",
        "query_memory_context",
    ):
        assert hasattr(PostgresWorldGraph(""), name)
        assert hasattr(Neo4jRunGraph("", "", ""), name)


def test_query_shape_matches_neo4j_disabled():
    pg = PostgresWorldGraph("").query_memory_context(bucket_id="b", memory_scope="user")
    neo = Neo4jRunGraph("", "", "").query_memory_context(bucket_id="b", memory_scope="user")
    assert pg == neo == {"memories": [], "topics": [], "relations": []}
