from __future__ import annotations

from test_generated_artifacts import AUTH_HEADERS, client


def _cognitive_graph_payload(*, message: str) -> dict[str, object]:
    return {
        "schema_version": "frontier-graph/1.0",
        "nodes": [
            {
                "id": "trigger",
                "title": "Trigger",
                "type": "trigger",
                "x": 60,
                "y": 100,
                "config": {"trigger_mode": "manual"},
            },
            {
                "id": "goal",
                "title": "Goal",
                "type": "goal",
                "x": 260,
                "y": 80,
                "config": {
                    "intent": "Produce a bounded release recommendation",
                    "success_criteria": ["Explain evidence", "State next action"],
                },
            },
            {
                "id": "evidence",
                "title": "Evidence",
                "type": "evidence",
                "x": 260,
                "y": 220,
                "config": {
                    "required_evidence": ["test results"],
                    "allowed_sources": ["kb://release"],
                },
            },
            {
                "id": "assembly",
                "title": "Assembly",
                "type": "assembly",
                "x": 520,
                "y": 150,
                "config": {
                    "consensus_policy": "weighted-support",
                    "confidence_threshold": 0.55,
                },
            },
            {
                "id": "commitment",
                "title": "Commitment",
                "type": "commitment",
                "x": 760,
                "y": 150,
                "config": {
                    "autonomy_level": "bounded",
                    "confidence_threshold": 0.55,
                },
            },
            {
                "id": "output",
                "title": "Output",
                "type": "output",
                "x": 980,
                "y": 150,
                "config": {"destination": "artifact_store", "format": "json"},
            },
        ],
        "links": [
            {"from": "trigger", "to": "goal", "from_port": "out", "to_port": "in"},
            {"from": "trigger", "to": "evidence", "from_port": "payload", "to_port": "context"},
            {"from": "goal", "to": "assembly", "from_port": "out", "to_port": "goal"},
            {"from": "evidence", "to": "assembly", "from_port": "out", "to_port": "evidence"},
            {"from": "assembly", "to": "commitment", "from_port": "out", "to_port": "commitment"},
            {"from": "commitment", "to": "output", "from_port": "out", "to_port": "in"},
            {"from": "commitment", "to": "output", "from_port": "result", "to_port": "result"},
        ],
        "input": {"message": message},
    }


def test_node_definitions_include_cognitive_mvp_nodes() -> None:
    response = client.get("/node-definitions", headers=AUTH_HEADERS)

    assert response.status_code == 200
    type_keys = {item["type_key"] for item in response.json()}
    assert {
        "frontier/goal",
        "frontier/evidence",
        "frontier/assembly",
        "frontier/commitment",
    }.issubset(type_keys)


def test_graph_validation_accepts_cognitive_mvp_graph() -> None:
    response = client.post(
        "/graph/validate",
        json=_cognitive_graph_payload(message="test results show no critical regressions"),
    )

    assert response.status_code == 200
    assert response.json()["valid"] is True


def test_graph_run_executes_cognitive_mvp_graph() -> None:
    response = client.post(
        "/graph/runs",
        json=_cognitive_graph_payload(message="test results show no critical regressions"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["node_results"]["assembly"]["commitment"]["blockers"] == []
    assert body["node_results"]["commitment"]["commitment"]["status"] == "committed"


def test_graph_run_escalates_when_required_evidence_is_missing() -> None:
    response = client.post(
        "/graph/runs",
        json=_cognitive_graph_payload(message="deployment notes only"),
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["node_results"]["assembly"]["commitment"]["blockers"] == [
        "Missing required evidence: test results"
    ]
    assert body["node_results"]["commitment"]["commitment"]["status"] == "escalated"
