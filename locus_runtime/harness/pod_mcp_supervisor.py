"""Trusted MCP request handler for durable Gateway-issued pods (LOCUS-416).

This is the SUPERVISOR-SIDE endpoint, not the subprocess-side stdio proxy.
A future transport must authenticate and bind its peer to exactly this child
process/lease. Never pass the PodLease, this handler, or its broker to an LLM,
pod, CLI plugin, skill, browser, or network client. This module introduces no
listener, executable, independent Gateway, or broad policy authority.

Every tool maps to an administrator-registered fixed command, executes only
through SandboxedPodToolBroker and its existing PodActionReceipts, and returns
a typed status. Raw prompts, argv, shell results, credentials and action
receipts are not sent back to the model.
"""
from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, cast

from locus_runtime.action_receipts import ActionReplayDenied
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_tool_broker import SandboxedPodToolBroker
from locus_runtime.tool_result_contract import ToolResultRejected

_VERSION = "2025-03-26"
_MAX_MESSAGE = 65536
_MAX_ID = 96
_EXPORT = re.compile(r"locus_[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
_REQ_ID = re.compile(r"[A-Za-z0-9_.:-]{1,96}\Z", re.ASCII)


class PodMcpContractDenied(AdmissionDenied):
    """Invalid or untrusted MCP request, binding or delegated capability."""


def _bounded_request_id(value: object) -> str:
    """Preserve JSON-RPC string/integer id types for durable replay identity."""
    if type(value) is int and 0 <= value <= 2**53 - 1:
        return "i:" + str(value)
    if isinstance(value, str) and len(value) <= _MAX_ID and _REQ_ID.fullmatch(value):
        return "s:" + value
    raise PodMcpContractDenied("invalid bounded MCP request id")


def _action_key(lease: PodLease, value: object) -> str:
    # Do not use model-chosen names or arbitrary action ids directly. One
    # JSON-RPC id maps to one durable pod action, even if the tool name changes.
    canonical = _bounded_request_id(value).encode("ascii")
    content = (
        b"locus.pod-mcp.action.v1\x00"
        + lease.run_id.encode("utf-8") + b"\x00" + canonical
    )
    return "mcp-" + hashlib.sha256(content).hexdigest()


def _unique_object_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PodMcpContractDenied("duplicate JSON-RPC field")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise PodMcpContractDenied("non-finite MCP number denied")


def decode_mcp_frame(raw: bytes) -> dict[str, object]:
    """Decode one bounded JSON-RPC frame; reject ambiguous duplicate fields.

    Only an authenticated per-child transport may call the handler after
    this decoder. A well-formed frame is not an authentication credential.
    """
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_MESSAGE:
        raise PodMcpContractDenied("MCP frame unavailable or oversized")
    try:
        obj: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object_fields,
            parse_constant=_invalid_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise PodMcpContractDenied("malformed MCP frame") from None
    if not isinstance(obj, dict) or any(not isinstance(k, str) for k in obj):
        raise PodMcpContractDenied("MCP envelope must be a JSON object")
    return cast(dict[str, object], obj)


class TrustedPodMcpHandler:
    """Minimal MCP tools/list + tools/call over one pre-admitted Gateway pod.

    Only operator-registered, fixed-argv tool descriptors are eligible. The
    model may select an allowed *name* and bounded JSON-RPC id, never the
    executable, arguments, cwd, egress, receipt key, or sandbox backend.
    """

    __slots__ = ("_lease", "_broker", "_tool_map")

    def __init__(
        self,
        lease: PodLease,
        broker: SandboxedPodToolBroker,
        *,
        exposed_tools: tuple[str, ...],
    ) -> None:
        if not isinstance(lease, PodLease):
            raise PodMcpContractDenied("trusted child lease required")
        if not isinstance(broker, SandboxedPodToolBroker):
            raise PodMcpContractDenied("administrator tool broker required")
        if (
            not isinstance(exposed_tools, tuple)
            or not exposed_tools
            or len(exposed_tools) > 32
            or len(set(exposed_tools)) != len(exposed_tools)
            or any(not isinstance(name, str) or _EXPORT.fullmatch(name) is None
                   for name in exposed_tools)
        ):
            raise PodMcpContractDenied("bounded explicit MCP tool grant required")
        reservation = lease._ledger.require_active(lease.reservation_id)
        receipt_authority = broker._executor._receipts
        supervisor = receipt_authority._supervisor
        if (
            receipt_authority.reservations.path != lease._ledger.path
            or supervisor is None
            or supervisor.caller.run_id != reservation.parent_run_id
            or supervisor.gateway is not lease._session.gateway
        ):
            raise PodMcpContractDenied("foreign MCP receipt supervisor or pod")
        allowed = lease._session.capabilities.allowed_tools
        # Existing broker controls the complete exact administrator catalog.
        registry = broker._tools
        mapped: dict[str, str] = {}
        for external in exposed_tools:
            native = external[len("locus_"):]
            if native not in registry or native not in allowed:
                raise PodMcpContractDenied("MCP grant exceeds admitted broker catalog")
            mapped[external] = native
        # A read-only snapshot prevents model-controlled catalog mutation.
        self._lease = lease
        self._broker = broker
        self._tool_map = MappingProxyType(dict(sorted(mapped.items())))

    def _active(self) -> None:
        self._lease._ledger.require_active(self._lease.reservation_id)
        if self._lease._session.gateway._authenticate(self._lease._session.caller) is None:
            raise PodMcpContractDenied("Gateway child identity no longer active")

    @staticmethod
    def _response(request_id: object, payload: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, **payload}

    @classmethod
    def _error(
        cls, request_id: object, *, code: int, message: str,
    ) -> dict[str, Any]:
        return cls._response(request_id, {
            "error": {"code": code, "message": message},
        })

    def handle_frame(self, raw: bytes) -> dict[str, Any] | None:
        """Strict frame parser for a separately authenticated supervisor IPC."""
        try:
            parsed = decode_mcp_frame(raw)
        except PodMcpContractDenied:
            return self._error(None, code=-32700, message="Invalid JSON-RPC frame")
        return self.handle(parsed)

    def handle(self, message: object) -> dict[str, Any] | None:
        """Consume a single already transport-authenticated JSON-RPC envelope."""
        if not isinstance(message, dict):
            return self._error(None, code=-32600, message="Invalid request")
        # Validate the entire message BEFORE constructing any tool/grant
        # response. A future wire adapter must also cap raw bytes pre-parse.
        try:
            encoded = json.dumps(
                message, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii")
        except (TypeError, ValueError, OverflowError, RecursionError):
            return self._error(None, code=-32600, message="Invalid request")
        if len(encoded) > _MAX_MESSAGE or message.get("jsonrpc") != "2.0":
            return self._error(None, code=-32600, message="Invalid request")
        method = message.get("method")
        if not isinstance(method, str) or len(method) > 128:
            return self._error(None, code=-32600, message="Invalid request")
        if method == "notifications/initialized" and "id" not in message:
            return None
        request_id = message.get("id")
        try:
            _bounded_request_id(request_id)
        except PodMcpContractDenied:
            return self._error(None, code=-32600, message="Invalid request")
        if set(message) - {"jsonrpc", "id", "method", "params"}:
            return self._error(request_id, code=-32600, message="Invalid request")
        try:
            self._active()
        except AdmissionDenied:
            return self._error(request_id, code=-32001, message="Pod lease unavailable")
        params = message.get("params", {})
        if not isinstance(params, dict):
            return self._error(request_id, code=-32602, message="Invalid params")
        if method == "initialize":
            if set(params) - {"protocolVersion", "capabilities", "clientInfo"}:
                return self._error(request_id, code=-32602, message="Invalid params")
            if params.get("protocolVersion") not in (None, "2024-11-05", _VERSION):
                return self._error(request_id, code=-32602, message="Unsupported MCP protocol")
            return self._response(request_id, {
                "result": {
                    "protocolVersion": _VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "locus-pod-gateway", "version": "1.0.0"},
                },
            })
        if method == "ping":
            if params:
                return self._error(request_id, code=-32602, message="Invalid params")
            return self._response(request_id, {"result": {}})
        if method == "tools/list":
            if params:
                return self._error(request_id, code=-32602, message="Invalid params")
            return self._response(request_id, {
                "result": {"tools": [
                    {
                        "name": name,
                        "description": "Administrator-registered, Gateway-fenced Locus action",
                        "inputSchema": {
                            "type": "object", "properties": {},
                            "additionalProperties": False,
                        },
                    }
                    for name in self._tool_map
                ]},
            })
        if method != "tools/call":
            return self._error(request_id, code=-32601, message="Method not found")
        name = params.get("name")
        # Never accept an LLM-supplied argv, root, timeout, environment,
        # action key or even a free-form tool payload in this first increment.
        if set(params) != {"name", "arguments"} or params.get("arguments") != {}:
            return self._error(request_id, code=-32602, message="Invalid params")
        if not isinstance(name, str) or name not in self._tool_map:
            return self._error(request_id, code=-32602, message="Unknown tool")
        try:
            result = self._broker.execute_status(
                self._lease,
                tool_name=self._tool_map[name],
                action_idempotency_key=_action_key(self._lease, request_id),
            )
        except (ActionReplayDenied, AdmissionDenied, ToolResultRejected):
            # A rejected action might have dispatched. Never suggest retrying
            # with a fresh key to regain a potentially effectful side effect.
            return self._error(request_id, code=-32002, message="MCP action denied or already dispatched")
        except Exception:
            # Hide all process output, paths, usernames, provider errors and
            # raw exception text; ambiguous state remains in durable receipts.
            return self._error(request_id, code=-32003, message="MCP action requires reconciliation")
        status = result.status
        safe = json.dumps({
            "schema": status.schema,
            "ok": status.ok,
            "code": status.code,
            "digest": status.digest,
        }, separators=(",", ":"), sort_keys=True)
        return self._response(request_id, {
            "result": {
                "content": [{"type": "text", "text": safe}],
                "isError": not status.ok,
            },
        })
