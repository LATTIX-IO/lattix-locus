from __future__ import annotations

import httpx
import pytest

from locus_runtime.loop_runner.linear import LinearNotConfigured
from locus_runtime.loop_runner.linear_mcp import LinearMcpTracker


def test_tracker_uses_authenticated_local_bridge_without_linear_token() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "http://127.0.0.1:8000/loop/tracker"
        assert request.headers["Authorization"] == "Bearer local-api-test-token"
        assert request.read() == b'{"operation":"list_project_issues","project_slug":"locus"}'
        return httpx.Response(200, json={"result": []})

    tracker = LinearMcpTracker(
        api_token="local-api-test-token",
        transport=httpx.MockTransport(handle),
    )

    assert tracker.list_project_issues("locus") == []


def test_tracker_fails_closed_when_local_bridge_reports_missing_oauth() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Linear MCP connection is unavailable"})

    tracker = LinearMcpTracker(
        api_token="local-api-test-token",
        transport=httpx.MockTransport(handle),
    )

    with pytest.raises(LinearNotConfigured, match="unavailable or unauthorized"):
        tracker.list_project_issues("locus")
