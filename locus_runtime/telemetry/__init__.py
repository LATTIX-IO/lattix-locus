"""AI observability for Locus (LOCUS-375): OpenTelemetry traces + a local SQLite store.

The observability module of D-28 (port: :class:`~.contract.TelemetrySink`).
Call sites use the small helper API re-exported here; the composition root
(backend startup, the loop runner) calls :func:`configure` once.

* What is traced, privacy defaults and backends: ``docs/OBSERVABILITY.md``.
* Attribute names: :mod:`.semconv` (OTel GenAI conventions, pinned).
* Content capture is off by default; every exported string is redacted (P10).
"""

from __future__ import annotations

from locus_runtime.telemetry.contract import (
    PORT_VERSION,
    ExporterSettings,
    RunQuery,
    TelemetryPosture,
    TelemetryReader,
    TelemetrySettings,
    TelemetrySink,
)
from locus_runtime.telemetry.setup import (
    configure,
    content_capture_enabled,
    default_db_path,
    ensure_configured,
    force_flush,
    get_tracer,
    local_store,
    posture,
    reset,
    settings,
    widening_changes,
)
from locus_runtime.telemetry.spans import (
    RunContext,
    SpanHandle,
    agent_run,
    capture_context,
    current,
    current_run_id,
    fallback_hop,
    gate,
    gateway_decision,
    loop_tick,
    record_decision,
    record_exec_result,
    record_fallback,
    record_run_result,
    record_score,
    record_tool_result,
    record_usage,
    resume_context,
    sandbox_exec,
    span,
    start_chat,
    tool_call,
    tool_refused,
)

__all__ = [
    "PORT_VERSION",
    "ExporterSettings",
    "RunContext",
    "RunQuery",
    "SpanHandle",
    "TelemetryPosture",
    "TelemetryReader",
    "TelemetrySettings",
    "TelemetrySink",
    "agent_run",
    "capture_context",
    "configure",
    "content_capture_enabled",
    "current",
    "current_run_id",
    "default_db_path",
    "ensure_configured",
    "fallback_hop",
    "force_flush",
    "gate",
    "gateway_decision",
    "get_tracer",
    "local_store",
    "loop_tick",
    "posture",
    "record_decision",
    "record_exec_result",
    "record_fallback",
    "record_run_result",
    "record_score",
    "record_tool_result",
    "record_usage",
    "reset",
    "resume_context",
    "sandbox_exec",
    "settings",
    "span",
    "start_chat",
    "tool_call",
    "tool_refused",
    "widening_changes",
]
