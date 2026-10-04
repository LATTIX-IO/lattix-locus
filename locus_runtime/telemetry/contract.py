"""The ``TelemetrySink`` port (D-28; observability module, LOCUS-375).

Locus traces every run with OpenTelemetry (GenAI semantic conventions, see
:mod:`.semconv`). The instrumentation side is one small API
(:mod:`locus_runtime.telemetry`); where spans go is this port:

* :class:`TelemetrySink` -- receives finished spans as :class:`SpanRecord`
  batches (already redacted). The always-on implementation is the local
  SQLite store (:class:`~locus_runtime.telemetry.sqlite_store.SqliteTelemetryStore`).
* :class:`TelemetryReader` -- the read side behind ``GET /telemetry/*``: runs,
  one run's span tree, and a summary over a window.

Opt-in external exporters (OTLP/HTTP, e.g. a self-hosted Langfuse, and the
LangSmith preset) are OpenTelemetry ``SpanExporter`` s configured from
:class:`TelemetrySettings`; they are off by default and their egress is
checked against the platform egress allowlist (fail closed).

Contract every sink keeps (``tests/unit/test_telemetry.py``):

1. ``export`` never raises and never blocks the agent (it runs on the batch
   processor's worker thread).
2. Records carry no message or tool content unless content capture is on; when
   it is, content is already redacted and truncated (P10). A sink never adds
   content back.
3. Audit stays in the audit log; telemetry is diagnostic and prunable.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

PORT_VERSION = "1.0"

#: Where an exporter sends spans. ``local``: the SQLite file in the app home;
#: ``loopback``: an OTLP collector on this machine; ``remote``: an OTLP endpoint
#: on another host (data leaves the machine); ``hosted_proprietary``: LangSmith.
DestinationClass = Literal["local", "loopback", "remote", "hosted_proprietary"]
ExporterKind = Literal["sqlite", "otlp", "langsmith"]
ExporterState = Literal["active", "off", "blocked"]

LANGSMITH_OTLP_ENDPOINT = "https://api.smith.langchain.com/otel/v1/traces"
LANGSMITH_LABEL = "hosted, proprietary; data leaves the machine"

DEFAULT_PAYLOAD_RETENTION_DAYS = 90
DEFAULT_SPAN_RETENTION_DAYS = 365
DEFAULT_CONTENT_MAX_CHARS = 4000
DEFAULT_QUEUE_SIZE = 2048
_MAX_NS = 2**63 - 1  # SQLite INTEGER


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class ExporterSettings(BaseModel):
    """One opt-in external exporter. Secrets are referenced by name, never held."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    endpoint: str = Field(default="", max_length=2048)
    #: Name of a native secret (``lattix secrets set NAME``) holding the auth value.
    auth_secret_ref: str = Field(default="", max_length=64)
    #: LangSmith project (sent as the ``Langsmith-Project`` header).
    project: str = Field(default="", max_length=128)


class TelemetrySettings(BaseModel):
    """What the principal configures. Defaults are the privacy defaults (P10, P14)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    local_enabled: bool = True
    #: SQLite file; the composition root resolves it under the app home.
    db_path: str = ""
    #: Message and tool content in spans. Off by default; redacted and truncated when on.
    capture_content: bool = False
    content_max_chars: int = Field(default=DEFAULT_CONTENT_MAX_CHARS, ge=64, le=100_000)
    payload_retention_days: int = Field(default=DEFAULT_PAYLOAD_RETENTION_DAYS, ge=1, le=3650)
    span_retention_days: int = Field(default=DEFAULT_SPAN_RETENTION_DAYS, ge=1, le=3650)
    queue_size: int = Field(default=DEFAULT_QUEUE_SIZE, ge=16, le=65_536)
    otlp: ExporterSettings = Field(default_factory=ExporterSettings)
    langsmith: ExporterSettings = Field(
        default_factory=lambda: ExporterSettings(
            endpoint=LANGSMITH_OTLP_ENDPOINT, auth_secret_ref="LANGSMITH_API_KEY"
        )
    )


# --------------------------------------------------------------------------- #
# Records (what a sink receives and the reader returns)
# --------------------------------------------------------------------------- #
class SpanEventRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    time_ns: int
    attributes: dict[str, Any] = Field(default_factory=dict)
    #: Content attributes (only with content capture on); pruned after retention.
    payload: dict[str, Any] | None = None


class SpanRecord(BaseModel):
    """One finished span, redacted, with GenAI facts lifted into columns."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: str = "INTERNAL"
    operation: str = ""
    run_id: str = ""
    status: Literal["UNSET", "OK", "ERROR"] = "UNSET"
    status_message: str = ""
    start_ns: int
    end_ns: int
    provider: str = ""
    model: str = ""
    tool: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error_type: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] | None = None
    events: list[SpanEventRecord] = Field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.end_ns - self.start_ns) / 1e6)


class ScoreRecord(BaseModel):
    """A gate / eval / verification result on a run trace."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    run_id: str = ""
    name: str
    value: float | None = None
    label: str = ""
    source: str = ""
    comment: str = ""
    time_ns: int


class RunSummary(BaseModel):
    run_id: str
    trace_id: str
    name: str
    agent: str = ""
    runtime: str = ""
    provider: str = ""
    model: str = ""
    status: str = "UNSET"
    end_state: str = ""
    verified: bool | None = None
    start_ns: int
    end_ns: int
    duration_ms: float
    model_calls: int = 0
    tool_calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    scores: list[ScoreRecord] = Field(default_factory=list)


class RunPage(BaseModel):
    runs: list[RunSummary]
    total: int
    limit: int
    offset: int


class RunQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    since_ns: int | None = Field(default=None, ge=0, le=_MAX_NS)
    until_ns: int | None = Field(default=None, ge=0, le=_MAX_NS)
    end_state: str = Field(default="", max_length=32)
    runtime: str = Field(default="", max_length=64)
    status: str = Field(default="", max_length=16)


class SpanNode(BaseModel):
    span: SpanRecord
    children: list[SpanNode] = Field(default_factory=list)


SpanNode.model_rebuild()


class TraceView(BaseModel):
    run_id: str
    trace_ids: list[str]
    roots: list[SpanNode]
    span_count: int
    scores: list[ScoreRecord] = Field(default_factory=list)


class Percentiles(BaseModel):
    count: int = 0
    p50: float | None = None
    p90: float | None = None
    p99: float | None = None


class TelemetrySummary(BaseModel):
    since_ns: int
    until_ns: int
    runs: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    run_latency_ms: Percentiles = Field(default_factory=Percentiles)
    model_latency_ms: Percentiles = Field(default_factory=Percentiles)
    tool_latency_ms: Percentiles = Field(default_factory=Percentiles)
    #: ``{operation: count}`` of spans with status ERROR.
    errors: dict[str, int] = Field(default_factory=dict)
    #: ``{score name: {label: count}}`` (gate and eval outcomes).
    gate_outcomes: dict[str, dict[str, int]] = Field(default_factory=dict)
    end_states: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Posture
# --------------------------------------------------------------------------- #
class ExporterPosture(BaseModel):
    kind: ExporterKind
    state: ExporterState
    destination: DestinationClass
    host: str = ""
    label: str = ""
    reason: str = ""


class TelemetryPosture(BaseModel):
    port_version: str = PORT_VERSION
    configured: bool = False
    local: ExporterPosture
    external: list[ExporterPosture] = Field(default_factory=list)
    capture_content: bool = False
    payload_retention_days: int = DEFAULT_PAYLOAD_RETENTION_DAYS
    semconv: str = ""


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
@runtime_checkable
class TelemetrySink(Protocol):
    """Where finished spans go (see the module docstring for the contract)."""

    name: str
    port_version: str
    destination: DestinationClass

    def export(self, spans: list[SpanRecord]) -> bool:
        """Persist ``spans``; ``False`` on failure. Never raises."""
        ...

    def shutdown(self) -> None: ...


@runtime_checkable
class TelemetryReader(Protocol):
    """The read side of the local store (``GET /telemetry/*``)."""

    def list_runs(self, query: RunQuery) -> RunPage: ...

    def trace(self, run_id: str) -> TraceView | None: ...

    def summary(self, since_ns: int, until_ns: int) -> TelemetrySummary: ...


__all__ = [
    "DEFAULT_CONTENT_MAX_CHARS",
    "DEFAULT_PAYLOAD_RETENTION_DAYS",
    "DEFAULT_QUEUE_SIZE",
    "DEFAULT_SPAN_RETENTION_DAYS",
    "LANGSMITH_LABEL",
    "LANGSMITH_OTLP_ENDPOINT",
    "PORT_VERSION",
    "DestinationClass",
    "ExporterKind",
    "ExporterPosture",
    "ExporterSettings",
    "ExporterState",
    "Percentiles",
    "RunPage",
    "RunQuery",
    "RunSummary",
    "ScoreRecord",
    "SpanEventRecord",
    "SpanNode",
    "SpanRecord",
    "TelemetryPosture",
    "TelemetryReader",
    "TelemetrySettings",
    "TelemetrySink",
    "TelemetrySummary",
    "TraceView",
]
