"""LOCUS-375: AI observability -- span shapes, privacy, the SQLite store, exporters.

Everything runs in process: spans go to an in-memory exporter and a SQLite file
under ``tmp_path``; external exporters are built with a capturing factory, so
nothing touches the network.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from locus_runtime import gateway as gw
from locus_runtime import model_client as mc
from locus_runtime import telemetry
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.contract import (
    LANGSMITH_LABEL,
    PORT_VERSION,
    ExporterSettings,
    RunQuery,
    SpanRecord,
    TelemetryReader,
    TelemetrySettings,
    TelemetrySink,
)
from locus_runtime.telemetry.exporters import build_external_exporter, endpoint_host
from locus_runtime.telemetry.sqlite_store import (
    SCHEMA_VERSION,
    SqliteTelemetryStore,
    TelemetryStoreError,
)
from tests.gateway_support import AllowAllAuthorizer, FakeEngine, installed
from tests.unit.test_model_client import MockServer, RecordingGate, _client

SECRETS = (
    "sk-live-0123456789abcdefABCDEF",
    "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8",
    "AKIA" + "ABCDEFGHIJKLMNOP",
)
AUTH_VALUE = "Basic cGstbGYtMTIzOnNrLWxmLTQ1Njc4OTAxMjM0"


class CapturingExporter(SpanExporter):
    """Stands in for the OTLP/HTTP exporter: records spans, no network."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []
        self.built: list[tuple[str, dict[str, str]]] = []

    def factory(self, endpoint: str, headers: dict[str, str]) -> SpanExporter:
        self.built.append((endpoint, dict(headers)))
        return self

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


@pytest.fixture()
def memory(tmp_path: Path) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    telemetry.configure(
        TelemetrySettings(db_path=str(tmp_path / "telemetry.db")),
        exporters=[exporter],
        synchronous=True,
    )
    yield exporter
    telemetry.reset()


def _by_op(exporter: InMemorySpanExporter, operation: str) -> list[ReadableSpan]:
    return [
        s
        for s in exporter.get_finished_spans()
        if (s.attributes or {}).get(sc.GEN_AI_OPERATION_NAME) == operation
    ]


def _all_text(spans: Sequence[ReadableSpan]) -> str:
    parts: list[str] = []
    for span in spans:
        parts.append(span.name)
        parts.append(json.dumps(dict(span.attributes or {}), default=str))
        parts.append(str(span.status.description or ""))
        for event in span.events:
            parts.append(event.name)
            parts.append(json.dumps(dict(event.attributes or {}), default=str))
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Not configured / port
# --------------------------------------------------------------------------- #
def test_unconfigured_telemetry_is_a_no_op() -> None:
    telemetry.reset()
    assert telemetry.posture().configured is False
    with telemetry.agent_run(run_id="r", agent="a", runtime="verified-loop") as span:
        assert span.recording is False
        span.set("x", 1)
        telemetry.record_score("s", 1.0)
    assert telemetry.local_store() is None


def test_store_implements_the_ports(tmp_path: Path) -> None:
    store = SqliteTelemetryStore(tmp_path / "t.db")
    assert isinstance(store, TelemetrySink) and isinstance(store, TelemetryReader)
    assert store.port_version == PORT_VERSION == telemetry.PORT_VERSION
    assert store.destination == "local"


# --------------------------------------------------------------------------- #
# Model calls
# --------------------------------------------------------------------------- #
def test_chat_span_has_genai_attributes_and_no_content(memory: InMemorySpanExporter) -> None:
    client = _client(MockServer(), "nim")
    client.complete([{"role": "user", "content": f"key {SECRETS[0]}"}], temperature=0.2)
    (span,) = _by_op(memory, sc.OP_CHAT)
    attrs = dict(span.attributes or {})
    assert span.name == f"chat {client.model}"
    assert span.kind.name == "CLIENT"
    assert attrs[sc.GEN_AI_PROVIDER_NAME] == attrs[sc.GEN_AI_SYSTEM] == "nim"
    assert attrs[sc.GEN_AI_REQUEST_MODEL] == client.model
    assert attrs[sc.GEN_AI_RESPONSE_MODEL] == client.model
    assert attrs[sc.GEN_AI_USAGE_INPUT_TOKENS] == 11
    assert attrs[sc.GEN_AI_USAGE_OUTPUT_TOKENS] == 7
    assert attrs[sc.LOCUS_COST_USD] >= 0.0
    assert attrs[sc.GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)
    assert attrs[sc.GEN_AI_REQUEST_TEMPERATURE] == 0.2
    assert attrs[sc.SERVER_ADDRESS] == "integrate.api.nvidia.com"
    assert attrs[sc.LOCUS_GATEWAY_AUDIT_ID] == "audit-1"
    # Content capture is off by default: no message content at all.
    assert not set(attrs) & sc.CONTENT_ATTRIBUTES
    assert SECRETS[0] not in _all_text(memory.get_finished_spans())


def test_chat_span_records_provider_failure_and_denial(memory: InMemorySpanExporter) -> None:
    with pytest.raises(mc.ModelProviderError):
        _client(MockServer(status=500, body="boom"), "nim").complete(
            [{"role": "user", "content": "x"}]
        )

    class DenyGate(RecordingGate):
        def authorize(self, call: mc.ModelCall) -> str:
            raise mc.ModelCallDenied(provider=call.provider, model=call.model, reason="deny: no")

    with pytest.raises(mc.ModelCallDenied):
        _client(MockServer(), "nim", gate=DenyGate()).complete([{"role": "user", "content": "x"}])
    failed, denied = _by_op(memory, sc.OP_CHAT)
    assert failed.status.status_code.name == "ERROR"
    assert dict(failed.attributes or {})[sc.ERROR_TYPE] == mc.PROVIDER_CALL_FAILED
    assert dict(denied.attributes or {})[sc.ERROR_TYPE] == mc.MODEL_CALL_DENIED


def test_streamed_chat_span_ends_with_usage(memory: InMemorySpanExporter) -> None:
    gate = RecordingGate()
    result = _client(MockServer(), "openai", gate=gate).stream([{"role": "user", "content": "hi"}])
    assert result.text == "Hello stream"
    (span,) = _by_op(memory, sc.OP_CHAT)
    attrs = dict(span.attributes or {})
    (usage,) = gate.usage
    assert attrs[sc.LOCUS_STREAM] is True
    assert attrs[sc.GEN_AI_USAGE_INPUT_TOKENS] == usage.tokens_in
    assert attrs[sc.GEN_AI_USAGE_OUTPUT_TOKENS] == usage.tokens_out
    assert attrs[sc.LOCUS_USAGE_REPORTED] is usage.usage_reported
    assert attrs[sc.GEN_AI_RESPONSE_FINISH_REASONS] == ("stop",)


def test_fallback_hops_are_recorded(memory: InMemorySpanExporter) -> None:
    servers = {"nim": MockServer(status=503, body="down"), "ollama": MockServer()}
    router = mc.ModelRouter(
        [mc.ModelTier("nim", "a"), mc.ModelTier("ollama", "b")],
        client_factory=lambda tier: _client(
            servers[tier.provider], tier.provider, model=tier.model
        ),
    )
    with telemetry.agent_run(run_id="fb", agent="t", runtime="test"):
        router.complete([{"role": "user", "content": "x"}])
    first, second = _by_op(memory, sc.OP_CHAT)
    assert dict(first.attributes or {})[sc.ERROR_TYPE] == mc.PROVIDER_CALL_FAILED
    assert sc.LOCUS_FALLBACK_HOP not in dict(first.attributes or {})
    assert dict(second.attributes or {})[sc.LOCUS_FALLBACK_HOP] == 1
    assert dict(second.attributes or {})[sc.LOCUS_FALLBACK_FROM] == "nim/a"
    (run,) = _by_op(memory, sc.OP_INVOKE_AGENT)
    (event,) = [e for e in run.events if e.name == sc.LOCUS_FALLBACK_EVENT]
    assert dict(event.attributes or {})[sc.LOCUS_FALLBACK_REASON] == mc.PROVIDER_CALL_FAILED


# --------------------------------------------------------------------------- #
# Tools, gateway decisions, sandbox exec
# --------------------------------------------------------------------------- #
def test_tool_span_carries_gateway_outcome_and_risk(memory: InMemorySpanExporter) -> None:
    gateway = gw.Gateway(FakeEngine(), lambda _record: None)
    session = gateway.open_session(
        run_id="tool-run", principal="p", engine="e", capabilities=gw.Capabilities()
    )
    with telemetry.tool_call("str_replace_editor", call_id="c1") as span:
        decision = session.authorize(
            kind="file_write", tool="str_replace_editor", target="/secret/path/token.txt"
        )
        telemetry.record_tool_result(span, "ok")
    (tool,) = _by_op(memory, sc.OP_EXECUTE_TOOL)
    (gate,) = _by_op(memory, sc.OP_GATEWAY)
    attrs, gate_attrs = dict(tool.attributes or {}), dict(gate.attributes or {})
    assert tool.name == "execute_tool str_replace_editor"
    assert attrs[sc.GEN_AI_TOOL_NAME] == "str_replace_editor"
    assert attrs[sc.GEN_AI_TOOL_CALL_ID] == "c1"
    assert attrs[sc.LOCUS_GATEWAY_OUTCOME] == gate_attrs[sc.LOCUS_GATEWAY_OUTCOME]
    assert attrs[sc.LOCUS_GATEWAY_RISK] == f"R{int(decision.risk)}"
    assert attrs[sc.LOCUS_TOOL_STATUS] in {"ok", "blocked"}
    assert gate.parent is not None and gate.parent.span_id == tool.context.span_id
    assert gate_attrs[sc.LOCUS_GATEWAY_ACTION] == "file_write"
    assert gate_attrs[sc.LOCUS_GATEWAY_AUDIT_ID] == decision.audit_id
    assert tuple(gate_attrs[sc.LOCUS_GATEWAY_REASONS]) == telemetry.spans.safe_reasons(
        decision.reasons
    )
    # No payloads: the target path never appears on any span.
    assert "token.txt" not in _all_text(memory.get_finished_spans())


def test_denied_tool_is_blocked_and_reasons_are_codes_only(memory: InMemorySpanExporter) -> None:
    gateway = gw.Gateway(FakeEngine(default=False), lambda _record: None)
    session = gateway.open_session(
        run_id="deny-run", principal="p", engine="e", capabilities=gw.Capabilities()
    )
    with telemetry.tool_call("execute_bash"):
        session.authorize(kind="process_exec", tool="execute_bash", target="/w", command="rm -rf /")
    (tool,) = _by_op(memory, sc.OP_EXECUTE_TOOL)
    (gate,) = _by_op(memory, sc.OP_GATEWAY)
    assert dict(tool.attributes or {})[sc.LOCUS_TOOL_STATUS] == "blocked"
    assert dict(gate.attributes or {})[sc.ERROR_TYPE] == "gateway_deny"
    assert "rm -rf" not in _all_text(memory.get_finished_spans())
    assert telemetry.spans.safe_reasons(["policy.allow", "has spaces & stuff"]) == (
        "policy.allow",
        "[withheld]",
    )


def test_sandbox_exec_span_has_tier_exit_code_and_no_command(
    memory: InMemorySpanExporter, tmp_path: Path
) -> None:
    executor = LocalDirectExecutor(tmp_path)
    with installed(AllowAllAuthorizer()):
        result = executor.run([sys.executable, "-c", "import sys; sys.exit(3)  # SECRET-ARG"])
    assert result.exit_code == 3
    (span,) = _by_op(memory, sc.OP_SANDBOX_EXEC)
    attrs = dict(span.attributes or {})
    assert attrs[sc.LOCUS_SANDBOX_TIER] == "local-direct"
    assert attrs[sc.LOCUS_SANDBOX_EXIT_CODE] == 3
    assert attrs[sc.LOCUS_SANDBOX_TIMED_OUT] is False
    assert attrs[sc.LOCUS_SANDBOX_DURATION_MS] >= 0
    assert attrs[sc.LOCUS_SANDBOX_EXECUTABLE].lower().startswith("python")
    assert "SECRET-ARG" not in _all_text(memory.get_finished_spans())


def test_scores_land_on_the_current_span_or_their_own(memory: InMemorySpanExporter) -> None:
    with telemetry.gate("quality", run_id="score-run"):
        telemetry.record_score("quality_gate", 1.0, label="pass", source="test")
    telemetry.record_score("orphan", 0.0, label="fail", run_id="score-run")
    (gate,) = _by_op(memory, sc.OP_GATE)
    (event,) = gate.events
    assert event.name == sc.GEN_AI_EVALUATION_EVENT
    assert dict(event.attributes or {})[sc.GEN_AI_EVALUATION_SCORE_LABEL] == "pass"
    assert dict(event.attributes or {})[sc.LOCUS_RUN_ID] == "score-run"
    (own,) = _by_op(memory, sc.OP_EVALUATION)
    assert dict(own.attributes or {})[sc.LOCUS_RUN_ID] == "score-run"


# --------------------------------------------------------------------------- #
# Privacy (P10)
# --------------------------------------------------------------------------- #
def test_content_capture_on_is_redacted_and_truncated(tmp_path: Path) -> None:
    exporter = InMemorySpanExporter()
    telemetry.configure(
        TelemetrySettings(db_path="", capture_content=True, content_max_chars=200),
        exporters=[exporter],
        synchronous=True,
    )
    with telemetry.tool_call("execute_bash", arguments={"command": f"echo {SECRETS[1]}"}) as span:
        telemetry.record_tool_result(span, "x" * 1000)
    (tool,) = _by_op(exporter, sc.OP_EXECUTE_TOOL)
    attrs = dict(tool.attributes or {})
    assert "echo" in attrs[sc.GEN_AI_TOOL_CALL_ARGUMENTS]
    assert SECRETS[1] not in attrs[sc.GEN_AI_TOOL_CALL_ARGUMENTS]
    assert len(attrs[sc.GEN_AI_TOOL_CALL_RESULT]) <= 200
    assert attrs[sc.LOCUS_CONTENT_TRUNCATED] is True


def test_a_secret_never_reaches_any_exporter(tmp_path: Path) -> None:
    """Content capture ON, every sink attached: SQLite, an in-memory exporter and an
    OTLP exporter (capturing factory). Secrets in the prompt, the model output, tool
    arguments and output, an error and a raw attribute never reach any of them."""
    memory = InMemorySpanExporter()
    otlp = CapturingExporter()
    db = tmp_path / "telemetry.db"
    telemetry.configure(
        TelemetrySettings(
            db_path=str(db),
            capture_content=True,
            otlp=ExporterSettings(
                enabled=True,
                endpoint="https://langfuse.example.test/api/public/otel/v1/traces",
                auth_secret_ref="LOCUS_TELEMETRY_OTLP_AUTH",
            ),
        ),
        egress_check=lambda host: host == "langfuse.example.test",
        secret_resolver=lambda name: AUTH_VALUE if name == "LOCUS_TELEMETRY_OTLP_AUTH" else None,
        exporters=[memory],
        synchronous=True,
        exporter_factory=otlp.factory,
    )
    assert telemetry.posture().external[0].state == "active"

    class EchoServer(MockServer):
        def handler(self, request: httpx.Request) -> httpx.Response:
            response = super().handler(request)
            body = json.loads(response.content)
            body["choices"][0]["message"]["content"] = f"your key is {SECRETS[0]}"
            return httpx.Response(200, json=body)

    with telemetry.agent_run(run_id="secret-run", agent="t", runtime="test") as run:
        _client(EchoServer(), "nim").complete(
            [{"role": "user", "content": f"use {SECRETS[0]} and {SECRETS[2]} and {AUTH_VALUE}"}]
        )
        with telemetry.tool_call("execute_bash", arguments={"command": SECRETS[1]}) as span:
            telemetry.record_tool_result(span, f"output {SECRETS[1]}", failed=True)
            span.set("custom.raw", f"token={SECRETS[0]}")
        run.error(RuntimeError(f"failed with {SECRETS[2]}"))
    assert telemetry.force_flush()
    exported = memory.get_finished_spans()
    assert any(sc.GEN_AI_INPUT_MESSAGES in dict(s.attributes or {}) for s in exported)
    texts = [_all_text(exported), _all_text(otlp.spans)]
    with sqlite3.connect(str(db)) as conn:
        for table in ("spans", "span_events", "scores"):
            texts.append(json.dumps(conn.execute(f"SELECT * FROM {table}").fetchall()))
    texts.append(json.dumps(telemetry.posture().model_dump()))
    for text in texts:
        for secret in (*SECRETS, AUTH_VALUE, AUTH_VALUE.split()[1]):
            assert secret not in text
    # The OTLP exporter got the auth header by reference resolution only.
    assert otlp.built == [
        (
            "https://langfuse.example.test/api/public/otel/v1/traces",
            {"Authorization": AUTH_VALUE},
        )
    ]


# --------------------------------------------------------------------------- #
# SQLite store
# --------------------------------------------------------------------------- #
def test_sqlite_round_trip_runs_trace_summary(tmp_path: Path) -> None:
    db = tmp_path / "telemetry.db"
    telemetry.configure(TelemetrySettings(db_path=str(db)), synchronous=True)
    for run_id, state in (("run-a", "done"), ("run-b", "blocked")):
        with telemetry.agent_run(run_id=run_id, agent="agent", runtime="verified-loop") as run:
            _client(MockServer(), "nim").complete([{"role": "user", "content": "x"}])
            with telemetry.tool_call("execute_bash", call_id="t1"):
                pass
            with telemetry.gate("verify", run_id=run_id):
                telemetry.record_score("verification", 1.0 if state == "done" else 0.0, label=state)
            run.set(sc.LOCUS_END_STATE, state)
    store = telemetry.local_store()
    assert store is not None
    page = store.list_runs(RunQuery(limit=10))
    assert page.total == 2 and [r.run_id for r in page.runs] == ["run-b", "run-a"]
    run_a = page.runs[1]
    assert run_a.end_state == "done" and run_a.runtime == "verified-loop"
    assert (run_a.model_calls, run_a.tool_calls) == (1, 1)
    assert (run_a.input_tokens, run_a.output_tokens) == (11, 7)
    assert [s.label for s in run_a.scores] == ["done"]
    assert store.list_runs(RunQuery(end_state="blocked")).total == 1
    assert store.list_runs(RunQuery(limit=1, offset=1)).runs[0].run_id == "run-a"

    view = store.trace("run-a")
    assert view is not None and len(view.trace_ids) == 1 and len(view.roots) == 1
    root = view.roots[0]
    assert root.span.operation == sc.OP_INVOKE_AGENT
    assert {c.span.operation for c in root.children} == {sc.OP_CHAT, sc.OP_EXECUTE_TOOL, sc.OP_GATE}
    assert store.trace("missing") is None

    summary = store.summary(0, time.time_ns() + 1)
    assert summary.runs == 2 and summary.model_calls == 2 and summary.tool_calls == 2
    assert summary.input_tokens == 22
    assert summary.model_latency_ms.count == 2 and summary.model_latency_ms.p50 is not None
    assert summary.gate_outcomes == {"verification": {"done": 1, "blocked": 1}}
    assert summary.end_states == {"done": 1, "blocked": 1}


def _record(span_id: str, *, end_ns: int, payload: dict[str, Any] | None) -> SpanRecord:
    return SpanRecord(
        trace_id="0" * 31 + "1",
        span_id=span_id.rjust(16, "0"),
        name="chat m",
        operation=sc.OP_CHAT,
        run_id="ret",
        start_ns=end_ns - 1000,
        end_ns=end_ns,
        payload=payload,
    )


def test_retention_clears_payloads_then_deletes_spans(tmp_path: Path) -> None:
    day = 86_400 * 10**9
    now = 1000 * day
    store = SqliteTelemetryStore(
        tmp_path / "t.db",
        payload_retention_days=90,
        span_retention_days=365,
        prune_interval_seconds=1e12,  # no automatic prune on export in this test
    )
    assert store.export(
        [
            _record("1", end_ns=now - 10 * day, payload={"gen_ai.input.messages": "fresh"}),
            _record("2", end_ns=now - 100 * day, payload={"gen_ai.input.messages": "old"}),
            _record("3", end_ns=now - 400 * day, payload=None),
        ]
    )
    result = store.prune(now_ns=now)
    assert result["payloads_cleared"] == 1 and result["spans"] == 1
    with sqlite3.connect(str(tmp_path / "t.db")) as conn:
        rows = dict(conn.execute("SELECT span_id, payload FROM spans").fetchall())
    assert rows == {
        "1".rjust(16, "0"): json.dumps({"gen_ai.input.messages": "fresh"}).replace(" ", ""),
        "2".rjust(16, "0"): None,
    }


def test_schema_is_versioned_and_newer_files_are_refused(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = SqliteTelemetryStore(db)
    assert store.migrate() == SCHEMA_VERSION and store.schema_version() == SCHEMA_VERSION
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(TelemetryStoreError):
        SqliteTelemetryStore(db).migrate()
    posture = telemetry.configure(TelemetrySettings(db_path=str(db)), synchronous=True)
    assert posture.local.state == "blocked"
    assert telemetry.local_store() is None


def test_export_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    store = SqliteTelemetryStore(blocker / "t.db")
    assert store.export([_record("1", end_ns=10, payload=None)]) is False


# --------------------------------------------------------------------------- #
# External exporters
# --------------------------------------------------------------------------- #
def test_external_exporters_are_off_by_default(tmp_path: Path) -> None:
    defaults = TelemetrySettings()
    assert defaults.capture_content is False
    assert not defaults.otlp.enabled and not defaults.langsmith.enabled
    posture = telemetry.configure(
        TelemetrySettings(db_path=str(tmp_path / "t.db")),
        egress_check=lambda _host: True,
        synchronous=True,
    )
    assert posture.local.state == "active"
    assert [(e.kind, e.state) for e in posture.external] == [("otlp", "off"), ("langsmith", "off")]


def test_langsmith_preset_is_labelled_hosted_proprietary() -> None:
    capture = CapturingExporter()
    exporter, posture = build_external_exporter(
        "langsmith",
        ExporterSettings(
            enabled=True,
            endpoint=TelemetrySettings().langsmith.endpoint,
            auth_secret_ref="LANGSMITH_API_KEY",
            project="locus",
        ),
        egress_check=lambda host: host == "api.smith.langchain.com",
        secret_resolver=lambda _name: "lsv2_pt_0123456789abcdef0123",
        content_limit=100,
        factory=capture.factory,
    )
    assert exporter is not None and posture.state == "active"
    assert posture.destination == "hosted_proprietary"
    assert posture.label == LANGSMITH_LABEL == "hosted, proprietary; data leaves the machine"
    assert capture.built[0][1] == {
        "x-api-key": "lsv2_pt_0123456789abcdef0123",
        "Langsmith-Project": "locus",
    }
    _none, off = build_external_exporter(
        "langsmith", TelemetrySettings().langsmith, egress_check=None, content_limit=100
    )
    assert off.state == "off" and off.destination == "hosted_proprietary"


def test_exporter_egress_fails_closed() -> None:
    settings = ExporterSettings(enabled=True, endpoint="https://collector.example.test/v1/traces")
    capture = CapturingExporter()
    for check in (None, lambda _h: False, lambda _h: 1 / 0):
        exporter, posture = build_external_exporter(
            "otlp", settings, egress_check=check, content_limit=100, factory=capture.factory
        )
        assert exporter is None and posture.state == "blocked"
        assert posture.reason == "egress_not_allowed" and posture.destination == "remote"
    assert capture.built == []

    allowed = {"collector.example.test"}
    exporter, posture = build_external_exporter(
        "otlp",
        settings,
        egress_check=lambda host: host in allowed,
        content_limit=100,
        factory=capture.factory,
    )
    assert exporter is not None and posture.state == "active"
    allowed.clear()  # the allowlist changes after start: nothing more is sent
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert capture.spans == []


def test_exporter_endpoint_and_secret_checks() -> None:
    assert endpoint_host("http://collector.example.test/v1/traces")[1] == "endpoint_requires_https"
    assert endpoint_host("https://u:p@collector.example.test/v1")[1] == "endpoint_has_credentials"
    assert endpoint_host("http://127.0.0.1:4318/v1/traces") == ("127.0.0.1", "")
    assert endpoint_host("ftp://x")[1] == "endpoint_invalid"

    def build(**kwargs: Any) -> str:
        settings = ExporterSettings(
            enabled=True, endpoint="http://localhost:4318/v1/traces", **kwargs
        )
        _exporter, posture = build_external_exporter(
            "otlp",
            settings,
            egress_check=lambda _h: True,
            secret_resolver=lambda _n: None,
            content_limit=100,
            factory=CapturingExporter().factory,
        )
        assert posture.destination == "loopback"
        return posture.reason or posture.state

    assert build() == "active"  # a local collector without auth
    assert build(auth_secret_ref="lower-case") == "auth_secret_ref_invalid"
    assert build(auth_secret_ref="MISSING_SECRET") == "auth_secret_unavailable"


def test_widening_changes() -> None:
    base = TelemetrySettings()
    on = base.model_copy(
        update={
            "capture_content": True,
            "otlp": ExporterSettings(enabled=True, endpoint="https://a.test/v1/traces"),
        }
    )
    assert telemetry.widening_changes(base, on) == [
        "capture_content",
        "otlp.enabled",
        "otlp.endpoint",
    ]
    assert telemetry.widening_changes(on, base) == []
