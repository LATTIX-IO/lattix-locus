"""LOCUS-470/B: supervisor-only, default-deny Codex -> local Ollama relay.

The untrusted Docker child has --network=none and no host mounts other than its
disposable stage. JSONL frames travel on Docker-attached stdin/stdout, never a
network listener or inherited credential. Only this trusted supervisor can
contact the loopback Ollama API and authorize each model call through the
original child Gateway identity and monotone PodReservationLedger budget.

The wire protocol carries raw model text in memory: NEVER log frames or
include them in receipts, telemetry, thrown errors, or model-visible failure
messages. Frames are not themselves an authority or a bearer token.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import http.client
import json
import re
import socket
import threading
from dataclasses import dataclass

from locus_runtime.model_client import (
    GatewayModelGate, ModelCall, provenance_denial,
)
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, ResourceBudget

_SCHEMA = "locus.codex.stdio.v1"
_MODEL = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,100}\Z", re.ASCII)
_PATHS = frozenset({"/v1/responses", "/v1/chat/completions"})
_MAX_FRAME = 2 * 1024 * 1024
_MAX_BODY = 1024 * 1024
_MAX_RESPONSE = 4 * 1024 * 1024
_MAX_CALLS = 16
_MAX_OUTPUT_TOKENS = 512


class CodexRelayDenied(AdmissionDenied):
    """Invalid child frame, Gateway denial, or unavailable trusted inference."""


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CodexRelayDenied("duplicate relay JSON field")
        result[key] = value
    return result


def _reject_nan(_: str) -> None:
    raise CodexRelayDenied("non-finite relay number")


def _frame(raw: bytes) -> dict[str, object]:
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_FRAME:
        raise CodexRelayDenied("relay frame size invalid")
    try:
        frame = json.loads(raw, object_pairs_hook=_unique_fields, parse_constant=_reject_nan)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise CodexRelayDenied("invalid relay frame") from exc
    if not isinstance(frame, dict):
        raise CodexRelayDenied("relay frame must be an object")
    return frame


def _body(encoded: object) -> bytes:
    if not isinstance(encoded, str) or not encoded or len(encoded) > (_MAX_BODY * 4 // 3 + 8):
        raise CodexRelayDenied("invalid encoded inference request")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CodexRelayDenied("invalid inference base64") from exc
    if not 0 < len(data) <= _MAX_BODY:
        raise CodexRelayDenied("inference request exceeds size limit")
    return data


def _request(raw: bytes, pinned_model: str) -> tuple[int, str, bytes, bool, int, int]:
    message = _frame(raw)
    if set(message) != {"schema", "kind", "id", "method", "path", "body"}:
        raise CodexRelayDenied("unknown relay fields")
    if message["schema"] != _SCHEMA or message["kind"] != "model_request":
        raise CodexRelayDenied("unsupported relay schema or operation")
    ident = message["id"]
    if type(ident) is not int or not 1 <= ident <= 2**31 - 1:
        raise CodexRelayDenied("invalid relay sequence")
    if message["method"] != "POST" or message["path"] not in _PATHS:
        raise CodexRelayDenied("unapproved inference endpoint")
    raw_body = _body(message["body"])
    request = _frame(raw_body)
    model = request.get("model")
    if model != pinned_model:
        raise CodexRelayDenied("unapproved inference model")
    # A real Codex CLI advertises built-in tools even with shell/unified_exec
    # disabled. Its declarations are *not* authorized capabilities. Actively
    # downscope the request before the model sees it: no exposed tool schema,
    # no forced tool_choice and no chance for a provider tool dispatch.
    tools = request.get("tools", [])
    if tools is None:
        tools = []
    if (
        not isinstance(tools, list) or len(tools) > 32
        or any(not isinstance(tool, dict) for tool in tools)
    ):
        raise CodexRelayDenied("invalid tool declarations")
    choice = request.get("tool_choice", "auto")
    if choice not in ("auto", "none", None):
        raise CodexRelayDenied("forced model tool call refused")
    suppressed = len(tools)
    request.pop("tools", None)
    request.pop("tool_choice", None)
    request.pop("parallel_tool_calls", None)
    stream = request.get("stream", False)
    if type(stream) is not bool:
        raise CodexRelayDenied("invalid streaming mode")
    requested = request.get("max_output_tokens", request.get("max_tokens", _MAX_OUTPUT_TOKENS))
    # Codex may request a generous logical ceiling (e.g. 8192 tokens).
    # Never forward that ceiling; clamp to our supervisor-enforced 512-token
    # cap. Preserve fail-closed behavior for invalid or absurdly large values.
    if type(requested) is not int or not 1 <= requested <= 65536:
        raise CodexRelayDenied("unbounded model completion request")
    requested = min(requested, _MAX_OUTPUT_TOKENS)
    # Bound *every* provider request, even when the Codex CLI omits a limit.
    # Never rely solely on a server's default output cap for budget admission.
    if "max_output_tokens" in request and "max_tokens" in request:
        raise CodexRelayDenied("ambiguous completion token ceilings")
    endpoint_limit = (
        "max_output_tokens" if message["path"] == "/v1/responses" else "max_tokens"
    )
    request.pop("max_output_tokens", None)
    request.pop("max_tokens", None)
    request[endpoint_limit] = requested
    if any(field in request for field in ("max_completion_tokens", "num_predict")):
        raise CodexRelayDenied("unsupported secondary output token ceiling")
    bounded_body = json.dumps(
        request, separators=(",", ":"), sort_keys=True, ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    if len(bounded_body) > _MAX_BODY:
        raise CodexRelayDenied("bounded model request too large")
    return ident, str(message["path"]), bounded_body, stream, requested, suppressed


def _reject_tool_responses(body: bytes, media: str) -> None:
    """Reject semantic tool invocation in bounded JSON/Responses SSE payloads.

    Stripping tool registration prevents legitimate calls. This second boundary
    also denies misbehaving local models that return a tool-call result without
    receiving a tool declaration. Nothing is executed or forwarded on denial.
    """
    def contains_tool(value: object) -> bool:
        if isinstance(value, dict):
            kind = value.get("type")
            if kind in {
                "function_call", "custom_tool_call", "tool_call",
                "response.function_call_arguments.delta",
                "response.function_call_arguments.done",
                "response.custom_tool_call_input.delta",
                "response.custom_tool_call_input.done",
            }:
                return True
            if value.get("tool_calls") or value.get("function_call"):
                return True
            return any(contains_tool(v) for v in value.values())
        if isinstance(value, list):
            return any(contains_tool(v) for v in value)
        return False

    try:
        if media == "application/json":
            if contains_tool(json.loads(
                body, object_pairs_hook=_unique_fields, parse_constant=_reject_nan,
            )):
                raise CodexRelayDenied("unmediated model tool response refused")
        elif media == "text/event-stream":
            # SSE event data can span multiple data lines and use CRLF.
            for block in body.replace(b"\r\n", b"\n").split(b"\n\n"):
                data = b"\n".join(
                    line[5:].lstrip() for line in block.split(b"\n")
                    if line.startswith(b"data:")
                ).strip()
                if not data or data == b"[DONE]":
                    continue
                item = json.loads(
                    data, object_pairs_hook=_unique_fields, parse_constant=_reject_nan,
                )
                if contains_tool(item):
                    raise CodexRelayDenied("unmediated model tool response refused")
        else:
            raise CodexRelayDenied("unsupported inference content type")
    except (UnicodeError, ValueError, RecursionError, TypeError) as exc:
        raise CodexRelayDenied("unparseable model response") from exc


def _reply(ident: int, status: int, body: bytes, content_type: str) -> bytes:
    if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE:
        raise CodexRelayDenied("bounded model reply required")
    return (json.dumps({
        "schema": _SCHEMA, "kind": "model_reply", "id": ident,
        "status": status, "content_type": content_type,
        "body": base64.b64encode(body).decode("ascii"),
        }, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n")


@dataclass(frozen=True, slots=True)
class CodexModelReceipt:
    sequence: int
    audit_sha256: str
    input_sha256: str
    response_sha256: str
    status: int
    reserved_tokens: int
    suppressed_tools: int


class CodexOllamaBridge:
    """One child/one exact model/one local endpoint, supervisor-owned.

    The worker never receives the child Gateway session or an Ollama token.
    The bridge charges a conservative full worst-case usage reservation before
    any network call. No timeout/error/refusal automatically refunds it.
    """

    def __init__(
        self, lease: PodLease, *, model: str,
        host: str = "127.0.0.1", port: int = 11434,
    ) -> None:
        if not isinstance(lease, PodLease):
            raise CodexRelayDenied("trusted child lease required")
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise CodexRelayDenied("canonical model pin required")
        if host != "127.0.0.1" or type(port) is not int or port != 11434:
            raise CodexRelayDenied("only fixed local Ollama endpoint is permitted")
        denial = provenance_denial("ollama", model, "http://127.0.0.1:11434/v1")
        if denial:
            raise CodexRelayDenied("model provenance is not authorized")
        self.lease = lease
        self.model = model
        self.host = host
        self.port = port
        self._last_id = 0
        self._receipts: list[CodexModelReceipt] = []
        self._lock = threading.Lock()
        # A cancellation must never wait on the model RPC serialization lock:
        # handle() can be blocked in a 50-second Ollama read while holding it.
        self._cancelled = threading.Event()
        self._connection_lock = threading.Lock()
        self._active_connection: http.client.HTTPConnection | None = None

    def abort(self) -> None:
        """Abort in-flight local inference on container cancel/revoke/timeout.

        Socket.shutdown interrupts a blocking recv on Linux; close alone is
        not always enough. This is idempotent, never waits on handle's lock,
        and also prohibits future requests on this child lease.
        """
        self._cancelled.set()
        with self._connection_lock:
            active = self._active_connection
        if active is not None:
            sock = active.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            active.close()

    @property
    def receipts(self) -> tuple[CodexModelReceipt, ...]:
        return tuple(self._receipts)

    def handle(self, raw: bytes) -> bytes:
        """One authenticated RPC; exception always kills/denies the task."""
        if self._cancelled.is_set():
            raise CodexRelayDenied("Codex inference lease cancelled")
        with self._lock:
            if self._cancelled.is_set():
                raise CodexRelayDenied("Codex inference lease cancelled")
            ident, path, body, stream, max_output, suppressed = _request(raw, self.model)
            if ident != self._last_id + 1 or ident > _MAX_CALLS:
                raise CodexRelayDenied("replayed, skipped, or exhausted model request")
            self._last_id = ident
            # Conservative reservation bounds even usage-less streamed Ollama
            # replies. UTF-8 token counts cannot exceed input byte count.
            reserve_tokens = len(body) + max_output
            active = self.lease._ledger.require_active(self.lease.reservation_id)
            used = active.used
            total = ResourceBudget(
                actions=used.actions + 1,
                tool_calls=used.tool_calls + 1,
                tokens=used.tokens + reserve_tokens,
                wall_seconds=used.wall_seconds,
                spend_microunits=used.spend_microunits,
            )
            # Concurrency/sibling budgeting is handled transactionally by the
            # durable ledger. If the reservation is inadequate, no egress.
            self.lease.settle(total)
            call = ModelCall(
                provider="ollama", model=self.model, egress_host="127.0.0.1",
                stream=stream, tools=0, run_id=self.lease.run_id,
            )
            audit_id = GatewayModelGate(session=self.lease._session).authorize(call)
            # The bridge is allowed to contact one fixed loopback service; no
            # client-controlled host, path, port, headers, redirects, or proxies.
            connection = http.client.HTTPConnection(self.host, self.port, timeout=50)
            with self._connection_lock:
                if self._cancelled.is_set():
                    raise CodexRelayDenied("Codex inference lease cancelled")
                self._active_connection = connection
            try:
                if self._cancelled.is_set():
                    raise CodexRelayDenied("Codex inference lease cancelled")
                connection.request("POST", path, body=body, headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if stream else "application/json",
                })
                if self._cancelled.is_set():
                    raise CodexRelayDenied("Codex inference lease cancelled")
                response = connection.getresponse()
                code = response.status
                media = response.getheader("Content-Type", "application/json").split(";", 1)[0].strip()
                if code != 200 or media not in {"application/json", "text/event-stream"}:
                    raise CodexRelayDenied("local model returned an unsupported response")
                # Avoid indefinite response streaming, redirects, oversized
                # chunks or unbounded output. The parent deadline still applies.
                size_hint = response.getheader("Content-Length")
                if size_hint is not None and (not size_hint.isdecimal() or int(size_hint) > _MAX_RESPONSE):
                    raise CodexRelayDenied("model response exceeds maximum size")
                output = response.read(_MAX_RESPONSE + 1)
                if len(output) > _MAX_RESPONSE:
                    raise CodexRelayDenied("model response exceeds maximum size")
                _reject_tool_responses(output, media)
                if self._cancelled.is_set():
                    raise CodexRelayDenied("Codex inference lease cancelled")
            except (OSError, http.client.HTTPException, TimeoutError) as exc:
                raise CodexRelayDenied("approved loopback model unavailable") from exc
            finally:
                with self._connection_lock:
                    if self._active_connection is connection:
                        self._active_connection = None
                connection.close()
            if self._cancelled.is_set():
                raise CodexRelayDenied("Codex inference lease cancelled")
            self._receipts.append(CodexModelReceipt(
                sequence=ident,
                audit_sha256=hashlib.sha256(audit_id.encode("utf-8")).hexdigest(),
                input_sha256=hashlib.sha256(body).hexdigest(),
                response_sha256=hashlib.sha256(output).hexdigest(),
                status=code, reserved_tokens=reserve_tokens,
                suppressed_tools=suppressed,
            ))
            return _reply(ident, code, output, media)


def relay_error(ident: int) -> bytes:
    """Generic model-visible error, never raw Gateway or provider details."""
    return _reply(ident, 503, b'{"error":{"message":"model call refused"}}', "application/json")
