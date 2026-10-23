"""Real Linux/Docker + official Codex CLI + local Ollama acceptance (LOCUS-470/B).

NOT a synthetic mock. This test is skipped without an explicitly configured
digest-pinned Codex image and a running loopback Ollama instance with a genuine
model. The dedicated GitHub workflow MUST run it without skipping.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from pathlib import Path

import pytest

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.harness.codex_cli_worker import CodexCliWorker, ENGINE_ID, ENTRYPOINT
from locus_runtime.pod_artifact_escrow import DetachedArtifactEscrow
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger, ResourceBudget
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_stage_docker import StageDockerBroker
from locus_runtime.pod_workspace_staging import staged_workspace
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.gateway_support import FakeEngine
from tests.unit.stage_guardian_support import managed_guardian_for_test

REAL = pytest.mark.skipif(
    os.getenv("LOCUS_REAL_CODEX_ACCEPTANCE") != "1"
    or sys.platform != "linux" or os.geteuid() == 0,
    reason="explicit nonroot Linux/Ollama/Codex acceptance only",
)


def real_rig(tmp_path: Path):
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "source.txt").write_text("This source must never be modified", encoding="utf-8")
    ledger = PodReservationLedger(tmp_path / "runs.db")
    gateway = Gateway(FakeEngine(), lambda _: None)
    parent = gateway.open_session(
        run_id="codex-parent", principal="operator", engine="supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"run_command", "model:ollama"}),
            allowed_executables=(ENTRYPOINT,),
            allowed_egress_hosts=("127.0.0.1",),
            read_roots=(str(source),),
            max_actions=22, max_tool_calls=22,
            max_risk_class=RiskClass.R2,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=50000,
                duration_used_seconds=0, max_duration_seconds=120,
                cost_used_usd=0, max_cost_usd=0,
            ),
        ),
    )
    plan = compile_task_plan(
        {"contract_version": "1.0", "nodes": [{
            "node_id": "codex", "depends_on": [],
            "capabilities": ["run_command", "model:ollama"],
            "risk_ceiling": "R2",
            "budget": {
                "actions": 18, "tokens": 35000, "wall_seconds": 65,
                "spend_microunits": 0,
            },
            "deliverables": ["one bounded answer.txt artifact"],
            "acceptance_criteria": ["genuine model response and source integrity"],
        }]},
        intent=TaskIntent(
            "Write a short answer from a local model", "professional", "operator",
            ("never modify original source",),
        ),
        parent_authority=TaskAuthority(
            frozenset({"run_command", "model:ollama"}), "R2",
            TaskBudget(22, 50000, 120, 0),
        ),
    )
    scheduler = DurableTaskScheduler(ledger)
    scheduler.bind(plan, parent, max_tool_calls=22)
    scheduler.bind_engine_pins(plan, parent, (("codex", ENGINE_ID),))
    claim = scheduler.claim_next(
        plan, parent, expected_engine_pins=(("codex", ENGINE_ID),),
    )
    assert claim is not None
    scheduler.reserve(
        plan, parent, claim, budget=ResourceBudget(18, 18, 35000, 64, 0),
        lease_seconds=100, pod_revision="codex-stdio-v1",
    )
    lease = scheduler.admit(plan, parent, claim, engine=ENGINE_ID)
    receipts = PodActionReceipts(ledger, supervisor=parent)
    stages = PodStageBindings(ledger, receipts)
    broker = StageDockerBroker(
        stages, receipts, image=os.environ["LOCUS_CODEX_ACCEPTANCE_IMAGE"],
    )
    return source, lease, parent, receipts, stages, broker


@REAL
def test_real_codex_calls_ollama_and_escrows_single_artifact(tmp_path):
    source, lease, _, receipts, stages, broker = real_rig(tmp_path)
    before = (source / "source.txt").read_bytes()
    with staged_workspace(source, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ):
        bound = stages.bind(lease, snapshot)
        result = CodexCliWorker(broker, model="llama3.2:1b").run(
            lease, bound.stage_id,
            prompt="In a single short sentence, explain why the sea is blue. Do not use tools.",
            output_name="answer.txt", idempotency_key="real-codex-once",
            timeout_seconds=58,
        )
        assert result.model_calls >= 1, "real Ollama model must be called"
        assert result.reserved_tokens > 0
        output = (snapshot.staged / "answer.txt").read_bytes()
        assert 0 < len(output) < 65536
        assert hashlib.sha256(output).hexdigest() == result.output_sha256
        assert (source / "source.txt").read_bytes() == before
        assert not (source / "answer.txt").exists()
        verified = DetachedArtifactEscrow(stages).seal(
            lease, bound.stage_id, allowed_output_paths=frozenset({"answer.txt"}),
        )
        assert verified.escrow_id
        # No approval and no promotion: source still byte-identical.
        assert not (source / "answer.txt").exists()
        with pytest.raises(AdmissionDenied):
            CodexCliWorker(broker, model="llama3.2:1b").run(
                lease, bound.stage_id, prompt="repeat",
                output_name="other.txt", idempotency_key="real-codex-once",
            )
        assert (source / "source.txt").read_bytes() == before


@REAL
def test_cancelled_codex_pod_does_not_prepare_dispatch(tmp_path):
    source, lease, _, receipts, stages, broker = real_rig(tmp_path)
    with staged_workspace(source, staging_parent=tmp_path) as snapshot, (
        managed_guardian_for_test(tmp_path / "runs.db")
    ):
        bound = stages.bind(lease, snapshot)
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(Exception):
            CodexCliWorker(broker, model="llama3.2:1b").run(
                lease, bound.stage_id, prompt="test",
                output_name="answer.txt", idempotency_key="cancel-before-dispatch",
                cancel=cancel,
            )
        with receipts.reservations._connect() as db:
            assert db.execute("SELECT count(*) FROM pod_action_receipt").fetchone()[0] == 0
    assert not (source / "answer.txt").exists()
