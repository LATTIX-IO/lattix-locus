"""LOCUS-472: mandatory *real* native Deep Agents + official Codex acceptance.

Runs against actual local Ollama and actual pinned Codex CLI image under the
kernel-constrained Linux StageDockerBroker. Zero skipped tests are release
acceptance. No synthetic agent responses or unmediated file publication.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.harness.codex_cli_worker import ENTRYPOINT
from locus_runtime.pod_artifact_escrow import DetachedArtifactEscrow
from locus_runtime.pod_reservations import AdmissionDenied, PodReservationLedger
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_stage_docker import StageDockerBroker
from locus_runtime.pod_two_specialist import execute_two_specialists, _PINS
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from locus_runtime.task_scheduler import DurableTaskScheduler
from tests.gateway_support import FakeEngine
from tests.unit.stage_guardian_support import managed_guardian_for_test
from tests.unit.test_detached_artifact_escrow import _operator, _human_approve

REAL = pytest.mark.skipif(
    os.getenv("LOCUS_REAL_TWO_SPECIALIST_ACCEPTANCE") != "1"
    or sys.platform != "linux" or os.geteuid() == 0,
    reason="explicit nonroot Linux with real Ollama + official Codex image required",
)


def _rig(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.txt").write_text("This original workspace is immutable.", encoding="utf-8")
    ledger = PodReservationLedger(tmp_path / "runs.db")
    gateway = Gateway(FakeEngine(), lambda _: None)
    principal = "operator"
    parent = gateway.open_session(
        run_id="two-specialist-parent", principal=principal, engine="supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"run_command", "model:ollama", "read_file"}),
            allowed_executables=(ENTRYPOINT, "/usr/bin/python3"),
            allowed_egress_hosts=("127.0.0.1",),
            read_roots=(str(source), str(tmp_path)),
            write_roots=(),
            max_actions=38, max_tool_calls=38,
            max_risk_class=RiskClass.R2,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=72000,
                duration_used_seconds=0, max_duration_seconds=150,
                cost_used_usd=0, max_cost_usd=0,
            ),
        ),
    )
    plan = compile_task_plan(
        {"contract_version": "1.0", "nodes": [
            {
                "node_id": "native", "depends_on": [],
                "capabilities": ["run_command", "model:ollama", "read_file"],
                "risk_ceiling": "R2",
                "budget": {
                    "actions": 14, "tokens": 30000, "wall_seconds": 60,
                    "spend_microunits": 0,
                },
                "deliverables": ["independently sealed draft.txt"],
                "acceptance_criteria": ["genuine Locus native Deep Agents inference"],
            },
            {
                "node_id": "codex", "depends_on": ["native"],
                "capabilities": ["run_command", "model:ollama"],
                "risk_ceiling": "R2",
                "budget": {
                    "actions": 18, "tokens": 35000, "wall_seconds": 64,
                    "spend_microunits": 0,
                },
                "deliverables": ["independently sealed answer.txt"],
                "acceptance_criteria": ["genuine Codex critique from verified native draft"],
            },
        ]},
        intent=TaskIntent(
            "Produce and refine a technical brief", "professional", principal,
            ("no source workspace writes",),
        ),
        parent_authority=TaskAuthority(
            frozenset({"run_command", "model:ollama", "read_file"}), "R2",
            TaskBudget(38, 72000, 150, 0),
        ),
    )
    scheduler = DurableTaskScheduler(ledger)
    scheduler.bind(plan, parent, max_tool_calls=38)
    scheduler.bind_engine_pins(plan, parent, _PINS)
    receipts = PodActionReceipts(ledger, supervisor=parent)
    stages = PodStageBindings(ledger, receipts)
    broker = StageDockerBroker(
        stages, receipts, image=os.environ["LOCUS_CODEX_ACCEPTANCE_IMAGE"],
    )
    return source, plan, parent, scheduler, broker, stages


@REAL
def test_genuine_native_and_codex_handoff_only_human_can_promote(tmp_path):
    source, plan, parent, scheduler, broker, stages = _rig(tmp_path)
    original = (source / "source.txt").read_bytes()
    with managed_guardian_for_test(tmp_path / "runs.db"):
        pending = execute_two_specialists(
            plan, parent, scheduler, broker,
            source=source, staging_parent=tmp_path,
            task="Explain Zero Trust least privilege for delegated agents in 3 factual sentences.",
            rubric="Ensure clarity, factual correctness, and distinctions between parent and child authority.",
            model="llama3.2:1b",
        )
    assert pending.native_model_calls >= 1
    assert pending.codex_model_calls >= 1
    assert pending.native_escrow.child_run_id != pending.revised_escrow.child_run_id
    assert pending.native_escrow.manifest_sha256 != pending.revised_escrow.manifest_sha256
    assert len(pending.handoff_fingerprint) == 64
    assert (source / "source.txt").read_bytes() == original
    assert not (source / "draft.txt").exists()
    assert not (source / "answer.txt").exists()
    escrow = DetachedArtifactEscrow(stages)
    reviewer = _operator(parent.gateway, source)
    preview = pending.review(escrow, reviewer)
    assert preview.after_sha256 == pending.revised_escrow.payload_sha256
    with pytest.raises(AdmissionDenied):
        pending.publish(escrow, reviewer)
    assert not (source / "answer.txt").exists()
    _human_approve(reviewer, source, pending.revised_escrow)
    promoted = pending.publish(escrow, reviewer)
    assert promoted.state == "promoted"
    assert hashlib.sha256((source / "answer.txt").read_bytes()).hexdigest() == (
        pending.revised_escrow.payload_sha256
    )
    assert (source / "source.txt").read_bytes() == original


@REAL
def test_escalated_or_wrong_engine_plan_refused_before_any_child(tmp_path):
    source, plan, parent, scheduler, broker, stages = _rig(tmp_path)
    # No direct engine substitution or replay of a branch-supplied identity.
    with pytest.raises(AdmissionDenied):
        scheduler.claim_next(
            plan, parent,
            expected_engine_pins=(("codex", "untrusted/engine"), ("native", "untrusted/engine")),
        )
    assert not (source / "answer.txt").exists()
