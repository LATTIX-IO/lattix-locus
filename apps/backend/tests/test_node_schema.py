"""Focused tests for declarative node-definition input schemas (Phase B)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

from app.main import app

client = TestClient(app)
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-frontier-actor": "frontier-admin"}


def test_node_definitions_carry_typed_input_schemas() -> None:
    response = client.get("/node-definitions", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    by_key = {node["type_key"]: node for node in response.json()}

    agent = by_key["frontier/agent"]
    fields = {field["name"]: field for field in agent["inputs"]}
    assert "agent_id" in fields
    assert fields["agent_id"]["field_type"] == "dropdown"
    assert fields["agent_id"]["required"] is True
    assert fields["agent_id"]["options_source"] == "agents"
    assert fields["temperature"]["field_type"] == "slider"
    assert fields["temperature"]["advanced"] is True
    assert fields["temperature"]["min"] == 0.0
    assert fields["temperature"]["max"] == 1.0


def test_field_types_are_within_the_supported_set() -> None:
    response = client.get("/node-definitions", headers=ADMIN_HEADERS)
    allowed = {"text", "textarea", "number", "slider", "bool", "dropdown", "secret", "code"}
    for node in response.json():
        for field in node.get("inputs", []):
            assert field["field_type"] in allowed
            assert field["name"] and field["label"]


def test_tool_call_node_references_integrations_source() -> None:
    response = client.get("/node-definitions", headers=ADMIN_HEADERS)
    by_key = {node["type_key"]: node for node in response.json()}
    tool_node = by_key["frontier/tool-call"]
    sources = {field.get("options_source") for field in tool_node["inputs"]}
    assert "integrations" in sources
