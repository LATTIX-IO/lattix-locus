"""Tracer provider setup: the composition root calls :func:`configure` once (D-28).

Locus owns its ``TracerProvider`` and does not install it as the OpenTelemetry
global, so third-party libraries cannot write into Locus sinks and Locus spans
never go to an exporter a library configured. Until :func:`configure` runs,
:func:`get_tracer` returns a no-op tracer (nothing is recorded).

Processors (all batch, bounded queues, drop on overflow; the agent never waits):

* the local SQLite store (always on unless ``local_enabled`` is false);
* each enabled external exporter (OTLP/HTTP, LangSmith), only when its host is
  on the platform egress allowlist and its auth secret resolves.

Reconfiguring swaps the provider (the "live" swap class): new spans go to the
new provider at once; the old one is flushed and shut down in the background.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

from locus_runtime.telemetry import content
from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.contract import (
    DestinationClass,
    ExporterKind,
    ExporterPosture,
    ExporterSettings,
    TelemetryPosture,
    TelemetrySettings,
)
from locus_runtime.telemetry.exporters import (
    EgressCheck,
    RedactingSpanExporter,
    SecretResolver,
    SinkSpanExporter,
    build_external_exporter,
)
from locus_runtime.telemetry.sqlite_store import SqliteTelemetryStore

logger = logging.getLogger(__name__)

INSTRUMENTATION_NAME = "locus"
SERVICE_NAME = "lattix-locus"
#: Batch processor: schedule delay and batch size (the queue size is a setting).
_SCHEDULE_DELAY_MS = 500
_MAX_BATCH = 256


def _off(kind: ExporterKind, destination: DestinationClass, reason: str = "") -> ExporterPosture:
    return ExporterPosture(kind=kind, state="off", destination=destination, reason=reason)


@dataclass
class _State:
    provider: TracerProvider | None = None
    settings: TelemetrySettings = field(
        default_factory=lambda: TelemetrySettings(local_enabled=False)
    )
    store: SqliteTelemetryStore | None = None
    posture: TelemetryPosture = field(
        default_factory=lambda: TelemetryPosture(
            configured=False,
            local=_off("sqlite", "local", "not_configured"),
            semconv=sc.GENAI_SEMCONV_VERSION,
        )
    )


_STATE = _State()
_LOCK = threading.Lock()


def get_tracer(name: str = INSTRUMENTATION_NAME) -> trace.Tracer:
    """Locus's tracer; a no-op tracer until :func:`configure` ran."""
    provider = _STATE.provider
    if provider is None:
        return trace.NoOpTracer()
    return provider.get_tracer(name)


def settings() -> TelemetrySettings:
    return _STATE.settings


def content_capture_enabled() -> bool:
    return _STATE.provider is not None and _STATE.settings.capture_content


def content_limit() -> int:
    return _STATE.settings.content_max_chars


def local_store() -> SqliteTelemetryStore | None:
    """The configured local store (the reader behind ``GET /telemetry/*``)."""
    return _STATE.store


def posture() -> TelemetryPosture:
    return _STATE.posture


def configure(
    config: TelemetrySettings,
    *,
    egress_check: EgressCheck | None = None,
    secret_resolver: SecretResolver | None = None,
    redactor: Callable[[str], str] | None = None,
    exporters: Sequence[SpanExporter] = (),
    synchronous: bool = False,
    exporter_factory: Callable[[str, dict[str, str]], SpanExporter] | None = None,
) -> TelemetryPosture:
    """Build and install a provider for ``config``; returns the resulting posture.

    ``egress_check`` answers whether a host is on the platform egress allowlist;
    without one, no external exporter is installed (fail closed). ``exporters``
    are extra exporters (tests: an in-memory exporter), also redacted;
    ``synchronous`` exports on span end instead of in a batch (tests only).
    """
    content.set_extra_redactor(redactor)
    content.set_known_secrets(())
    limit = config.content_max_chars
    processors: list[SpanProcessor] = []

    def processor(exporter: SpanExporter) -> SpanProcessor:
        if synchronous:
            return SimpleSpanProcessor(exporter)
        return BatchSpanProcessor(
            exporter,
            max_queue_size=config.queue_size,
            schedule_delay_millis=_SCHEDULE_DELAY_MS,
            max_export_batch_size=min(_MAX_BATCH, config.queue_size),
        )

    store: SqliteTelemetryStore | None = None
    local = _off("sqlite", "local", "disabled" if not config.local_enabled else "no_path")
    if config.local_enabled and config.db_path:
        store = SqliteTelemetryStore(
            Path(config.db_path),
            payload_retention_days=config.payload_retention_days,
            span_retention_days=config.span_retention_days,
        )
        try:
            store.migrate()
            processors.append(processor(SinkSpanExporter(store, content_limit=limit)))
            local = ExporterPosture(kind="sqlite", state="active", destination="local")
        except Exception as exc:  # noqa: BLE001 - reported in posture, never fatal
            logger.exception("telemetry.local_store_unavailable")
            local = ExporterPosture(
                kind="sqlite",
                state="blocked",
                destination="local",
                reason=f"store_unavailable:{type(exc).__name__}",
            )
            store = None

    external: list[ExporterPosture] = []
    kinds: tuple[tuple[ExporterKind, ExporterSettings], ...] = (
        ("otlp", config.otlp),
        ("langsmith", config.langsmith),
    )
    for kind, exporter_settings in kinds:
        exporter, state = build_external_exporter(
            kind,
            exporter_settings,
            egress_check=egress_check,
            secret_resolver=secret_resolver,
            content_limit=limit,
            factory=exporter_factory,
        )
        external.append(state)
        if exporter is not None:
            processors.append(processor(exporter))
    for extra in exporters:
        processors.append(processor(RedactingSpanExporter(extra, content_limit=limit)))

    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME, "telemetry.sdk.language": "python"})
    )
    for item in processors:
        provider.add_span_processor(item)
    new_posture = TelemetryPosture(
        configured=True,
        local=local,
        external=external,
        capture_content=config.capture_content,
        payload_retention_days=config.payload_retention_days,
        semconv=sc.GENAI_SEMCONV_VERSION,
    )
    with _LOCK:
        old = _STATE.provider
        _STATE.provider = provider
        _STATE.settings = config
        _STATE.store = store
        _STATE.posture = new_posture
    if old is not None:
        _shutdown_later(old)
    return new_posture


def _shutdown_later(provider: TracerProvider) -> None:
    def run() -> None:
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001 - cleanup
            logger.exception("telemetry.provider_shutdown_error")

    threading.Thread(target=run, name="locus-telemetry-shutdown", daemon=True).start()


def force_flush(timeout_millis: int = 5000) -> bool:
    provider = _STATE.provider
    return True if provider is None else bool(provider.force_flush(timeout_millis))


def reset() -> None:
    """Shut down synchronously and return to the unconfigured (no-op) state."""
    with _LOCK:
        old = _STATE.provider
        _STATE.provider = None
        _STATE.settings = TelemetrySettings(local_enabled=False)
        _STATE.store = None
        _STATE.posture = _State().posture
    content.set_extra_redactor(None)
    content.set_known_secrets(())
    if old is not None:
        try:
            old.shutdown()
        except Exception:  # noqa: BLE001 - cleanup
            logger.exception("telemetry.provider_shutdown_error")


DB_PATH_ENV = "LOCUS_TELEMETRY_DB"
LOCAL_ENV = "LOCUS_TELEMETRY_LOCAL"


def default_db_path(app_home: Path | None = None) -> Path:
    """``LOCUS_TELEMETRY_DB``, else ``<app_home>/data/telemetry/telemetry.db``."""
    explicit = str(os.getenv(DB_PATH_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if app_home is None:
        from locus_runtime.win_toolchain import toolchain_app_home

        app_home = toolchain_app_home()
    return Path(app_home) / "data" / "telemetry" / "telemetry.db"


def local_enabled_by_env() -> bool:
    """``LOCUS_TELEMETRY_LOCAL=0`` turns the local store off (it is on by default)."""
    return str(os.getenv(LOCAL_ENV) or "1").strip().lower() not in {"0", "false", "no", "off"}


def ensure_configured(app_home: Path | None = None) -> TelemetryPosture:
    """Composition roots without platform settings (the loop runner, CLIs): local
    SQLite only, privacy defaults. A no-op when already configured."""
    if _STATE.provider is not None:
        return _STATE.posture
    return configure(
        TelemetrySettings(
            local_enabled=local_enabled_by_env(), db_path=str(default_db_path(app_home))
        )
    )


# --------------------------------------------------------------------------- #
# Settings policy
# --------------------------------------------------------------------------- #
def widening_changes(current: TelemetrySettings, candidate: TelemetrySettings) -> list[str]:
    """Setting names whose change widens what leaves the run or the machine.

    Widening (needs the principal's confirmation, P32): turning content capture
    on; enabling an external exporter; changing an enabled exporter's endpoint;
    lengthening the payload retention. Narrowing changes are never listed.
    """
    widened: list[str] = []
    if candidate.capture_content and not current.capture_content:
        widened.append("capture_content")
    if candidate.payload_retention_days > current.payload_retention_days:
        widened.append("payload_retention_days")
    for kind in ("otlp", "langsmith"):
        old = getattr(current, kind)
        new = getattr(candidate, kind)
        if new.enabled and not old.enabled:
            widened.append(f"{kind}.enabled")
        if new.enabled and new.endpoint.strip() != old.endpoint.strip():
            widened.append(f"{kind}.endpoint")
    return widened
