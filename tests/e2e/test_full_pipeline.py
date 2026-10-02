def test_health_endpoint(test_client) -> None:
    response = test_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_federation_status_endpoint(test_client, auth_headers) -> None:
    response = test_client.get("/federation/status", headers=auth_headers)
    assert response.status_code == 200
    assert "enabled" in response.json()


def test_protected_route_requires_auth(test_client) -> None:
    response = test_client.get("/federation/status")
    assert response.status_code == 401


def test_cognitive_graph_end_to_end(test_client, auth_headers) -> None:
    response = test_client.post(
        "/graph/runs",
        headers=auth_headers,
        json={
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
                    "x": 240,
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
                    "x": 240,
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
                {
                    "from": "assembly",
                    "to": "commitment",
                    "from_port": "out",
                    "to_port": "commitment",
                },
                {"from": "commitment", "to": "output", "from_port": "out", "to_port": "in"},
                {"from": "commitment", "to": "output", "from_port": "result", "to_port": "result"},
            ],
            "input": {"message": "test results show no critical regressions"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["node_results"]["assembly"]["commitment"]["blockers"] == []
    assert body["node_results"]["commitment"]["commitment"]["status"] == "committed"
    assert "Proceed" in body["node_results"]["commitment"]["commitment"]["decision"]
