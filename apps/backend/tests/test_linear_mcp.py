from __future__ import annotations

import json
from typing import Any

import pytest

from app.linear_mcp import LinearMcpAdapter, LinearMcpError


class FakeLinearMcp:
    def __init__(self, *, list_issues: list[dict[str, Any]] | None = None) -> None:
        self.tools = [
            {"name": "list_projects", "input_schema": {"properties": {"query": {}, "limit": {}}}},
            {"name": "list_issues", "input_schema": {"properties": {"projectId": {}, "limit": {}}}},
            {"name": "get_issue", "input_schema": {"properties": {"id": {}}}},
            {"name": "list_issue_statuses", "input_schema": {"properties": {"teamId": {}}}},
            {
                "name": "update_issue",
                "input_schema": {"properties": {"id": {}, "stateId": {}, "priority": {}}},
            },
            {
                "name": "create_issue",
                "input_schema": {
                    "properties": {"teamId": {}, "projectId": {}, "title": {}, "description": {}}
                },
            },
            {"name": "add_issue_label", "input_schema": {"properties": {"id": {}, "labelIds": {}}}},
        ]
        self.issues = {str(issue["id"]): issue for issue in list_issues or []}
        self.calls: list[tuple[str, dict[str, Any], Any]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        return self.tools

    def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, decision: Any = None
    ) -> str:
        args = arguments or {}
        self.calls.append((name, args, decision))
        if name == "list_projects":
            return json.dumps(
                {"projects": [{"id": "project-1", "slugId": "locus", "team": {"id": "team-1"}}]}
            )
        if name == "list_issues":
            return json.dumps({"issues": list(self.issues.values())})
        if name == "get_issue":
            return json.dumps(self.issues[args["id"]])
        if name == "list_issue_statuses":
            return json.dumps(
                {"states": [{"id": "state-progress", "name": "In Progress", "type": "started"}]}
            )
        if name == "update_issue":
            issue = self.issues[args["id"]]
            if args.get("stateId"):
                issue["state"] = {"id": args["stateId"], "name": "In Progress"}
            if args.get("priority") is not None:
                issue["priority"] = args["priority"]
            return "Issue updated successfully"
        if name == "create_issue":
            issue = {
                "id": "issue-new",
                "identifier": "LOCUS-10",
                "title": args["title"],
                "description": args["description"],
                "state": {"name": "Todo"},
                "team": {"id": args["teamId"]},
            }
            self.issues[issue["id"]] = issue
            return json.dumps({"issue": issue})
        if name == "add_issue_label":
            issue = self.issues[args["id"]]
            issue["labels"] = {"nodes": [{"name": str(label)} for label in args["labelIds"]]}
            return "Label added"
        raise AssertionError(f"Unexpected tool: {name}")


def _issue(state: str = "Todo") -> dict[str, Any]:
    return {
        "id": "issue-1",
        "identifier": "LOCUS-1",
        "title": "Test issue",
        "description": "Description",
        "priority": 2,
        "url": "https://linear.app/acme/issue/LOCUS-1",
        "state": {"id": "state-todo", "name": state},
        "team": {"id": "team-1"},
        "labels": {"nodes": [{"name": "agent:eligible"}]},
    }


def test_project_issue_board_and_team_are_normalized() -> None:
    server = FakeLinearMcp(list_issues=[_issue()])
    adapter = LinearMcpAdapter(server, lambda _name, _args: "gateway-allow")

    issues = adapter.issues("locus")

    assert adapter.project_team_id("locus") == "team-1"
    assert issues[0]["identifier"] == "LOCUS-1"
    assert issues[0]["state"] == "Todo"
    assert issues[0]["labels"] == ["agent:eligible"]
    assert all(call[2] == "gateway-allow" for call in server.calls)


def test_transition_accepts_text_ack_and_reads_back_requested_state() -> None:
    server = FakeLinearMcp(list_issues=[_issue()])
    cleaned: list[bool] = []

    def authorize(_name: str, _args: dict[str, Any]) -> tuple[str, Any]:
        return "gateway-allow", lambda: cleaned.append(True)

    adapter = LinearMcpAdapter(server, authorize)
    adapter.transition("issue-1", "In Progress")

    update = next(call for call in server.calls if call[0] == "update_issue")
    assert update[1] == {"id": "issue-1", "stateId": "state-progress"}
    assert server.issues["issue-1"]["state"]["name"] == "In Progress"
    assert len(cleaned) == len(server.calls)


def test_create_issue_stays_in_project_and_sets_priority_state_and_label() -> None:
    server = FakeLinearMcp()
    adapter = LinearMcpAdapter(server, lambda _name, _args: "gateway-allow")

    identifier = adapter.create_issue(
        team_id="team-1",
        title="A focused research experiment",
        description="Test it",
        project_slug="locus",
        priority=2,
    )

    assert identifier == "LOCUS-10"
    create_call = next(call for call in server.calls if call[0] == "create_issue")
    assert create_call[1]["projectId"] == "project-1"
    assert create_call[1]["teamId"] == "team-1"
    assert server.issues["issue-new"]["priority"] == 2
    assert any(call[0] == "add_issue_label" for call in server.calls)

    adapter.set_priority("issue-new", 0)
    assert server.issues["issue-new"]["priority"] == 0


def test_reads_fail_closed_when_the_server_returns_unstructured_text() -> None:
    class TextOnlyServer(FakeLinearMcp):
        def call_tool(
            self, name: str, arguments: dict[str, Any] | None = None, *, decision: Any = None
        ) -> str:
            if name == "list_issues":
                return "Project list unavailable"
            return super().call_tool(name, arguments, decision=decision)

    adapter = LinearMcpAdapter(TextOnlyServer(), lambda _name, _args: None)

    with pytest.raises(LinearMcpError, match="structured data"):
        adapter.issues("locus")


def test_issue_query_is_not_sent_without_project_scope() -> None:
    class NoProjectLookupServer(FakeLinearMcp):
        def list_tools(self) -> list[dict[str, Any]]:
            return [tool for tool in self.tools if tool["name"] != "list_projects"]

    server = NoProjectLookupServer()
    adapter = LinearMcpAdapter(server, lambda _name, _args: None)

    with pytest.raises(LinearMcpError, match="unsupported arguments"):
        adapter.issues("locus")

    assert all(name != "list_issues" for name, _args, _decision in server.calls)


def test_missing_tool_is_rejected_before_gateway_or_network_call() -> None:
    server = FakeLinearMcp()
    server.tools = [item for item in server.tools if item["name"] != "get_issue"]
    authorized: list[str] = []
    adapter = LinearMcpAdapter(server, lambda name, _args: authorized.append(name))

    with pytest.raises(LinearMcpError, match="get_issue"):
        adapter.issue("issue-1")

    assert authorized == []
    assert server.calls == []
