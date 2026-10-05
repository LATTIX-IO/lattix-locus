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


@pytest.mark.parametrize(
    "base_url",
    [
        "http://linear-proxy.example/",
        "http://192.168.1.20:8000",
        "http://user:secret@127.0.0.1:8000",
        "file:///tmp/locus",
    ],
)
def test_tracker_rejects_insecure_or_credentialed_bridge_urls(base_url: str) -> None:
    with pytest.raises(LinearNotConfigured, match="HTTPS, or HTTP on a loopback"):
        LinearMcpTracker(api_base_url=base_url, api_token="local-api-test-token")


def test_tracker_accepts_https_for_remote_bridge() -> None:
    tracker = LinearMcpTracker(
        api_base_url="https://locus.example",
        api_token="local-api-test-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"result": []})),
    )
    assert tracker.list_project_issues("locus") == []


def test_tracker_preserves_explicit_empty_issue_state_and_label() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        payload = request.read()
        assert b'"state_name":""' in payload
        assert b'"label_name":""' in payload
        return httpx.Response(200, json={"result": "LOCUS-9"})

    tracker = LinearMcpTracker(
        api_token="local-api-test-token",
        transport=httpx.MockTransport(handle),
    )
    assert (
        tracker.create_issue(
            team_id="team-1",
            title="Feedback",
            description="Needs human triage",
            state_name="",
            label_name="",
        )
        == "LOCUS-9"
    )
