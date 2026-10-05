from __future__ import annotations

from pathlib import Path
from typing import Any

from locus_runtime.harness.codex_mcp_server import McpToolServer, _capabilities


class _FakeToolset:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "execute_bash",
                    "description": "Run a Locus-gated workspace command.",
                    "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
                },
            },
            {
                "type": "function",
                "function": {"name": "submit", "parameters": {"type": "object"}},
            },
        ]

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return "result"


def test_mcp_server_exposes_only_coding_tools_and_dispatches_allowed_call():
    toolset = _FakeToolset()
    server = McpToolServer(toolset)  # type: ignore[arg-type]
    tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools is not None
    assert [tool["name"] for tool in tools["result"]["tools"]] == ["execute_bash"]

    result = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "execute_bash", "arguments": {"command": "pytest -q"}},
        }
    )
    assert result is not None and result["result"]["content"][0]["text"] == "result"
    assert toolset.calls == [("execute_bash", {"command": "pytest -q"})]

    denied = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "submit", "arguments": {}},
        }
    )
    assert denied is not None and denied["error"]["code"] == -32602
    assert len(toolset.calls) == 1


def test_mcp_server_kill_switch_denies_tool_dispatch(tmp_path: Path):
    toolset = _FakeToolset()
    kill_switch = tmp_path / "DISABLED"
    kill_switch.write_text("disabled", encoding="utf-8")
    server = McpToolServer(toolset, kill_switch_path=kill_switch)  # type: ignore[arg-type]
    result = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "execute_bash", "arguments": {"command": "pytest -q"}},
        }
    )
    assert result is not None and result["result"]["isError"] is True
    assert not toolset.calls


def test_mcp_server_retains_zero_action_budget() -> None:
    assert _capabilities({"max_actions": 0}).max_actions == 0
