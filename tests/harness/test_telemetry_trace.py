"""LOCUS-375: one trace per run, across runtime -> model -> tool -> gateway -> sandbox.

Runs every runtime behind the port (the contract suite's production path:
gated model client against a scripted endpoint, real gateway session, real
executor) with an in-memory exporter, and checks that every span of the run
shares one trace rooted at the ``invoke_agent`` span, with the same span shapes
(``invoke_agent``, ``chat``, ``execute_tool``, ``gateway``, ``sandbox``, the verify
``gate``) for the verified loop and Deep Agents (LOCUS-361). A second test
drives :class:`RunController` (the Deep Agents path) from a framework thread
that has lost the run's context.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from locus_runtime import gateway as gw
from locus_runtime import telemetry
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.runtime_controller import RunController
from locus_runtime.harness.runtimes import VERIFIED_LOOP
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.verified_loop import Blocker
from locus_runtime.harness.workspace import Workspace
from locus_runtime.telemetry import semconv as sc
from locus_runtime.telemetry.contract import TelemetrySettings
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tool_response
from tests.harness.test_runtime_contract import (
    PROFILE,
    RUNTIMES,
    ScriptedEndpoint,
    _tests_envelope,
    fix_step,
    gated_client,
    plan_step,
    run_task,
)
from tests.harness.test_swe_agent_e2e import _make_repo

pytestmark = [requires_bash, requires_git]


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


def _op(span: ReadableSpan) -> str:
    return str((span.attributes or {}).get(sc.GEN_AI_OPERATION_NAME) or "")


@pytest.mark.parametrize("runtime_name", RUNTIMES)
def test_one_run_is_one_trace(
    runtime_name: str,
    memory: InMemorySpanExporter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _make_repo(tmp_path)
    run = run_task(
        runtime_name,
        tmp_path,
        [
            plan_step(runtime_name),
            tool_response("v", "str_replace_editor", command="view", path="mathlib/core.py"),
            tool_response("b", "execute_bash", command="echo probe"),
            fix_step(),
            tool_response("t", "run_tests"),
            tool_response("s", "submit", answer="fixed add"),
        ],
        envelope=_tests_envelope(),
        monkeypatch=monkeypatch,
    )
    assert run.result.end_state == "done"
    spans = list(memory.get_finished_spans())
    run_spans = [s for s in spans if (s.attributes or {}).get(sc.LOCUS_RUN_ID) == "run-contract"]
    (root,) = [s for s in spans if _op(s) == sc.OP_INVOKE_AGENT]
    trace_id = root.context.trace_id
    assert root.parent is None
    assert {s.context.trace_id for s in run_spans} == {trace_id}

    ops = [_op(s) for s in run_spans]
    for operation in (
        sc.OP_CHAT,
        sc.OP_EXECUTE_TOOL,
        sc.OP_GATEWAY,
        sc.OP_SANDBOX_EXEC,
        sc.OP_GATE,
    ):
        assert operation in ops, operation
    assert ops.count(sc.OP_CHAT) == run.result.usage["model_calls"]

    by_id = {s.context.span_id: s for s in spans}

    def parent_op(span: ReadableSpan) -> str:
        return _op(by_id[span.parent.span_id]) if span.parent is not None else ""

    # Model calls hang off the run; their gateway model_call decisions off the call.
    for chat in (s for s in run_spans if _op(s) == sc.OP_CHAT):
        assert parent_op(chat) in {sc.OP_INVOKE_AGENT, sc.OP_GATE}
    gateway_parents = {parent_op(s) for s in run_spans if _op(s) == sc.OP_GATEWAY}
    assert {sc.OP_CHAT, sc.OP_EXECUTE_TOOL} <= gateway_parents
    sandbox_parents = {parent_op(s) for s in run_spans if _op(s) == sc.OP_SANDBOX_EXEC}
    assert sc.OP_EXECUTE_TOOL in sandbox_parents

    root_attrs = dict(root.attributes or {})
    assert root_attrs[sc.LOCUS_RUNTIME] == runtime_name
    assert root_attrs[sc.LOCUS_END_STATE] == "done" and root_attrs[sc.LOCUS_VERIFIED] is True
    (gate,) = [s for s in run_spans if _op(s) == sc.OP_GATE]
    (score,) = [e for e in gate.events if e.name == sc.GEN_AI_EVALUATION_EVENT]
    assert dict(score.attributes or {})[sc.GEN_AI_EVALUATION_SCORE_LABEL] == "pass"

    # The same trace, read back from the local store.
    store = telemetry.local_store()
    assert store is not None
    view = store.trace("run-contract")
    assert view is not None and len(view.trace_ids) == 1 and len(view.roots) == 1
    assert [s.label for s in view.scores] == ["pass"]


def test_run_controller_resumes_the_run_trace_from_another_thread(
    memory: InMemorySpanExporter, tmp_path: Path
) -> None:
    _make_repo(tmp_path)
    envelope = _tests_envelope()
    gateway = gw.Gateway(FakeEngine(), lambda _record: None)
    session = gateway.open_session(
        run_id="run-da",
        principal="tester",
        engine="contract",
        capabilities=envelope.gateway_capabilities(),
    )
    toolset = CodingToolset(
        workspace=Workspace(
            run_id="run-da", executor=LocalDirectExecutor(tmp_path, gateway_session=session)
        )
    )
    endpoint = ScriptedEndpoint([plan_step(VERIFIED_LOOP)])
    controller = RunController(
        client=gated_client(session, endpoint),
        toolset=toolset,
        profile=PROFILE,
        envelope=envelope,
        run_id="run-da",
        plan_mode="optional",
        runtime_name="deep-agents",
        provider_retry_backoff=0,
    )

    def body(c: RunController) -> None:
        def framework_thread() -> None:  # a fresh context, like a graph worker
            c.model_turn(list(c._st.messages), c._tool_schemas())  # noqa: SLF001
            c.tool_call("v", "str_replace_editor", {"command": "view", "path": "mathlib/core.py"})

        worker = threading.Thread(target=framework_thread)
        worker.start()
        worker.join()
        c.end_blocked(Blocker(kind="agent", detail="test", unblock="none"))

    result = controller.drive(body)
    assert result.end_state.value == "blocked"
    spans = list(memory.get_finished_spans())
    (root,) = [s for s in spans if _op(s) == sc.OP_INVOKE_AGENT]
    assert dict(root.attributes or {})[sc.LOCUS_RUNTIME] == "deep-agents"
    threaded = [s for s in spans if _op(s) in {sc.OP_CHAT, sc.OP_EXECUTE_TOOL}]
    assert {_op(s) for s in threaded} == {sc.OP_CHAT, sc.OP_EXECUTE_TOOL}
    assert {s.context.trace_id for s in threaded} == {root.context.trace_id}
    assert all((s.attributes or {}).get(sc.LOCUS_RUN_ID) == "run-da" for s in threaded)
