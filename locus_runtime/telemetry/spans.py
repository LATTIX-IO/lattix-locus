"""The span helper API: small call sites, GenAI semantic conventions, no content by default.

Every helper is safe to call when telemetry is not configured (no-op spans)
and never raises into the caller: a telemetry failure must not change what an
agent does. Exceptions raised by the instrumented code are recorded
(``error.type``, status ERROR) and re-raised unchanged; ``BaseException``
control flow (the verified loop's end states) passes through without being
marked as an error.

Context propagation keeps one run in one trace: the run span is the parent of
every model, tool, gateway and sandbox span started while it is current, and
the run id rides along in a context variable so each span carries
``locus.run.id``. :func:`capture_context` / :func:`resume_context` re-enter a
run's context from a framework thread that lost it.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.content import ATTRIBUTE_MAX_CHARS, redact, serialize, truncate
from locus_runtime.telemetry.setup import content_capture_enabled, content_limit, get_tracer

logger = logging.getLogger(__name__)

_RUN_ID: ContextVar[str] = ContextVar("locus_telemetry_run_id", default="")
_TOOL: ContextVar[_ToolAggregate | None] = ContextVar("locus_telemetry_tool", default=None)
_FALLBACK: ContextVar[tuple[int, str]] = ContextVar("locus_telemetry_fallback", default=(0, ""))
_REASON_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:/-"
)
_MAX_REASONS = 16


def _attr_value(value: Any) -> Any:
    """An OTel-compatible attribute value (bounded); ``None`` to skip."""
    if value is None:
        return None
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return truncate(value, ATTRIBUTE_MAX_CHARS)[0] if value else None
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        items = [str(v)[:ATTRIBUTE_MAX_CHARS] for v in list(value)[:64]]
        return tuple(items)
    return truncate(str(value), ATTRIBUTE_MAX_CHARS)[0]


def safe_reasons(reasons: Sequence[Any]) -> tuple[str, ...]:
    """Policy reason codes only: anything that is not a short code is withheld."""
    out: list[str] = []
    for item in list(reasons)[:_MAX_REASONS]:
        text = str(item or "")
        if 0 < len(text) <= 120 and all(ch in _REASON_SAFE_CHARS for ch in text):
            out.append(text)
        elif text:
            out.append("[withheld]")
    return tuple(out)


class SpanHandle:
    """A thin, never-raising wrapper over one span."""

    __slots__ = ("_span",)

    def __init__(self, span: Span) -> None:
        self._span = span

    @property
    def span(self) -> Span:
        return self._span

    @property
    def recording(self) -> bool:
        return bool(self._span.is_recording())

    def set(self, key: str, value: Any) -> None:
        if not self.recording:
            return
        try:
            coerced = _attr_value(value)
            if coerced is not None:
                self._span.set_attribute(key, coerced)
        except Exception:  # noqa: BLE001 - telemetry never fails the caller
            logger.debug("telemetry.set_attribute_error", exc_info=True)

    def set_many(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value)

    def content(self, key: str, value: Any) -> None:
        """Message / tool content: only with content capture on; redacted, truncated."""
        if not self.recording or not content_capture_enabled() or value is None:
            return
        try:
            limit = content_limit()
            text = serialize(value)
            redacted = redact(text, limit=limit, deep=True)
            self._span.set_attribute(key, redacted)
            if len(text) > limit:
                self._span.set_attribute(sc.LOCUS_CONTENT_TRUNCATED, True)
        except Exception:  # noqa: BLE001
            logger.debug("telemetry.content_error", exc_info=True)

    def event(self, name: str, attributes: Mapping[str, Any] | None = None) -> None:
        if not self.recording:
            return
        try:
            attrs = {k: _attr_value(v) for k, v in (attributes or {}).items()}
            self._span.add_event(name, {k: v for k, v in attrs.items() if v is not None})
        except Exception:  # noqa: BLE001
            logger.debug("telemetry.event_error", exc_info=True)

    def error(self, error: BaseException | str, message: str = "") -> None:
        """Record a failure: ``error.type`` and a redacted, bounded status message."""
        if not self.recording:
            return
        try:
            if isinstance(error, BaseException):
                kind = str(getattr(error, "code", "") or type(error).__name__)
                detail = message or str(error)
            else:
                kind, detail = str(error), message
            self._span.set_attribute(sc.ERROR_TYPE, truncate(kind, 128)[0])
            self._span.set_status(Status(StatusCode.ERROR, redact(detail, limit=300)))
        except Exception:  # noqa: BLE001
            logger.debug("telemetry.error_status_error", exc_info=True)

    def end(self) -> None:
        try:
            self._span.end()
        except Exception:  # noqa: BLE001
            logger.debug("telemetry.end_error", exc_info=True)

    @contextmanager
    def activate(self) -> Iterator[SpanHandle]:
        """Make this span current for the block (does not end it)."""
        token = _attach(trace.set_span_in_context(self._span))
        try:
            yield self
        finally:
            _detach(token)


def _attach(ctx: otel_context.Context) -> Token[otel_context.Context] | None:
    try:
        return otel_context.attach(ctx)
    except Exception:  # noqa: BLE001
        return None


def _detach(token: Token[otel_context.Context] | None) -> None:
    if token is None:
        return
    try:
        otel_context.detach(token)
    except Exception:  # noqa: BLE001 - a foreign context: nothing to restore
        logger.debug("telemetry.detach_error", exc_info=True)


def current_run_id() -> str:
    return _RUN_ID.get()


def current() -> SpanHandle:
    return SpanHandle(trace.get_current_span())


def _start(
    name: str,
    *,
    operation: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> SpanHandle:
    try:
        span = get_tracer().start_span(name, kind=kind)
    except Exception:  # noqa: BLE001
        span = trace.INVALID_SPAN
    handle = SpanHandle(span)
    if handle.recording:
        handle.set(sc.GEN_AI_OPERATION_NAME, operation)
        handle.set(sc.LOCUS_RUN_ID, run_id if run_id is not None else _RUN_ID.get())
        handle.set_many(attributes or {})
    return handle


@contextmanager
def _active(handle: SpanHandle, *, run_id: str | None = None) -> Iterator[SpanHandle]:
    token = _attach(trace.set_span_in_context(handle.span))
    run_token = _RUN_ID.set(run_id) if run_id else None
    try:
        yield handle
    except Exception as exc:
        handle.error(exc)
        raise
    finally:
        if run_token is not None:
            _RUN_ID.reset(run_token)
        _detach(token)
        handle.end()


@contextmanager
def span(
    name: str,
    *,
    operation: str = "",
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> Iterator[SpanHandle]:
    """A generic current span (loop tick, gates)."""
    handle = _start(name, operation=operation, kind=kind, attributes=attributes, run_id=run_id)
    with _active(handle, run_id=run_id) as active:
        yield active


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
@contextmanager
def agent_run(
    *,
    run_id: str,
    agent: str,
    runtime: str,
    provider: str = "",
    model: str = "",
) -> Iterator[SpanHandle]:
    """``invoke_agent {agent}``: the root of one run's spans."""
    handle = _start(
        f"{sc.OP_INVOKE_AGENT} {agent}",
        operation=sc.OP_INVOKE_AGENT,
        attributes={
            sc.GEN_AI_AGENT_NAME: agent,
            sc.GEN_AI_AGENT_ID: agent,
            sc.GEN_AI_CONVERSATION_ID: run_id,
            sc.LOCUS_RUNTIME: runtime,
            sc.GEN_AI_PROVIDER_NAME: provider,
            sc.GEN_AI_SYSTEM: provider,
            sc.GEN_AI_REQUEST_MODEL: model,
        },
        run_id=run_id,
    )
    with _active(handle, run_id=run_id) as active:
        yield active


def record_run_result(handle: SpanHandle, result: Any) -> None:
    """End state, verification and budget usage of a finished run (no content)."""
    try:
        usage = getattr(result, "usage", None)
        end_state = getattr(result, "end_state", None)
        state = str(getattr(end_state, "value", end_state) or "")
        verification = list(getattr(result, "verification", None) or [])
        verified = bool(state == "done" and verification and verification[-1].get("passed"))
        handle.set_many(
            {
                sc.LOCUS_END_STATE: state,
                sc.LOCUS_VERIFIED: verified,
                sc.LOCUS_STEPS: getattr(usage, "steps", None),
                sc.LOCUS_ACTIONS: getattr(usage, "actions", None),
                sc.GEN_AI_USAGE_INPUT_TOKENS: getattr(usage, "prompt_tokens", None),
                sc.GEN_AI_USAGE_OUTPUT_TOKENS: getattr(usage, "completion_tokens", None),
                sc.LOCUS_COST_USD: getattr(usage, "cost_usd", None),
            }
        )
        blocker = getattr(result, "blocker", None)
        stop = getattr(result, "stop", None)
        if state == "blocked" and blocker is not None:
            handle.error(f"blocked:{getattr(blocker, 'kind', '')}", "run ended blocked")
        elif state == "stopped" and stop is not None:
            handle.set("locus.run.stop_kind", getattr(stop, "kind", ""))
    except Exception:  # noqa: BLE001
        logger.debug("telemetry.run_result_error", exc_info=True)


@dataclass(frozen=True)
class RunContext:
    """A run's trace context, to re-enter from another thread."""

    context: otel_context.Context
    run_id: str


def capture_context() -> RunContext:
    return RunContext(otel_context.get_current(), _RUN_ID.get())


@contextmanager
def resume_context(saved: RunContext | None) -> Iterator[None]:
    """Re-enter ``saved`` when the current context is not inside that run's trace."""
    if saved is None:
        yield
        return
    want = trace.get_current_span(saved.context).get_span_context()
    have = trace.get_current_span().get_span_context()
    if not want.is_valid or (have.is_valid and have.trace_id == want.trace_id):
        yield
        return
    token = _attach(saved.context)
    run_token = _RUN_ID.set(saved.run_id) if saved.run_id else None
    try:
        yield
    finally:
        if run_token is not None:
            _RUN_ID.reset(run_token)
        _detach(token)


# --------------------------------------------------------------------------- #
# Model calls
# --------------------------------------------------------------------------- #
def start_chat(
    *,
    provider: str,
    model: str,
    server_address: str = "",
    stream: bool = False,
    request: Mapping[str, Any] | None = None,
) -> SpanHandle:
    """``chat {model}`` (CLIENT). Not current; use :meth:`SpanHandle.activate`."""
    req = request or {}
    hop, hop_from = _FALLBACK.get()
    handle = _start(
        f"{sc.OP_CHAT} {model}",
        operation=sc.OP_CHAT,
        kind=SpanKind.CLIENT,
        attributes={
            sc.GEN_AI_PROVIDER_NAME: provider,
            sc.GEN_AI_SYSTEM: provider,
            sc.GEN_AI_REQUEST_MODEL: model,
            sc.SERVER_ADDRESS: server_address,
            sc.LOCUS_STREAM: stream,
            sc.GEN_AI_REQUEST_TEMPERATURE: req.get("temperature"),
            sc.GEN_AI_REQUEST_TOP_P: req.get("top_p"),
            sc.GEN_AI_REQUEST_MAX_TOKENS: req.get("max_tokens"),
            sc.LOCUS_FALLBACK_HOP: hop or None,
            sc.LOCUS_FALLBACK_FROM: hop_from,
        },
    )
    if handle.recording and req.get("messages") is not None:
        handle.content(sc.GEN_AI_INPUT_MESSAGES, req.get("messages"))
    return handle


def record_usage(handle: SpanHandle, usage: Any) -> None:
    """Tokens, cost and latency facts from a ``model_client.ModelUsage``."""
    handle.set_many(
        {
            sc.GEN_AI_USAGE_INPUT_TOKENS: getattr(usage, "tokens_in", None),
            sc.GEN_AI_USAGE_OUTPUT_TOKENS: getattr(usage, "tokens_out", None),
            sc.LOCUS_COST_USD: getattr(usage, "est_cost_usd", None),
            sc.LOCUS_COST_KNOWN: getattr(usage, "cost_known", None),
            sc.LOCUS_USAGE_REPORTED: getattr(usage, "usage_reported", None),
            sc.LOCUS_GATEWAY_AUDIT_ID: getattr(usage, "audit_id", None),
        }
    )


@contextmanager
def fallback_hop(hop: int, from_tier: str) -> Iterator[None]:
    """Model calls in the block are fallback hop ``hop`` (0 = primary tier)."""
    token = _FALLBACK.set((int(hop), from_tier if hop else ""))
    try:
        yield
    finally:
        _FALLBACK.reset(token)


def record_fallback(*, from_tier: str, to_tier: str, reason_code: str) -> None:
    """A tier change (P16) as an event on the current span (no reason text)."""
    current().event(
        sc.LOCUS_FALLBACK_EVENT,
        {
            sc.LOCUS_FALLBACK_FROM: from_tier,
            sc.LOCUS_FALLBACK_TO: to_tier,
            sc.LOCUS_FALLBACK_REASON: reason_code,
        },
    )


# --------------------------------------------------------------------------- #
# Tools and gateway decisions
# --------------------------------------------------------------------------- #
@dataclass
class _ToolAggregate:
    decisions: int = 0
    outcome: str = ""
    risk: int = -1
    reasons: list[str] = field(default_factory=list)
    failed: bool = False

    def add(self, outcome: str, risk: int, reasons: Sequence[str]) -> None:
        self.decisions += 1
        order = sc.GATEWAY_OUTCOME_ORDER
        if outcome in order and (
            self.outcome not in order or order.index(outcome) > order.index(self.outcome)
        ):
            self.outcome = outcome
        self.risk = max(self.risk, risk)
        for reason in reasons:
            if reason not in self.reasons and len(self.reasons) < _MAX_REASONS:
                self.reasons.append(reason)


@contextmanager
def tool_call(name: str, *, call_id: str = "", arguments: Any = None) -> Iterator[SpanHandle]:
    """``execute_tool {name}``: gateway outcome / risk of the actions it caused."""
    handle = _start(
        f"{sc.OP_EXECUTE_TOOL} {name}",
        operation=sc.OP_EXECUTE_TOOL,
        attributes={
            sc.GEN_AI_TOOL_NAME: name,
            sc.GEN_AI_TOOL_CALL_ID: call_id,
            sc.GEN_AI_TOOL_TYPE: "function",
        },
    )
    if arguments is not None:
        handle.content(sc.GEN_AI_TOOL_CALL_ARGUMENTS, arguments)
    aggregate = _ToolAggregate()
    token = _TOOL.set(aggregate)
    try:
        with _active(handle) as active:
            try:
                yield active
            except Exception:
                aggregate.failed = True
                raise
            finally:
                _finish_tool(active, aggregate)
    finally:
        _TOOL.reset(token)


def _finish_tool(handle: SpanHandle, aggregate: _ToolAggregate) -> None:
    if aggregate.decisions:
        handle.set_many(
            {
                sc.LOCUS_GATEWAY_DECISIONS: aggregate.decisions,
                sc.LOCUS_GATEWAY_OUTCOME: aggregate.outcome,
                sc.LOCUS_GATEWAY_RISK: f"R{aggregate.risk}" if aggregate.risk >= 0 else None,
                sc.LOCUS_GATEWAY_REASONS: aggregate.reasons,
            }
        )
    if aggregate.outcome in {"ask", "deny"}:
        status = "blocked"
    elif aggregate.failed:
        status = "error"
    else:
        status = "ok"
    handle.set(sc.LOCUS_TOOL_STATUS, status)


def record_tool_result(handle: SpanHandle, result: Any, *, failed: bool = False) -> None:
    """The tool's observation (content capture only) and whether it failed."""
    handle.content(sc.GEN_AI_TOOL_CALL_RESULT, result)
    if failed:
        aggregate = _TOOL.get()
        if aggregate is not None:
            aggregate.failed = True
        handle.set(sc.ERROR_TYPE, "tool_error")


def tool_refused(name: str) -> None:
    current().event(sc.LOCUS_TOOL_REFUSED_EVENT, {sc.GEN_AI_TOOL_NAME: name})


@contextmanager
def gateway_decision(action_kind: str, tool: str) -> Iterator[SpanHandle]:
    """``gateway {action_kind}``: one policy decision (no target, args or command)."""
    handle = _start(
        f"gateway {action_kind}",
        operation=sc.OP_GATEWAY,
        attributes={sc.LOCUS_GATEWAY_ACTION: action_kind, sc.LOCUS_GATEWAY_TOOL: tool},
    )
    with _active(handle) as active:
        yield active


def record_decision(handle: SpanHandle, decision: Any) -> None:
    """Outcome, risk class, reason codes, policy version and audit id of a decision."""
    try:
        outcome = str(getattr(decision, "outcome", "") or "")
        risk_obj = getattr(decision, "risk", None)
        risk = int(risk_obj) if risk_obj is not None else -1
        reasons = safe_reasons(list(getattr(decision, "reasons", ()) or ()))
        handle.set_many(
            {
                sc.LOCUS_GATEWAY_OUTCOME: outcome,
                sc.LOCUS_GATEWAY_RISK: f"R{risk}" if risk >= 0 else None,
                sc.LOCUS_GATEWAY_REASONS: reasons,
                sc.LOCUS_GATEWAY_POLICY_VERSION: getattr(decision, "policy_version", None),
                sc.LOCUS_GATEWAY_AUDIT_ID: getattr(decision, "audit_id", None),
            }
        )
        if outcome == "deny":
            handle.set(sc.ERROR_TYPE, "gateway_deny")
        aggregate = _TOOL.get()
        if aggregate is not None and getattr(decision, "action_kind", "") != "model_call":
            aggregate.add(outcome, risk, reasons)
    except Exception:  # noqa: BLE001
        logger.debug("telemetry.decision_error", exc_info=True)


# --------------------------------------------------------------------------- #
# Sandbox
# --------------------------------------------------------------------------- #
@contextmanager
def sandbox_exec(
    *, backend: str, tier: str, network: bool | None, command: Sequence[str]
) -> Iterator[SpanHandle]:
    """``sandbox exec``: jail tier, exit code, duration (never the command text)."""
    executable = os.path.basename(str(command[0])) if command else ""
    handle = _start(
        f"sandbox exec {executable}".strip(),
        operation=sc.OP_SANDBOX_EXEC,
        attributes={
            sc.LOCUS_SANDBOX_BACKEND: backend,
            sc.LOCUS_SANDBOX_TIER: tier,
            sc.LOCUS_SANDBOX_NETWORK: network,
            sc.LOCUS_SANDBOX_EXECUTABLE: executable[:64],
        },
    )
    with _active(handle) as active:
        yield active


def record_exec_result(handle: SpanHandle, result: Any) -> None:
    exit_code = getattr(result, "exit_code", None)
    handle.set_many(
        {
            sc.LOCUS_SANDBOX_EXIT_CODE: exit_code,
            sc.LOCUS_SANDBOX_TIMED_OUT: getattr(result, "timed_out", None),
            sc.LOCUS_SANDBOX_DURATION_MS: round(
                float(getattr(result, "duration_seconds", 0.0) or 0.0) * 1000, 3
            ),
            sc.LOCUS_SANDBOX_BACKEND: getattr(result, "backend", None) or None,
        }
    )
    if getattr(result, "timed_out", False):
        handle.error("timeout", "sandboxed command timed out")


# --------------------------------------------------------------------------- #
# Loop runner, gates and scores
# --------------------------------------------------------------------------- #
@contextmanager
def loop_tick() -> Iterator[SpanHandle]:
    with span("locus.loop.tick", operation=sc.OP_LOOP_TICK) as handle:
        yield handle


@contextmanager
def gate(kind: str, *, run_id: str = "") -> Iterator[SpanHandle]:
    """``gate {kind}`` (quality / eval / verify) inside the run's trace."""
    with span(
        f"gate {kind}", operation=sc.OP_GATE, attributes={sc.LOCUS_GATE_KIND: kind}, run_id=run_id
    ) as handle:
        yield handle


def record_score(
    name: str,
    value: float | None,
    *,
    label: str = "",
    comment: str = "",
    source: str = "",
    run_id: str | None = None,
) -> None:
    """A result on the run trace (``gen_ai.evaluation.result`` event).

    Recorded on the current span; when there is none, on a short
    ``evaluation {name}`` span so the score is never lost.
    """
    rid = run_id if run_id is not None else _RUN_ID.get()
    attributes = {
        sc.GEN_AI_EVALUATION_NAME: name,
        sc.GEN_AI_EVALUATION_SCORE_VALUE: None if value is None else float(value),
        sc.GEN_AI_EVALUATION_SCORE_LABEL: label,
        sc.GEN_AI_EVALUATION_EXPLANATION: redact(comment, limit=300) if comment else None,
        sc.LOCUS_SCORE_SOURCE: source,
        sc.LOCUS_RUN_ID: rid,
    }
    target = current()
    if target.recording:
        target.event(sc.GEN_AI_EVALUATION_EVENT, attributes)
        return
    with span(f"evaluation {name}", operation=sc.OP_EVALUATION, run_id=rid or None) as handle:
        handle.event(sc.GEN_AI_EVALUATION_EVENT, attributes)


def now_ns() -> int:
    return time.time_ns()
