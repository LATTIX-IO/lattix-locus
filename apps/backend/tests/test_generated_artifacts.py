from __future__ import annotations

import json
import logging
import os
import sys
import time
import types
from urllib import error as urllib_error
from collections import defaultdict
from pathlib import Path
import tempfile
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from locus_runtime.cognition import (
    AssemblyDefinition,
    AssemblyState,
    BeliefRecord,
    ColumnKind,
    ColumnState,
    Commitment,
)
from locus_runtime.persistence import (
    load_assembly_causal_state,
    persist_assembly_state,
    persist_column_state,
    record_assembly_outcome,
    reset_shared_state_backend,
)
from locus_runtime.security import mint_token

if (
    not str(os.environ.get("A2A_JWT_SECRET") or "").strip()
    or "placeholder" in str(os.environ.get("A2A_JWT_SECRET") or "").lower()
):
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if (
    not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip()
    or "placeholder" in str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").lower()
):
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"


@pytest.fixture(autouse=True)
def _default_runtime_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCUS_RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("LOCUS_SECURE_LOCAL_MODE", raising=False)
    monkeypatch.delenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)
    monkeypatch.delenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)
    monkeypatch.delenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", raising=False)
    monkeypatch.delenv("LOCUS_ADMIN_ACTORS", raising=False)
    monkeypatch.delenv("LOCUS_BUILDER_ACTORS", raising=False)
    monkeypatch.setenv("LOCUS_BOOTSTRAP_ADMIN_USERNAME", "locus-admin")
    monkeypatch.setenv("LOCUS_BOOTSTRAP_ADMIN_EMAIL", "admin@locus.localhost")
    monkeypatch.setenv("LOCUS_BOOTSTRAP_ADMIN_SUBJECT", "locus-admin")


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

platform_services = types.ModuleType("app.platform_services")


class _FakePostgresStateStore:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.enabled = True
        self._payload: dict[str, object] | None = None

    def initialize(self) -> None:
        return

    def load_state(self) -> dict[str, object] | None:
        return self._payload

    def save_state(self, payload: dict[str, object]) -> None:
        self._payload = dict(payload)


class _FakeRedisMemoryStore:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.enabled = True
        self._entries: dict[str, list[dict[str, object]]] = defaultdict(list)
        self._nonces: dict[str, int] = {}
        self.wal_enabled = False
        self.wal_dir = Path(tempfile.gettempdir()) / "locus-test-memory-wal"

    def _entry_store(self) -> dict[str, list[dict[str, object]]]:
        entries = getattr(self, "_entries", None)
        if not isinstance(entries, dict):
            entries = defaultdict(list)
            self._entries = entries
        return entries

    def _wal_path(self, session_id: str) -> Path:
        wal_dir = getattr(self, "wal_dir", Path(tempfile.gettempdir()) / "locus-test-memory-wal")
        return Path(wal_dir) / f"{session_id}.jsonl"

    def _wal_append(self, session_id: str, entry: dict[str, object]) -> None:
        if not bool(getattr(self, "wal_enabled", False)):
            return
        wal_path = self._wal_path(session_id)
        wal_path.parent.mkdir(parents=True, exist_ok=True)
        with wal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def _wal_recover(self, session_id: str, *, limit: int = 200) -> list[dict[str, object]]:
        if not bool(getattr(self, "wal_enabled", False)):
            return []
        wal_path = self._wal_path(session_id)
        if not wal_path.exists():
            return []
        entries: list[dict[str, object]] = []
        for line in wal_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return entries[-max(1, limit) :]

    def cleanup_wal(self, session_id: str) -> None:
        if not bool(getattr(self, "wal_enabled", False)):
            return
        self._wal_path(session_id).unlink(missing_ok=True)

    def healthcheck(self) -> bool:
        return True

    def get_entries(self, session_id: str, *, limit: int = 100) -> list[dict[str, object]]:
        entries = self._entry_store().get(session_id, [])[-limit:]
        return entries or self._wal_recover(session_id, limit=limit)

    def append_entry(self, session_id: str, entry: dict[str, object]) -> None:
        self._entry_store().setdefault(session_id, []).append(dict(entry))
        self._wal_append(session_id, entry)

    def load_entries(self, session_id: str, entries: list[dict[str, object]]) -> None:
        for entry in entries:
            self.append_entry(session_id, entry)

    def clear_entries(self, session_id: str) -> None:
        self._entry_store()[session_id] = []

    def register_nonce_once(self, nonce: str, *, ttl_seconds: int) -> bool:
        nonce_text = str(nonce or "").strip()
        if not nonce_text:
            return False
        if nonce_text in self._nonces:
            return False
        self._nonces[nonce_text] = max(1, int(ttl_seconds))
        return True


class _FakeLongTermMemoryStore:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.enabled = True
        # Mirror the real store's attribute surface so callers that introspect
        # vector/embedding capabilities (e.g. the knowledge vector-store list)
        # work when this fake is bound in place of the real store.
        self.vector_enabled = False
        self.embedding_model = "text-embedding-3-small"
        self._entries: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
        self._consolidation_candidates: dict[str, dict[str, object]] = {}

    def initialize(self) -> None:
        return

    def healthcheck(self) -> bool:
        return True

    def append_entry(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, object],
        source: str,
        task_id: str | None = None,
    ) -> None:
        payload = dict(entry)
        payload.setdefault("bucket_id", bucket_id)
        payload.setdefault("session_id", session_id)
        payload.setdefault("memory_scope", memory_scope)
        payload.setdefault("source", source)
        payload.setdefault("task_id", task_id or "")
        payload.setdefault("tier", "long-term")
        self._entries[(bucket_id, session_id, memory_scope)].append(payload)

    def get_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        matches: list[dict[str, object]] = []
        for (stored_bucket, stored_session, stored_scope), entries in self._entries.items():
            if bucket_id and stored_bucket != bucket_id:
                continue
            if session_id and stored_session != session_id:
                continue
            if memory_scope and stored_scope != memory_scope:
                continue
            matches.extend(dict(item) for item in entries)
        return matches[-limit:]

    def search_entries(
        self,
        query_text: str,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, object]]:
        needle = str(query_text or "").lower()
        matches = [
            entry
            for entry in self.get_entries(
                bucket_id=bucket_id, session_id=session_id, memory_scope=memory_scope, limit=1000
            )
            if needle in str(entry.get("content") or "").lower()
        ]
        return (
            matches[:limit]
            if matches
            else self.get_entries(
                bucket_id=bucket_id, session_id=session_id, memory_scope=memory_scope, limit=limit
            )
        )

    def clear_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
    ) -> None:
        to_delete: list[tuple[str, str, str]] = []
        for key in self._entries:
            stored_bucket, stored_session, stored_scope = key
            if bucket_id and stored_bucket != bucket_id:
                continue
            if session_id and stored_session != session_id:
                continue
            if memory_scope and stored_scope != memory_scope:
                continue
            to_delete.append(key)
        for key in to_delete:
            self._entries.pop(key, None)

    def enqueue_consolidation_candidate(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, object],
        source: str,
        task_id: str | None = None,
        candidate_kind: str = "promotion",
    ) -> None:
        entry_id = str(entry.get("id") or uuid4())
        candidate_id = f"consolidation:{entry_id}"
        payload = dict(entry)
        payload.update(
            {
                "id": candidate_id,
                "entry_id": entry_id,
                "bucket_id": bucket_id,
                "session_id": session_id,
                "memory_scope": memory_scope,
                "source": source,
                "task_id": task_id or "",
                "candidate_kind": candidate_kind,
                "status": "pending",
            }
        )
        self._consolidation_candidates[candidate_id] = payload

    def list_consolidation_candidates(
        self,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        items = list(self._consolidation_candidates.values())
        if bucket_id:
            items = [item for item in items if str(item.get("bucket_id") or "") == bucket_id]
        if memory_scope:
            items = [item for item in items if str(item.get("memory_scope") or "") == memory_scope]
        if status:
            items = [item for item in items if str(item.get("status") or "") == status]
        return items[-limit:]

    def mark_consolidation_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        candidate = self._consolidation_candidates.get(candidate_id)
        if not candidate:
            return
        candidate["status"] = status
        if extra_metadata:
            metadata = (
                candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
            )
            metadata.update(extra_metadata)
            candidate["metadata"] = metadata
        self._consolidation_candidates[candidate_id] = candidate


class _FakeNeo4jRunGraph:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.enabled = True
        self.causal_projections: dict[str, dict[str, object]] = {}
        self.run_records: list[dict[str, object]] = []
        self.memory_projections: list[dict[str, object]] = []

    def healthcheck(self) -> bool:
        return True

    def record_run(self, **_kwargs: object) -> None:
        self.run_records.append(dict(_kwargs))

    def project_memory_summary(self, *, projection: dict[str, object]) -> None:
        self.memory_projections.append(dict(projection))

    def project_causal_assembly(self, *, projection: dict[str, object]) -> bool:
        assembly = (
            projection.get("assembly") if isinstance(projection.get("assembly"), dict) else {}
        )
        assembly_id = str(assembly.get("assembly_id") or "").strip()
        if not assembly_id:
            return False
        self.causal_projections[assembly_id] = dict(projection)
        return True

    def query_memory_context(
        self,
        *,
        bucket_id: str,
        memory_scope: str,
        query_text: str = "",
        limit: int = 10,
    ) -> dict[str, object]:
        owner_id = f"owner:{bucket_id}"
        projections = [
            projection
            for projection in self.memory_projections
            if str(projection.get("owner", {}).get("id") or "") == owner_id
        ]
        memories = []
        topics_by_id: dict[str, dict[str, object]] = {}
        relations: list[dict[str, object]] = []
        query = str(query_text or "").lower().strip()
        for projection in projections[-limit:]:
            memory = projection.get("memory") if isinstance(projection.get("memory"), dict) else {}
            content = str(memory.get("content") or "")
            if query and query not in content.lower():
                continue
            if memory:
                payload = dict(memory)
                payload.setdefault("tier", "world-graph")
                memories.append(payload)
            for topic in projection.get("topics", []):
                if isinstance(topic, dict) and str(topic.get("id") or ""):
                    topics_by_id[str(topic.get("id") or "")] = dict(topic)
            for relation in projection.get("relations", []):
                if isinstance(relation, dict):
                    relations.append(dict(relation))
        return {
            "memories": memories[-limit:],
            "topics": list(topics_by_id.values())[:limit],
            "relations": relations[: limit * 3],
        }


platform_services.Neo4jRunGraph = _FakeNeo4jRunGraph
platform_services.PostgresStateStore = _FakePostgresStateStore
platform_services.PostgresLongTermMemoryStore = _FakeLongTermMemoryStore
platform_services.RedisMemoryStore = _FakeRedisMemoryStore
platform_services.PostgresWorldGraph = _FakeNeo4jRunGraph
sys.modules.setdefault("app.platform_services", platform_services)

import app.main as main_module
from app.main import app, store
from app.request_security import validate_route_inventory

# The fakes above only take effect if THIS module is the first to import
# ``app.main`` (so the ``setdefault`` wins and main binds its singletons from the
# fakes). When another test module imports ``app.main`` first, main has already
# bound the real (and, without a database, disabled no-op) stores. Rebind the
# module-level singletons directly so these tests get in-memory fakes regardless
# of test-file import order.
main_module._POSTGRES_STATE = _FakePostgresStateStore()
main_module._REDIS_MEMORY = _FakeRedisMemoryStore()
main_module._POSTGRES_MEMORY = _FakeLongTermMemoryStore()
main_module._NEO4J_GRAPH = _FakeNeo4jRunGraph()


client = TestClient(app)

AUTH_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "tester"}
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}
MEMBER_AUTH_HEADERS = {
    "Authorization": "Bearer unit-test-bearer",
    "x-locus-actor": "member-user",
}
OWNER_AUTH_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "owner-user"}
NON_ADMIN_HEADERS = {"x-locus-actor": "member-user"}


def _signed_internal_headers(
    *,
    payload: bytes = b"",
    actor: str = "tester",
    subject: str = "backend",
    nonce: str = "nonce-1",
    correlation_id: str = "corr-1",
    timestamp: str | None = None,
) -> dict[str, str]:
    resolved_timestamp = timestamp or str(int(time.time()))
    return {
        "x-locus-actor": actor,
        "x-correlation-id": correlation_id,
        "x-locus-subject": subject,
        "x-locus-nonce": nonce,
        "x-locus-timestamp": resolved_timestamp,
        "x-locus-signature": main_module._build_runtime_signature(
            subject, nonce, correlation_id, payload, timestamp=resolved_timestamp
        ),
        "content-type": "application/json",
    }


def _sample_graph() -> dict[str, list[dict[str, object]]]:
    return {
        "nodes": [
            {
                "id": "trigger",
                "title": "Trigger",
                "type": "trigger",
                "x": 70,
                "y": 90,
                "config": {"trigger_mode": "manual"},
            },
            {
                "id": "prompt",
                "title": "Prompt",
                "type": "prompt",
                "x": 280,
                "y": 90,
                "config": {"system_prompt_text": "Help the user safely."},
            },
            {
                "id": "agent",
                "title": "Agent",
                "type": "agent",
                "x": 520,
                "y": 90,
                "config": {"agent_id": "generated-agent", "model": "gpt-5.2"},
            },
            {
                "id": "output",
                "title": "Output",
                "type": "output",
                "x": 790,
                "y": 90,
                "config": {"destination": "artifact_store", "format": "json"},
            },
        ],
        "links": [
            {"from": "trigger", "to": "agent", "from_port": "out", "to_port": "in"},
            {"from": "prompt", "to": "agent", "from_port": "prompt", "to_port": "prompt"},
            {"from": "agent", "to": "output", "from_port": "out", "to_port": "in"},
            {"from": "agent", "to": "output", "from_port": "response", "to_port": "result"},
        ],
    }


def _use_fake_chat_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit test-double provider (LOCUS-309: production has no simulated output)."""

    def _fake_runtime(**_kwargs: object) -> dict[str, object]:
        return {
            "provider": "openai",
            "model": "gpt-test-model",
            "available_models": ["gpt-test-model"],
            "base_url": "https://provider.invalid/v1",
            "api_key": "test-key",
            "preferred": True,
            "source": "test",
        }

    def _fake_chat(*, model: str, **_kwargs: object) -> tuple[str, dict[str, object]]:
        return "Fake provider response.", {"provider": "openai", "model": model, "mode": "live"}

    monkeypatch.setattr(main_module, "_resolve_request_chat_runtime", _fake_runtime)
    monkeypatch.setattr(main_module, "_run_openai_chat", _fake_chat)


def _run_access(owner: str, *, tenant: str = "") -> dict[str, object]:
    return {
        "actor": owner,
        "principal_id": owner,
        "principal_type": "user",
        "subject": "",
        "tenant": tenant,
        "references": [owner],
    }


def test_publish_agent_definition_emits_no_code_scaffolds() -> None:
    """LOCUS-352: publishing no longer writes LangGraph/MAF source text."""
    agent_id = str(uuid4())
    payload = {
        "id": agent_id,
        "name": "Published Agent Test",
        "config_json": {
            "graph_json": _sample_graph(),
            "security": {"classification": "restricted", "blocked_keywords": ["secret"]},
        },
    }
    artifacts_before = list(store.artifacts)
    try:
        save_response = client.post("/agent-definitions", json=payload, headers=ADMIN_HEADERS)
        assert save_response.status_code == 200

        publish_response = client.post(
            f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS
        )
        assert publish_response.status_code == 200
        assert publish_response.json() == {"ok": True}

        detail = client.get(f"/agent-definitions/{agent_id}").json()
        assert detail["status"] == "published"
        assert "generated_artifacts" not in detail
        assert store.artifacts == artifacts_before
    finally:
        store.agent_definitions.pop(agent_id, None)
        store.artifacts = artifacts_before


def test_publish_workflow_definition_emits_no_code_scaffolds() -> None:
    workflow_id = str(uuid4())
    payload = {
        "id": workflow_id,
        "name": "Published Workflow Test",
        "description": "Workflow publish no longer emits code scaffolds.",
        "graph_json": _sample_graph(),
        "security_config": {"classification": "confidential", "require_human_approval": True},
    }
    artifacts_before = list(store.artifacts)
    try:
        assert client.post("/workflow-definitions", json=payload).status_code == 200
        publish_response = client.post(f"/workflow-definitions/{workflow_id}/publish")
        assert publish_response.status_code == 200
        assert publish_response.json() == {"ok": True}

        detail = client.get(f"/workflow-definitions/{workflow_id}").json()
        assert detail["status"] == "published"
        assert "generated_artifacts" not in detail
        assert store.artifacts == artifacts_before
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.artifacts = artifacts_before


def test_stored_code_scaffolds_from_an_older_build_are_dropped_on_load() -> None:
    workflow = main_module.WorkflowDefinition.model_validate(
        {
            "id": "wf-legacy-scaffold",
            "name": "Legacy",
            "description": "",
            "version": 2,
            "status": "published",
            "generated_artifacts": [{"id": "gen-1", "framework": "langgraph"}],
        }
    )
    assert "generated_artifacts" not in workflow.model_dump()

    original_artifacts = list(store.artifacts)
    try:
        main_module._apply_store_state(
            {
                "artifacts": [
                    {"id": "gen-1", "name": "Legacy · LangGraph scaffold", "status": "Draft", "version": 2},
                    {
                        "id": "gen-2",
                        "name": "Legacy · Microsoft Agent Framework scaffold",
                        "status": "Draft",
                        "version": 2,
                    },
                    {"id": "run-artifact", "name": "Board summary", "status": "Draft", "version": 1},
                ]
            }
        )
        assert [artifact.id for artifact in store.artifacts] == ["run-artifact"]
    finally:
        store.artifacts = original_artifacts


def test_workflow_definition_defaults_graph_schema_version_when_missing() -> None:
    workflow_id = str(uuid4())
    payload = {
        "id": workflow_id,
        "name": "Schema Version Workflow",
        "description": "Workflow save should normalize graph schema version.",
        "graph_json": _sample_graph(),
    }

    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 200

    stored = store.workflow_definitions[workflow_id]
    assert stored.graph_json.get("schema_version") == "locus-graph/1.0"

    store.workflow_definitions.pop(workflow_id, None)


def test_workflow_definition_normalizes_agent_skill_paths_before_persisting() -> None:
    workflow_id = str(uuid4())
    graph = _sample_graph()
    agent_node = next(node for node in graph["nodes"] if node["type"] == "agent")
    agent_node["config"]["skills"] = [
        "incident-triage",
        "/tenant-oncall",
        "incident-triage",
    ]

    try:
        response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Skill Normalized Workflow",
                "description": "Workflow save should normalize agent skills.",
                "graph_json": graph,
            },
        )

        assert response.status_code == 200
        stored_agent_node = next(
            node
            for node in store.workflow_definitions[workflow_id].graph_json["nodes"]
            if node["type"] == "agent"
        )
        assert stored_agent_node["config"]["skills"] == [
            "/incident-triage",
            "/tenant-oncall",
        ]
    finally:
        store.workflow_definitions.pop(workflow_id, None)


def test_workflow_definition_rejects_unsupported_graph_schema_version() -> None:
    workflow_id = str(uuid4())
    payload = {
        "id": workflow_id,
        "name": "Unsupported Schema Workflow",
        "description": "Workflow save should reject unsupported graph schema versions.",
        "graph_json": {
            **_sample_graph(),
            "schema_version": "locus-graph/9.9",
        },
    }

    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "unsupported graph schema version" in detail["message"].lower()
    assert detail["issues"][0]["code"] == "GRAPH_SCHEMA_VERSION_UNSUPPORTED"
    assert workflow_id not in store.workflow_definitions


@pytest.mark.parametrize(
    ("field_value", "expected_code"),
    [
        ("incident-triage", "AGENT_SKILLS_INVALID"),
        (["/incident-triage", {"bad": "shape"}], "AGENT_SKILL_TYPE_INVALID"),
        (["bad skill"], "AGENT_SKILL_PATH_INVALID"),
        ([f"skill-{index}" for index in range(129)], "AGENT_SKILLS_TOO_MANY"),
    ],
)
def test_agent_definition_rejects_invalid_graph_agent_skills(
    field_value: object, expected_code: str
) -> None:
    agent_id = str(uuid4())
    graph = _sample_graph()
    agent_node = next(node for node in graph["nodes"] if node["type"] == "agent")
    agent_node["config"]["skills"] = field_value

    response = client.post(
        "/agent-definitions",
        json={
            "id": agent_id,
            "name": "Invalid Skill Agent",
            "config_json": {"graph_json": graph},
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "invalid agent skill paths" in detail["message"].lower()
    assert detail["issues"][0]["code"] == expected_code
    assert agent_id not in store.agent_definitions


def test_clean_inbox_prompt_strips_leading_agent_mentions() -> None:
    assert (
        main_module._clean_inbox_prompt("@planner   @reviewer   Build the deployment plan")
        == "Build the deployment plan"
    )
    assert main_module._clean_inbox_prompt("No mentions here") == "No mentions here"


def test_heuristic_workflow_run_title_trims_edge_punctuation_without_regex_backtracking() -> None:
    assert (
        main_module._heuristic_workflow_run_title(
            '  ::: "Please review the rollout plan," :::  ', "workflow"
        )
        == "review the rollout plan"
    )
    assert main_module._heuristic_workflow_run_title("   ", "chat") == "New conversation"


def test_truncate_event_summary_reports_observability_metadata() -> None:
    summary, metadata = main_module._truncate_text_with_metadata("x" * 705, max_chars=700)

    assert summary.endswith("…")
    assert metadata["truncated"] is True
    assert metadata["original_length"] == 705
    assert metadata["max_chars"] == 700
    assert metadata["truncated_chars"] == 5


def test_truncate_text_with_metadata_normalizes_nonpositive_max_chars() -> None:
    # max_chars=0 and negative values must be clamped to 1 so that metadata
    # and slicing behaviour stay consistent with each other.
    for bad_value in (0, -1, -100):
        summary, metadata = main_module._truncate_text_with_metadata(
            "hello world", max_chars=bad_value
        )
        assert metadata["max_chars"] == 1, f"expected max_chars=1 for input {bad_value}"
        assert len(summary.rstrip("…")) <= 1


def test_append_audit_event_marks_store_truncation() -> None:
    original_audit_events = list(store.audit_events)
    store.audit_events = [
        main_module.AuditEvent(
            id=f"evt-{index}",
            action="demo.action",
            actor="tester",
            outcome="allowed",
            created_at=main_module._now_iso(),
            metadata={},
        )
        for index in range(2000)
    ]

    try:
        main_module._append_audit_event(
            "demo.action",
            "tester",
            "allowed",
            {"scope": "unit"},
        )

        assert len(store.audit_events) == 2000
        newest = store.audit_events[0]
        assert newest.metadata["scope"] == "unit"
        assert newest.metadata["audit_store_truncated"] is True
        assert newest.metadata["audit_store_dropped_events"] == 1
        assert newest.metadata["audit_store_limit"] == 2000
    finally:
        store.audit_events = original_audit_events


def test_auth_session_hides_oidc_validation_details(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_JWKS_URL", "http://example.com/.well-known/jwks.json")

    response = client.get("/auth/session", headers=AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["oidc"]["validation_error"] == "OIDC configuration is invalid."
    assert "trusted_issuers" not in body["oidc"]["validation_error"].lower()
    assert "locus_auth_oidc_issuer" not in body["oidc"]["validation_error"].lower()


def test_platform_version_reports_update_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "_fetch_remote_release_manifest",
        lambda: {
            "version": "9.9.9",
            "update_command": "lattix update",
            "publicRepo": "https://github.com/LATTIX-IO/lattix-locus",
        },
    )

    response = client.get("/platform/version")

    assert response.status_code == 200
    body = response.json()
    assert body["current_version"]
    assert body["latest_version"] == "9.9.9"
    assert body["update_available"] is True
    assert body["status"] == "update_available"
    assert body["update_command"] == "lattix update"
    assert body["source"] == "remote_manifest"
    assert "without deleting workflows" in body["summary"].lower()


def test_platform_version_prefers_installed_distribution_metadata(monkeypatch) -> None:
    monkeypatch.delenv("LOCUS_APP_VERSION", raising=False)
    monkeypatch.setattr(main_module, "_fetch_remote_release_manifest", lambda: None)
    monkeypatch.setattr(main_module.importlib_metadata, "version", lambda _name: "2.4.6")

    body = client.get("/platform/version").json()

    assert body["current_version"] == "2.4.6"
    assert body["status"] == "unknown"
    assert body["summary"] == "Version metadata is unavailable right now."


def test_load_seeded_agents_supports_asset_roots_outside_repo(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    external_assets_root = tmp_path / "external-assets"
    agent_dir = external_assets_root / "demo-external-agent"
    agent_dir.mkdir(parents=True)

    (agent_dir / "agent.config.json").write_text(
        json.dumps(
            {
                "id": "demo-external-agent",
                "name": "External Demo Agent",
                "version": 1,
                "tags": ["demo"],
                "capabilities": ["research"],
                "owners": ["platform"],
                "tools": [],
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "system-prompt.md").write_text(
        "You are the external demo agent.", encoding="utf-8"
    )

    monkeypatch.setattr(main_module, "_repository_root", lambda: repo_root)
    monkeypatch.setenv("LOCUS_AGENT_ASSETS_ROOT", str(external_assets_root))

    seeded = main_module._load_seeded_agents_from_repo()

    seeded_agent = next(agent for agent in seeded.values() if agent.name == "External Demo Agent")
    assert seeded_agent.config_json["seed_source"] == "demo-external-agent/agent.config.json"
    assert seeded_agent.config_json["system_prompt"] == "You are the external demo agent."


def test_version_comparator_prefers_stable_over_prerelease() -> None:
    assert main_module._version_is_newer("1.2.3", "1.2.3-rc.1") is True
    assert main_module._version_is_newer("1.2.3-rc.1", "1.2.3") is False
    assert main_module._version_is_newer("1.2.3+build.5", "1.2.3") is False
    assert main_module._version_is_newer("1.2.4-beta.1", "1.2.3") is True


def test_remote_release_manifest_uses_ttl_cache(monkeypatch) -> None:
    call_count = {"count": 0}

    monkeypatch.setenv("LOCUS_UPDATE_MANIFEST_URL", "https://example.com/install/manifest.json")
    monkeypatch.setenv("LOCUS_UPDATE_MANIFEST_CACHE_TTL_SECONDS", "300")
    monkeypatch.setitem(main_module._REMOTE_RELEASE_MANIFEST_CACHE, "manifest_url", "")
    monkeypatch.setitem(
        main_module._REMOTE_RELEASE_MANIFEST_CACHE,
        "expires_at",
        main_module.datetime.fromtimestamp(0, tz=main_module.timezone.utc),
    )
    monkeypatch.setitem(main_module._REMOTE_RELEASE_MANIFEST_CACHE, "payload", None)

    def _fake_fetch(_manifest_url: str) -> dict[str, object]:
        call_count["count"] += 1
        return {"version": "3.0.0"}

    monkeypatch.setattr(main_module, "_fetch_remote_release_manifest_from_url", _fake_fetch)

    assert main_module._fetch_remote_release_manifest() == {"version": "3.0.0"}
    assert main_module._fetch_remote_release_manifest() == {"version": "3.0.0"}
    assert call_count["count"] == 1


def test_publish_workflow_definition_hides_internal_graph_parse_details() -> None:
    workflow_id = str(uuid4())
    payload = {
        "id": workflow_id,
        "name": "Invalid Publish Workflow",
        "description": "Workflow publish should not leak parser internals.",
        "graph_json": {
            "schema_version": "locus-graph/1.0",
            "nodes": [{"id": "trigger", "type": "trigger"}],
            "links": [],
        },
    }

    save_response = client.post("/workflow-definitions", json=payload)
    assert save_response.status_code == 200

    publish_response = client.post(f"/workflow-definitions/{workflow_id}/publish")
    assert publish_response.status_code == 400
    detail = publish_response.json()["detail"]
    assert detail["message"] == "Invalid workflow graph payload"
    assert "reason" not in detail

    store.workflow_definitions.pop(workflow_id, None)
    store.workflow_definition_revisions.pop(workflow_id, None)


def test_graph_run_rejects_unsupported_graph_schema_version() -> None:
    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/2.0",
            "nodes": _sample_graph()["nodes"],
            "links": _sample_graph()["links"],
            "input": {"message": "hello"},
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Graph validation failed"
    assert detail["validation"]["issues"][0]["code"] == "GRAPH_SCHEMA_VERSION_UNSUPPORTED"


def test_graph_run_threads_assembly_retrieval_policy_to_evidence_column() -> None:
    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/1.0",
            "nodes": [
                {
                    "id": "trigger",
                    "title": "Trigger",
                    "type": "locus/trigger",
                    "config": {"trigger_mode": "manual"},
                },
                {
                    "id": "retrieve",
                    "title": "Evidence Retrieval",
                    "type": "locus/retrieval",
                    "config": {
                        "column_kind": "evidence",
                        "source_type": "knowledge_base",
                        "source_id": "kb://default",
                        "top_k": 1,
                    },
                },
            ],
            "links": [
                {"from": "trigger", "to": "retrieve", "from_port": "out", "to_port": "query"}
            ],
            "input": {
                "message": "retrieve approved evidence",
                "session_id": "session:slice-8-graph-run",
                "assembly_policy": {"allowed_retrieval_sources": ["kb://default"]},
            },
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["node_results"]["retrieve"]["policy"]["source_id"] == "kb://default"


def test_workflow_definition_versions_and_rollback_restore_prior_snapshot() -> None:
    workflow_id = str(uuid4())
    original_artifacts = list(store.artifacts)

    try:
        save_v1 = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Initial Workflow",
                "description": "First draft of the workflow.",
                "graph_json": _sample_graph(),
            },
        )
        assert save_v1.status_code == 200

        updated_graph = _sample_graph()
        updated_graph["nodes"][1]["config"] = {
            "system_prompt_text": "Use the updated instructions."
        }
        save_v2 = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Updated Workflow",
                "description": "Second draft of the workflow.",
                "graph_json": updated_graph,
            },
        )
        assert save_v2.status_code == 200

        publish = client.post(f"/workflow-definitions/{workflow_id}/publish")
        assert publish.status_code == 200

        versions_response = client.get(f"/workflow-definitions/{workflow_id}/versions")
        assert versions_response.status_code == 200
        versions = versions_response.json()["versions"]
        assert [item["action"] for item in versions] == ["publish", "save", "save"]

        initial_revision = next(
            item for item in versions if item["action"] == "save" and item["version"] == 1
        )
        revision_detail = client.get(
            f"/workflow-definitions/{workflow_id}/versions/{initial_revision['id']}"
        )
        assert revision_detail.status_code == 200
        assert revision_detail.json()["snapshot"]["name"] == "Initial Workflow"

        rollback = client.post(
            f"/workflow-definitions/{workflow_id}/rollback",
            json={"revision_id": initial_revision["id"]},
        )
        assert rollback.status_code == 200
        rollback_body = rollback.json()
        assert rollback_body["status"] == "draft"
        assert rollback_body["version"] == 4
        assert rollback_body["restored_from"]["id"] == initial_revision["id"]

        restored = store.workflow_definitions[workflow_id]
        assert restored.name == "Initial Workflow"
        assert restored.description == "First draft of the workflow."

        versions_after = client.get(f"/workflow-definitions/{workflow_id}/versions").json()[
            "versions"
        ]
        assert versions_after[0]["action"] == "rollback"
        assert versions_after[0]["metadata"]["source_revision_id"] == initial_revision["id"]
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)
        store.artifacts = original_artifacts


def test_agent_definition_versions_and_rollback_restore_prior_snapshot() -> None:
    agent_id = str(uuid4())
    original_artifacts = list(store.artifacts)

    try:
        save_v1 = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Versioned Agent",
                "config_json": {
                    "system_prompt": "Respond with the original voice.",
                    "graph_json": _sample_graph(),
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert save_v1.status_code == 200

        publish = client.post(f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS)
        assert publish.status_code == 200
        assert store.agent_definitions[agent_id].status == "published"

        changed_graph = _sample_graph()
        changed_graph["nodes"][2]["config"] = {"agent_id": "generated-agent", "model": "gpt-5.4"}
        save_v2 = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Versioned Agent v2",
                "config_json": {
                    "system_prompt": "Respond with the updated voice.",
                    "graph_json": changed_graph,
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert save_v2.status_code == 200

        versions_response = client.get(f"/agent-definitions/{agent_id}/versions")
        assert versions_response.status_code == 200
        versions = versions_response.json()["versions"]
        assert [item["action"] for item in versions] == ["save", "publish", "save"]

        published_revision = next(item for item in versions if item["action"] == "publish")
        rollback = client.post(
            f"/agent-definitions/{agent_id}/rollback",
            json={"revision_id": published_revision["id"]},
            headers=ADMIN_HEADERS,
        )
        assert rollback.status_code == 200
        rollback_body = rollback.json()
        assert rollback_body["status"] == "draft"
        assert rollback_body["version"] == 4

        restored = store.agent_definitions[agent_id]
        assert restored.name == "Versioned Agent"
        assert restored.config_json["system_prompt"] == "Respond with the original voice."

        versions_after = client.get(f"/agent-definitions/{agent_id}/versions").json()["versions"]
        assert versions_after[0]["action"] == "rollback"
        assert versions_after[0]["metadata"]["source_revision_id"] == published_revision["id"]
    finally:
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.artifacts = original_artifacts


def test_guardrail_ruleset_versions_and_rollback_restore_prior_config() -> None:
    ruleset_id = str(uuid4())

    try:
        save_v1 = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Versioned Guardrail",
                "config_json": {
                    "blocked_keywords": ["secret"],
                    "tripwire_action": "reject_content",
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert save_v1.status_code == 200

        publish = client.post(f"/guardrail-rulesets/{ruleset_id}/publish", headers=ADMIN_HEADERS)
        assert publish.status_code == 200

        save_v2 = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Versioned Guardrail v2",
                "config_json": {
                    "blocked_keywords": ["secret", "token"],
                    "tripwire_action": "reject_content",
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert save_v2.status_code == 200

        versions_response = client.get(f"/guardrail-rulesets/{ruleset_id}/versions")
        assert versions_response.status_code == 200
        versions = versions_response.json()["versions"]
        assert [item["action"] for item in versions] == ["save", "publish", "save"]

        published_revision = next(item for item in versions if item["action"] == "publish")
        revision_detail = client.get(
            f"/guardrail-rulesets/{ruleset_id}/versions/{published_revision['id']}"
        )
        assert revision_detail.status_code == 200
        assert revision_detail.json()["snapshot"]["status"] == "published"

        rollback = client.post(
            f"/guardrail-rulesets/{ruleset_id}/rollback",
            json={"revision_id": published_revision["id"]},
            headers=ADMIN_HEADERS,
        )
        assert rollback.status_code == 200
        rollback_body = rollback.json()
        assert rollback_body["status"] == "draft"
        assert rollback_body["version"] == 4

        restored = store.guardrail_rulesets[ruleset_id]
        assert restored.name == "Versioned Guardrail"
        assert restored.config_json["blocked_keywords"] == ["secret"]

        versions_after = client.get(f"/guardrail-rulesets/{ruleset_id}/versions").json()["versions"]
        assert versions_after[0]["action"] == "rollback"
        assert versions_after[0]["metadata"]["source_revision_id"] == published_revision["id"]
    finally:
        store.guardrail_rulesets.pop(ruleset_id, None)
        store.guardrail_ruleset_revisions.pop(ruleset_id, None)


def test_workflow_save_after_publish_preserves_active_published_snapshot() -> None:
    workflow_id = str(uuid4())

    try:
        create = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Published Workflow",
                "description": "First published description.",
                "graph_json": _sample_graph(),
            },
        )
        assert create.status_code == 200

        publish = client.post(f"/workflow-definitions/{workflow_id}/publish")
        assert publish.status_code == 200
        published_pointer = store.workflow_definitions[workflow_id].published_revision_id
        assert published_pointer

        edit_after_publish = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Published Workflow Draft",
                "description": "Unpublished draft change.",
                "graph_json": _sample_graph(),
            },
        )
        assert edit_after_publish.status_code == 200

        current = store.workflow_definitions[workflow_id]
        assert current.status == "draft"
        assert current.published_revision_id == published_pointer

        published_listing = client.get("/workflows/published")
        assert published_listing.status_code == 200
        published_workflow = next(
            item for item in published_listing.json() if item["id"] == workflow_id
        )
        assert published_workflow["name"] == "Published Workflow"
        assert published_workflow["description"] == "First published description."
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)


def test_agent_and_guardrail_runtime_resolution_use_pinned_published_revisions() -> None:
    agent_id = str(uuid4())
    ruleset_id = str(uuid4())

    try:
        ruleset_create = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Pinned Ruleset",
                "config_json": {"blocked_keywords": ["alpha"], "tripwire_action": "reject_content"},
            },
            headers=ADMIN_HEADERS,
        )
        assert ruleset_create.status_code == 200
        ruleset_publish = client.post(
            f"/guardrail-rulesets/{ruleset_id}/publish", headers=ADMIN_HEADERS
        )
        assert ruleset_publish.status_code == 200
        published_ruleset_pointer = store.guardrail_rulesets[ruleset_id].published_revision_id
        assert published_ruleset_pointer

        ruleset_draft = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Pinned Ruleset Draft",
                "config_json": {"blocked_keywords": ["beta"], "tripwire_action": "reject_content"},
            },
            headers=ADMIN_HEADERS,
        )
        assert ruleset_draft.status_code == 200
        assert store.guardrail_rulesets[ruleset_id].status == "draft"
        assert (
            store.guardrail_rulesets[ruleset_id].published_revision_id == published_ruleset_pointer
        )

        workflow_using_ruleset = client.post(
            "/workflow-definitions",
            json={
                "id": str(uuid4()),
                "name": "Ruleset Consumer",
                "description": "Should validate against pinned published ruleset.",
                "graph_json": _sample_graph(),
                "security_config": {"guardrail_ruleset_id": ruleset_id},
            },
        )
        assert workflow_using_ruleset.status_code == 200

        agent_create = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Pinned Agent",
                "config_json": {
                    "system_prompt": "Published system prompt.",
                    "graph_json": _sample_graph(),
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_create.status_code == 200
        agent_publish = client.post(f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS)
        assert agent_publish.status_code == 200
        published_agent_pointer = store.agent_definitions[agent_id].published_revision_id
        assert published_agent_pointer

        agent_draft = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Pinned Agent Draft",
                "config_json": {
                    "system_prompt": "Draft system prompt.",
                    "graph_json": _sample_graph(),
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_draft.status_code == 200
        assert store.agent_definitions[agent_id].status == "draft"
        assert store.agent_definitions[agent_id].published_revision_id == published_agent_pointer

        resolved_published_agent = main_module._resolve_published_agent_definition(agent_id)
        assert resolved_published_agent is not None
        assert resolved_published_agent.name == "Pinned Agent"
        assert resolved_published_agent.config_json["system_prompt"] == "Published system prompt."
    finally:
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.guardrail_rulesets.pop(ruleset_id, None)
        store.guardrail_ruleset_revisions.pop(ruleset_id, None)
        for workflow_key, workflow in list(store.workflow_definitions.items()):
            if workflow.name == "Ruleset Consumer":
                store.workflow_definitions.pop(workflow_key, None)
                store.workflow_definition_revisions.pop(workflow_key, None)


def test_republish_activates_new_revision_immediately() -> None:
    # D-20 (docs/product/20 §2): publishing makes the new revision active.
    workflow_id = str(uuid4())

    try:
        create_v1 = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Release Workflow v1",
                "description": "Initial active release.",
                "graph_json": _sample_graph(),
            },
        )
        assert create_v1.status_code == 200

        publish_v1 = client.post(f"/workflow-definitions/{workflow_id}/publish")
        assert publish_v1.status_code == 200

        first_release = store.workflow_definitions[workflow_id]
        first_published_revision_id = first_release.published_revision_id
        first_active_revision_id = first_release.active_revision_id
        assert first_published_revision_id
        assert first_active_revision_id == first_published_revision_id

        create_v2 = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Release Workflow v2",
                "description": "Second published release.",
                "graph_json": _sample_graph(),
            },
        )
        assert create_v2.status_code == 200

        publish_v2 = client.post(f"/workflow-definitions/{workflow_id}/publish")
        assert publish_v2.status_code == 200

        current = store.workflow_definitions[workflow_id]
        assert current.published_revision_id
        assert current.published_revision_id != first_published_revision_id
        assert current.active_revision_id == current.published_revision_id
        assert current.active_revision_id != first_active_revision_id

        published_listing = client.get("/workflows/published")
        assert published_listing.status_code == 200
        published_workflow = next(
            item for item in published_listing.json() if item["id"] == workflow_id
        )
        assert published_workflow["name"] == "Release Workflow v2"
        assert (
            published_workflow["description"] == "Second published release."
        )

        active_listing = client.get("/workflows/active")
        assert active_listing.status_code == 200
        active_workflow = next(item for item in active_listing.json() if item["id"] == workflow_id)
        assert active_workflow["name"] == "Release Workflow v2"

        # Activate stays available and is idempotent for the current revision.
        activate = client.post(f"/workflow-definitions/{workflow_id}/activate")
        assert activate.status_code == 200
        assert activate.json()["active_revision"]["id"] == current.published_revision_id
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)


def test_definition_library_lifecycle_actions_update_status() -> None:
    workflow_id = str(uuid4())
    agent_id = str(uuid4())

    try:
        workflow_save = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Lifecycle Workflow",
                "description": "Tracks lifecycle actions.",
                "graph_json": _sample_graph(),
            },
        )
        assert workflow_save.status_code == 200
        assert client.post(f"/workflow-definitions/{workflow_id}/publish").status_code == 200

        unpublish_workflow = client.post(f"/workflow-definitions/{workflow_id}/unpublish")
        assert unpublish_workflow.status_code == 200
        assert store.workflow_definitions[workflow_id].status == "draft"
        assert store.workflow_definitions[workflow_id].published_revision_id is None

        archive_workflow = client.post(f"/workflow-definitions/{workflow_id}/archive")
        assert archive_workflow.status_code == 200
        assert store.workflow_definitions[workflow_id].status == "archived"

        agent_save = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Lifecycle Agent",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_save.status_code == 200
        assert (
            client.post(f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS).status_code
            == 200
        )

        unpublish_agent = client.post(
            f"/agent-definitions/{agent_id}/unpublish",
            headers=ADMIN_HEADERS,
        )
        assert unpublish_agent.status_code == 200
        assert store.agent_definitions[agent_id].status == "draft"
        assert store.agent_definitions[agent_id].published_revision_id is None

        archive_agent = client.post(
            f"/agent-definitions/{agent_id}/archive",
            headers=ADMIN_HEADERS,
        )
        assert archive_agent.status_code == 200
        assert store.agent_definitions[agent_id].status == "archived"
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)


def test_playbook_library_lifecycle_actions_normalize_statuses() -> None:
    playbook_id = str(uuid4())

    try:
        save_response = client.post(
            "/playbooks",
            json={
                "id": playbook_id,
                "name": "Lifecycle Playbook",
                "description": "Tracks playbook lifecycle actions.",
                "category": "operations",
                "status": "active",
                "graph_json": _sample_graph(),
            },
        )
        assert save_response.status_code == 200
        assert store.playbooks[playbook_id].status == "published"

        listing = client.get("/playbooks")
        assert listing.status_code == 200
        listed = next(item for item in listing.json() if item["id"] == playbook_id)
        assert listed["status"] == "published"

        unpublish_response = client.post(f"/playbooks/{playbook_id}/unpublish")
        assert unpublish_response.status_code == 200
        assert store.playbooks[playbook_id].status == "draft"

        archive_response = client.post(f"/playbooks/{playbook_id}/archive")
        assert archive_response.status_code == 200
        assert store.playbooks[playbook_id].status == "archived"

        publish_response = client.post(f"/playbooks/{playbook_id}/publish")
        assert publish_response.status_code == 200
        assert store.playbooks[playbook_id].status == "published"
    finally:
        store.playbooks.pop(playbook_id, None)


def test_definition_saves_persist_for_workflows_agents_and_playbooks() -> None:
    workflow_id = str(uuid4())
    agent_id = str(uuid4())
    playbook_id = str(uuid4())

    try:
        workflow_save = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Saveable Workflow",
                "description": "Workflow should save cleanly.",
                "graph_json": _sample_graph(),
            },
        )
        assert workflow_save.status_code == 200
        workflow_detail = client.get(f"/workflow-definitions/{workflow_id}")
        assert workflow_detail.status_code == 200
        assert workflow_detail.json()["name"] == "Saveable Workflow"
        assert workflow_detail.json()["status"] == "draft"

        agent_save = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Saveable Agent",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_save.status_code == 200
        agent_detail = client.get(f"/agent-definitions/{agent_id}")
        assert agent_detail.status_code == 200
        assert agent_detail.json()["name"] == "Saveable Agent"
        assert agent_detail.json()["status"] == "draft"

        playbook_save = client.post(
            "/playbooks",
            json={
                "id": playbook_id,
                "name": "Saveable Playbook",
                "description": "Playbook should save as draft.",
                "category": "operations",
                "status": "draft",
                "graph_json": _sample_graph(),
            },
        )
        assert playbook_save.status_code == 200
        playbook_detail = client.get(f"/playbooks/{playbook_id}")
        assert playbook_detail.status_code == 200
        assert playbook_detail.json()["name"] == "Saveable Playbook"
        assert playbook_detail.json()["status"] == "draft"
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.playbooks.pop(playbook_id, None)


def test_publish_activates_agent_and_guardrail_runtime_resolution() -> None:
    # D-20 (docs/product/20 §2): publishing makes the new revision the runtime revision.
    agent_id = str(uuid4())
    ruleset_id = str(uuid4())

    try:
        ruleset_v1 = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Runtime Ruleset v1",
                "config_json": {"blocked_keywords": ["alpha"], "tripwire_action": "reject_content"},
            },
            headers=ADMIN_HEADERS,
        )
        assert ruleset_v1.status_code == 200
        assert (
            client.post(
                f"/guardrail-rulesets/{ruleset_id}/publish", headers=ADMIN_HEADERS
            ).status_code
            == 200
        )

        ruleset_v2 = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Runtime Ruleset v2",
                "config_json": {"blocked_keywords": ["beta"], "tripwire_action": "reject_content"},
            },
            headers=ADMIN_HEADERS,
        )
        assert ruleset_v2.status_code == 200
        assert (
            client.post(
                f"/guardrail-rulesets/{ruleset_id}/publish", headers=ADMIN_HEADERS
            ).status_code
            == 200
        )

        guardrail_current = store.guardrail_rulesets[ruleset_id]
        assert guardrail_current.published_revision_id
        assert guardrail_current.active_revision_id
        assert guardrail_current.active_revision_id == guardrail_current.published_revision_id

        resolved_guardrail_before, _, _ = main_module._resolve_guardrail_config(
            {"ruleset_id": ruleset_id}
        )
        assert resolved_guardrail_before["blocked_keywords"] == ["beta"]

        # Activate stays available and is idempotent for the current revision.
        activate_ruleset = client.post(
            f"/guardrail-rulesets/{ruleset_id}/activate", headers=ADMIN_HEADERS
        )
        assert activate_ruleset.status_code == 200

        resolved_guardrail_after, _, _ = main_module._resolve_guardrail_config(
            {"ruleset_id": ruleset_id}
        )
        assert resolved_guardrail_after["blocked_keywords"] == ["beta"]

        agent_v1 = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Runtime Agent v1",
                "config_json": {
                    "system_prompt": "Use the first runtime prompt.",
                    "graph_json": _sample_graph(),
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_v1.status_code == 200
        assert (
            client.post(f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS).status_code
            == 200
        )

        agent_v2 = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Runtime Agent v2",
                "config_json": {
                    "system_prompt": "Use the second runtime prompt.",
                    "graph_json": _sample_graph(),
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_v2.status_code == 200
        assert (
            client.post(f"/agent-definitions/{agent_id}/publish", headers=ADMIN_HEADERS).status_code
            == 200
        )

        agent_current = store.agent_definitions[agent_id]
        assert agent_current.published_revision_id
        assert agent_current.active_revision_id
        assert agent_current.active_revision_id == agent_current.published_revision_id

        resolved_agent_before = main_module._resolve_published_agent_definition(agent_id)
        assert resolved_agent_before is not None
        assert resolved_agent_before.name == "Runtime Agent v2"
        assert resolved_agent_before.config_json["system_prompt"] == "Use the second runtime prompt."

        activate_agent = client.post(
            f"/agent-definitions/{agent_id}/activate", headers=ADMIN_HEADERS
        )
        assert activate_agent.status_code == 200

        resolved_agent_after = main_module._resolve_published_agent_definition(agent_id)
        assert resolved_agent_after is not None
        assert resolved_agent_after.name == "Runtime Agent v2"
        assert resolved_agent_after.config_json["system_prompt"] == "Use the second runtime prompt."
    finally:
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.guardrail_rulesets.pop(ruleset_id, None)
        store.guardrail_ruleset_revisions.pop(ruleset_id, None)


def test_memory_endpoint_loads_long_term_entries_into_short_term() -> None:
    session_id = "session:tester"
    store.memory_by_session[session_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(
        bucket_id=session_id, session_id=session_id, memory_scope="session"
    )
    main_module._POSTGRES_MEMORY.append_entry(
        bucket_id=session_id,
        session_id=session_id,
        memory_scope="session",
        entry={"id": "lt-1", "content": "Remember that Acme wants a weekly update cadence."},
        source="test",
    )

    response = client.get(f"/memory/{session_id}", headers={"x-locus-actor": "tester"})
    assert response.status_code == 200
    body = response.json()
    assert body["long_term_count"] == 1
    assert any("weekly update cadence" in entry["content"] for entry in body["entries"])
    assert any(
        "weekly update cadence" in entry["content"] for entry in store.memory_by_session[session_id]
    )


def test_memory_node_append_persists_short_and_long_term_memory() -> None:
    bucket_id = "agent:test-agent"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")

    result, _meta = main_module._run_framework_memory(
        action="append",
        scope="agent",
        bucket_id=bucket_id,
        node_id="memory-1",
        message="Remember customer preferences",
        source_payload={"message": "Customer prefers SOC 2 evidence with every proposal."},
    )

    assert result["memory_state"]["entries"] >= 1
    assert any("SOC 2 evidence" in entry["content"] for entry in store.memory_by_session[bucket_id])
    long_term_entries = main_module._POSTGRES_MEMORY.get_entries(
        bucket_id=bucket_id, memory_scope="agent", limit=10
    )
    assert any("SOC 2 evidence" in entry["content"] for entry in long_term_entries)
    consolidation_candidates = main_module._POSTGRES_MEMORY.list_consolidation_candidates(
        bucket_id=bucket_id, memory_scope="agent"
    )
    assert any(
        "SOC 2 evidence" in str(entry.get("content") or "") for entry in consolidation_candidates
    )


def test_task_learning_persists_agent_memory_entry() -> None:
    agent_id = "agent-learning"
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=f"agent:{agent_id}", memory_scope="agent")

    main_module._record_task_learning(
        run_id="run-learning-1",
        actor="tester",
        prompt_text="Remember that Acme prefers weekly status emails.",
        response_text="Captured and acknowledged the weekly status email preference.",
        selected_agent_id=agent_id,
        selected_agent_name="Learning Agent",
        requested_workflows=[],
        requested_tags=["memory", "learning"],
    )

    learned_entries = main_module._POSTGRES_MEMORY.get_entries(
        bucket_id=f"agent:{agent_id}", memory_scope="agent", limit=10
    )
    assert learned_entries
    assert any("Acme prefers weekly status emails" in entry["content"] for entry in learned_entries)
    consolidation_candidates = main_module._POSTGRES_MEMORY.list_consolidation_candidates(
        bucket_id=f"agent:{agent_id}", memory_scope="agent"
    )
    assert any(entry.get("candidate_kind") == "task-learning" for entry in consolidation_candidates)


def test_memory_consolidation_processor_generates_summary_and_marks_candidates() -> None:
    bucket_id = "agent:consolidation-target"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._NEO4J_GRAPH.memory_projections = []

    main_module._memory_append_entry(
        bucket_id,
        {"id": "mem-1", "content": "Acme prefers weekly status emails with compliance highlights."},
        memory_scope="agent",
        source="memory-node",
    )
    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "mem-2",
            "content": "Acme also wants proposal packets to include recent SOC 2 evidence.",
        },
        memory_scope="agent",
        source="memory-node",
    )

    result = main_module._run_memory_consolidation(
        actor="tester", bucket_id=bucket_id, memory_scope="agent", limit=10
    )

    assert result["ok"] is True
    assert result["status"] == "processed"
    assert result["consolidated_candidates"] >= 2
    assert result["generated_entries"]
    assert any(
        "Consolidated memory summary" in entry["content"] for entry in result["generated_entries"]
    )
    assert any(entry.get("world_graph_projection") for entry in result["generated_entries"])

    long_term_entries = main_module._POSTGRES_MEMORY.get_entries(
        bucket_id=bucket_id, memory_scope="agent", limit=20
    )
    assert any(entry.get("kind") == "memory-consolidation" for entry in long_term_entries)

    candidates = main_module._POSTGRES_MEMORY.list_consolidation_candidates(
        bucket_id=bucket_id, memory_scope="agent", status="consolidated"
    )
    assert len(candidates) >= 2
    assert len(main_module._NEO4J_GRAPH.memory_projections) >= 1
    assert any(
        projection.get("owner", {}).get("id") == f"owner:{bucket_id}"
        for projection in main_module._NEO4J_GRAPH.memory_projections
    )


def test_internal_memory_consolidation_endpoint_runs_processor() -> None:
    bucket_id = "workflow:consolidation-endpoint"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="workflow")
    main_module._NEO4J_GRAPH.memory_projections = []

    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "wf-mem-1",
            "content": "Ops workflow should escalate Sev-1 incidents to humans within five minutes.",
        },
        memory_scope="workflow",
        source="memory-node",
    )
    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "wf-mem-2",
            "content": "The same workflow should notify the incident commander immediately after the human escalation.",
        },
        memory_scope="workflow",
        source="memory-node",
    )

    payload = {"bucket_id": bucket_id, "scope": "workflow", "limit": 10, "actor": "tester"}
    raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/internal/memory/consolidation/run",
        content=raw,
        headers=_signed_internal_headers(payload=raw, nonce="memory-consolidation-nonce"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["processed_candidates"] >= 1
    assert any(
        "summary" in str(entry.get("content") or "").lower() for entry in body["generated_entries"]
    )
    assert len(main_module._NEO4J_GRAPH.memory_projections) >= 1


def test_memory_consolidation_defers_when_evidence_threshold_not_met() -> None:
    bucket_id = "agent:deferred-memory"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")

    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "defer-1",
            "content": "Capture that weekly executive reports should mention control drift.",
        },
        memory_scope="agent",
        source="memory-node",
    )

    result = main_module._run_memory_consolidation(
        actor="tester", bucket_id=bucket_id, memory_scope="agent", limit=10
    )

    assert result["ok"] is True
    assert result["generated_entries"] == []
    deferred_candidates = main_module._POSTGRES_MEMORY.list_consolidation_candidates(
        bucket_id=bucket_id, memory_scope="agent", status="deferred"
    )
    assert len(deferred_candidates) == 1
    assert deferred_candidates[0]["metadata"].get("reason") == "insufficient_evidence"


def test_memory_consolidation_skips_duplicate_summaries() -> None:
    bucket_id = "agent:duplicate-memory"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._NEO4J_GRAPH.memory_projections = []

    original_overlap = os.environ.get("LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_MIN_OVERLAP")
    os.environ["LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_MIN_OVERLAP"] = "60"
    try:
        main_module._memory_append_entry(
            bucket_id,
            {
                "id": "dup-1",
                "content": "Acme requires weekly executive reports with control drift highlights.",
            },
            memory_scope="agent",
            source="memory-node",
        )
        main_module._memory_append_entry(
            bucket_id,
            {
                "id": "dup-2",
                "content": "Acme wants weekly executive reports that call out control drift and risks.",
            },
            memory_scope="agent",
            source="memory-node",
        )

        first_result = main_module._run_memory_consolidation(
            actor="tester", bucket_id=bucket_id, memory_scope="agent", limit=10
        )
        assert len(first_result["generated_entries"]) == 1

        main_module._memory_append_entry(
            bucket_id,
            {
                "id": "dup-3",
                "content": "Weekly executive reports should keep calling out control drift for Acme leadership.",
            },
            memory_scope="agent",
            source="memory-node",
        )
        main_module._memory_append_entry(
            bucket_id,
            {
                "id": "dup-4",
                "content": "Leadership updates for Acme must continue to include control drift highlights each week.",
            },
            memory_scope="agent",
            source="memory-node",
        )

        second_result = main_module._run_memory_consolidation(
            actor="tester", bucket_id=bucket_id, memory_scope="agent", limit=10
        )
        assert second_result["generated_entries"] == []

        duplicate_candidates = main_module._POSTGRES_MEMORY.list_consolidation_candidates(
            bucket_id=bucket_id, memory_scope="agent", status="duplicate"
        )
        assert len(duplicate_candidates) >= 2
    finally:
        if original_overlap is None:
            os.environ.pop("LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_MIN_OVERLAP", None)
        else:
            os.environ["LOCUS_MEMORY_CONSOLIDATION_DUPLICATE_MIN_OVERLAP"] = original_overlap


def test_internal_world_graph_projection_endpoint_replays_consolidated_memories() -> None:
    bucket_id = "agent:world-graph-replay"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._NEO4J_GRAPH.memory_projections = []

    consolidated_entry = {
        "id": "memory-summary-1",
        "at": "2026-03-21T00:00:00Z",
        "content": "Consolidated memory summary for agent:world-graph-replay:\n- Acme wants executive reports to highlight control drift.",
        "kind": "memory-consolidation",
        "memory_scope": "agent",
        "candidate_kind": "promotion",
        "bucket_id": bucket_id,
        "session_id": bucket_id,
        "source_candidate_ids": ["mem-a", "mem-b"],
        "source_count": 2,
    }
    main_module._POSTGRES_MEMORY.append_entry(
        bucket_id=bucket_id,
        session_id=bucket_id,
        memory_scope="agent",
        entry=consolidated_entry,
        source="memory-consolidation",
    )

    payload = {"bucket_id": bucket_id, "scope": "agent", "limit": 10, "actor": "tester"}
    raw = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/internal/memory/world-graph/project",
        content=raw,
        headers=_signed_internal_headers(payload=raw, nonce="world-graph-project-nonce"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["projected"] >= 1
    assert len(main_module._NEO4J_GRAPH.memory_projections) >= 1
    assert any(
        projection.get("memory", {}).get("id") == "memory-summary-1"
        for projection in main_module._NEO4J_GRAPH.memory_projections
    )


def test_hybrid_memory_context_includes_world_graph_results() -> None:
    bucket_id = "agent:hybrid-context"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._NEO4J_GRAPH.memory_projections = []

    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "hyb-1",
            "content": "Acme needs executive updates to highlight control drift and SOC 2 status.",
        },
        memory_scope="agent",
        source="memory-node",
    )
    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "hyb-2",
            "content": "Acme also expects proposal packets to include recent SOC 2 evidence.",
        },
        memory_scope="agent",
        source="memory-node",
    )
    main_module._run_memory_consolidation(
        actor="tester", bucket_id=bucket_id, memory_scope="agent", limit=10
    )

    hybrid = main_module._memory_get_hybrid_context(
        bucket_id, limit=20, memory_scope="agent", query_text="SOC 2"
    )
    assert hybrid["entries"]
    assert hybrid["world_graph_entries"]
    assert any(
        "SOC 2" in str(entry.get("content") or "") for entry in hybrid["world_graph_entries"]
    )
    assert hybrid["world_graph_topics"]


def test_causal_assembly_graph_projection_projects_persisted_runtime_state(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    goal_state = ColumnState(
        column_id="goal-1",
        assembly_id="assembly:causal-1",
        kind=ColumnKind.GOAL,
    ).with_beliefs((BeliefRecord(key="goal", value="reduce risk", confidence=0.85),))
    evidence_state = ColumnState(
        column_id="evidence-1",
        assembly_id="assembly:causal-1",
        kind=ColumnKind.EVIDENCE,
    ).with_beliefs((BeliefRecord(key="source", value="doc-7", confidence=0.88),))
    evaluation_state = ColumnState(
        column_id="evaluation-1",
        assembly_id="assembly:causal-1",
        kind=ColumnKind.EVALUATION,
    ).with_beliefs((BeliefRecord(key="score", value=0.86, confidence=0.86),))
    synthesis_state = ColumnState(
        column_id="synthesis-1",
        assembly_id="assembly:causal-1",
        kind=ColumnKind.SYNTHESIS,
    ).with_beliefs(
        (
            BeliefRecord(
                key="decision",
                value={"status": "approve"},
                confidence=0.91,
                evidence_refs=("doc-7",),
            ),
        )
    )
    commitment = Commitment(
        decision="approve",
        confidence=0.92,
        supporting_columns=("goal-1", "evidence-1", "evaluation-1", "synthesis-1"),
        dissenting_columns=(),
        blockers=(),
        next_actions=("publish",),
    )
    assembly_state = (
        AssemblyState(
            definition=AssemblyDefinition(
                assembly_id="assembly:causal-1",
                columns=("goal-1", "evidence-1", "evaluation-1", "synthesis-1"),
            )
        )
        .register_column_state(goal_state)
        .register_column_state(evidence_state)
        .register_column_state(evaluation_state)
        .register_column_state(synthesis_state)
        .with_commitment(commitment)
    )
    persist_assembly_state(
        assembly_state,
        cause={"phase": "consensus"},
        outcome="committed",
        outcome_metadata={"actor": "tester"},
        recorded_at=500.0,
    )

    result = main_module._run_causal_assembly_graph_projection(
        actor="tester", assembly_id="assembly:causal-1"
    )

    assert result["ok"] is True
    assert result["status"] == "processed"
    assert result["projected"] == 1
    projection = result["projections"][0]
    assert projection["assembly"]["assembly_id"] == "assembly:causal-1"
    assert len(projection["columns"]) == 4
    assert projection["belief_snapshots"]
    assert projection["confidence_samples"]
    assert projection["outcomes"][0]["outcome"] == "committed"
    assert any(relation["type"] == "HAS_COLUMN" for relation in projection["relations"])
    assert any(relation["type"] == "HAS_OUTCOME" for relation in projection["relations"])
    assert any(relation["type"] == "SUPPORTED_BY" for relation in projection["relations"])
    assert (
        main_module._NEO4J_GRAPH.causal_projections["assembly:causal-1"]["outcomes"][0]["outcome"]
        == "committed"
    )


def test_causal_assembly_graph_projection_overwrites_existing_graph_projection(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    initial_state = ColumnState(
        column_id="evaluation-1",
        assembly_id="assembly:causal-2",
        kind=ColumnKind.EVALUATION,
    ).with_beliefs((BeliefRecord(key="score", value=0.4, confidence=0.4),))
    persist_column_state(initial_state, cause={"phase": "draft"})

    first_result = main_module._run_causal_assembly_graph_projection(
        actor="tester", assembly_id="assembly:causal-2"
    )
    assert first_result["projected"] == 1
    assert main_module._NEO4J_GRAPH.causal_projections["assembly:causal-2"]["outcomes"] == []

    updated_state = initial_state.with_beliefs(
        (BeliefRecord(key="score", value=0.7, confidence=0.7),)
    )
    persist_column_state(updated_state, cause={"phase": "review"})
    record_assembly_outcome(
        "assembly:causal-2",
        outcome="escalated",
        metadata={"reason": "confidence_below_threshold"},
        recorded_at=900.0,
    )

    second_result = main_module._run_causal_assembly_graph_projection(
        actor="tester", assembly_id="assembly:causal-2"
    )
    stored = main_module._NEO4J_GRAPH.causal_projections["assembly:causal-2"]

    assert second_result["projected"] == 1
    assert len(main_module._NEO4J_GRAPH.causal_projections) == 1
    assert len(stored["confidence_samples"]) == 2
    assert stored["outcomes"][0]["outcome"] == "escalated"


def test_causal_assembly_graph_projection_denies_wrong_tenant(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    state = ColumnState(
        column_id="evidence-tenant",
        assembly_id="assembly:projection-tenant",
        kind=ColumnKind.EVIDENCE,
    ).with_beliefs((BeliefRecord(key="source", value="doc-1", confidence=0.8),))
    persist_column_state(
        state,
        cause={"phase": "tenant-test"},
        tenant_id="tenant-a",
        actor="tester",
        task="Tenant projection",
    )

    with pytest.raises(HTTPException) as missing_tenant:
        main_module._run_causal_assembly_graph_projection(
            actor="tester", assembly_id="assembly:projection-tenant"
        )
    with pytest.raises(HTTPException) as wrong_tenant:
        main_module._run_causal_assembly_graph_projection(
            actor="tester", assembly_id="assembly:projection-tenant", tenant_id="tenant-b"
        )

    assert missing_tenant.value.status_code == 403
    assert missing_tenant.value.detail == "Assembly tenant authorization required"
    assert wrong_tenant.value.status_code == 403
    assert wrong_tenant.value.detail == "Assembly tenant access denied"
    assert "assembly:projection-tenant" not in main_module._NEO4J_GRAPH.causal_projections


def test_causal_assembly_graph_projection_rejects_oversized_projection_before_write(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    monkeypatch.setenv("LOCUS_CAUSAL_GRAPH_MAX_COLUMNS", "1")
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    for column_id, kind in (("goal-1", ColumnKind.GOAL), ("evidence-1", ColumnKind.EVIDENCE)):
        persist_column_state(
            ColumnState(
                column_id=column_id,
                assembly_id="assembly:projection-oversized",
                kind=kind,
            ).with_beliefs((BeliefRecord(key=f"belief_{column_id}", value="ok"),)),
            cause={"phase": "oversized-test"},
            tenant_id="tenant-a",
            actor="tester",
            task="Oversized projection",
        )

    with pytest.raises(HTTPException) as exc_info:
        main_module._run_causal_assembly_graph_projection(
            actor="tester", assembly_id="assembly:projection-oversized", tenant_id="tenant-a"
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail["reason_code"] == "projection_size_exceeded"
    assert exc_info.value.detail["limit"] == "columns"
    assert "assembly:projection-oversized" not in main_module._NEO4J_GRAPH.causal_projections


def test_causal_assembly_graph_projection_unavailable_graph_reports_safe_status(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    state = ColumnState(
        column_id="goal-unavailable",
        assembly_id="assembly:projection-unavailable",
        kind=ColumnKind.GOAL,
    ).with_beliefs((BeliefRecord(key="goal", value="safe"),))
    persist_column_state(state, tenant_id="tenant-a", actor="tester", task="Unavailable projection")
    original_enabled = main_module._NEO4J_GRAPH.enabled
    try:
        main_module._NEO4J_GRAPH.enabled = False
        result = main_module._run_causal_assembly_graph_projection(
            actor="tester", assembly_id="assembly:projection-unavailable", tenant_id="tenant-a"
        )
    finally:
        main_module._NEO4J_GRAPH.enabled = original_enabled

    assert result == {
        "ok": False,
        "status": "unavailable",
        "projected": 0,
        "requested_assembly_id": "assembly:projection-unavailable",
        "projections": [],
    }
    assert "assembly:projection-unavailable" not in main_module._NEO4J_GRAPH.causal_projections


def test_causal_assembly_graph_projection_write_failure_preserves_causal_state(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}
    state = ColumnState(
        column_id="goal-write-failure",
        assembly_id="assembly:projection-write-failure",
        kind=ColumnKind.GOAL,
    ).with_beliefs((BeliefRecord(key="goal", value="preserve"),))
    persist_column_state(
        state,
        cause={"phase": "before-projection"},
        tenant_id="tenant-a",
        actor="tester",
        task="Write failure projection",
    )
    before = load_assembly_causal_state("assembly:projection-write-failure")

    def _failed_projection(*, projection: dict[str, object]) -> bool:
        return False

    monkeypatch.setattr(main_module._NEO4J_GRAPH, "project_causal_assembly", _failed_projection)
    result = main_module._run_causal_assembly_graph_projection(
        actor="tester", assembly_id="assembly:projection-write-failure", tenant_id="tenant-a"
    )
    after = load_assembly_causal_state("assembly:projection-write-failure")

    assert result["ok"] is False
    assert result["status"] == "write_failed"
    assert result["projected"] == 0
    assert before == after
    assert "assembly:projection-write-failure" not in main_module._NEO4J_GRAPH.causal_projections


def test_cortical_assembly_execution_persists_and_projects_result(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    result = main_module._run_cortical_assembly_execution(
        actor="tester",
        assembly_id="assembly:execution-1",
        tenant_id="tenant-1",
        task="Summarize operational risk",
        context={"document": "SOC2 evidence"},
    )

    assert result["ok"] is True
    assert result["assembly_id"] == "assembly:execution-1"
    assert result["outcome"] == "committed"
    assert result["commitment"]["is_ready"] is True
    assert result["projected"] == 1
    projection = main_module._NEO4J_GRAPH.causal_projections["assembly:execution-1"]
    assert projection["assembly"]["column_count"] == 4
    assert projection["outcomes"][0]["outcome"] == "committed"


def test_cortical_assembly_execution_escalates_low_confidence_result(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    main_module._NEO4J_GRAPH.causal_projections = {}

    result = main_module._run_cortical_assembly_execution(
        actor="tester",
        assembly_id="assembly:execution-2",
        task="Approve risky deployment",
        confidence_threshold=0.8,
    )

    assert result["outcome"] == "escalated"
    assert "confidence_below_threshold" in result["commitment"]["blockers"]
    assert result["projected"] == 1
    assert (
        main_module._NEO4J_GRAPH.causal_projections["assembly:execution-2"]["outcomes"][0][
            "outcome"
        ]
        == "escalated"
    )


def test_memory_read_returns_world_graph_context() -> None:
    bucket_id = "workflow:hybrid-read"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="workflow")
    main_module._NEO4J_GRAPH.memory_projections = []

    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "read-1",
            "content": "Incident workflows must notify the incident commander immediately.",
        },
        memory_scope="workflow",
        source="memory-node",
    )
    main_module._memory_append_entry(
        bucket_id,
        {
            "id": "read-2",
            "content": "Incident workflows should escalate Sev-1 cases to humans within five minutes.",
        },
        memory_scope="workflow",
        source="memory-node",
    )
    main_module._run_memory_consolidation(
        actor="tester", bucket_id=bucket_id, memory_scope="workflow", limit=10
    )

    result, _meta = main_module._run_framework_memory(
        action="read",
        scope="workflow",
        bucket_id=bucket_id,
        node_id="memory-read-1",
        message="incident commander",
        source_payload={"message": "incident commander"},
    )

    assert result["memory_items"]
    assert result["world_graph"]["entries"]
    assert result["world_graph"]["topics"]


def test_node_definitions_hide_internal_memory_node_by_default() -> None:
    default_response = client.get("/node-definitions", headers=AUTH_HEADERS)
    assert default_response.status_code == 200
    default_types = {item["type_key"] for item in default_response.json()}
    assert {
        "locus/router",
        "locus/iterator",
        "locus/transform",
        "locus/event",
        "locus/data-store",
        "locus/error-handler",
        "locus/wait",
    }.issubset(default_types)
    assert "locus/memory" not in default_types

    internal_response = client.get("/node-definitions?include_internal=true", headers=AUTH_HEADERS)
    assert internal_response.status_code == 200
    internal_types = {item["type_key"] for item in internal_response.json()}
    assert "locus/memory" in internal_types


def test_node_definition_delete_fails_closed_until_custom_lifecycle_exists() -> None:
    response = client.delete("/node-definitions/locus/router", headers=ADMIN_HEADERS)

    assert response.status_code == 501
    assert "read-only" in response.json()["detail"]


def test_guardrail_save_returns_created_identifier() -> None:
    response = client.post(
        "/guardrail-rulesets",
        json={"name": "Guardrail Save Contract", "config_json": {"stage": "output"}},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert isinstance(payload["id"], str)
    assert payload["id"]


def test_memory_scope_policy_rejects_disallowed_scope() -> None:
    execution_state = {
        "effective_security_policy": {
            "effective": {
                "allowed_memory_scopes": ["session", "agent"],
            },
        }
    }

    try:
        main_module._enforce_memory_scope_policy("global", execution_state, node_id="memory-guard")
    except RuntimeError as exc:
        assert "memory-guard" in str(exc)
        assert "global" in str(exc)
    else:
        raise AssertionError("Expected disallowed memory scope to raise RuntimeError")

    main_module._enforce_memory_scope_policy("session", execution_state, node_id="memory-guard")


def test_definition_mutations_require_auth_when_enabled_and_emit_audit_events() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    workflow_id = str(uuid4())
    store.workflow_definitions.pop(workflow_id, None)
    original_audit_events = list(store.audit_events)
    original_artifacts = list(store.artifacts)
    store.audit_events = []

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized_response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Auth Required Workflow",
                "description": "Should require authenticated mutation requests.",
                "graph_json": _sample_graph(),
            },
        )
        assert unauthorized_response.status_code == 401
        assert any(
            event.action == "workflow.definition.save" and event.outcome == "blocked"
            for event in store.audit_events
        )

        authorized_response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Auth Required Workflow",
                "description": "Should require authenticated mutation requests.",
                "graph_json": _sample_graph(),
            },
            headers=ADMIN_HEADERS,
        )
        assert authorized_response.status_code == 200

        publish_response = client.post(
            f"/workflow-definitions/{workflow_id}/publish",
            headers=ADMIN_HEADERS,
        )
        assert publish_response.status_code == 200

        save_events = [
            event
            for event in store.audit_events
            if event.action == "workflow.definition.save" and event.outcome == "allowed"
        ]
        publish_events = [
            event
            for event in store.audit_events
            if event.action == "workflow.definition.publish" and event.outcome == "allowed"
        ]
        assert save_events
        assert publish_events
        assert save_events[0].metadata.get("entity_type") == "workflow_definition"
        assert save_events[0].metadata.get("entity_id") == workflow_id
        assert publish_events[0].metadata.get("after", {}).get("status") == "published"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.workflow_definitions.pop(workflow_id, None)
        store.artifacts = original_artifacts
        store.audit_events = original_audit_events


def test_legacy_security_policy_aliases_resolve_to_canonical_endpoints() -> None:
    workflow_id = str(uuid4())
    agent_id = str(uuid4())
    store.workflow_definitions[workflow_id] = main_module.WorkflowDefinition(
        id=workflow_id,
        name="Legacy Alias Workflow",
        description="Workflow for legacy security policy alias coverage.",
        version=1,
        status="draft",
        graph_json=_sample_graph(),
        security_config={"classification": "restricted"},
    )
    store.agent_definitions[agent_id] = main_module.AgentDefinition(
        id=agent_id,
        name="Legacy Alias Agent",
        version=1,
        status="draft",
        type="graph",
        config_json={"security": {"classification": "confidential"}},
    )

    try:
        workflow_alias = client.get(f"/workflows/{workflow_id}/security-policy")
        workflow_canonical = client.get(f"/workflow-definitions/{workflow_id}/security-policy")
        assert workflow_alias.status_code == 200
        assert workflow_alias.json() == workflow_canonical.json()

        agent_alias = client.get(f"/agents/{agent_id}/security-policy")
        agent_canonical = client.get(f"/agent-definitions/{agent_id}/security-policy")
        assert agent_alias.status_code == 200
        assert agent_alias.json() == agent_canonical.json()
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.agent_definitions.pop(agent_id, None)


def test_integration_mutations_require_auth_and_emit_audit_events() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    integration_id = str(uuid4())
    store.integrations.pop(integration_id, None)
    original_audit_events = list(store.audit_events)
    store.audit_events = []

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized_response = client.post(
            "/integrations",
            json={
                "id": integration_id,
                "name": "Secure Integration",
                "type": "http",
                "base_url": "http://localhost:9999/test",
            },
        )
        assert unauthorized_response.status_code == 401
        assert any(
            event.action == "integration.save" and event.outcome == "blocked"
            for event in store.audit_events
        )

        authorized_response = client.post(
            "/integrations",
            json={
                "id": integration_id,
                "name": "Secure Integration",
                "type": "http",
                "base_url": "http://localhost:9999/test",
            },
            headers=ADMIN_HEADERS,
        )
        assert authorized_response.status_code == 200

        test_response = client.post(
            f"/integrations/{integration_id}/test",
            headers=ADMIN_HEADERS,
        )
        assert test_response.status_code == 200

        delete_response = client.delete(
            f"/integrations/{integration_id}",
            headers=ADMIN_HEADERS,
        )
        assert delete_response.status_code == 200

        assert any(
            event.action == "integration.save" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "integration.test" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "integration.delete" and event.outcome == "allowed"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.integrations.pop(integration_id, None)
        store.audit_events = original_audit_events


def test_save_integration_persists_valid_routing_and_policy_lists() -> None:
    integration_id = str(uuid4())
    store.integrations.pop(integration_id, None)

    try:
        response = client.post(
            "/integrations",
            json={
                "id": integration_id,
                "name": "Incident Connector",
                "type": "http",
                "base_url": "https://incident.example.com/api",
                "capabilities": ["/incident-triage", "ops", "ops"],
                "permission_scopes": ["tickets:read", "tickets:write"],
                "data_access": ["incidents.summary", "incidents:read"],
                "egress_allowlist": ["incident.example.com:443", "https://api.example.com/v1"],
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        stored = store.integrations[integration_id]
        assert stored.capabilities == ["/incident-triage", "ops"]
        assert stored.permission_scopes == ["tickets:read", "tickets:write"]
        assert stored.data_access == ["incidents.summary", "incidents:read"]
        assert stored.egress_allowlist == ["incident.example.com:443", "https://api.example.com/v1"]
    finally:
        store.integrations.pop(integration_id, None)


def test_save_integration_persists_oauth_form_authored_metadata() -> None:
    integration_id = str(uuid4())
    store.integrations.pop(integration_id, None)

    try:
        response = client.post(
            "/integrations",
            json={
                "id": integration_id,
                "name": "Microsoft Graph Connector",
                "type": "http",
                "status": "draft",
                "base_url": "https://graph.microsoft.com/v1.0",
                "auth_type": "oauth2",
                "secret_ref": "secret/integrations/microsoft/client-secret",
                "capabilities": ["/mail", "/calendar"],
                "metadata_json": {
                    "auth": {
                        "method": "oauth2",
                        "provider": "microsoft",
                        "grant_type": "authorization_code",
                        "authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                        "client_id": "locus-microsoft-client",
                        "scopes": ["User.Read", "Mail.ReadWrite", "offline_access"],
                        "audience": "https://graph.microsoft.com",
                        "resource": "",
                        "tenant": "common",
                        "redirect_path": "/builder/integrations?oauth_panel=1",
                        "client_secret_ref": "secret/integrations/microsoft/client-secret",
                        "token_secret_ref": "secret/integrations/microsoft/access-token",
                        "refresh_token_secret_ref": "secret/integrations/microsoft/refresh-token",
                        "account_label": "Customer success mailbox",
                    }
                },
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        stored = store.integrations[integration_id]
        auth = stored.metadata_json["auth"]
        assert stored.auth_type == "oauth2"
        assert stored.secret_ref == "secret/integrations/microsoft/client-secret"
        assert stored.capabilities == ["/mail", "/calendar"]
        assert auth["method"] == "oauth2"
        assert auth["provider"] == "microsoft"
        assert auth["grant_type"] == "authorization_code"
        assert (
            auth["authorize_url"]
            == "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        )
        assert auth["token_url"] == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        assert auth["client_id"] == "locus-microsoft-client"
        assert auth["scopes"] == ["User.Read", "Mail.ReadWrite", "offline_access"]
        assert auth["audience"] == "https://graph.microsoft.com"
        assert auth["tenant"] == "common"
        assert auth["redirect_path"] == "/builder/integrations?oauth_panel=1"
        assert auth["client_secret_ref"] == "secret/integrations/microsoft/client-secret"
        assert auth["token_secret_ref"] == "secret/integrations/microsoft/access-token"
        assert auth["refresh_token_secret_ref"] == "secret/integrations/microsoft/refresh-token"
        assert auth["account_label"] == "Customer success mailbox"
        assert "oauth_session" not in stored.metadata_json
    finally:
        store.integrations.pop(integration_id, None)


def test_save_integration_rejects_discouraged_google_client_credentials_oauth() -> None:
    response = client.post(
        "/integrations",
        json={
            "id": str(uuid4()),
            "name": "Google Workspace Backend Sync",
            "type": "http",
            "status": "draft",
            "base_url": "https://www.googleapis.com",
            "auth_type": "oauth2",
            "secret_ref": "secret/integrations/google/client-secret",
            "metadata_json": {
                "auth": {
                    "method": "oauth2",
                    "provider": "google",
                    "grant_type": "client_credentials",
                    "token_url": "https://oauth2.googleapis.com/token",
                    "client_id": "locus-google-client",
                    "scopes": [],
                    "audience": "https://www.googleapis.com",
                }
            },
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "oauth2 provider google does not support generic client_credentials connectors; use authorization_code or a custom provider flow"
    )


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_detail"),
    [
        ("capabilities", "incident-triage", "capabilities must be a list"),
        ("capabilities", ["incident-triage", {"bad": "shape"}], "capabilities[1] must be a string"),
        ("capabilities", ["bad capability"], "capabilities contains an invalid entry"),
        ("permission_scopes", ["scope" * 80], "permission_scopes contains an invalid entry"),
        ("data_access", ["incidents read"], "data_access contains an invalid entry"),
        ("egress_allowlist", "api.example.com", "egress_allowlist must be a list"),
        ("egress_allowlist", ["api.example.com", 42], "egress_allowlist[1] must be a string"),
        (
            "egress_allowlist",
            ["ftp://api.example.com"],
            "egress_allowlist contains an unsupported URL scheme",
        ),
        (
            "egress_allowlist",
            ["https://user:pass@api.example.com"],
            "egress_allowlist contains an invalid entry",
        ),
        ("egress_allowlist", ["api example.com"], "egress_allowlist contains an invalid entry"),
    ],
)
def test_save_integration_rejects_invalid_policy_list_fields(
    field_name: str, field_value: object, expected_detail: str
) -> None:
    response = client.post(
        "/integrations",
        json={
            "id": str(uuid4()),
            "name": "Invalid Integration",
            "type": "http",
            "base_url": "https://invalid.example.com/api",
            field_name: field_value,
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_get_integration_starters_requires_auth_and_returns_catalog() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized = client.get("/integrations/starters")
        assert unauthorized.status_code == 401

        authorized = client.get("/integrations/starters", headers=ADMIN_HEADERS)
        assert authorized.status_code == 200
        body = authorized.json()
        assert any(item["id"] == "github" and item["wave"] == 1 for item in body)
        assert any(item["id"] == "pinecone" and item["wave"] == 2 for item in body)
        assert any(item["id"] == "microsoft-graph" and item["wave"] == 3 for item in body)
        assert any(item["id"] == "google-workspace" and item["wave"] == 3 for item in body)
        assert any(item["id"] == "salesforce" and item["wave"] == 3 for item in body)
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_get_mcp_connection_starters_requires_auth_and_returns_catalog() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized = client.get("/integrations/mcp/starters")
        assert unauthorized.status_code == 401

        authorized = client.get("/integrations/mcp/starters", headers=ADMIN_HEADERS)
        assert authorized.status_code == 200
        body = authorized.json()
        assert any(item["id"] == "github" and item["wave"] == 1 for item in body)
        assert any(item["id"] == "pinecone" and item["wave"] == 2 for item in body)
        assert any(item["id"] == "microsoft-graph" and item["wave"] == 3 for item in body)
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_save_mcp_connection_validate_and_approve() -> None:
    original_allowed_urls = list(store.platform_settings.allowed_mcp_server_urls)
    original_require_local = store.platform_settings.mcp_require_local_server

    try:
        store.platform_settings.allowed_mcp_server_urls = ["http://localhost:7071/mcp/github"]
        store.platform_settings.mcp_require_local_server = True

        create_response = client.post(
            "/integrations/mcp",
            json={
                "starter_id": "github",
                "name": "GitHub MCP",
                "server_url": "http://localhost:7071/mcp/github",
                "auth_type": "bearer",
                "secret_ref": "secret/integrations/mcp/github/token",
            },
            headers=ADMIN_HEADERS,
        )

        assert create_response.status_code == 200
        connection_id = create_response.json()["id"]

        list_response = client.get("/integrations/mcp", headers=ADMIN_HEADERS)
        assert list_response.status_code == 200
        created = next(item for item in list_response.json() if item["id"] == connection_id)
        assert created["status"] == "draft"
        assert created["server_url"] == "http://localhost:7071/mcp/github"
        assert created["secret_configured"] is True

        validate_response = client.post(
            f"/integrations/mcp/{connection_id}/validate",
            headers=ADMIN_HEADERS,
        )
        assert validate_response.status_code == 200
        validate_body = validate_response.json()
        assert validate_body["ok"] is True
        assert validate_body["status"] == "validated"
        assert validate_body["validation"]["ok"] is True

        approve_response = client.post(
            f"/integrations/mcp/{connection_id}/approve",
            headers=ADMIN_HEADERS,
        )
        assert approve_response.status_code == 200
        approve_body = approve_response.json()
        assert approve_body["ok"] is True
        assert approve_body["status"] == "approved"
        assert store.mcp_connections[connection_id].approved_by == "locus-admin"
    finally:
        store.platform_settings.allowed_mcp_server_urls = original_allowed_urls
        store.platform_settings.mcp_require_local_server = original_require_local


def test_mcp_connection_validation_fails_closed_before_approval() -> None:
    original_allowed_urls = list(store.platform_settings.allowed_mcp_server_urls)
    original_require_local = store.platform_settings.mcp_require_local_server

    try:
        store.platform_settings.allowed_mcp_server_urls = ["http://localhost:7071/mcp"]
        store.platform_settings.mcp_require_local_server = True

        create_response = client.post(
            "/integrations/mcp",
            json={
                "starter_id": "microsoft-graph",
                "name": "Remote Graph MCP",
                "server_url": "https://graph.example.com/mcp",
                "auth_type": "oauth2",
                "secret_ref": "secret/integrations/mcp/graph/client-secret",
            },
            headers=ADMIN_HEADERS,
        )

        assert create_response.status_code == 200
        connection_id = create_response.json()["id"]

        validate_response = client.post(
            f"/integrations/mcp/{connection_id}/validate",
            headers=ADMIN_HEADERS,
        )
        assert validate_response.status_code == 200
        validate_body = validate_response.json()
        assert validate_body["ok"] is False
        assert validate_body["status"] == "validation_failed"
        assert any(
            "allowed_mcp_server_urls" in message or "local/private" in message
            for message in validate_body["validation"]["errors"]
        )

        approve_response = client.post(
            f"/integrations/mcp/{connection_id}/approve",
            headers=ADMIN_HEADERS,
        )
        assert approve_response.status_code == 409
        assert (
            approve_response.json()["detail"]
            == "mcp connection must validate successfully before approval"
        )
    finally:
        store.platform_settings.allowed_mcp_server_urls = original_allowed_urls
        store.platform_settings.mcp_require_local_server = original_require_local


def test_oauth_authorization_code_connect_and_callback_persists_session(monkeypatch) -> None:
    integration_id = str(uuid4())
    store.integrations[integration_id] = main_module.IntegrationDefinition(
        id=integration_id,
        name="Google Drive",
        type="http",
        status="draft",
        base_url="https://www.googleapis.com/drive/v3",
        auth_type="oauth2",
        secret_ref="secret/integrations/google/client-secret",
        metadata_json={
            "auth": {
                "method": "oauth2",
                "provider": "google",
                "grant_type": "authorization_code",
                "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_url": "https://oauth2.googleapis.com/token",
                "client_id": "google-client-id",
                "scopes": [
                    "openid",
                    "email",
                    "https://www.googleapis.com/auth/drive.readonly",
                ],
                "redirect_path": "/builder/integrations",
            }
        },
    )
    monkeypatch.setenv(
        main_module._secret_ref_to_env_var("secret/integrations/google/client-secret"),
        "google-client-secret",
    )
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "access_token": "drive-access-token",
                "refresh_token": "drive-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }

    def _fake_httpx_post(url: str, *, data=None, headers=None, timeout=None, follow_redirects=None):
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        captured["follow_redirects"] = follow_redirects
        return _FakeResponse()

    monkeypatch.setattr(main_module.httpx, "post", _fake_httpx_post)

    try:
        with TestClient(app, base_url="https://console.example.com") as local_client:
            connect = local_client.post(
                f"/integrations/{integration_id}/oauth/connect",
                json={"return_to": "/builder/integrations"},
                headers=ADMIN_HEADERS,
            )
            assert connect.status_code == 200
            connect_body = connect.json()
            connect_query = dict(
                main_module.parse_qsl(main_module.urlsplit(connect_body["connect_url"]).query)
            )
            assert connect_query["client_id"] == "google-client-id"
            assert connect_query["access_type"] == "offline"
            assert connect_query["prompt"] == "consent"
            state = connect_query["state"]

            callback = local_client.get(
                f"/integrations/{integration_id}/oauth/callback?code=auth-code&state={state}",
                follow_redirects=False,
            )

            assert callback.status_code == 302
            assert callback.headers["location"].startswith("/builder/integrations?oauth=connected")

            stored = store.integrations[integration_id]
            session = stored.metadata_json["oauth_session"]
            assert session["access_token_encrypted"]
            assert session["refresh_token_encrypted"]
            assert (
                main_module._integration_auth_headers(stored)["Authorization"]
                == "Bearer drive-access-token"
            )

            status_response = local_client.get(
                f"/integrations/{integration_id}/oauth/status",
                headers=ADMIN_HEADERS,
            )
            assert status_response.status_code == 200
            status_body = status_response.json()
            assert status_body["connected"] is True
            assert status_body["has_refresh_token"] is True
            assert status_body["provider"] == "google"

            token_request = captured["data"]
            assert isinstance(token_request, dict)
            assert token_request["grant_type"] == "authorization_code"
            assert token_request["code"] == "auth-code"
            assert token_request["client_id"] == "google-client-id"
            assert captured["url"] == "https://oauth2.googleapis.com/token"
    finally:
        store.integrations.pop(integration_id, None)


def test_linear_mcp_catalog_connect_registers_public_client_and_uses_pkce(monkeypatch) -> None:
    from app.linear_mcp_oauth import EXPECTED_ISSUER, EXPECTED_RESOURCE

    integration_id = str(uuid4())
    integration = main_module.IntegrationDefinition(
        id=integration_id,
        name="Linear MCP",
        type="custom",
        status="draft",
        base_url="https://mcp.linear.app/mcp",
        auth_type="oauth2",
        metadata_json={"catalog_id": "mcp-linear"},
    )
    store.integrations[integration_id] = integration
    calls: list[tuple[str, str, dict[str, object]]] = []

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    def _fake_httpx_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(("get", url, kwargs))
        return _FakeResponse(
            {
                "issuer": EXPECTED_ISSUER,
                "authorization_endpoint": "https://mcp.linear.app/authorize",
                "token_endpoint": "https://mcp.linear.app/token",
                "registration_endpoint": "https://mcp.linear.app/register",
                "resource": EXPECTED_RESOURCE,
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    def _fake_httpx_post(url: str, **kwargs: object) -> _FakeResponse:
        calls.append(("post", url, kwargs))
        if url.endswith("/register"):
            registration = kwargs["json"]
            assert isinstance(registration, dict)
            return _FakeResponse(
                {
                    "client_id": "linear-client-id",
                    "redirect_uris": registration["redirect_uris"],
                    "token_endpoint_auth_method": "none",
                }
            )
        token_request = kwargs["data"]
        assert isinstance(token_request, dict)
        calls.append(("token-request", url, token_request))
        return _FakeResponse(
            {
                "access_token": "linear-access-token",
                "refresh_token": "linear-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        )

    monkeypatch.setattr(main_module.httpx, "get", _fake_httpx_get)
    monkeypatch.setattr(main_module.httpx, "post", _fake_httpx_post)

    try:
        with TestClient(app, base_url="https://console.example.com") as local_client:
            connect = local_client.post(
                f"/integrations/{integration_id}/oauth/connect",
                json={"return_to": "/settings/self-improvement"},
                headers=ADMIN_HEADERS,
            )

            assert connect.status_code == 200, connect.text
            body = connect.json()
            query = dict(main_module.parse_qsl(main_module.urlsplit(body["connect_url"]).query))
            assert query["client_id"] == "linear-client-id"
            assert query["scope"] == "read write"
            assert query["resource"] == EXPECTED_RESOURCE
            assert query["code_challenge_method"] == "S256"
            assert query["code_challenge"]
            registration_calls = [
                call for call in calls if call[0] == "post" and call[1].endswith("/register")
            ]
            assert len(registration_calls) == 1

            callback = local_client.get(
                f"/integrations/{integration_id}/oauth/callback"
                f"?code=auth-code&state={query['state']}",
                follow_redirects=False,
            )
            assert callback.status_code == 302
            assert "settings/self-improvement?oauth=connected" in callback.headers["location"]

            token_request = next(call[2] for call in calls if call[0] == "token-request")
            assert token_request["grant_type"] == "authorization_code"
            assert token_request["client_id"] == "linear-client-id"
            assert token_request["redirect_uri"] == body["redirect_uri"]
            assert token_request["code_verifier"]
            assert "client_secret" not in token_request

            session = store.integrations[integration_id].metadata_json["oauth_session"]
            assert session["access_token_encrypted"]
            assert session["refresh_token_encrypted"]
            assert session["pkce_verifier_encrypted"] == ""
            assert (
                main_module._integration_auth_headers(
                    store.integrations[integration_id]
                )["Authorization"]
                == "Bearer linear-access-token"
            )
    finally:
        store.integrations.pop(integration_id, None)


def test_oauth_refresh_and_disconnect_support_client_credentials(monkeypatch) -> None:
    integration_id = str(uuid4())
    store.integrations[integration_id] = main_module.IntegrationDefinition(
        id=integration_id,
        name="Microsoft Graph",
        type="http",
        status="draft",
        base_url="https://graph.microsoft.com/v1.0",
        auth_type="oauth2",
        secret_ref="secret/integrations/microsoft/client-secret",
        metadata_json={
            "auth": {
                "method": "oauth2",
                "provider": "microsoft",
                "grant_type": "client_credentials",
                "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "client_id": "microsoft-client-id",
                "scopes": ["https://graph.microsoft.com/.default"],
            }
        },
    )
    monkeypatch.setenv(
        main_module._secret_ref_to_env_var("secret/integrations/microsoft/client-secret"),
        "microsoft-client-secret",
    )

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "access_token": "graph-client-token",
                "token_type": "Bearer",
                "expires_in": 1800,
            }

    monkeypatch.setattr(main_module.httpx, "post", lambda *args, **kwargs: _FakeResponse())

    try:
        refresh = client.post(
            f"/integrations/{integration_id}/oauth/refresh",
            headers=ADMIN_HEADERS,
        )
        assert refresh.status_code == 200
        assert refresh.json()["status"]["connected"] is True

        disconnect = client.post(
            f"/integrations/{integration_id}/oauth/disconnect",
            headers=ADMIN_HEADERS,
        )
        assert disconnect.status_code == 200
        assert disconnect.json()["status"]["connected"] is False
        assert main_module._integration_auth_headers(store.integrations[integration_id]) == {}
    finally:
        store.integrations.pop(integration_id, None)


def test_template_and_collaboration_mutations_require_auth_and_emit_audit_events() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    store.audit_events = []

    template_id = str(uuid4())
    store.agent_templates[template_id] = main_module.AgentTemplate(
        id=template_id,
        name="Secure Template",
        description="Template for auth coverage.",
        config_json={"graph_json": _sample_graph()},
    )

    try:
        store.platform_settings.require_authenticated_requests = True

        unauthorized_template = client.post(
            f"/templates/agents/{template_id}/instantiate",
            json={"name": "Template Instance"},
        )
        assert unauthorized_template.status_code == 401

        template_response = client.post(
            f"/templates/agents/{template_id}/instantiate",
            json={"name": "Template Instance"},
            headers=ADMIN_HEADERS,
        )
        assert template_response.status_code == 200
        created_agent_id = template_response.json()["id"]

        unauthorized_join = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": created_agent_id,
                "user_id": "tester",
                "display_name": "Tester",
            },
        )
        assert unauthorized_join.status_code == 401

        join_response = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": created_agent_id,
                "user_id": "tester",
                "display_name": "Tester",
            },
            headers=AUTH_HEADERS,
        )
        assert join_response.status_code == 200
        session_id = join_response.json()["session"]["id"]

        sync_response = client.post(
            f"/collab/sessions/{session_id}/sync",
            json={"user_id": "tester", "graph_json": _sample_graph()},
            headers=AUTH_HEADERS,
        )
        assert sync_response.status_code == 200

        assert any(
            event.action == "template.agent.instantiate" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "collab.session.join" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "collab.session.sync" and event.outcome == "allowed"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.agent_templates.pop(template_id, None)
        for agent_id in list(store.agent_definitions.keys()):
            if str(store.agent_definitions[agent_id].name).startswith("Template Instance"):
                store.agent_definitions.pop(agent_id, None)
        store.collaboration_sessions = {}
        store.audit_events = original_audit_events


def test_sensitive_read_routes_require_auth_when_enabled() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    store.audit_events = []
    session_id = "session:tester"
    store.memory_by_session[session_id] = [{"id": "mem-1", "content": "keep this safe"}]

    collab_session = main_module.CollaborationSession(
        id="agent:auth-read-agent",
        entity_type="agent",
        entity_id="auth-read-agent",
        graph_json=_sample_graph(),
        version=1,
        updated_at=main_module._now_iso(),
        participants=[],
    )
    store.collaboration_sessions[collab_session.id] = collab_session

    try:
        store.platform_settings.require_authenticated_requests = True

        assert client.get("/platform/settings").status_code == 401
        assert client.get(f"/memory/{session_id}").status_code == 401
        assert client.get("/audit/events").status_code == 401
        assert client.get(f"/collab/sessions/{collab_session.id}").status_code == 401
        assert client.delete(f"/memory/{session_id}").status_code == 401

        headers = AUTH_HEADERS
        assert client.get("/platform/settings", headers=headers).status_code == 200
        assert client.get(f"/memory/{session_id}", headers=headers).status_code == 200
        assert client.get("/audit/events", headers=headers).status_code == 403
        assert client.get("/audit/events", headers=ADMIN_HEADERS).status_code == 200
        assert (
            client.get(f"/collab/sessions/{collab_session.id}", headers=headers).status_code == 200
        )
        assert client.delete(f"/memory/{session_id}", headers=headers).status_code == 200

        assert any(
            event.action == "platform.settings.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "memory.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "audit.events.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "collab.session.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
        assert any(
            event.action == "memory.clear" and event.outcome == "allowed"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.audit_events = original_audit_events
        store.collaboration_sessions.pop(collab_session.id, None)
        store.memory_by_session.pop(session_id, None)


def test_secure_local_mode_fail_closes_sensitive_diagnostics(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    store.audit_events = []

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_SECURE_LOCAL_MODE", "true")
        monkeypatch.delenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)

        assert client.get("/platform/security-policy").status_code == 401
        assert client.get("/runtime/providers").status_code == 401
        assert client.get("/runtime/local-integration-readiness").status_code == 401

        headers = AUTH_HEADERS
        assert client.get("/platform/security-policy", headers=headers).status_code == 200
        assert client.get("/runtime/providers", headers=headers).status_code == 200
        assert (
            client.get("/runtime/local-integration-readiness", headers=headers).status_code == 200
        )

        assert any(
            event.action == "platform.security_policy.read" and event.outcome == "blocked"
            for event in store.audit_events
        )
        assert any(
            event.action == "runtime.providers.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.audit_events = original_audit_events


def test_secure_local_mode_keeps_public_health_minimal_and_gates_details(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    store.audit_events = []

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_SECURE_LOCAL_MODE", "true")
        monkeypatch.delenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)

        public_health = client.get("/healthz")
        assert public_health.status_code == 200
        assert public_health.json()["status"] == "ok"
        assert "postgres" not in public_health.json()

        assert client.get("/healthz/details").status_code == 401

        detailed_health = client.get("/healthz/details", headers=AUTH_HEADERS)
        assert detailed_health.status_code == 200
        assert "postgres" in detailed_health.json()
        assert any(
            event.action == "health.details.read" and event.outcome == "allowed"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.audit_events = original_audit_events


def test_health_details_exposes_postgres_reason_when_state_store_is_degraded(monkeypatch) -> None:
    class _DegradedStateStore:
        enabled = False

        def status(self) -> tuple[str, str]:
            return "degraded", "ImportError: libpq library not found"

    class _ConnectedLongTermStore:
        enabled = True

        def status(self) -> tuple[str, str]:
            return "connected", ""

    original_state_store = main_module._POSTGRES_STATE
    original_long_term_store = main_module._POSTGRES_MEMORY

    try:
        monkeypatch.setattr(main_module, "_POSTGRES_STATE", _DegradedStateStore())
        monkeypatch.setattr(main_module, "_POSTGRES_MEMORY", _ConnectedLongTermStore())

        response = client.get("/healthz/details", headers=AUTH_HEADERS)

        assert response.status_code == 200
        assert response.json()["postgres"] == "degraded"
        assert "libpq library not found" in response.json()["postgres_reason"]
    finally:
        monkeypatch.setattr(main_module, "_POSTGRES_STATE", original_state_store)
        monkeypatch.setattr(main_module, "_POSTGRES_MEMORY", original_long_term_store)


def test_persist_store_state_logs_failures(caplog, monkeypatch) -> None:
    class _BrokenStateStore:
        def save_state(self, _payload: dict[str, object]) -> None:
            raise RuntimeError("write failed")

    original_state_store = main_module._POSTGRES_STATE

    try:
        monkeypatch.setattr(main_module, "_POSTGRES_STATE", _BrokenStateStore())

        with caplog.at_level(logging.WARNING):
            main_module._persist_store_state()

        assert any(
            "Failed to persist state store snapshot" in message for message in caplog.messages
        )
    finally:
        monkeypatch.setattr(main_module, "_POSTGRES_STATE", original_state_store)


def test_secure_local_mode_fail_closes_mutation_routes_without_store_toggle(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    workflow_id = str(uuid4())
    store.workflow_definitions.pop(workflow_id, None)

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_SECURE_LOCAL_MODE", "true")
        monkeypatch.delenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)

        unauthorized_response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Secure Local Auth Workflow",
                "description": "Should require auth in secure local mode.",
                "graph_json": _sample_graph(),
            },
        )
        assert unauthorized_response.status_code == 401

        authorized_response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Secure Local Auth Workflow",
                "description": "Should require auth in secure local mode.",
                "graph_json": _sample_graph(),
            },
            headers=ADMIN_HEADERS,
        )
        assert authorized_response.status_code == 200
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.workflow_definitions.pop(workflow_id, None)


def test_secure_local_mode_uses_expiring_nonce_replay_cache(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_seen_nonces = dict(store.a2a_seen_nonces)
    original_redis_enabled = main_module._REDIS_MEMORY.enabled

    try:
        store.platform_settings.require_authenticated_requests = False
        store.a2a_seen_nonces = {}
        main_module._REDIS_MEMORY.enabled = False
        monkeypatch.setenv("LOCUS_SECURE_LOCAL_MODE", "true")
        monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")
        monkeypatch.setenv("LOCUS_A2A_NONCE_TTL_SECONDS", "60")

        headers = _signed_internal_headers(nonce="nonce-1")

        first = client.get("/platform/security-policy", headers=headers)
        assert first.status_code == 200

        replay = client.get("/platform/security-policy", headers=headers)
        assert replay.status_code == 409

        store.a2a_seen_nonces["nonce-1"] = (
            main_module.datetime.now(main_module.timezone.utc) - main_module.timedelta(seconds=1)
        ).isoformat()

        expired_reuse = client.get("/platform/security-policy", headers=headers)
        assert expired_reuse.status_code == 200
        assert "nonce-1" in store.a2a_seen_nonces
    finally:
        main_module._REDIS_MEMORY.enabled = original_redis_enabled
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.a2a_seen_nonces = original_seen_nonces


def test_signed_a2a_nonce_replay_survives_in_memory_reset_with_redis_cache(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_seen_nonces = dict(store.a2a_seen_nonces)
    original_redis_enabled = main_module._REDIS_MEMORY.enabled
    original_redis_nonces = dict(getattr(main_module._REDIS_MEMORY, "_nonces", {}))

    try:
        store.platform_settings.require_authenticated_requests = False
        store.a2a_seen_nonces = {}
        main_module._REDIS_MEMORY.enabled = True
        if hasattr(main_module._REDIS_MEMORY, "_nonces"):
            main_module._REDIS_MEMORY._nonces = {}
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        monkeypatch.setenv("LOCUS_A2A_NONCE_TTL_SECONDS", "60")

        headers = _signed_internal_headers(nonce="redis-backed-nonce-1")

        first = client.get("/platform/security-policy", headers=headers)
        assert first.status_code == 200

        store.a2a_seen_nonces = {}

        replay = client.get("/platform/security-policy", headers=headers)
        assert replay.status_code == 409
    finally:
        main_module._REDIS_MEMORY.enabled = original_redis_enabled
        if hasattr(main_module._REDIS_MEMORY, "_nonces"):
            main_module._REDIS_MEMORY._nonces = original_redis_nonces
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.a2a_seen_nonces = original_seen_nonces


def test_signed_a2a_nonce_replay_survives_restart_via_state_snapshot_when_redis_unavailable(
    monkeypatch,
) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_seen_nonces = dict(store.a2a_seen_nonces)
    original_redis_enabled = main_module._REDIS_MEMORY.enabled
    original_state_payload = main_module._POSTGRES_STATE.load_state()

    try:
        store.platform_settings.require_authenticated_requests = False
        store.a2a_seen_nonces = {}
        main_module._REDIS_MEMORY.enabled = False
        main_module._POSTGRES_STATE._payload = None
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        monkeypatch.setenv("LOCUS_A2A_NONCE_TTL_SECONDS", "60")

        headers = _signed_internal_headers(nonce="persisted-nonce-1")

        first = client.get("/platform/security-policy", headers=headers)
        assert first.status_code == 200

        persisted = main_module._POSTGRES_STATE.load_state()
        assert isinstance(persisted, dict)
        assert persisted.get("a2a_seen_nonces", {}).get("persisted-nonce-1")

        store.a2a_seen_nonces = {}
        main_module._apply_store_state(persisted)

        replay = client.get("/platform/security-policy", headers=headers)
        assert replay.status_code == 409
    finally:
        main_module._REDIS_MEMORY.enabled = original_redis_enabled
        main_module._POSTGRES_STATE._payload = original_state_payload
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.a2a_seen_nonces = original_seen_nonces


def test_platform_settings_round_trip_persists_banner_and_console_fields_across_restart() -> None:
    original_settings = store.platform_settings.model_copy(deep=True)
    original_state_payload = main_module._POSTGRES_STATE.load_state()

    payload = {
        "org_name": "Acme Locus",
        "org_slug": "acme-locus",
        "support_email": "ops@acme.example",
        "website": "https://acme.example/locus",
        "console_classification_banner_enabled": False,
        "console_classification_banner_text": "Restricted • Incident Console",
        "console_classification_banner_background_color": "#1d4ed8",
        "console_classification_banner_text_color": "#eff6ff",
        "default_kickoff_workflow": "Incident Triage",
        "preferred_review_depth": "Deep",
        "idle_timeout": "90 minutes",
        "tenant_scoped_skills": ["/tenant-oncall", "tenant-research"],
    }

    try:
        response = client.post(
            "/platform/settings",
            json=payload,
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200

        persisted = main_module._serialize_store_state()
        store.platform_settings = main_module.PlatformSettings()

        main_module._apply_store_state(persisted)

        assert store.platform_settings.org_name == payload["org_name"]
        assert store.platform_settings.org_slug == payload["org_slug"]
        assert store.platform_settings.support_email == payload["support_email"]
        assert store.platform_settings.website == payload["website"]
        assert (
            store.platform_settings.console_classification_banner_enabled
            == payload["console_classification_banner_enabled"]
        )
        assert (
            store.platform_settings.console_classification_banner_text
            == payload["console_classification_banner_text"]
        )
        assert (
            store.platform_settings.console_classification_banner_background_color
            == payload["console_classification_banner_background_color"]
        )
        assert (
            store.platform_settings.console_classification_banner_text_color
            == payload["console_classification_banner_text_color"]
        )
        assert (
            store.platform_settings.default_kickoff_workflow == payload["default_kickoff_workflow"]
        )
        assert store.platform_settings.preferred_review_depth == payload["preferred_review_depth"]
        assert store.platform_settings.idle_timeout == payload["idle_timeout"]
        assert store.platform_settings.tenant_scoped_skills == [
            "/tenant-oncall",
            "/tenant-research",
        ]
    finally:
        store.platform_settings = original_settings
        main_module._POSTGRES_STATE._payload = original_state_payload


def test_platform_settings_read_returns_effective_immutable_controls_for_secure_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_settings = store.platform_settings.model_copy(deep=True)
    original_seen_nonces = dict(store.a2a_seen_nonces)

    try:
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        store.a2a_seen_nonces = {}
        store.platform_settings.require_authenticated_requests = False
        store.platform_settings.require_a2a_runtime_headers = False
        store.platform_settings.a2a_require_signed_messages = False
        store.platform_settings.a2a_replay_protection = False
        store.platform_settings.enforce_egress_allowlist = False
        store.platform_settings.mcp_require_local_server = False

        response = client.get(
            "/platform/settings",
            headers=_signed_internal_headers(
                actor="locus-admin", nonce="hosted-settings-read-nonce"
            ),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["require_authenticated_requests"] is True
        assert body["require_a2a_runtime_headers"] is True
        assert body["a2a_require_signed_messages"] is True
        assert body["a2a_replay_protection"] is True
        assert body["secure_profile"]["status"] == "blocked"
        assert body["secure_profile"]["requirements"]["enforce_egress_allowlist"] is True
        assert body["secure_profile"]["requirements"]["mcp_require_local_server"] is True
        assert "enforce_egress_allowlist" in body["secure_profile"]["failures"]
        assert "mcp_require_local_server" in body["secure_profile"]["failures"]
    finally:
        store.platform_settings = original_settings
        store.a2a_seen_nonces = original_seen_nonces


def test_hosted_profile_rejects_insecure_deployment_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_settings = store.platform_settings.model_copy(deep=True)
    original_seen_nonces = dict(store.a2a_seen_nonces)

    try:
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        store.a2a_seen_nonces = {}

        payload = {"enforce_egress_allowlist": False, "confirm_security_change": True}
        payload_bytes = json.dumps(payload).encode("utf-8")
        response = client.post(
            "/platform/settings",
            content=payload_bytes,
            headers={
                **_signed_internal_headers(
                    actor="locus-admin",
                    nonce="hosted-reject-egress-disable",
                    correlation_id="hosted-reject-egress-disable",
                    payload=payload_bytes,
                ),
                "Authorization": "Bearer unit-test-bearer",
            },
        )

        assert response.status_code == 400
        assert "enforce_egress_allowlist cannot be disabled" in str(response.json()["detail"])

        payload = {"mcp_require_local_server": False, "confirm_security_change": True}
        payload_bytes = json.dumps(payload).encode("utf-8")
        response = client.post(
            "/platform/settings",
            content=payload_bytes,
            headers={
                **_signed_internal_headers(
                    actor="locus-admin",
                    nonce="hosted-reject-mcp-disable",
                    correlation_id="hosted-reject-mcp-disable",
                    payload=payload_bytes,
                ),
                "Authorization": "Bearer unit-test-bearer",
            },
        )

        assert response.status_code == 400
        assert "LOCUS_CONFIRM_REMOTE_MCP_SERVERS" in str(response.json()["detail"])
    finally:
        store.platform_settings = original_settings
        store.a2a_seen_nonces = original_seen_nonces


def test_local_dev_profile_remains_usable() -> None:
    original_settings = store.platform_settings.model_copy(deep=True)

    try:
        store.platform_settings.require_authenticated_requests = False
        store.platform_settings.require_a2a_runtime_headers = False

        public_health = client.get("/healthz")
        settings_response = client.get("/platform/settings")

        assert public_health.status_code == 200
        assert public_health.json()["status"] == "ok"
        assert settings_response.status_code == 200
        assert settings_response.json()["secure_profile"]["profile"] == "local-lightweight"
        assert settings_response.json()["secure_profile"]["status"] == "ok"
    finally:
        store.platform_settings = original_settings


def test_health_details_reports_blocked_status_for_unsafe_hosted_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_settings = store.platform_settings.model_copy(deep=True)
    original_seen_nonces = dict(store.a2a_seen_nonces)

    try:
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        store.a2a_seen_nonces = {}
        store.platform_settings.enforce_egress_allowlist = False
        store.platform_settings.mcp_require_local_server = False

        response = client.get(
            "/healthz/details",
            headers=_signed_internal_headers(nonce="hosted-health-unsafe-config"),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "blocked"
        assert body["secure_profile"]["status"] == "blocked"
        assert "enforce_egress_allowlist" in body["secure_profile"]["failures"]
        assert "mcp_require_local_server" in body["secure_profile"]["failures"]
    finally:
        store.platform_settings = original_settings
        store.a2a_seen_nonces = original_seen_nonces


def test_saved_graph_definitions_round_trip_layouts_and_configs_across_restart() -> None:
    workflow_id = str(uuid4())
    agent_id = str(uuid4())
    playbook_id = str(uuid4())
    original_state_payload = main_module._POSTGRES_STATE.load_state()

    workflow_graph = {
        "schema_version": "locus-graph/1.0",
        "nodes": [
            {"id": "trigger", "title": "Trigger", "type": "trigger", "x": 120, "y": 80},
            {
                "id": "agent",
                "title": "Research Agent",
                "type": "agent",
                "x": 420,
                "y": 240,
                "config": {
                    "agent_id": "demo-research-agent",
                    "temperature": 0.2,
                    "instructions": "Inspect the latest filings first.",
                },
            },
            {"id": "output", "title": "Output", "type": "output", "x": 760, "y": 240},
        ],
        "links": [
            {"from": "trigger", "to": "agent", "from_port": "out", "to_port": "in"},
            {"from": "agent", "to": "output", "from_port": "out", "to_port": "in"},
        ],
    }
    agent_graph = {
        "schema_version": "locus-graph/1.0",
        "nodes": [
            {"id": "trigger", "title": "Trigger", "type": "trigger", "x": 60, "y": 60},
            {
                "id": "prompt",
                "title": "Prompt",
                "type": "prompt",
                "x": 320,
                "y": 120,
                "config": {
                    "system_prompt_text": "Summarize operational risk in three bullets.",
                    "max_tokens": 1200,
                },
            },
            {"id": "output", "title": "Output", "type": "output", "x": 640, "y": 180},
        ],
        "links": [
            {"from": "trigger", "to": "prompt"},
            {"from": "prompt", "to": "output"},
        ],
    }
    playbook_graph = {
        "schema_version": "locus-graph/1.0",
        "nodes": [
            {
                "id": "intake",
                "title": "Intake",
                "type": "trigger",
                "x": 100,
                "y": 140,
                "config": {"channel": "pagerduty"},
            },
            {
                "id": "containment",
                "title": "Containment",
                "type": "workflow",
                "x": 460,
                "y": 140,
                "config": {"workflow_id": "containment-flow", "owner": "secops"},
            },
        ],
        "links": [{"from": "intake", "to": "containment"}],
    }

    try:
        workflow_response = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Persisted Workflow",
                "description": "Round-trip workflow graph through persisted state.",
                "graph_json": workflow_graph,
            },
        )
        assert workflow_response.status_code == 200

        agent_response = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Persisted Agent",
                "config_json": {
                    "system_prompt": "Keep the saved graph configuration intact.",
                    "graph_json": agent_graph,
                },
            },
            headers=ADMIN_HEADERS,
        )
        assert agent_response.status_code == 200

        store.playbooks[playbook_id] = main_module.PlaybookDefinition(
            id=playbook_id,
            name="Persisted Playbook",
            description="Round-trip playbook graph through persisted state.",
            category="operations",
            status="active",
            graph_json=playbook_graph,
            metadata_json={"owner": "operations"},
        )

        persisted = main_module._serialize_store_state()

        store.workflow_definitions = {}
        store.agent_definitions = {}
        store.playbooks = {}

        main_module._apply_store_state(persisted)

        restored_workflow = store.workflow_definitions[workflow_id]
        assert restored_workflow.graph_json == workflow_graph
        assert restored_workflow.graph_json["nodes"][1]["x"] == 420
        assert restored_workflow.graph_json["nodes"][1]["config"]["instructions"] == (
            "Inspect the latest filings first."
        )

        restored_agent = store.agent_definitions[agent_id]
        assert restored_agent.config_json["graph_json"] == agent_graph
        assert restored_agent.config_json["graph_json"]["nodes"][1]["y"] == 120
        assert (
            restored_agent.config_json["graph_json"]["nodes"][1]["config"]["system_prompt_text"]
            == "Summarize operational risk in three bullets."
        )

        restored_playbook = store.playbooks[playbook_id]
        assert restored_playbook.graph_json == playbook_graph
        assert restored_playbook.graph_json["nodes"][1]["config"]["workflow_id"] == (
            "containment-flow"
        )
        assert restored_playbook.metadata_json["owner"] == "operations"
    finally:
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.playbooks.pop(playbook_id, None)
        main_module._POSTGRES_STATE._payload = original_state_payload


def test_playbook_builder_can_save_and_join_collaboration_session() -> None:
    playbook_id = str(uuid4())

    try:
        save_response = client.post(
            "/playbooks",
            json={
                "id": playbook_id,
                "name": "Builder Playbook",
                "description": "Coordinate workflow steps.",
                "category": "operations",
                "status": "active",
                "graph_json": {
                    "schema_version": "locus-graph/1.0",
                    "nodes": [
                        {"id": "trigger", "title": "Trigger", "type": "trigger", "x": 0, "y": 0},
                        {
                            "id": "child",
                            "title": "Workflow",
                            "type": "workflow",
                            "x": 320,
                            "y": 0,
                            "config": {"workflow_id": "wf-demo"},
                        },
                    ],
                    "links": [{"from": "trigger", "to": "child"}],
                },
            },
            headers=AUTH_HEADERS,
        )
        assert save_response.status_code == 200
        assert store.playbooks[playbook_id].graph_json["nodes"][1]["type"] == "workflow"

        join_response = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "playbook",
                "entity_id": playbook_id,
                "user_id": "tester",
                "display_name": "Tester",
            },
            headers=AUTH_HEADERS,
        )
        assert join_response.status_code == 200
        assert join_response.json()["session"]["entity_type"] == "playbook"
        assert join_response.json()["session"]["graph_json"]["nodes"][1]["type"] == "workflow"
    finally:
        store.playbooks.pop(playbook_id, None)
        store.collaboration_sessions.pop(f"playbook:{playbook_id}", None)


def test_signed_a2a_json_requests_require_raw_request_body_for_signature_verification() -> None:
    payload = {"bucket_id": "agent:test", "scope": "agent", "limit": 10}
    payload_bytes = json.dumps(payload).encode("utf-8")
    timestamp = str(int(time.time()))
    request = types.SimpleNamespace(
        headers={
            "x-correlation-id": "corr-raw-body",
            "x-locus-timestamp": timestamp,
            "content-type": "application/json",
            "content-length": str(len(payload_bytes)),
        },
        method="POST",
        state=types.SimpleNamespace(locus_raw_body=None),
        _body=None,
    )

    with pytest.raises(main_module.HTTPException) as missing_raw_body:
        main_module._verify_runtime_signature(
            request,
            subject="backend",
            nonce="raw-body-nonce",
            signature=main_module._build_runtime_signature(
                "backend",
                "raw-body-nonce",
                "corr-raw-body",
                payload_bytes,
                timestamp=timestamp,
            ),
            payload=payload,
        )

    assert missing_raw_body.value.status_code == 401
    assert (
        missing_raw_body.value.detail
        == "Signed A2A request body must be verified from raw request bytes"
    )


def test_signed_a2a_requests_require_timestamp_header() -> None:
    request = types.SimpleNamespace(
        headers={"x-correlation-id": "corr-missing-ts"},
        method="GET",
        state=types.SimpleNamespace(locus_raw_body=b""),
        _body=b"",
    )

    with pytest.raises(main_module.HTTPException) as missing_timestamp:
        main_module._verify_runtime_signature(
            request,
            subject="backend",
            nonce="nonce-no-ts",
            signature="sig",
            payload=None,
        )

    assert missing_timestamp.value.status_code == 401
    assert missing_timestamp.value.detail == "Missing A2A timestamp header"


def test_runtime_profile_local_secure_matches_fail_closed_local_behavior(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    store.audit_events = []

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.delenv("LOCUS_SECURE_LOCAL_MODE", raising=False)
        monkeypatch.delenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", raising=False)
        monkeypatch.delenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)

        public_health = client.get("/healthz")
        assert public_health.status_code == 200
        assert public_health.json()["mode"] == "local-secure"
        assert "postgres" not in public_health.json()

        assert client.get("/platform/security-policy").status_code == 401

        detailed = client.get("/platform/security-policy", headers=AUTH_HEADERS)
        assert detailed.status_code == 200
        body = detailed.json()
        assert body["runtime_profile"]["name"] == "local-secure"
        assert body["runtime_profile"]["controls"]["require_authenticated_requests"] is True
        assert body["runtime_profile"]["controls"]["require_a2a_runtime_headers"] is False
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.audit_events = original_audit_events


def test_runtime_profile_hosted_is_immutable_and_requires_a2a_headers(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_seen_nonces = dict(store.a2a_seen_nonces)

    try:
        store.platform_settings.require_authenticated_requests = False
        store.a2a_seen_nonces = {}
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
        monkeypatch.setenv("LOCUS_REQUIRE_AUTHENTICATED_REQUESTS", "false")
        monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "false")
        monkeypatch.delenv("LOCUS_SECURE_LOCAL_MODE", raising=False)

        public_health = client.get("/healthz")
        assert public_health.status_code == 200
        assert public_health.json()["mode"] == "hosted"
        assert "postgres" not in public_health.json()

        assert client.get("/platform/security-policy").status_code == 401
        assert (
            client.get(
                "/platform/security-policy", headers={"x-locus-actor": "tester"}
            ).status_code
            == 401
        )

        hosted_headers = _signed_internal_headers(nonce="hosted-profile-nonce-1")
        allowed = client.get("/platform/security-policy", headers=hosted_headers)
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["runtime_profile"]["name"] == "hosted"
        assert body["runtime_profile"]["controls"]["require_authenticated_requests"] is True
        assert body["runtime_profile"]["controls"]["require_a2a_runtime_headers"] is True
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.a2a_seen_nonces = original_seen_nonces


def test_cors_preflight_uses_explicit_methods_and_headers() -> None:
    response = client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-locus-actor,x-locus-signature,x-locus-nonce,x-locus-timestamp",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-methods"] != "*"
    assert "POST" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-headers"] != "*"
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-locus-actor" in allowed_headers
    assert "x-locus-signature" in allowed_headers
    assert "x-locus-nonce" in allowed_headers
    assert "x-locus-timestamp" in allowed_headers


def test_security_headers_are_applied_from_shared_policy() -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert (
        response.headers["content-security-policy"]
        == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    assert response.headers["referrer-policy"] == "no-referrer"
    assert (
        response.headers["permissions-policy"]
        == "camera=(), microphone=(), geolocation=(), browsing-topics=()"
    )


def test_hosted_runtime_profile_adds_hsts_header(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


def test_route_inventory_covers_registered_backend_routes() -> None:
    validate_route_inventory(app)


def test_central_route_policy_protects_previously_unenforced_read_surfaces() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = True

        protected_paths = [
            "/workflow-runs",
            "/inbox",
            "/templates/agents",
            "/integrations/starters",
            "/integrations/mcp",
            "/integrations/mcp/starters",
            "/observability/dashboard",
            "/audit/atf-alignment-report",
            "/artifacts",
        ]

        for path in protected_paths:
            unauthorized = client.get(path)
            assert unauthorized.status_code == 401, path

            header_only = client.get(path, headers={"x-locus-actor": "tester"})
            assert header_only.status_code == 401, path

            authorized = client.get(
                path,
                headers=ADMIN_HEADERS
                if path
                in {
                    "/templates/agents",
                    "/integrations/starters",
                    "/integrations/mcp",
                    "/integrations/mcp/starters",
                    "/observability/dashboard",
                }
                else AUTH_HEADERS,
            )
            assert authorized.status_code == 200, path
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_workflow_runs_are_scoped_to_authenticated_owner_when_auth_required() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    owner_run_id = str(uuid4())
    other_run_id = str(uuid4())

    try:
        store.platform_settings.require_authenticated_requests = True
        store.runs[owner_run_id] = main_module.WorkflowRunSummary(
            id=owner_run_id,
            title="Owner Visible Run",
            status="Running",
            updatedAt="just now",
            progressLabel="Step 1/2",
        )
        store.run_details[owner_run_id] = {
            "artifacts": [],
            "status": "Running",
            "graph": {"nodes": [], "links": []},
            "agent_traces": [],
            "approvals": {"required": False, "pending": False},
            "access": _run_access("tester"),
        }
        store.run_events[owner_run_id] = []

        store.runs[other_run_id] = main_module.WorkflowRunSummary(
            id=other_run_id,
            title="Other User Run",
            status="Running",
            updatedAt="just now",
            progressLabel="Step 1/2",
        )
        store.run_details[other_run_id] = {
            "artifacts": [],
            "status": "Running",
            "graph": {"nodes": [], "links": []},
            "agent_traces": [],
            "approvals": {"required": False, "pending": False},
            "access": _run_access("member-user"),
        }
        store.run_events[other_run_id] = []

        list_response = client.get("/workflow-runs", headers=AUTH_HEADERS)
        assert list_response.status_code == 200
        visible_ids = {item["id"] for item in list_response.json()}
        assert owner_run_id in visible_ids
        assert other_run_id not in visible_ids

        own_detail = client.get(f"/workflow-runs/{owner_run_id}", headers=AUTH_HEADERS)
        assert own_detail.status_code == 200

        foreign_detail = client.get(f"/workflow-runs/{other_run_id}", headers=AUTH_HEADERS)
        assert foreign_detail.status_code == 403

        admin_detail = client.get(f"/workflow-runs/{other_run_id}", headers=ADMIN_HEADERS)
        assert admin_detail.status_code == 200
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.runs.pop(owner_run_id, None)
        store.runs.pop(other_run_id, None)
        store.run_details.pop(owner_run_id, None)
        store.run_details.pop(other_run_id, None)
        store.run_events.pop(owner_run_id, None)
        store.run_events.pop(other_run_id, None)


def test_inbox_and_artifacts_are_scoped_by_run_owner_when_auth_required() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    owner_run_id = str(uuid4())
    other_run_id = str(uuid4())
    owner_artifact_id = str(uuid4())
    other_artifact_id = str(uuid4())
    owner_inbox_id = str(uuid4())
    other_inbox_id = str(uuid4())

    try:
        store.platform_settings.require_authenticated_requests = True
        store.runs[owner_run_id] = main_module.WorkflowRunSummary(
            id=owner_run_id,
            title="Owner Artifact Run",
            status="Needs Review",
            updatedAt="just now",
            progressLabel="Awaiting review",
        )
        store.run_details[owner_run_id] = {
            "artifacts": [
                {
                    "id": owner_artifact_id,
                    "name": "Owner Artifact",
                    "status": "Needs Review",
                    "version": 1,
                }
            ],
            "status": "Needs Review",
            "graph": {"nodes": [], "links": []},
            "agent_traces": [{"output": "owner artifact body"}],
            "response_text": "owner artifact body",
            "approvals": {
                "required": True,
                "pending": True,
                "artifact_id": owner_artifact_id,
                "version": 1,
            },
            "access": _run_access("tester"),
        }
        store.run_events[owner_run_id] = [
            main_module.WorkflowRunEvent(
                id=f"evt-{uuid4()}",
                type="artifact_created",
                title="Owner artifact created",
                summary="created",
                createdAt=main_module._now_iso(),
                metadata={"artifact_id": owner_artifact_id},
            )
        ]

        store.runs[other_run_id] = main_module.WorkflowRunSummary(
            id=other_run_id,
            title="Foreign Artifact Run",
            status="Needs Review",
            updatedAt="just now",
            progressLabel="Awaiting review",
        )
        store.run_details[other_run_id] = {
            "artifacts": [
                {
                    "id": other_artifact_id,
                    "name": "Foreign Artifact",
                    "status": "Needs Review",
                    "version": 1,
                }
            ],
            "status": "Needs Review",
            "graph": {"nodes": [], "links": []},
            "agent_traces": [{"output": "foreign artifact body"}],
            "response_text": "foreign artifact body",
            "approvals": {
                "required": True,
                "pending": True,
                "artifact_id": other_artifact_id,
                "version": 1,
            },
            "access": _run_access("member-user"),
        }
        store.run_events[other_run_id] = [
            main_module.WorkflowRunEvent(
                id=f"evt-{uuid4()}",
                type="artifact_created",
                title="Foreign artifact created",
                summary="created",
                createdAt=main_module._now_iso(),
                metadata={"artifact_id": other_artifact_id},
            )
        ]

        store.inbox.insert(
            0,
            main_module.InboxItem(
                id=owner_inbox_id,
                runId=owner_run_id,
                runName="Owner Artifact Run",
                artifactType="Owner Artifact",
                reason="Owner review",
                queue="Needs Approval",
            ),
        )
        store.inbox.insert(
            0,
            main_module.InboxItem(
                id=other_inbox_id,
                runId=other_run_id,
                runName="Foreign Artifact Run",
                artifactType="Foreign Artifact",
                reason="Foreign review",
                queue="Needs Approval",
            ),
        )

        inbox_response = client.get("/inbox", headers=AUTH_HEADERS)
        assert inbox_response.status_code == 200
        inbox_run_ids = {item["runId"] for item in inbox_response.json()}
        assert owner_run_id in inbox_run_ids
        assert other_run_id not in inbox_run_ids

        artifact_list = client.get("/artifacts", headers=AUTH_HEADERS)
        assert artifact_list.status_code == 200
        artifact_ids = {item["id"] for item in artifact_list.json()}
        assert owner_artifact_id in artifact_ids
        assert other_artifact_id not in artifact_ids

        own_artifact = client.get(f"/artifacts/{owner_artifact_id}", headers=AUTH_HEADERS)
        assert own_artifact.status_code == 200
        assert own_artifact.json()["content"] == "owner artifact body"

        foreign_artifact = client.get(f"/artifacts/{other_artifact_id}", headers=AUTH_HEADERS)
        assert foreign_artifact.status_code == 403

        admin_artifact = client.get(f"/artifacts/{other_artifact_id}", headers=ADMIN_HEADERS)
        assert admin_artifact.status_code == 200
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.runs.pop(owner_run_id, None)
        store.runs.pop(other_run_id, None)
        store.run_details.pop(owner_run_id, None)
        store.run_details.pop(other_run_id, None)
        store.run_events.pop(owner_run_id, None)
        store.run_events.pop(other_run_id, None)
        store.inbox = [
            item for item in store.inbox if item.id not in {owner_inbox_id, other_inbox_id}
        ]


def test_run_mutations_require_owner_or_builder_when_auth_required() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    run_id = str(uuid4())
    artifact_id = str(uuid4())
    inbox_id = str(uuid4())

    try:
        store.platform_settings.require_authenticated_requests = True
        store.runs[run_id] = main_module.WorkflowRunSummary(
            id=run_id,
            title="Owner Mutation Run",
            status="Needs Review",
            updatedAt="just now",
            progressLabel="Awaiting approval",
        )
        store.run_details[run_id] = {
            "artifacts": [
                {
                    "id": artifact_id,
                    "name": "Approval Artifact",
                    "status": "Needs Review",
                    "version": 1,
                }
            ],
            "status": "Needs Review",
            "graph": {"nodes": [], "links": []},
            "agent_traces": [],
            "approvals": {
                "required": True,
                "pending": True,
                "artifact_id": artifact_id,
                "version": 1,
                "scope": "final send/export",
            },
            "access": _run_access("tester"),
        }
        store.run_events[run_id] = []
        store.inbox.insert(
            0,
            main_module.InboxItem(
                id=inbox_id,
                runId=run_id,
                runName="Owner Mutation Run",
                artifactType="Approval Artifact",
                reason="Approval required",
                queue="Needs Approval",
            ),
        )

        denied_approval = client.post(
            "/approvals",
            json={"run_id": run_id, "decision": "approved"},
            headers=MEMBER_AUTH_HEADERS,
        )
        assert denied_approval.status_code == 403

        allowed_approval = client.post(
            "/approvals",
            json={"run_id": run_id, "decision": "approved"},
            headers=AUTH_HEADERS,
        )
        assert allowed_approval.status_code == 200
        assert store.run_details[run_id]["approvals"]["pending"] is False

        store.run_details[run_id]["approvals"]["pending"] = True
        store.run_details[run_id]["status"] = "Needs Review"
        store.runs[run_id].status = "Needs Review"
        store.runs[run_id].progressLabel = "Awaiting approval"
        store.inbox.insert(
            0,
            main_module.InboxItem(
                id=str(uuid4()),
                runId=run_id,
                runName="Owner Mutation Run",
                artifactType="Approval Artifact",
                reason="Approval required",
                queue="Needs Approval",
            ),
        )

        denied_archive = client.post(
            f"/workflow-runs/{run_id}/archive", headers=MEMBER_AUTH_HEADERS
        )
        assert denied_archive.status_code == 403

        allowed_archive = client.post(f"/workflow-runs/{run_id}/archive", headers=ADMIN_HEADERS)
        assert allowed_archive.status_code == 200
        assert store.runs[run_id].status == "Archived"
        assert store.run_details[run_id]["status"] == "Archived"

        visible_runs = client.get("/workflow-runs", headers=ADMIN_HEADERS)
        assert visible_runs.status_code == 200
        assert run_id not in {item["id"] for item in visible_runs.json()}

        archived_runs = client.get("/workflow-runs?status=Archived", headers=ADMIN_HEADERS)
        assert archived_runs.status_code == 200
        assert run_id in {item["id"] for item in archived_runs.json()}
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.runs.pop(run_id, None)
        store.run_details.pop(run_id, None)
        store.run_events.pop(run_id, None)
        store.inbox = [item for item in store.inbox if item.runId != run_id and item.id != inbox_id]


def test_audit_events_require_builder_access_when_auth_required() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = True

        denied = client.get("/audit/events", headers=AUTH_HEADERS)
        assert denied.status_code == 403

        allowed = client.get("/audit/events", headers=ADMIN_HEADERS)
        assert allowed.status_code == 200
        body = allowed.json()
        assert "events" in body
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_workflow_run_without_explicit_agent_redeploys_and_uses_default_chat_agent() -> None:
    default_agent_id = main_module._default_chat_agent_id()
    original_default_agent = store.agent_definitions.pop(default_agent_id, None)
    original_revisions = list(store.agent_definition_revisions.get(default_agent_id, []))
    store.agent_definition_revisions.pop(default_agent_id, None)

    try:
        response = client.post(
            "/workflow-runs",
            json={"prompt": "Draft a secure rollout checklist for a local-first release."},
            headers={"x-locus-actor": "tester"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in {"started", "failed"}

        run_id = body["id"]
        assert default_agent_id in store.agent_definitions
        default_agent = store.agent_definitions[default_agent_id]
        assert default_agent.status == "published"
        assert default_agent.name == "Default Chat Agent"

        # Execution runs on the bounded worker pool; wait for it to finish.
        def _execution_settled() -> bool:
            if store.runs[run_id].status == "Running":
                return False
            detail_payload = store.run_details.get(run_id)
            return isinstance(detail_payload, dict) and bool(detail_payload.get("agent_traces"))

        deadline = time.time() + 15
        while time.time() < deadline and not _execution_settled():
            time.sleep(0.05)
        assert _execution_settled(), "run execution did not settle within 15s"

        detail = store.run_details[run_id]
        assert detail["agent_traces"][0]["agent"] == "Default Chat Agent"
        assert detail["graph"]["nodes"][1]["config"]["agent_id"] == default_agent_id
    finally:
        if original_default_agent is not None:
            store.agent_definitions[default_agent_id] = original_default_agent
        else:
            store.agent_definitions.pop(default_agent_id, None)
        if original_revisions:
            store.agent_definition_revisions[default_agent_id] = original_revisions
        else:
            store.agent_definition_revisions.pop(default_agent_id, None)


def test_workflow_run_kind_is_derived_from_backend_metadata() -> None:
    workflow_response = client.post(
        "/workflow-runs",
        json={"workflow_definition_id": "wf-demo", "title": "Workflow kickoff"},
        headers={"x-locus-actor": "tester"},
    )
    assert workflow_response.status_code == 200
    workflow_run = store.runs[workflow_response.json()["id"]]
    assert workflow_run.kind == "workflow"

    follow_up_response = client.post(
        "/workflow-runs",
        json={"title": "Follow-up", "source_run_id": "run-123", "prompt": "Continue this thread"},
        headers={"x-locus-actor": "tester"},
    )
    assert follow_up_response.status_code == 200
    follow_up_run = store.runs[follow_up_response.json()["id"]]
    assert follow_up_run.kind == "chat"

    playbook_response = client.post(
        "/workflow-runs",
        json={"title": "Playbook task", "playbook_id": "pbk-demo", "prompt": "Run the playbook"},
        headers={"x-locus-actor": "tester"},
    )
    assert playbook_response.status_code == 200
    playbook_run = store.runs[playbook_response.json()["id"]]
    assert playbook_run.kind == "playbook"

    explicit_response = client.post(
        "/workflow-runs",
        json={"title": "Task kickoff", "prompt": "Do the task", "session_kind": "task"},
        headers={"x-locus-actor": "tester"},
    )
    assert explicit_response.status_code == 200
    explicit_run = store.runs[explicit_response.json()["id"]]
    assert explicit_run.kind == "task"


def test_follow_up_run_uses_hidden_recent_context_without_exposing_it_in_user_message(
    monkeypatch,
) -> None:
    captured_prompt: dict[str, str] = {}

    def _fake_resolve_request_chat_runtime(**_: object) -> dict[str, str]:
        return {
            "provider": "openai",
            "model": "gpt-5.4",
            "source": "user_config",
        }

    def _fake_collect_chat_response_chunks(
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        messages=None,
        runtime=None,
        on_chunk=None,
    ):
        del system_prompt, temperature, messages, runtime
        captured_prompt["user_prompt"] = user_prompt
        captured_prompt["model"] = model
        if on_chunk is not None:
            on_chunk("follow-up response")
        return (
            ["follow-up response"],
            {"provider": "openai", "model": model, "mode": "live", "source": "user_config"},
        )

    # Execute the run inline in the request (bounded-executor escape hatch) so
    # the assertions below observe the completed run deterministically.
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")
    monkeypatch.setattr(
        main_module, "_resolve_request_chat_runtime", _fake_resolve_request_chat_runtime
    )
    monkeypatch.setattr(
        main_module, "_collect_chat_response_chunks", _fake_collect_chat_response_chunks
    )

    store.runs["run-123"] = main_module.WorkflowRunSummary(
        id="run-123",
        title="Follow-up",
        title_source="user",
        status="Done",
        updatedAt="just now",
        progressLabel="Completed",
        kind="chat",
    )
    store.run_events["run-123"] = []
    store.run_details["run-123"] = {
        "artifacts": [],
        "status": "Done",
        "graph": {"nodes": [], "links": []},
        "agent_traces": [],
        "approvals": {"required": False, "pending": False},
        "access": {"actor": "tester", "references": ["tester"]},
    }

    response = client.post(
        "/workflow-runs",
        json={
            "title": "Follow-up",
            "source_run_id": "run-123",
            "follow_up_to_run_id": "run-123",
            "prompt": "Continue this thread",
            "context": {
                "mode": "follow_up",
                "recent_context": "User: Summarize the current risks.\nAgent: I drafted the review plan.",
            },
        },
        headers={"x-locus-actor": "tester"},
    )

    assert response.status_code == 200
    run_id = response.json()["id"]
    assert run_id == "run-123"
    assert store.run_events[run_id][1].type == "user_message"
    assert store.run_events[run_id][1].summary == "Continue this thread"
    assert captured_prompt["model"] == "gpt-5.4"
    assert "Conversation context from the previous run:" in captured_prompt["user_prompt"]
    assert "User: Summarize the current risks." in captured_prompt["user_prompt"]
    assert captured_prompt["user_prompt"].endswith("Follow-up request:\nContinue this thread")


def test_presidio_analyzer_is_disabled_by_default(monkeypatch) -> None:
    analyzer_calls: list[str] = []

    class _StubAnalyzer:
        def __init__(self) -> None:
            analyzer_calls.append("called")

    monkeypatch.delenv("LOCUS_ENABLE_PRESIDIO_PII_ANALYZER", raising=False)
    monkeypatch.setattr(main_module, "_PRESIDIO_ANALYZER", None)
    monkeypatch.setattr(main_module, "AnalyzerEngine", _StubAnalyzer)

    assert main_module._get_presidio_analyzer() is None
    assert analyzer_calls == []


def test_workflow_run_generates_title_when_client_omits_one(monkeypatch) -> None:
    # Leave the run queued: background run work goes through the bounded run
    # executor, so stub the submission seam instead of threading.Thread.
    monkeypatch.setattr(main_module, "_submit_run_task", lambda _fn: None)
    monkeypatch.setattr(
        main_module,
        "_generate_workflow_run_title",
        lambda **_: ("Incident triage", "generated"),
    )

    response = client.post(
        "/workflow-runs",
        json={"prompt": "Please triage the latest incident and summarize owner actions."},
        headers={"x-locus-actor": "tester"},
    )

    assert response.status_code == 200
    run = store.runs[response.json()["id"]]
    assert run.title == "Incident triage"
    assert run.title_source == "generated"


def test_workflow_run_title_can_be_renamed_by_user(monkeypatch) -> None:
    # Leave the run queued: background run work goes through the bounded run
    # executor, so stub the submission seam instead of threading.Thread.
    monkeypatch.setattr(main_module, "_submit_run_task", lambda _fn: None)

    created = client.post(
        "/workflow-runs",
        json={"title": "Original title", "prompt": "Keep this run idle."},
        headers={"x-locus-actor": "tester"},
    )
    assert created.status_code == 200

    run_id = created.json()["id"]
    renamed = client.patch(
        f"/workflow-runs/{run_id}",
        json={"title": "Renamed session"},
        headers={"x-locus-actor": "tester"},
    )

    assert renamed.status_code == 200
    assert store.runs[run_id].title == "Renamed session"
    assert store.runs[run_id].title_source == "user"
    assert renamed.json()["title"] == "Renamed session"


def test_workflow_run_uses_preferred_user_runtime_provider_and_model(monkeypatch) -> None:
    principal_id = "tester"
    captured_runtime: dict[str, object] = {}

    def _fake_collect_chat_response_chunks(
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        messages=None,
        runtime=None,
        on_chunk=None,
    ):
        del system_prompt, user_prompt, temperature, messages
        assert runtime is not None
        captured_runtime.update(runtime)
        captured_runtime["model_arg"] = model
        if on_chunk is not None:
            on_chunk("provider-selected response")
        return (
            ["provider-selected response"],
            {
                "provider": str(runtime.get("provider") or ""),
                "model": str(runtime.get("model") or model),
                "mode": "live",
                "source": str(runtime.get("source") or "user_config"),
            },
        )

    # Execute the run inline in the request (bounded-executor escape hatch) so
    # the assertions below observe the completed run deterministically.
    monkeypatch.setenv("LOCUS_SYNC_RUN_EXECUTION", "1")
    monkeypatch.setattr(
        main_module,
        "_collect_chat_response_chunks",
        _fake_collect_chat_response_chunks,
    )

    try:
        save_response = client.put(
            "/runtime/user-providers/openai-compatible",
            json={
                "model": "llama3.3:70b-instruct",
                "available_models": ["llama3.3:70b-instruct", "phi-4:14b"],
                "base_url": "http://localhost:11434/v1",
                "api_key": "local-token",
                "preferred": True,
            },
            headers=AUTH_HEADERS,
        )
        assert save_response.status_code == 200

        response = client.post(
            "/workflow-runs",
            json={
                "title": "Runtime selected task",
                "prompt": "Use my saved runtime.",
                "runtime": {
                    "provider": "openai-compatible",
                    "model": "not-an-allowed-model",
                },
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200

        run_id = response.json()["id"]
        assert captured_runtime["provider"] == "openai-compatible"
        assert captured_runtime["model"] == "llama3.3:70b-instruct"
        assert captured_runtime["model_arg"] == "llama3.3:70b-instruct"
        assert captured_runtime["source"] == "user_config"

        detail = store.run_details[run_id]
        assert detail["runtime"] == {
            "provider": "openai-compatible",
            "model": "llama3.3:70b-instruct",
            "mode": "live",
            "source": "user_config",
        }
        assert detail["response_text"] == "provider-selected response"
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_graph_run_uses_preferred_user_runtime_provider_and_model(monkeypatch) -> None:
    principal_id = "tester"
    captured_runtime: dict[str, object] = {}

    def _fake_run_openai_chat(
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        messages=None,
        runtime=None,
    ):
        del system_prompt, user_prompt, temperature, messages
        assert runtime is not None
        captured_runtime.update(runtime)
        captured_runtime["model_arg"] = model
        return (
            "provider-selected graph response",
            {
                "provider": str(runtime.get("provider") or ""),
                "model": str(runtime.get("model") or model),
                "mode": "live",
                "source": str(runtime.get("source") or "user_config"),
            },
        )

    monkeypatch.setattr(main_module, "_run_openai_chat", _fake_run_openai_chat)

    try:
        save_response = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-4o-mini",
                "available_models": ["gpt-4o-mini", "gpt-5.4"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "user-openai-key",
                "preferred": True,
            },
            headers=AUTH_HEADERS,
        )
        assert save_response.status_code == 200

        response = client.post(
            "/graph/runs",
            json={
                "schema_version": "locus-graph/1.0",
                "nodes": _sample_graph()["nodes"],
                "links": _sample_graph()["links"],
                "input": {
                    "message": "Use my saved runtime.",
                    "runtime": {
                        "provider": "openai",
                        "model": "not-an-allowed-model",
                    },
                },
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert captured_runtime["provider"] == "openai"
        assert captured_runtime["model"] == "gpt-4o-mini"
        assert captured_runtime["model_arg"] == "gpt-4o-mini"
        assert captured_runtime["source"] == "user_config"
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_graph_run_rejects_runtime_override_to_tenant_disallowed_provider() -> None:
    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/1.0",
            "nodes": _sample_graph()["nodes"],
            "links": _sample_graph()["links"],
            "input": {
                "message": "Use a tenant-approved provider.",
                "tenant_runtime_policy": {
                    "allowed_providers": ["openai"],
                    "allowed_models": ["gpt-4o-mini"],
                },
                "runtime": {
                    "provider": "anthropic",
                    "model": "claude-3-7-sonnet-latest",
                },
            },
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["provider"] == "anthropic"
    assert response.json()["detail"]["allowed_providers"] == ["openai"]


def test_graph_run_fallback_chooses_tenant_allowed_model(monkeypatch) -> None:
    principal_id = "tester"
    captured_runtime: dict[str, object] = {}

    def _fake_run_openai_chat(
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        messages=None,
        runtime=None,
    ):
        del system_prompt, user_prompt, temperature, messages
        assert runtime is not None
        captured_runtime.update(runtime)
        captured_runtime["model_arg"] = model
        return (
            "tenant model response",
            {
                "provider": str(runtime.get("provider") or ""),
                "model": str(runtime.get("model") or model),
                "mode": "live",
                "source": str(runtime.get("source") or "user_config"),
            },
        )

    monkeypatch.setattr(main_module, "_run_openai_chat", _fake_run_openai_chat)

    try:
        save_response = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "gpt-4o-mini"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "user-openai-key",
                "preferred": True,
            },
            headers=AUTH_HEADERS,
        )
        assert save_response.status_code == 200

        response = client.post(
            "/graph/runs",
            json={
                "schema_version": "locus-graph/1.0",
                "nodes": _sample_graph()["nodes"],
                "links": _sample_graph()["links"],
                "input": {
                    "message": "Fallback to tenant policy.",
                    "tenant_runtime_policy": {
                        "allowed_providers": ["openai"],
                        "allowed_models": ["gpt-4o-mini"],
                    },
                    "runtime": {
                        "provider": "openai",
                        "model": "gpt-5.4",
                    },
                },
            },
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 200
        assert captured_runtime["provider"] == "openai"
        assert captured_runtime["model"] == "gpt-4o-mini"
        assert captured_runtime["model_arg"] == "gpt-4o-mini"
        assert captured_runtime["available_models"] == ["gpt-4o-mini"]
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


@pytest.mark.parametrize(
    ("payload", "expected_detail"),
    [
        (
            {
                "model": "gpt 4o mini",
                "available_models": ["gpt-4o-mini"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "user-openai-key",
            },
            "model contains an invalid model id",
        ),
        (
            {
                "model": "gpt-4o-mini",
                "available_models": ["gpt-4o-mini", "bad model"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "user-openai-key",
            },
            "available_models[1] contains an invalid model id",
        ),
        (
            {
                "model": "gpt-4o-mini",
                "available_models": ["gpt-4o-mini", "a" * 161],
                "base_url": "https://api.openai.com/v1",
                "api_key": "user-openai-key",
            },
            "available_models[1] contains an invalid model id",
        ),
    ],
)
def test_save_user_runtime_provider_rejects_invalid_model_allowlist_entries(
    payload: dict[str, object], expected_detail: str
) -> None:
    principal_id = "tester"

    try:
        response = client.put(
            "/runtime/user-providers/openai",
            json=payload,
            headers=AUTH_HEADERS,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail
        assert principal_id not in store.user_runtime_provider_configs
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_user_skills_round_trip_persists_per_principal_scope() -> None:
    principal_id = "tester"
    original_state_payload = main_module._POSTGRES_STATE.load_state()
    original_user_skills = store.user_skills.get(principal_id)

    try:
        save_response = client.put(
            "/skills/user",
            json={"skills": ["/incident-triage", "research-brief"]},
            headers=AUTH_HEADERS,
        )
        assert save_response.status_code == 200
        assert save_response.json()["skills"] == ["/incident-triage", "/research-brief"]

        read_response = client.get("/skills/user", headers=AUTH_HEADERS)
        assert read_response.status_code == 200
        assert read_response.json()["principal_id"] == principal_id
        assert read_response.json()["skills"] == ["/incident-triage", "/research-brief"]

        persisted = main_module._serialize_store_state()
        store.user_skills = {}
        main_module._apply_store_state(persisted)

        assert store.user_skills[principal_id].skills == [
            "/incident-triage",
            "/research-brief",
        ]
    finally:
        if original_user_skills is None:
            store.user_skills.pop(principal_id, None)
        else:
            store.user_skills[principal_id] = original_user_skills
        main_module._POSTGRES_STATE._payload = original_state_payload


@pytest.mark.parametrize(
    ("payload", "expected_detail"),
    [
        ({"skills": "incident-triage"}, "skills must be a list"),
        ({"skills": ["/incident-triage", {"bad": "shape"}]}, "skills[1] must be a string"),
        ({"skills": ["bad skill"]}, "skills contains an invalid skill path"),
        ({"skills": ["a" * 128]}, "skills contains an invalid skill path"),
        ({"skills": [f"skill-{index}" for index in range(129)]}, "skills has too many entries"),
    ],
)
def test_save_user_skills_rejects_invalid_skill_payloads(
    payload: dict[str, object], expected_detail: str
) -> None:
    response = client.put("/skills/user", json=payload, headers=AUTH_HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


@pytest.mark.parametrize(
    ("tenant_scoped_skills", "expected_detail"),
    [
        ("tenant-oncall", "tenant_scoped_skills must be a list"),
        (["/tenant-oncall", 42], "tenant_scoped_skills[1] must be a string"),
        (["tenant oncall"], "tenant_scoped_skills contains an invalid skill path"),
        ([f"tenant-{index}" for index in range(129)], "tenant_scoped_skills has too many entries"),
    ],
)
def test_platform_settings_rejects_invalid_tenant_scoped_skills(
    tenant_scoped_skills: object, expected_detail: str
) -> None:
    response = client.post(
        "/platform/settings",
        json={"tenant_scoped_skills": tenant_scoped_skills},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_platform_settings_normalizes_network_allowlists_before_persisting() -> None:
    original_settings = store.platform_settings.model_copy(deep=True)

    try:
        response = client.post(
            "/platform/settings",
            json={
                "allowed_egress_hosts": [
                    "Example.COM:443",
                    "*.api.example.com",
                    "127.0.0.1",
                    "::1",
                    "example.com",
                ],
                "allowed_mcp_server_urls": [
                    "HTTP://LOCALHOST:7071/mcp/",
                    "https://mcp.example.com/api",
                ],
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        assert store.platform_settings.allowed_egress_hosts == [
            "example.com",
            "api.example.com",
            "127.0.0.1",
            "::1",
        ]
        assert store.platform_settings.allowed_mcp_server_urls == [
            "http://localhost:7071/mcp",
            "https://mcp.example.com/api",
        ]
    finally:
        store.platform_settings = original_settings


@pytest.mark.parametrize(
    ("payload", "expected_detail"),
    [
        ({"allowed_egress_hosts": "example.com"}, "allowed_egress_hosts must be a list"),
        ({"allowed_egress_hosts": ["com"]}, "allowed_egress_hosts[0] contains an invalid host"),
        (
            {"allowed_egress_hosts": ["https://api.example.com"]},
            "allowed_egress_hosts[0] contains an invalid host",
        ),
        (
            {"allowed_egress_hosts": ["bad host"]},
            "allowed_egress_hosts[0] contains an invalid host",
        ),
        (
            {"allowed_mcp_server_urls": "http://localhost:7071/mcp"},
            "allowed_mcp_server_urls must be a list",
        ),
        (
            {"allowed_mcp_server_urls": ["ftp://mcp.example.com"]},
            "allowed_mcp_server_urls[0] must be an absolute http(s) URL",
        ),
        (
            {"allowed_mcp_server_urls": ["https://user:pass@mcp.example.com"]},
            "allowed_mcp_server_urls[0] must not include userinfo",
        ),
        (
            {"allowed_mcp_server_urls": ["https://mcp.example.com/api?token=secret"]},
            "allowed_mcp_server_urls[0] must not include query or fragment components",
        ),
    ],
)
def test_platform_settings_rejects_invalid_network_allowlists(
    payload: dict[str, object], expected_detail: str
) -> None:
    response = client.post(
        "/platform/settings",
        json=payload,
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


def test_save_agent_definition_rejects_disallowed_model_defaults() -> None:
    principal_id = "locus-admin"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "codex-1"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/agent-definitions",
            json={
                "name": "Restricted Model Agent",
                "config_json": {
                    "model_defaults": {
                        "provider": "openai",
                        "model": "gpt-4.1",
                    },
                },
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 400
        assert "not in the current allowed model set" in response.json()["detail"]
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_save_agent_definition_rejects_tenant_disallowed_model_defaults() -> None:
    principal_id = "locus-admin"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "gpt-4.1"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/agent-definitions",
            json={
                "name": "Tenant Restricted Model Agent",
                "config_json": {
                    "security": {
                        "allowed_providers": ["openai"],
                        "allowed_models": ["gpt-5.4"],
                    },
                    "model_defaults": {
                        "provider": "openai",
                        "model": "gpt-4.1",
                    },
                },
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 400
        assert "denied by tenant runtime policy" in response.json()["detail"]
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_save_platform_settings_accepts_boolean_local_hostname_toggle() -> None:
    original = list(store.platform_settings.allow_local_network_hostnames)

    try:
        enabled_response = client.post(
            "/platform/settings",
            json={
                "allow_local_network_hostnames": True,
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert enabled_response.status_code == 200
        assert store.platform_settings.allow_local_network_hostnames == (
            original or ["localhost", ".local"]
        )

        disabled_response = client.post(
            "/platform/settings",
            json={
                "allow_local_network_hostnames": False,
                "confirm_security_change": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert disabled_response.status_code == 200
        assert store.platform_settings.allow_local_network_hostnames == []
    finally:
        store.platform_settings.allow_local_network_hostnames = original


def test_save_workflow_definition_rejects_disallowed_agent_node_model() -> None:
    principal_id = "locus-admin"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "codex-1"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/workflow-definitions",
            json={
                "name": "Restricted Workflow",
                "graph_json": {
                    "nodes": [
                        {
                            "id": "trigger",
                            "title": "Trigger",
                            "type": "locus/trigger",
                            "x": 10,
                            "y": 10,
                            "config": {"trigger_mode": "manual"},
                        },
                        {
                            "id": "agent",
                            "title": "Agent",
                            "type": "locus/agent",
                            "x": 120,
                            "y": 10,
                            "config": {
                                "agent_id": "generated-agent",
                                "model": "gpt-4.1",
                            },
                        },
                    ],
                    "links": [
                        {"from": "trigger", "to": "agent", "from_port": "out", "to_port": "in"},
                    ],
                },
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 400
        assert "not in the current allowed model set" in response.json()["detail"]
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_save_workflow_definition_rejects_disallowed_agent_subtype_model() -> None:
    principal_id = "locus-admin"
    graph = _sample_graph()
    agent_node = next(node for node in graph["nodes"] if node["type"] == "agent")
    agent_node["type"] = "locus/agent-delegate"
    agent_node["config"]["model"] = "gpt-4.1"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "codex-1"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/workflow-definitions",
            json={
                "name": "Restricted Subtype Workflow",
                "graph_json": graph,
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 400
        assert "not in the current allowed model set" in response.json()["detail"]
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_save_agent_definition_rejects_disallowed_graph_agent_subtype_model() -> None:
    principal_id = "locus-admin"
    agent_id = str(uuid4())
    graph = _sample_graph()
    agent_node = next(node for node in graph["nodes"] if node["type"] == "agent")
    agent_node["type"] = "locus/agent-delegate"
    agent_node["config"]["model"] = "gpt-4.1"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4", "codex-1"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Restricted Subtype Agent",
                "config_json": {"graph_json": graph},
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 400
        assert "not in the current allowed model set" in response.json()["detail"]
        assert agent_id not in store.agent_definitions
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)
        store.agent_definitions.pop(agent_id, None)


def test_save_workflow_definition_rejects_agent_subtype_tenant_disallowed_provider() -> None:
    principal_id = "locus-admin"
    graph = _sample_graph()
    agent_node = next(node for node in graph["nodes"] if node["type"] == "agent")
    agent_node["type"] = "locus/agent-delegate"
    agent_node["config"]["provider"] = "anthropic"
    agent_node["config"]["model"] = "gpt-5.4"

    try:
        save_provider = client.put(
            "/runtime/user-providers/openai",
            json={
                "model": "gpt-5.4",
                "available_models": ["gpt-5.4"],
                "base_url": "https://api.openai.com/v1",
                "api_key": "test-openai-key",
                "preferred": True,
            },
            headers=ADMIN_HEADERS,
        )
        assert save_provider.status_code == 200

        response = client.post(
            "/workflow-definitions",
            json={
                "name": "Tenant Provider Restricted Subtype Workflow",
                "security_config": {
                    "allowed_providers": ["openai"],
                    "allowed_models": ["gpt-5.4"],
                },
                "graph_json": graph,
            },
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 403
        assert response.json()["detail"]["provider"] == "anthropic"
    finally:
        store.user_runtime_provider_configs.pop(principal_id, None)


def test_graph_run_environment_provider_cannot_override_tenant_deny_policy(
    monkeypatch,
) -> None:
    principal_id = "tester"
    store.user_runtime_provider_configs.pop(principal_id, None)
    monkeypatch.setenv("OPENAI_API_KEY", "env-openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")

    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/1.0",
            "nodes": _sample_graph()["nodes"],
            "links": _sample_graph()["links"],
            "input": {
                "message": "Do not allow environment provider override.",
                "tenant_runtime_policy": {
                    "allowed_providers": ["anthropic"],
                    "allowed_models": ["claude-3-7-sonnet-latest"],
                },
                "runtime": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            },
        },
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["provider"] == "openai"
    assert response.json()["detail"]["allowed_providers"] == ["anthropic"]


def test_admin_only_mutation_routes_block_non_admin_and_allow_admin(monkeypatch) -> None:
    agent_id = str(uuid4())
    ruleset_id = str(uuid4())
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        denied_agent = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Blocked Agent",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=MEMBER_AUTH_HEADERS,
        )
        assert denied_agent.status_code == 403

        denied_ruleset = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Blocked Ruleset",
                "config_json": {"tripwire_action": "reject_content"},
            },
            headers=MEMBER_AUTH_HEADERS,
        )
        assert denied_ruleset.status_code == 403

        denied_settings = client.post(
            "/platform/settings",
            json={"require_human_approval": True},
            headers=MEMBER_AUTH_HEADERS,
        )
        assert denied_settings.status_code == 403

        allowed_agent = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Allowed Agent",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=ADMIN_HEADERS,
        )
        assert allowed_agent.status_code == 200

        allowed_ruleset = client.post(
            "/guardrail-rulesets",
            json={
                "id": ruleset_id,
                "name": "Allowed Ruleset",
                "config_json": {"tripwire_action": "reject_content"},
            },
            headers=ADMIN_HEADERS,
        )
        assert allowed_ruleset.status_code == 200

        allowed_settings = client.post(
            "/platform/settings",
            json={"require_human_approval": True, "confirm_security_change": True},
            headers=ADMIN_HEADERS,
        )
        assert allowed_settings.status_code == 200
        assert store.platform_settings.require_human_approval is True
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.platform_settings.require_human_approval = False
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.guardrail_rulesets.pop(ruleset_id, None)
        store.guardrail_ruleset_revisions.pop(ruleset_id, None)


def test_builder_routes_require_builder_capability_in_secure_mode(monkeypatch) -> None:
    workflow_id = str(uuid4())
    original_require_auth = store.platform_settings.require_authenticated_requests

    member_token = mint_token(
        "member-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "member-user",
            "roles": ["member"],
        },
    )
    builder_token = mint_token(
        "builder-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "builder-user",
            "roles": ["builder"],
        },
    )

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")

        denied = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Builder Protected Workflow",
                "description": "Builder routes should reject non-builder users in secure mode.",
                "graph_json": _sample_graph(),
            },
            headers={"authorization": f"Bearer {member_token}"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"] == "Builder access required"

        allowed = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Builder Protected Workflow",
                "description": "Builder routes should allow builder-capable users in secure mode.",
                "graph_json": _sample_graph(),
            },
            headers={"authorization": f"Bearer {builder_token}"},
        )
        assert allowed.status_code == 200
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)


def test_auth_session_reports_bootstrap_admin_capabilities_from_claim_references(
    monkeypatch,
) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    admin_token = mint_token(
        "bootstrap-admin-subject",
        ttl_seconds=60,
        additional_claims={
            "actor": "bootstrap-admin-subject",
            "preferred_username": "locus-admin",
            "email": "admin@locus.localhost",
            "roles": ["member"],
        },
    )

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,admin@locus.localhost")
        monkeypatch.setenv("LOCUS_BUILDER_ACTORS", "locus-admin,admin@locus.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )

        response = client.get(
            "/auth/session",
            headers={"authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["capabilities"]["can_admin"] is True
        assert body["capabilities"]["can_builder"] is True
        assert body["allowed_modes"] == ["user", "builder"]
        assert body["preferred_username"] == "locus-admin"
        assert body["email"] == "admin@locus.localhost"
        assert body["oidc"]["configured"] is True
        assert body["oidc"]["issuer"] == "http://casdoor.localhost"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_auth_session_treats_local_oidc_operator_as_bootstrap_admin_when_enabled(
    monkeypatch,
) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    operator_token = mint_token(
        "different-local-operator",
        ttl_seconds=60,
        additional_claims={
            "actor": "different-local-operator",
            "preferred_username": "james",
            "email": "james@locus.localhost",
            "roles": ["member"],
        },
    )

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR", "true")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )
        monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,admin@locus.localhost")
        monkeypatch.setenv("LOCUS_BUILDER_ACTORS", "locus-admin,admin@locus.localhost")

        response = client.get(
            "/auth/session", headers={"authorization": f"Bearer {operator_token}"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["capabilities"]["can_admin"] is True
        assert body["capabilities"]["can_builder"] is True
        assert body["allowed_modes"] == ["user", "builder"]
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_auth_session_remains_public_for_auth_bootstrap_in_secure_profiles(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_AUTH_MODE", "oidc")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )
        monkeypatch.setenv("LOCUS_ALLOW_HEADER_ACTOR_AUTH", "true")

        anonymous = client.get("/auth/session")
        assert anonymous.status_code == 200
        anonymous_body = anonymous.json()
        assert anonymous_body["authenticated"] is False
        assert anonymous_body["auth_mode"] == "oidc"
        assert anonymous_body["oidc"]["configured"] is True
        assert anonymous_body["oidc"]["provider"] == "casdoor"

        header_only = client.get("/auth/session", headers={"x-locus-actor": "locus-admin"})
        assert header_only.status_code == 200
        header_only_body = header_only.json()
        assert header_only_body["authenticated"] is False
        assert header_only_body["actor"] == "anonymous"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_local_password_login_sets_cookie_and_authenticates_session(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )
        monkeypatch.setattr(
            main_module,
            "_authenticate_local_casdoor_user",
            lambda username, password: {
                "owner": "built-in",
                "name": username,
                "displayName": "Locus Admin",
                "email": "admin@locus.localhost",
                "isAdmin": True,
            },
        )

        with TestClient(app, base_url="http://localhost") as local_client:
            login = local_client.post(
                "/auth/login",
                json={"username": "locus-admin", "password": "correct horse battery staple"},
            )
            assert login.status_code == 200
            assert main_module._operator_session_cookie_name() in login.headers.get(
                "set-cookie", ""
            )

            session = local_client.get("/auth/session")
            assert session.status_code == 200
            body = session.json()
            assert body["authenticated"] is True
            assert body["preferred_username"] == "locus-admin"
            assert body["display_name"] == "Locus Admin"
            assert body["capabilities"]["can_admin"] is True
            assert body["capabilities"]["can_builder"] is True
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        client.cookies.clear()


def test_local_password_register_creates_member_session(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )
        monkeypatch.setattr(
            main_module,
            "_provision_local_casdoor_user",
            lambda username, email, display_name, password: {
                "owner": "built-in",
                "name": username,
                "displayName": display_name,
                "email": email,
                "isAdmin": False,
            },
        )

        with TestClient(app, base_url="http://localhost") as local_client:
            register = local_client.post(
                "/auth/register",
                json={
                    "username": "member-user",
                    "email": "member@locus.localhost",
                    "display_name": "Member User",
                    "password": "correct horse battery staple",
                },
            )
            assert register.status_code == 200
            assert register.json()["created"] is True

            session = local_client.get("/auth/session")
            assert session.status_code == 200
            body = session.json()
            assert body["authenticated"] is True
            assert body["preferred_username"] == "member-user"
            assert body["display_name"] == "Member User"
            assert body["capabilities"]["can_admin"] is False
            assert body["capabilities"]["can_builder"] is False
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        client.cookies.clear()


def test_local_password_logout_clears_operator_session_cookie(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )
        monkeypatch.setattr(
            main_module,
            "_authenticate_local_casdoor_user",
            lambda username, password: {
                "owner": "built-in",
                "name": username,
                "displayName": "Locus Admin",
                "email": "admin@locus.localhost",
                "isAdmin": True,
            },
        )

        with TestClient(app, base_url="http://localhost") as local_client:
            assert (
                local_client.post(
                    "/auth/login",
                    json={"username": "locus-admin", "password": "correct horse battery staple"},
                ).status_code
                == 200
            )
            assert local_client.get("/auth/session").status_code == 200

            logout = local_client.post("/auth/logout")
            assert logout.status_code == 200

            anonymous = local_client.get("/auth/session")
            assert anonymous.status_code == 401
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        client.cookies.clear()


def test_auth_session_reports_oidc_browser_flow_capability(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "https://issuer.example.com/.well-known/jwks.json"
    )
    monkeypatch.setenv("LOCUS_AUTH_TRUSTED_ISSUERS", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_CLIENT_ID", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_AUTHORIZATION_URL", "https://issuer.example.com/oauth2/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_TOKEN_URL", "https://issuer.example.com/oauth2/token")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_SIGNIN_URL", "https://issuer.example.com/oauth2/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_SIGNUP_URL", "https://issuer.example.com/signup")

    response = client.get("/auth/session")

    assert response.status_code == 200
    body = response.json()
    assert body["oidc"]["configured"] is True
    assert body["oidc"]["browser_flow_configured"] is True
    assert body["oidc"]["browser_flow_error"] == ""


def test_oidc_browser_start_redirects_to_provider_with_pkce_and_state_cookie(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "https://issuer.example.com/.well-known/jwks.json"
    )
    monkeypatch.setenv("LOCUS_AUTH_TRUSTED_ISSUERS", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_CLIENT_ID", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_AUTHORIZATION_URL", "https://issuer.example.com/oauth2/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_TOKEN_URL", "https://issuer.example.com/oauth2/token")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_SIGNIN_URL",
        "https://issuer.example.com/oauth2/authorize?prompt=login",
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_SIGNUP_URL", "https://issuer.example.com/signup")

    with TestClient(app, base_url="https://console.example.com") as local_client:
        response = local_client.get("/auth/oidc/start?intent=signin", follow_redirects=False)

    assert response.status_code == 302
    assert main_module._oidc_browser_flow_cookie_name() in response.headers.get("set-cookie", "")
    location = response.headers["location"]
    parsed = main_module.urlsplit(location)
    query = dict(main_module.parse_qsl(parsed.query, keep_blank_values=True))
    assert parsed.scheme == "https"
    assert parsed.netloc == "issuer.example.com"
    assert query["client_id"] == "locus-ui"
    assert query["response_type"] == "code"
    assert query["redirect_uri"] == "https://console.example.com/auth/callback"
    assert query["code_challenge_method"] == "S256"
    assert query["state"]


def test_oidc_browser_start_rejects_invalid_forwarded_host_header(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "https://issuer.example.com/.well-known/jwks.json"
    )
    monkeypatch.setenv("LOCUS_AUTH_TRUSTED_ISSUERS", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_CLIENT_ID", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_AUTHORIZATION_URL", "https://issuer.example.com/oauth2/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_TOKEN_URL", "https://issuer.example.com/oauth2/token")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_SIGNIN_URL",
        "https://issuer.example.com/oauth2/authorize?prompt=login",
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_SIGNUP_URL", "https://issuer.example.com/signup")

    with TestClient(app, base_url="https://console.example.com") as local_client:
        response = local_client.get(
            "/auth/oidc/start?intent=signin",
            headers={"x-forwarded-host": "console.example.com/evil"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/auth?error=")
    assert main_module._oidc_browser_flow_cookie_name() in response.headers.get("set-cookie", "")
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_oidc_browser_callback_sets_operator_session_cookie(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://127.0.0.1:8081")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_JWKS_URL", "http://127.0.0.1:8081/.well-known/jwks.json")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_CLIENT_ID", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_AUTHORIZATION_URL", "http://127.0.0.1:8081/login/oauth/authorize"
    )
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_TOKEN_URL", "http://127.0.0.1:8081/api/login/oauth/access_token"
    )
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_SIGNIN_URL", "http://127.0.0.1:8081/login/oauth/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_SIGNUP_URL", "http://127.0.0.1:8081/signup")

    captured: dict[str, object] = {}

    class _FakeTokenResponse:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, str]:
            return {"id_token": "oidc-session-token"}

    def _fake_httpx_post(url: str, *, data=None, headers=None, timeout=None, follow_redirects=None):
        captured["url"] = url
        captured["data"] = dict(data or {})
        captured["headers"] = dict(headers or {})
        captured["follow_redirects"] = follow_redirects
        return _FakeTokenResponse()

    def _fake_decode_operator_bearer_token(token: str) -> dict[str, object]:
        assert token == "oidc-session-token"
        return {
            "sub": "user-123",
            "email": "operator@example.com",
            "preferred_username": "operator",
            "name": "Operator Example",
            "roles": ["admin"],
        }

    monkeypatch.setattr(main_module.httpx, "post", _fake_httpx_post)
    monkeypatch.setattr(
        main_module, "_decode_operator_bearer_token", _fake_decode_operator_bearer_token
    )

    with TestClient(app, base_url="http://localhost") as local_client:
        start = local_client.get("/auth/oidc/start?intent=signin", follow_redirects=False)
        assert start.status_code == 302
        state = dict(
            main_module.parse_qsl(main_module.urlsplit(start.headers["location"]).query)
        ).get("state")

        callback = local_client.get(
            f"/auth/oidc/callback?code=demo-code&state={state}",
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "/inbox"
        assert main_module._operator_session_cookie_name() in callback.headers.get("set-cookie", "")
        assert captured["url"] == "http://127.0.0.1:8081/api/login/oauth/access_token"
        assert captured["follow_redirects"] is False
        token_request = captured["data"]
        assert isinstance(token_request, dict)
        assert token_request["code"] == "demo-code"
        assert token_request["client_id"] == "locus-ui"
        assert token_request["redirect_uri"] == "http://localhost/auth/callback"
        assert token_request["code_verifier"]

        session = local_client.get("/auth/session")
        assert session.status_code == 200
        body = session.json()
        assert body["authenticated"] is True
        assert body["auth_mode"] == "oidc"
        assert body["display_name"] == "Operator Example"
        assert body["preferred_username"] == "operator"


def test_oidc_browser_callback_redirects_back_to_auth_when_exchange_fails(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://127.0.0.1:8081")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_JWKS_URL", "http://127.0.0.1:8081/.well-known/jwks.json")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_CLIENT_ID", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_AUTHORIZATION_URL", "http://127.0.0.1:8081/login/oauth/authorize"
    )
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_TOKEN_URL", "http://127.0.0.1:8081/api/login/oauth/access_token"
    )
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_SIGNIN_URL", "http://127.0.0.1:8081/login/oauth/authorize"
    )
    monkeypatch.setenv("LOCUS_AUTH_OIDC_SIGNUP_URL", "http://127.0.0.1:8081/signup")

    def _fake_httpx_post(url: str, *, data=None, headers=None, timeout=None, follow_redirects=None):
        raise main_module.httpx.HTTPError("boom")

    monkeypatch.setattr(main_module.httpx, "post", _fake_httpx_post)

    with TestClient(app, base_url="http://localhost") as local_client:
        start = local_client.get("/auth/oidc/start?intent=signin", follow_redirects=False)
        assert start.status_code == 302
        state = dict(
            main_module.parse_qsl(main_module.urlsplit(start.headers["location"]).query)
        ).get("state")

        callback = local_client.get(
            f"/auth/oidc/callback?code=demo-code&state={state}",
            follow_redirects=False,
        )

    assert callback.status_code == 302
    assert callback.headers["location"].startswith("/auth?error=")


def test_authenticate_local_casdoor_user_accepts_malformed_login_payload_when_account_session_exists(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
    )
    monkeypatch.setattr(
        main_module, "_casdoor_http_base_candidates", lambda: [("http://casdoor:8000", {})]
    )

    def _fake_urlopen_json(opener, url, **kwargs):
        if url.endswith("/api/get-account"):
            return {
                "status": "ok",
                "data": {
                    "owner": "built-in",
                    "name": "jpbooth",
                    "displayName": "jpbooth",
                    "email": "jpbooth@lattix.io",
                },
            }
        return {
            "status": "error",
            "msg": "invalid character 'a' looking for beginning of value",
            "data": None,
        }

    monkeypatch.setattr(main_module, "_casdoor_urlopen_json", _fake_urlopen_json)

    account = main_module._authenticate_local_casdoor_user("jpbooth", "PhenoiX1!")

    assert account["name"] == "jpbooth"
    assert account["owner"] == "built-in"


def test_authenticate_local_casdoor_user_preserves_auth_error_over_unreachable_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://127.0.0.1:8081")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_JWKS_URL", "http://127.0.0.1:8081/.well-known/jwks.json")
    monkeypatch.setattr(
        main_module,
        "_casdoor_http_base_candidates",
        lambda: [("http://casdoor:8000", {}), ("http://127.0.0.1:8081", {})],
    )

    def _fake_urlopen_json(opener, url, **kwargs):
        del opener, kwargs
        if url.startswith("http://casdoor:8000") and "/api/login" in url:
            return {
                "status": "error",
                "msg": "Invalid username or password",
                "data": None,
            }
        if url.startswith("http://casdoor:8000") and url.endswith("/api/get-account"):
            return {
                "status": "error",
                "msg": "Please login first",
                "data": None,
            }
        raise urllib_error.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(main_module, "_casdoor_urlopen_json", _fake_urlopen_json)

    with pytest.raises(HTTPException) as exc_info:
        main_module._authenticate_local_casdoor_user("jpbooth", "wrong-password")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid username or password"


def test_casdoor_login_admin_accepts_malformed_login_payload_when_session_exists(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "_casdoor_urlopen_json",
        lambda opener, url, **kwargs: (
            {
                "status": "ok",
                "data": {
                    "owner": "built-in",
                    "name": "admin",
                    "displayName": "Admin",
                },
            }
            if url.endswith("/api/get-account")
            else {
                "status": "error",
                "msg": "invalid character 'a' looking for beginning of value",
                "data": None,
            }
        ),
    )

    opener = main_module.urllib_request.build_opener()

    main_module._casdoor_login_admin(opener, "http://casdoor:8000", {})


def test_auth_session_hides_identity_and_capabilities_for_header_actor_only_requests(
    monkeypatch,
) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-lightweight")
        monkeypatch.setenv("LOCUS_ALLOW_HEADER_ACTOR_AUTH", "true")
        monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,admin@locus.localhost")
        monkeypatch.setenv("LOCUS_BUILDER_ACTORS", "locus-admin,admin@locus.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
        monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
        monkeypatch.setenv(
            "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
        )

        response = client.get("/auth/session", headers={"x-locus-actor": "locus-admin"})
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is False
        assert body["actor"] == "anonymous"
        assert body["principal_id"] == "anonymous"
        assert body["display_name"] == ""
        assert body["roles"] == []
        assert body["capabilities"]["can_admin"] is False
        assert body["capabilities"]["can_builder"] is False
        assert body["allowed_modes"] == ["user"]
        assert body["oidc"]["configured"] is True
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_boolean_admin_and_builder_claims_do_not_grant_privileges_without_roles_or_bootstrap_reference(
    monkeypatch,
) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    elevated_flags_token = mint_token(
        "flagged-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "flagged-user",
            "admin": True,
            "builder": True,
            "roles": ["member"],
        },
    )
    workflow_id = str(uuid4())

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.delenv("LOCUS_ADMIN_ACTORS", raising=False)
        monkeypatch.delenv("LOCUS_BUILDER_ACTORS", raising=False)

        session = client.get(
            "/auth/session",
            headers={"authorization": f"Bearer {elevated_flags_token}"},
        )
        assert session.status_code == 200
        body = session.json()
        assert body["capabilities"]["can_admin"] is False
        assert body["capabilities"]["can_builder"] is False

        denied_builder = client.post(
            "/workflow-definitions",
            json={
                "id": workflow_id,
                "name": "Flag Claims Should Not Elevate",
                "description": "Boolean builder/admin flags alone should not authorize builder routes.",
                "graph_json": _sample_graph(),
            },
            headers={"authorization": f"Bearer {elevated_flags_token}"},
        )
        assert denied_builder.status_code == 403
        assert denied_builder.json()["detail"] == "Builder access required"

        denied_admin = client.post(
            "/platform/settings",
            json={"require_human_approval": True, "confirm_security_change": True},
            headers={"authorization": f"Bearer {elevated_flags_token}"},
        )
        assert denied_admin.status_code == 403
        assert denied_admin.json()["detail"] == "Administrator access required"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.workflow_definitions.pop(workflow_id, None)
        store.workflow_definition_revisions.pop(workflow_id, None)
        store.platform_settings.require_human_approval = False


def test_header_actor_auth_opt_in_does_not_grant_admin_without_bearer_identity(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_ALLOW_HEADER_ACTOR_AUTH", "true")
        monkeypatch.setenv("LOCUS_ADMIN_ACTORS", "locus-admin,admin@locus.localhost")

        response = client.post(
            "/platform/settings",
            json={"require_human_approval": True, "confirm_security_change": True},
            headers={"x-locus-actor": "locus-admin"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Administrator access required"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.platform_settings.require_human_approval = False


def test_header_actor_auth_is_disabled_outside_lightweight_profile(monkeypatch) -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests

    try:
        store.platform_settings.require_authenticated_requests = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("LOCUS_ALLOW_HEADER_ACTOR_AUTH", "true")

        assert main_module._header_actor_auth_allowed() is False
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth


def test_configured_operator_oidc_requires_trusted_issuer_allowlist_for_non_local_hosts(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "oidc")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "https://issuer.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "https://issuer.example.com/.well-known/jwks.json"
    )
    monkeypatch.delenv("LOCUS_AUTH_TRUSTED_ISSUERS", raising=False)

    with pytest.raises(ValueError, match="LOCUS_AUTH_TRUSTED_ISSUERS"):
        main_module._configured_operator_oidc()

    monkeypatch.setenv("LOCUS_AUTH_TRUSTED_ISSUERS", "https://issuer.example.com")
    config = main_module._configured_operator_oidc()
    assert config["issuer"] == "https://issuer.example.com"


def test_localhost_oidc_issuer_remains_valid_without_trusted_issuer_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "http://casdoor.localhost")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "http://casdoor.localhost/.well-known/jwks.json"
    )
    monkeypatch.delenv("LOCUS_AUTH_TRUSTED_ISSUERS", raising=False)

    config = main_module._configured_operator_oidc()
    assert config["issuer"] == "http://casdoor.localhost"


def test_validate_runtime_security_configuration_requires_a2a_secret_when_signed_transport_enabled(
    monkeypatch,
) -> None:
    original_signed_messages = store.platform_settings.a2a_require_signed_messages
    original_require_a2a_headers = store.platform_settings.require_a2a_runtime_headers

    try:
        store.platform_settings.a2a_require_signed_messages = True
        store.platform_settings.require_a2a_runtime_headers = False
        monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-secure")
        monkeypatch.setenv("A2A_JWT_SECRET", "")
        monkeypatch.delenv("LOCUS_AUTH_OIDC_ISSUER", raising=False)
        monkeypatch.delenv("LOCUS_AUTH_OIDC_AUDIENCE", raising=False)
        monkeypatch.delenv("LOCUS_AUTH_OIDC_JWKS_URL", raising=False)

        with pytest.raises(main_module.HTTPException) as missing_secret:
            main_module._validate_runtime_security_configuration()
        assert missing_secret.value.status_code == 500
        assert missing_secret.value.detail == "A2A_JWT_SECRET is required for signed A2A transport"

        monkeypatch.setenv("A2A_JWT_SECRET", "unit-test-super-secret-value-32bytes")
        main_module._validate_runtime_security_configuration()
    finally:
        store.platform_settings.a2a_require_signed_messages = original_signed_messages
        store.platform_settings.require_a2a_runtime_headers = original_require_a2a_headers


def test_cors_allowed_origins_reject_invalid_urls(monkeypatch) -> None:
    monkeypatch.setenv("LOCUS_CORS_ALLOWED_ORIGINS", "javascript:alert(1)")

    with pytest.raises(ValueError, match=r"absolute http\(s\) URL"):
        main_module._cors_allowed_origins()

    monkeypatch.setenv("LOCUS_CORS_ALLOWED_ORIGINS", "https://console.example.com/app")
    with pytest.raises(ValueError, match="bare origins"):
        main_module._cors_allowed_origins()

    monkeypatch.setenv(
        "LOCUS_CORS_ALLOWED_ORIGINS", "https://console.example.com, http://localhost:3000"
    )
    assert main_module._cors_allowed_origins() == [
        "https://console.example.com",
        "http://localhost:3000",
    ]


def test_agent_definition_save_and_template_instantiation_attach_iam_identity(monkeypatch) -> None:
    agent_id = str(uuid4())
    template_id = str(uuid4())

    monkeypatch.setenv("LOCUS_AUTH_OIDC_PROVIDER", "casdoor")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_ISSUER", "https://casdoor.example.com")
    monkeypatch.setenv("LOCUS_AUTH_OIDC_AUDIENCE", "locus-ui")
    monkeypatch.setenv(
        "LOCUS_AUTH_OIDC_JWKS_URL", "https://casdoor.example.com/.well-known/jwks.json"
    )

    store.agent_templates[template_id] = main_module.AgentTemplate(
        id=template_id,
        name="Planner Template",
        description="Template with IAM binding",
        config_json={"graph_json": _sample_graph()},
    )

    try:
        save_response = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Planner Agent",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=ADMIN_HEADERS,
        )
        assert save_response.status_code == 200

        saved_detail = client.get(f"/agent-definitions/{agent_id}")
        assert saved_detail.status_code == 200
        saved_iam = saved_detail.json()["config_json"]["iam"]
        assert saved_iam["principal_id"] == f"agent:{agent_id}"
        assert saved_iam["principal_type"] == "agent"
        assert saved_iam["provider"] == "casdoor"
        assert saved_iam["subject"].startswith("https://casdoor.example.com/npe/agents/")
        assert saved_iam["provisioning"]["state"] == "active"

        instantiate_response = client.post(
            f"/templates/agents/{template_id}/instantiate",
            json={"name": "Template Planner"},
            headers=ADMIN_HEADERS,
        )
        assert instantiate_response.status_code == 200
        instantiated_id = instantiate_response.json()["id"]

        instantiated_detail = client.get(f"/agent-definitions/{instantiated_id}")
        assert instantiated_detail.status_code == 200
        instantiated_iam = instantiated_detail.json()["config_json"]["iam"]
        assert instantiated_iam["principal_id"] == f"agent:{instantiated_id}"
        assert instantiated_iam["principal_type"] == "agent"
        assert instantiated_iam["provider"] == "casdoor"
    finally:
        store.agent_templates.pop(template_id, None)
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        for entity_id, item in list(store.agent_definitions.items()):
            if item.name == "Template Planner":
                store.agent_definitions.pop(entity_id, None)
                store.agent_definition_revisions.pop(entity_id, None)


def test_delete_agent_definition_deprovisions_identity_and_removes_collaboration_session() -> None:
    agent_id = str(uuid4())
    original_sessions = dict(store.collaboration_sessions)

    try:
        save_response = client.post(
            "/agent-definitions",
            json={
                "id": agent_id,
                "name": "Delete Me",
                "config_json": {"graph_json": _sample_graph()},
            },
            headers=ADMIN_HEADERS,
        )
        assert save_response.status_code == 200

        join_response = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": agent_id,
                "user_id": "owner-user",
                "display_name": "Owner",
            },
            headers=OWNER_AUTH_HEADERS,
        )
        assert join_response.status_code == 200
        session_id = join_response.json()["session"]["id"]
        assert session_id in store.collaboration_sessions

        delete_response = client.delete(f"/agent-definitions/{agent_id}", headers=ADMIN_HEADERS)
        assert delete_response.status_code == 200
        assert session_id not in store.collaboration_sessions

        history = store.agent_definition_revisions[agent_id]
        delete_revision = history[-1]
        assert delete_revision.action == "delete"
        assert delete_revision.metadata["deleted_collaboration_session"] is True
        assert (
            delete_revision.snapshot["config_json"]["iam"]["provisioning"]["state"]
            == "deprovisioned"
        )
        assert delete_revision.metadata["revoked_principal_id"] == f"agent:{agent_id}"
    finally:
        store.agent_definitions.pop(agent_id, None)
        store.agent_definition_revisions.pop(agent_id, None)
        store.collaboration_sessions = original_sessions


def test_agent_principal_can_collaborate_with_principal_id_claims() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_sessions = dict(store.collaboration_sessions)

    agent_token = mint_token(
        "https://casdoor.example.com/npe/agents/locus-agent-planner",
        ttl_seconds=60,
        additional_claims={
            "principal_id": "agent:planner",
            "principal_type": "agent",
            "agent_id": "planner",
            "name": "Planner Agent",
        },
    )

    try:
        store.platform_settings.require_authenticated_requests = True

        join_agent = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "workflow",
                "entity_id": "iam-workflow",
                "principal_id": "agent:planner",
                "display_name": "Planner Agent",
            },
            headers={"authorization": f"Bearer {agent_token}"},
        )
        assert join_agent.status_code == 200
        join_body = join_agent.json()
        assert join_body["participant"]["principal_id"] == "agent:planner"
        assert join_body["participant"]["principal_type"] == "agent"
        session_id = join_body["session"]["id"]

        join_member = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "workflow",
                "entity_id": "iam-workflow",
                "display_name": "Member",
            },
            headers=MEMBER_AUTH_HEADERS,
        )
        assert join_member.status_code == 200

        sync_mismatch = client.post(
            f"/collab/sessions/{session_id}/sync",
            json={"principal_id": "agent:spoofed", "graph_json": _sample_graph()},
            headers={"authorization": f"Bearer {agent_token}"},
        )
        assert sync_mismatch.status_code == 403

        sync_ok = client.post(
            f"/collab/sessions/{session_id}/sync",
            json={"graph_json": _sample_graph()},
            headers={"authorization": f"Bearer {agent_token}"},
        )
        assert sync_ok.status_code == 200

        permissions_ok = client.post(
            f"/collab/sessions/{session_id}/permissions",
            json={"target_user_id": "member-user", "role": "viewer"},
            headers={"authorization": f"Bearer {agent_token}"},
        )
        assert permissions_ok.status_code == 200
        member = next(
            item
            for item in permissions_ok.json()["session"]["participants"]
            if item["user_id"] == "member-user"
        )
        assert member["role"] == "viewer"
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.collaboration_sessions = original_sessions


def test_memory_scope_authorization_denies_cross_actor_user_bucket_reads() -> None:
    bucket_id = "user:owner-user"
    store.memory_by_session[bucket_id] = [{"id": "mem-user-1", "content": "owner-only memory"}]

    try:
        denied = client.get(
            f"/memory/{bucket_id}?scope=user", headers={"x-locus-actor": "other-user"}
        )
        assert denied.status_code == 403

        allowed = client.get(
            f"/memory/{bucket_id}?scope=user", headers={"x-locus-actor": "owner-user"}
        )
        assert allowed.status_code == 200
        assert allowed.json()["session_id"] == bucket_id
    finally:
        store.memory_by_session.pop(bucket_id, None)


def test_memory_scope_authorization_requires_tenant_claim_for_tenant_bucket() -> None:
    bucket_id = "tenant:acme"
    store.memory_by_session[bucket_id] = [{"id": "mem-tenant-1", "content": "tenant-scoped memory"}]
    actor_token = mint_token(
        "tenant-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "tenant-user",
        },
    )
    tenant_token = mint_token(
        "tenant-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "tenant-user",
            "tenant_id": "acme",
        },
    )

    try:
        missing_claim = client.get(
            f"/memory/{bucket_id}?scope=tenant",
            headers={"authorization": f"Bearer {actor_token}"},
        )
        assert missing_claim.status_code == 403

        wrong_claim = client.get(
            f"/memory/{bucket_id}?scope=tenant",
            headers={
                "authorization": f"Bearer {actor_token}",
                "x-locus-tenant": "other",
            },
        )
        assert wrong_claim.status_code == 403

        allowed = client.get(
            f"/memory/{bucket_id}?scope=tenant",
            headers={"authorization": f"Bearer {tenant_token}"},
        )
        assert allowed.status_code == 200
    finally:
        store.memory_by_session.pop(bucket_id, None)


def test_runtime_bearer_token_supplies_actor_and_tenant_claims() -> None:
    bucket_id = "tenant:acme"
    store.memory_by_session[bucket_id] = [
        {"id": "mem-tenant-jwt", "content": "tenant-scoped memory"}
    ]

    token = mint_token(
        "tenant-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "tenant-user",
            "tenant_id": "acme",
        },
    )

    try:
        settings_response = client.get(
            "/platform/settings",
            headers={"authorization": f"Bearer {token}"},
        )
        assert settings_response.status_code == 200

        memory_response = client.get(
            f"/memory/{bucket_id}?scope=tenant",
            headers={"authorization": f"Bearer {token}"},
        )
        assert memory_response.status_code == 200
        assert memory_response.json()["session_id"] == bucket_id
    finally:
        store.memory_by_session.pop(bucket_id, None)


def test_non_internal_runtime_bearer_token_cannot_access_internal_memory_endpoints() -> None:
    bucket_id = "agent:runtime-bearer-user"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._memory_append_entry(
        bucket_id,
        {"id": "runtime-user-1", "content": "user bearer token should not gain internal access"},
        memory_scope="agent",
        source="memory-node",
    )

    token = mint_token(
        "member-user",
        ttl_seconds=60,
        additional_claims={
            "actor": "member-user",
        },
    )

    try:
        denied = client.post(
            "/internal/memory/consolidation/run",
            json={"bucket_id": bucket_id, "scope": "agent", "limit": 10},
            headers={"authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 401
    finally:
        store.memory_by_session.pop(bucket_id, None)
        main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")


def test_internal_runtime_bearer_token_requires_signed_headers_for_internal_memory_endpoints() -> (
    None
):
    bucket_id = "agent:runtime-bearer-internal"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._memory_append_entry(
        bucket_id,
        {"id": "runtime-internal-1", "content": "internal bearer token maintenance target"},
        memory_scope="agent",
        source="memory-node",
    )

    token = mint_token(
        "backend",
        ttl_seconds=60,
        additional_claims={
            "subject": "backend",
            "internal_service": True,
        },
    )
    payload = {"bucket_id": bucket_id, "scope": "agent", "limit": 10}
    payload_bytes = json.dumps(payload).encode("utf-8")

    try:
        denied = client.post(
            "/internal/memory/consolidation/run",
            json=payload,
            headers={"authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 401

        allowed = client.post(
            "/internal/memory/consolidation/run",
            content=payload_bytes,
            headers={
                **_signed_internal_headers(
                    payload=payload_bytes, nonce="internal-runtime-bearer-nonce"
                ),
                "authorization": f"Bearer {token}",
            },
        )
        assert allowed.status_code == 200
    finally:
        store.memory_by_session.pop(bucket_id, None)
        main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")


def test_resolve_memory_bucket_id_requires_verified_tenant_claim() -> None:
    execution_state = {"run_id": "run-1", "session_id": "session:run-1", "auth_context": {}}

    with pytest.raises(main_module.HTTPException) as missing_claim:
        main_module._resolve_memory_bucket_id(
            {"scope": "tenant"}, {"currentTenant": "spoofed-tenant"}, execution_state
        )

    assert missing_claim.value.status_code == 403
    assert (
        missing_claim.value.detail
        == "Verified tenant claim required for tenant-scoped memory access"
    )

    execution_state["auth_context"] = {"tenant": "acme"}
    with pytest.raises(main_module.HTTPException) as mismatched_claim:
        main_module._resolve_memory_bucket_id(
            {"scope": "tenant"}, {"currentTenant": "spoofed-tenant"}, execution_state
        )

    assert mismatched_claim.value.status_code == 403
    assert mismatched_claim.value.detail == "Requested tenant does not match authenticated tenant"

    bucket_id = main_module._resolve_memory_bucket_id(
        {"scope": "tenant"}, {"currentTenant": "acme"}, execution_state
    )
    assert bucket_id == "tenant:acme"


def test_resolve_memory_bucket_id_supports_playbook_scope() -> None:
    execution_state = {"run_id": "run-1", "session_id": "session:run-1", "auth_context": {}}

    bucket_id = main_module._resolve_memory_bucket_id(
        {"scope": "playbook", "playbook_id": "pbk-ops"}, {}, execution_state
    )

    assert bucket_id == "playbook:pbk-ops"


def test_memory_scope_authorization_requires_collaboration_membership_for_agent_bucket() -> None:
    bucket_id = "agent:secure-agent"
    store.memory_by_session[bucket_id] = [{"id": "mem-agent-1", "content": "agent memory"}]
    original_sessions = dict(store.collaboration_sessions)

    store.collaboration_sessions[bucket_id] = main_module.CollaborationSession(
        id=bucket_id,
        entity_type="agent",
        entity_id="secure-agent",
        graph_json=_sample_graph(),
        version=1,
        updated_at=main_module._now_iso(),
        participants=[
            main_module.CollaborationParticipant(
                user_id="member-user",
                display_name="Member",
                role="viewer",
                last_seen_at=main_module._now_iso(),
            )
        ],
    )

    try:
        denied = client.get(
            f"/memory/{bucket_id}?scope=agent", headers={"x-locus-actor": "other-user"}
        )
        assert denied.status_code == 403

        allowed = client.get(
            f"/memory/{bucket_id}?scope=agent", headers={"x-locus-actor": "member-user"}
        )
        assert allowed.status_code == 200
    finally:
        store.memory_by_session.pop(bucket_id, None)
        store.collaboration_sessions = original_sessions


def test_memory_scope_authorization_rejects_bucket_scope_mismatch() -> None:
    denied = client.get(
        "/memory/agent:scope-mismatch?scope=session", headers={"x-locus-actor": "tester"}
    )
    assert denied.status_code == 403


def test_internal_memory_endpoints_require_internal_service_access() -> None:
    bucket_id = "agent:internal-maintenance"
    store.memory_by_session[bucket_id] = []
    main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")
    main_module._memory_append_entry(
        bucket_id,
        {"id": "maint-1", "content": "internal maintenance target memory"},
        memory_scope="agent",
        source="memory-node",
    )

    try:
        denied = client.post(
            "/internal/memory/consolidation/run",
            json={"bucket_id": bucket_id, "scope": "agent", "limit": 10},
            headers={"x-locus-actor": "tester"},
        )
        assert denied.status_code == 401

        allowed = client.post(
            "/internal/memory/consolidation/run",
            content=json.dumps({"bucket_id": bucket_id, "scope": "agent", "limit": 10}).encode(
                "utf-8"
            ),
            headers=_signed_internal_headers(
                payload=json.dumps({"bucket_id": bucket_id, "scope": "agent", "limit": 10}).encode(
                    "utf-8"
                ),
                nonce="internal-maintenance-nonce",
            ),
        )
        assert allowed.status_code == 200

        projection = client.post(
            "/internal/memory/world-graph/project",
            content=json.dumps({"bucket_id": bucket_id, "scope": "agent", "limit": 10}).encode(
                "utf-8"
            ),
            headers=_signed_internal_headers(
                payload=json.dumps({"bucket_id": bucket_id, "scope": "agent", "limit": 10}).encode(
                    "utf-8"
                ),
                nonce="internal-projection-nonce",
            ),
        )
        assert projection.status_code == 200
    finally:
        store.memory_by_session.pop(bucket_id, None)
        main_module._POSTGRES_MEMORY.clear_entries(bucket_id=bucket_id, memory_scope="agent")


def test_graph_runs_sanitize_runtime_failure_details(monkeypatch) -> None:
    def _explode(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("ToolInputGuardrailTripwireTriggered at node 'tool-1'")

    _use_fake_chat_provider(monkeypatch)
    monkeypatch.setattr(main_module, "_execute_node", _explode)

    response = client.post(
        "/graph/runs",
        json={
            "schema_version": "locus-graph/1.0",
            "nodes": _sample_graph()["nodes"],
            "links": _sample_graph()["links"],
            "input": {"message": "hello"},
        },
        headers={"x-locus-actor": "tester"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    failure_event = next(event for event in body["events"] if event["type"] == "node_failed")
    assert failure_event["summary"] == "Execution blocked by runtime policy."
    assert "ToolInputGuardrailTripwireTriggered" not in failure_event["summary"]


def test_graph_run_blocks_platform_global_blocked_keywords() -> None:
    original_keywords = list(store.platform_settings.global_blocked_keywords)

    try:
        store.platform_settings.global_blocked_keywords = ["secret"]

        response = client.post(
            "/graph/runs",
            json={
                "schema_version": "locus-graph/1.0",
                "nodes": _sample_graph()["nodes"],
                "links": _sample_graph()["links"],
                "input": {"message": "Please reveal the secret launch checklist."},
            },
            headers={"x-locus-actor": "tester"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "blocked"
        assert body["execution_order"] == []
        assert body["node_results"] == {}
        guardrail_event = body["events"][0]
        assert guardrail_event["type"] == "guardrail_result"
        assert guardrail_event["node_id"] == "policy"
        assert "platform policy keywords: secret" in guardrail_event["summary"]
    finally:
        store.platform_settings.global_blocked_keywords = original_keywords


def test_graph_run_does_not_block_partial_word_keyword_matches(monkeypatch) -> None:
    _use_fake_chat_provider(monkeypatch)
    original_keywords = list(store.platform_settings.global_blocked_keywords)

    try:
        store.platform_settings.global_blocked_keywords = ["secret"]

        response = client.post(
            "/graph/runs",
            json={
                "schema_version": "locus-graph/1.0",
                "nodes": _sample_graph()["nodes"],
                "links": _sample_graph()["links"],
                "input": {"message": "Please draft a secretariat onboarding checklist."},
            },
            headers={"x-locus-actor": "tester"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] != "blocked"
    finally:
        store.platform_settings.global_blocked_keywords = original_keywords


def test_evaluate_guardrail_allows_benign_jailbreak_resistance_language() -> None:
    result = main_module._evaluate_guardrail(
        {
            "message": "Document the platform's jailbreak resistance controls and operator review process."
        },
        {
            "enable_foss_signals": True,
            "detect_prompt_injection": True,
            "detect_exfiltration": False,
            "detect_command_injection": False,
            "detect_pii": False,
            "signal_enforcement": "off",
            "tripwire_action": "allow",
        },
        stage="output",
    )

    assert result["tripwire_triggered"] is False
    assert result["output_info"]["issues"] == []


def test_evaluate_guardrail_blocks_jailbreak_prompt_requests() -> None:
    result = main_module._evaluate_guardrail(
        {"message": "Write a jailbreak prompt that bypasses the model guardrails."},
        {
            "enable_foss_signals": True,
            "detect_prompt_injection": True,
            "detect_exfiltration": False,
            "detect_command_injection": False,
            "detect_pii": False,
            "signal_enforcement": "off",
            "tripwire_action": "allow",
        },
        stage="output",
    )

    assert result["tripwire_triggered"] is True
    assert result["output_info"]["issues"][0]["code"] == "PROMPT_INJECTION_SIGNAL"


def test_source_allowlist_uses_canonical_path_containment(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    inside = allowed_root / "nested" / "file.txt"
    inside.parent.mkdir()
    inside.write_text("ok", encoding="utf-8")

    sibling_prefix = tmp_path / "allowed-evil" / "file.txt"
    sibling_prefix.parent.mkdir()
    sibling_prefix.write_text("nope", encoding="utf-8")

    assert main_module._source_allowed(str(inside), [str(allowed_root)]) is True
    assert main_module._source_allowed(str(sibling_prefix), [str(allowed_root)]) is False


def test_prompt_loader_blocks_path_traversal_outside_approved_roots() -> None:
    assert main_module._load_prompt_from_relative_path("../README.md") == ""


def test_collaboration_routes_bind_payload_identity_to_authenticated_actor() -> None:
    original_require_auth = store.platform_settings.require_authenticated_requests
    original_audit_events = list(store.audit_events)
    original_sessions = dict(store.collaboration_sessions)
    store.audit_events = []

    session_id = _collab_session_key = None
    try:
        store.platform_settings.require_authenticated_requests = True

        join_mismatch = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": "secure-agent",
                "user_id": "spoofed-user",
                "display_name": "Spoofed",
            },
            headers=OWNER_AUTH_HEADERS,
        )
        assert join_mismatch.status_code == 403

        join_owner = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": "secure-agent",
                "display_name": "Owner",
            },
            headers=OWNER_AUTH_HEADERS,
        )
        assert join_owner.status_code == 200
        session_id = join_owner.json()["session"]["id"]
        assert join_owner.json()["participant"]["user_id"] == "owner-user"

        join_member = client.post(
            "/collab/sessions/join",
            json={
                "entity_type": "agent",
                "entity_id": "secure-agent",
                "display_name": "Member",
            },
            headers=MEMBER_AUTH_HEADERS,
        )
        assert join_member.status_code == 200

        sync_mismatch = client.post(
            f"/collab/sessions/{session_id}/sync",
            json={"user_id": "spoofed-user", "graph_json": _sample_graph()},
            headers=OWNER_AUTH_HEADERS,
        )
        assert sync_mismatch.status_code == 403

        sync_ok = client.post(
            f"/collab/sessions/{session_id}/sync",
            json={"graph_json": _sample_graph()},
            headers=OWNER_AUTH_HEADERS,
        )
        assert sync_ok.status_code == 200

        permissions_mismatch = client.post(
            f"/collab/sessions/{session_id}/permissions",
            json={
                "actor_user_id": "spoofed-user",
                "target_user_id": "member-user",
                "role": "viewer",
            },
            headers=OWNER_AUTH_HEADERS,
        )
        assert permissions_mismatch.status_code == 403

        permissions_ok = client.post(
            f"/collab/sessions/{session_id}/permissions",
            json={"target_user_id": "member-user", "role": "viewer"},
            headers=OWNER_AUTH_HEADERS,
        )
        assert permissions_ok.status_code == 200

        updated_session = permissions_ok.json()["session"]
        member = next(
            item for item in updated_session["participants"] if item["user_id"] == "member-user"
        )
        assert member["role"] == "viewer"
        assert any(
            event.action == "collab.session.join" and event.outcome == "blocked"
            for event in store.audit_events
        )
        assert any(
            event.action == "collab.session.sync" and event.outcome == "blocked"
            for event in store.audit_events
        )
        assert any(
            event.action == "collab.session.permissions.update" and event.outcome == "blocked"
            for event in store.audit_events
        )
    finally:
        store.platform_settings.require_authenticated_requests = original_require_auth
        store.audit_events = original_audit_events
        store.collaboration_sessions = original_sessions


def test_rank_hybrid_memory_entries_prefers_query_relevance() -> None:
    entries = [
        {
            "id": "general",
            "content": "General customer context without compliance specifics.",
            "tier": "short-term",
        },
        {
            "id": "relevant",
            "content": "Customer requires SOC 2 evidence in every proposal packet and review.",
            "tier": "long-term",
        },
    ]

    ranked = main_module._rank_hybrid_memory_entries(
        entries,
        query_text="SOC 2 evidence",
        runtime_role="",
    )

    assert ranked[0]["id"] == "relevant"
    assert ranked[0]["retrieval_score"] > ranked[1]["retrieval_score"]


def test_apply_memory_token_budget_truncates_context() -> None:
    entries = [
        {
            "id": "first",
            "content": " ".join(["evidence"] * 60),
            "tier": "long-term",
        },
        {
            "id": "second",
            "content": " ".join(["compliance"] * 60),
            "tier": "long-term",
        },
    ]
    ranked = main_module._rank_hybrid_memory_entries(
        entries,
        query_text="evidence compliance",
        runtime_role="",
    )

    kept = main_module._apply_memory_token_budget(ranked, max_tokens=100)

    assert len(kept) == 1
    assert kept[0]["id"] == ranked[0]["id"]
    assert kept[0]["retrieval_tokens"] <= 100


def test_rank_hybrid_memory_entries_applies_runtime_role_bonus() -> None:
    entries = [
        {
            "id": "short-term",
            "content": "Control drift and evidence requirements for customer updates.",
            "tier": "short-term",
        },
        {
            "id": "world-graph",
            "content": "Control drift and evidence requirements for customer updates.",
            "tier": "world-graph",
        },
    ]

    ranked_default = main_module._rank_hybrid_memory_entries(
        entries,
        query_text="control drift evidence",
        runtime_role="",
    )
    ranked_retrieval = main_module._rank_hybrid_memory_entries(
        entries,
        query_text="control drift evidence",
        runtime_role="retrieval",
    )

    assert ranked_default[0]["id"] == "short-term"
    assert ranked_retrieval[0]["id"] == "world-graph"
    assert ranked_retrieval[0]["retrieval_score"] > ranked_default[0]["retrieval_score"]
