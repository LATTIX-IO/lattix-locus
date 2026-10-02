"""Minimal Model Context Protocol client (streamable HTTP transport).

Patterned after Open WebUI's MIT-licensed MCP integration, implemented
independently for the xFrontier runtime. Supports the initialize handshake,
tools/list, and tools/call over JSON-RPC 2.0. Responses may arrive as plain
JSON or as a server-sent-event stream; both are handled.

Only HTTP/SSE transports are supported — stdio servers require process
supervision and are deferred to the plugin runtime.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_INFO = {"name": "lattix-xfrontier", "version": "0.1.0"}


class McpError(RuntimeError):
    """Raised when an MCP server returns an error or an unusable response."""


class McpHttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str = "",
        timeout_seconds: float = 20.0,
    ) -> None:
        self.base_url = str(base_url or "").strip()
        if not self.base_url.lower().startswith(("http://", "https://")):
            raise McpError("MCP server URL must be an absolute http(s) URL")
        self.bearer_token = str(bearer_token or "").strip()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.session_id = ""
        self._request_counter = 0
        self._initialized = False

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse_sse_payload(raw: str) -> dict[str, Any] | None:
        """Extract the last JSON-RPC message from an SSE body."""
        message: dict[str, Any] | None = None
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk:
                continue
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                message = parsed
        return message

    def _post(self, payload: dict[str, Any], *, expect_response: bool = True) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - URL validated against integration allowlists by the caller
            session_id = response.headers.get("Mcp-Session-Id") or response.headers.get(
                "mcp-session-id"
            )
            if session_id:
                self.session_id = session_id.strip()
            body = response.read().decode("utf-8", errors="replace")
            content_type = str(response.headers.get("Content-Type") or "")

        if not expect_response:
            return {}
        if "text/event-stream" in content_type:
            message = self._parse_sse_payload(body)
        else:
            try:
                parsed = json.loads(body) if body.strip() else None
            except json.JSONDecodeError as exc:
                raise McpError(f"MCP server returned non-JSON response: {body[:120]}") from exc
            message = parsed if isinstance(parsed, dict) else None
        if message is None:
            raise McpError("MCP server returned no JSON-RPC message")
        error = message.get("error")
        if isinstance(error, dict):
            raise McpError(str(error.get("message") or "MCP server error"))
        return message

    def _call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_counter += 1
        message = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._request_counter,
                "method": method,
                "params": params or {},
            }
        )
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method}, expect_response=False)

    # -- protocol operations -------------------------------------------------

    def initialize(self) -> None:
        if self._initialized:
            return
        self._call(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(_CLIENT_INFO),
            },
        )
        try:
            self._notify("notifications/initialized")
        except Exception:  # noqa: BLE001 - some servers reject the notification; harmless
            pass
        self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._call("tools/list")
        tools: list[dict[str, Any]] = []
        for item in result.get("tools") or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            tools.append(
                {
                    "name": str(item["name"]),
                    "description": str(item.get("description") or ""),
                    "input_schema": (
                        item.get("inputSchema")
                        if isinstance(item.get("inputSchema"), dict)
                        else {"type": "object", "properties": {}}
                    ),
                }
            )
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        self.initialize()
        result = self._call("tools/call", {"name": str(name), "arguments": arguments or {}})
        if result.get("isError"):
            parts = result.get("content") or []
            detail = "; ".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            )
            raise McpError(detail or f"Tool '{name}' reported an error")
        texts: list[str] = []
        for part in result.get("content") or []:
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type == "text":
                texts.append(str(part.get("text") or ""))
            elif part_type == "resource" and isinstance(part.get("resource"), dict):
                texts.append(str(part["resource"].get("text") or ""))
        if texts:
            return "\n".join(text for text in texts if text)
        structured = result.get("structuredContent")
        if structured is not None:
            return json.dumps(structured)[:8000]
        return json.dumps(result)[:8000]
