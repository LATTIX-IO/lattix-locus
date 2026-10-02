"""Focused tests for MCP runtime tool invocation (reference-plan Phase A)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("FRONTIER_API_BEARER_TOKEN") or "").strip():
    os.environ["FRONTIER_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import IntegrationDefinition, store


def test_mcp_sse_payload_parsing() -> None:
    from app.mcp_client import McpHttpClient

    body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
    parsed = McpHttpClient._parse_sse_payload(body)
    assert parsed is not None
    assert parsed["result"]["tools"] == []


def test_mcp_client_rejects_non_http_url() -> None:
    from app.mcp_client import McpError, McpHttpClient

    try:
        McpHttpClient("stdio://local")
    except McpError as exc:
        assert "absolute" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("expected McpError for non-http URL")


def _make_configured_mcp_integration(**overrides) -> str:
    integration_id = "mcp-test-integration"
    integration = IntegrationDefinition(
        id=integration_id,
        name=overrides.get("name", "Test MCP"),
        type="custom",
        status=overrides.get("status", "configured"),
        base_url=overrides.get("base_url", "http://mcp.internal.test/mcp"),
        auth_type=overrides.get("auth_type", "none"),
        secret_ref=overrides.get("secret_ref", ""),
        metadata_json=overrides.get("metadata_json", {"protocol": "mcp", "transport": "http"}),
    )
    store.integrations[integration_id] = integration
    return integration_id


class _FakeMcpServer:
    def __init__(self, *_args, **_kwargs) -> None:
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        return [
            {
                "name": "search",
                "description": "Search the corpus",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        return f"result for {name}({arguments})"


def test_gather_tools_skips_draft_integrations(monkeypatch) -> None:
    integration_id = _make_configured_mcp_integration(status="draft")
    monkeypatch.setattr(main_module.mcp_client, "McpHttpClient", _FakeMcpServer)
    try:
        schemas, dispatch, sources = main_module._gather_mcp_run_tools()
        assert all("test" not in name.lower() for name in dispatch)
        assert "Test MCP" not in sources
    finally:
        store.integrations.pop(integration_id, None)


def test_gather_tools_exposes_configured_mcp_tools(monkeypatch) -> None:
    integration_id = _make_configured_mcp_integration()
    monkeypatch.setattr(main_module.mcp_client, "McpHttpClient", _FakeMcpServer)
    original_local = store.platform_settings.mcp_require_local_server
    try:
        store.platform_settings.mcp_require_local_server = False
        schemas, dispatch, sources = main_module._gather_mcp_run_tools()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        qualified = schemas[0]["function"]["name"]
        assert qualified in dispatch
        server, real_name = dispatch[qualified]
        assert real_name == "search"
        assert "Test MCP" in sources
    finally:
        store.platform_settings.mcp_require_local_server = original_local
        store.integrations.pop(integration_id, None)


def test_gather_tools_high_risk_pattern_is_excluded(monkeypatch) -> None:
    integration_id = _make_configured_mcp_integration()
    monkeypatch.setattr(main_module.mcp_client, "McpHttpClient", _FakeMcpServer)
    original_local = store.platform_settings.mcp_require_local_server
    original_patterns = list(store.platform_settings.high_risk_tool_patterns or [])
    try:
        store.platform_settings.mcp_require_local_server = False
        store.platform_settings.high_risk_tool_patterns = ["search"]
        schemas, dispatch, _ = main_module._gather_mcp_run_tools()
        assert schemas == []
        assert dispatch == {}
    finally:
        store.platform_settings.mcp_require_local_server = original_local
        store.platform_settings.high_risk_tool_patterns = original_patterns
        store.integrations.pop(integration_id, None)


def test_gather_tools_blocked_when_tool_calls_disabled(monkeypatch) -> None:
    integration_id = _make_configured_mcp_integration()
    monkeypatch.setattr(main_module.mcp_client, "McpHttpClient", _FakeMcpServer)
    original = store.platform_settings.block_tool_calls
    try:
        store.platform_settings.block_tool_calls = True
        schemas, dispatch, sources = main_module._gather_mcp_run_tools()
        assert (schemas, dispatch, sources) == ([], {}, [])
    finally:
        store.platform_settings.block_tool_calls = original
        store.integrations.pop(integration_id, None)


def test_run_openai_chat_executes_tool_loop(monkeypatch) -> None:
    """The chat loop should call the executor then return the model's final text."""

    class _Fn:
        def __init__(self, name, arguments):
            self.name = name
            self.arguments = arguments

    class _ToolCall:
        def __init__(self, call_id, name, arguments):
            self.id = call_id
            self.function = _Fn(name, arguments)

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Choice:
        def __init__(self, message):
            self.message = message

    class _Response:
        def __init__(self, message):
            self.choices = [_Choice(message)]

    turns = [
        _Response(_Msg(None, [_ToolCall("c1", "search", '{"q": "lattix"}')])),
        _Response(_Msg("Final answer using the tool result.")),
    ]

    class _Completions:
        def create(self, **_kwargs):
            return turns.pop(0)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    monkeypatch.setattr(main_module, "_get_chat_client", lambda provider: (_Client(), ""))

    executed: list[tuple[str, dict]] = []
    events: list[tuple[str, bool]] = []

    def _executor(name, arguments):
        executed.append((name, arguments))
        return "tool says hi"

    def _on_event(name, arguments, output, succeeded):
        events.append((name, succeeded))

    text, meta = main_module._run_openai_chat(
        system_prompt="sys",
        user_prompt="hello",
        model="ollama/test",
        temperature=0.2,
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        tool_executor=_executor,
        on_tool_event=_on_event,
    )
    assert text == "Final answer using the tool result."
    assert executed == [("search", {"q": "lattix"})]
    assert events == [("search", True)]
    assert meta["tool_calls_made"] == 1


def test_run_openai_chat_enforces_max_tool_calls(monkeypatch) -> None:
    class _Fn:
        def __init__(self, name):
            self.name = name
            self.arguments = "{}"

    class _ToolCall:
        _counter = 0

        def __init__(self):
            type(self)._counter += 1
            self.id = f"c{type(self)._counter}"
            self.function = _Fn("loop")

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls

    class _Response:
        def __init__(self, message):
            self.choices = [type("C", (), {"message": message})()]

    class _Completions:
        def create(self, **kwargs):
            # Stops requesting tools once the loop disables them.
            if "tools" in kwargs:
                return _Response(_Msg(None, [_ToolCall()]))
            return _Response(_Msg("done after cap"))

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(main_module, "_get_chat_client", lambda provider: (_Client(), ""))

    calls = {"n": 0}

    def _executor(name, arguments):
        calls["n"] += 1
        return "x"

    text, meta = main_module._run_openai_chat(
        system_prompt="",
        user_prompt="loop please",
        model="ollama/test",
        temperature=0.0,
        tools=[{"type": "function", "function": {"name": "loop", "parameters": {}}}],
        tool_executor=_executor,
        max_tool_calls=2,
    )
    assert calls["n"] == 2
    assert meta["tool_calls_made"] == 2
    assert text == "done after cap"
