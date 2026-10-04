"""Span conversion, the redaction layer and the opt-in external exporters.

* :func:`span_to_record` -- an OTel ``ReadableSpan`` as a redacted
  :class:`~locus_runtime.telemetry.contract.SpanRecord` (content attributes
  split into ``payload``).
* :class:`SinkSpanExporter` -- adapts a :class:`TelemetrySink` (the SQLite
  store) to the OTel ``SpanExporter`` interface.
* :class:`RedactingSpanExporter` -- re-redacts every string attribute of every
  span and event before an inner exporter sees it (P10, defence in depth).
* :class:`EgressGuardedExporter` -- checks the destination host against the
  platform egress allowlist on every export; a host that is not allowed is
  never contacted (fail closed).
* :func:`build_external_exporter` -- the OTLP/HTTP exporter (self-hosted
  Langfuse or any collector) and the LangSmith preset, from settings. Auth
  values are resolved from native secrets by reference and never logged.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.content import (
    ATTRIBUTE_MAX_CHARS,
    redact,
    scrub_value,
    set_known_secrets,
)
from locus_runtime.telemetry.contract import (
    LANGSMITH_LABEL,
    DestinationClass,
    ExporterKind,
    ExporterPosture,
    ExporterSettings,
    ExporterState,
    SpanEventRecord,
    SpanRecord,
    TelemetrySink,
)

logger = logging.getLogger(__name__)

#: ``host -> allowed``. ``None`` means no allowlist was supplied: deny everything.
EgressCheck = Callable[[str], bool]
#: ``secret name -> value`` (``None`` when absent); default: native secrets.
SecretResolver = Callable[[str], "str | None"]

_SECRET_REF = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
_PROJECT = re.compile(r"[A-Za-z0-9 _.\-]{0,128}")
_LOOPBACK_NAMES = frozenset({"localhost"})
EXPORT_TIMEOUT_SECONDS = 10.0


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
_STATUS: dict[str, Literal["UNSET", "OK", "ERROR"]] = {
    "UNSET": "UNSET",
    "OK": "OK",
    "ERROR": "ERROR",
}


def _hex(value: int, width: int) -> str:
    return format(value, f"0{width}x")


def scrub_attributes(
    attributes: Mapping[str, Any] | None, *, content_limit: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(attributes, payload)``: every string redacted; content split out."""
    plain: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if key in sc.CONTENT_ATTRIBUTES:
            payload[key] = scrub_value(value, limit=content_limit + 32)
        else:
            plain[key] = scrub_value(value, limit=ATTRIBUTE_MAX_CHARS)
    return plain, payload


def _jsonable(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def span_to_record(span: ReadableSpan, *, content_limit: int) -> SpanRecord:
    ctx = span.context
    if ctx is None:
        raise ValueError("span has no context")
    attrs, payload = scrub_attributes(span.attributes, content_limit=content_limit)
    events: list[SpanEventRecord] = []
    for event in span.events:
        event_attrs, event_payload = scrub_attributes(event.attributes, content_limit=content_limit)
        events.append(
            SpanEventRecord(
                name=redact(event.name, limit=128),
                time_ns=int(event.timestamp or 0),
                attributes={k: _jsonable(v) for k, v in event_attrs.items()},
                payload={k: _jsonable(v) for k, v in event_payload.items()} or None,
            )
        )
    status = _STATUS.get(span.status.status_code.name, "UNSET")
    start = int(span.start_time or 0)
    end = int(span.end_time or start)
    return SpanRecord(
        trace_id=_hex(ctx.trace_id, 32),
        span_id=_hex(ctx.span_id, 16),
        parent_span_id=_hex(span.parent.span_id, 16) if span.parent is not None else None,
        name=redact(span.name, limit=256),
        kind=span.kind.name,
        operation=str(attrs.get(sc.GEN_AI_OPERATION_NAME) or ""),
        run_id=str(attrs.get(sc.LOCUS_RUN_ID) or ""),
        status=status,
        status_message=redact(span.status.description or "", limit=ATTRIBUTE_MAX_CHARS),
        start_ns=start,
        end_ns=end,
        provider=str(attrs.get(sc.GEN_AI_PROVIDER_NAME) or attrs.get(sc.GEN_AI_SYSTEM) or ""),
        model=str(attrs.get(sc.GEN_AI_RESPONSE_MODEL) or attrs.get(sc.GEN_AI_REQUEST_MODEL) or ""),
        tool=str(attrs.get(sc.GEN_AI_TOOL_NAME) or ""),
        input_tokens=_as_int(attrs.get(sc.GEN_AI_USAGE_INPUT_TOKENS)),
        output_tokens=_as_int(attrs.get(sc.GEN_AI_USAGE_OUTPUT_TOKENS)),
        cost_usd=_as_float(attrs.get(sc.LOCUS_COST_USD)),
        error_type=str(attrs.get(sc.ERROR_TYPE) or ""),
        attributes={k: _jsonable(v) for k, v in attrs.items()},
        payload={k: _jsonable(v) for k, v in payload.items()} or None,
        events=events,
    )


# --------------------------------------------------------------------------- #
# Exporter adapters
# --------------------------------------------------------------------------- #
class SinkSpanExporter(SpanExporter):
    """A :class:`TelemetrySink` behind the OTel exporter interface (never raises)."""

    def __init__(self, sink: TelemetrySink, *, content_limit: int) -> None:
        self.sink = sink
        self._content_limit = content_limit

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            records = [span_to_record(s, content_limit=self._content_limit) for s in spans]
            ok = self.sink.export(records)
        except Exception:  # noqa: BLE001 - telemetry never fails the agent
            logger.exception("telemetry.sink_export_error", extra={"sink": self.sink.name})
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS if ok else SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self.sink.shutdown()
        except Exception:  # noqa: BLE001 - cleanup
            logger.exception("telemetry.sink_shutdown_error")

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        return True


def redacted_copy(span: ReadableSpan, *, content_limit: int) -> ReadableSpan:
    """The same span with every string attribute (span and events) redacted."""
    attrs, payload = scrub_attributes(span.attributes, content_limit=content_limit)
    attrs.update(payload)
    events = []
    for event in span.events:
        event_attrs, event_payload = scrub_attributes(event.attributes, content_limit=content_limit)
        event_attrs.update(event_payload)
        events.append(Event(event.name, event_attrs, event.timestamp))
    status = span.status
    if status.description:
        status = Status(status.status_code, redact(status.description))
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attrs,
        events=events,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class RedactingSpanExporter(SpanExporter):
    """Redacts every span again before ``inner`` sees it (P10)."""

    def __init__(self, inner: SpanExporter, *, content_limit: int) -> None:
        self.inner = inner
        self._content_limit = content_limit

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            copies = [redacted_copy(s, content_limit=self._content_limit) for s in spans]
            return self.inner.export(copies)
        except Exception:  # noqa: BLE001 - telemetry never fails the agent
            logger.exception("telemetry.export_error")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


class EgressGuardedExporter(SpanExporter):
    """Exports only while ``host`` is on the platform egress allowlist (fail closed)."""

    def __init__(self, inner: SpanExporter, *, host: str, egress_check: EgressCheck | None) -> None:
        self.inner = inner
        self.host = host
        self._check = egress_check
        self._warned = False

    def allowed(self) -> bool:
        if self._check is None:
            return False
        try:
            return bool(self._check(self.host))
        except Exception:  # noqa: BLE001 - an unanswerable allowlist denies
            return False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not self.allowed():
            if not self._warned:
                self._warned = True
                logger.warning("telemetry.egress_denied", extra={"host": self.host})
            return SpanExportResult.FAILURE
        try:
            return self.inner.export(spans)
        except Exception:  # noqa: BLE001 - a failed export drops the batch
            logger.exception("telemetry.external_export_error", extra={"host": self.host})
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


# --------------------------------------------------------------------------- #
# External exporters
# --------------------------------------------------------------------------- #
def is_loopback(host: str) -> bool:
    value = host.strip("[]").lower()
    if value in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def endpoint_host(endpoint: str) -> tuple[str, str]:
    """``(host, problem)`` for an exporter endpoint; ``problem`` is "" when valid.

    https is required except for a loopback collector; credentials in the URL are
    refused (they would be logged by HTTP stacks and stored in settings).
    """
    text = str(endpoint or "").strip()
    if not text:
        return "", "endpoint_missing"
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "", "endpoint_invalid"
    if parts.scheme not in {"http", "https"} or not host:
        return "", "endpoint_invalid"
    if parts.username or parts.password:
        return host, "endpoint_has_credentials"
    if parts.scheme == "http" and not is_loopback(host):
        return host, "endpoint_requires_https"
    return host, ""


def destination_of(kind: ExporterKind, host: str) -> DestinationClass:
    if kind == "sqlite":
        return "local"
    if kind == "langsmith":
        return "hosted_proprietary"
    return "loopback" if host and is_loopback(host) else "remote"


def _default_resolver(name: str) -> str | None:
    from locus_tooling.native_secrets import get_secret

    return get_secret(name)


def build_external_exporter(
    kind: ExporterKind,
    settings: ExporterSettings,
    *,
    egress_check: EgressCheck | None,
    secret_resolver: SecretResolver | None = None,
    content_limit: int,
    factory: Callable[[str, dict[str, str]], SpanExporter] | None = None,
) -> tuple[SpanExporter | None, ExporterPosture]:
    """The exporter for ``kind`` and its posture. ``None`` when off or blocked."""
    host, problem = endpoint_host(settings.endpoint)
    destination = destination_of(kind, host)
    label = LANGSMITH_LABEL if kind == "langsmith" else ""

    def posture(state: ExporterState, reason: str = "") -> ExporterPosture:
        return ExporterPosture(
            kind=kind,
            state=state,
            destination=destination,
            host=host,
            label=label,
            reason=reason,
        )

    if not settings.enabled:
        return None, posture("off")
    if problem:
        return None, posture("blocked", problem)
    guard_probe = EgressGuardedExporter(_NullExporter(), host=host, egress_check=egress_check)
    if not guard_probe.allowed():
        return None, posture("blocked", "egress_not_allowed")
    headers: dict[str, str] = {}
    ref = settings.auth_secret_ref.strip()
    if kind == "langsmith" and not ref:
        return None, posture("blocked", "auth_secret_ref_missing")
    if ref:
        if not _SECRET_REF.fullmatch(ref):
            return None, posture("blocked", "auth_secret_ref_invalid")
        try:
            value = (secret_resolver or _default_resolver)(ref)
        except Exception:  # noqa: BLE001 - no secure store: fail closed, no plaintext
            value = None
        if not value or any(ch in value for ch in "\r\n"):
            return None, posture("blocked", "auth_secret_unavailable")
        set_known_secrets((value,))
        if kind == "langsmith":
            headers["x-api-key"] = value
        else:
            headers["Authorization"] = value
    if kind == "langsmith" and settings.project:
        if not _PROJECT.fullmatch(settings.project):
            return None, posture("blocked", "project_invalid")
        headers["Langsmith-Project"] = settings.project
    build = factory or _otlp_http_exporter
    try:
        inner = build(settings.endpoint.strip(), headers)
    except Exception:  # noqa: BLE001 - never log headers
        logger.exception("telemetry.exporter_build_error", extra={"kind": kind})
        return None, posture("blocked", "exporter_unavailable")
    guarded = EgressGuardedExporter(inner, host=host, egress_check=egress_check)
    return RedactingSpanExporter(guarded, content_limit=content_limit), posture("active")


def _otlp_http_exporter(endpoint: str, headers: dict[str, str]) -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=EXPORT_TIMEOUT_SECONDS)


class _NullExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:  # noqa: ARG002
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None
