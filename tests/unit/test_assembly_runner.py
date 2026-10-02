from pathlib import Path

import pytest

from frontier_runtime.assembly_runner import (
    AssemblyRunner,
    AssemblyRunRequest,
    ColumnRuntimeBudgetCounters,
    ColumnRuntimeGateError,
    ColumnRuntimeGateRequest,
    admit_column_runtime_step,
)
from frontier_runtime.cognition import (
    AssemblyAdmissionPolicy,
    AssemblyBudget,
    AssemblyDefinition,
    AssemblyDefinitionAdmissionError,
    ColumnCapability,
    ColumnKind,
)
from frontier_runtime.persistence import load_assembly_causal_state, reset_shared_state_backend


def test_assembly_runner_persists_committed_assembly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    result = AssemblyRunner().run(
        AssemblyRunRequest(
            assembly_id="assembly:runner-1",
            task="Summarize operational risk",
            actor="tester",
            tenant_id="tenant-1",
            context={"document": "SOC2 evidence"},
        )
    )
    restored = load_assembly_causal_state("assembly:runner-1")

    assert result.outcome == "committed"
    assert result.state.commitment is not None
    assert result.state.commitment.is_ready is True
    assert restored["tenant_id"] == "tenant-1"
    assert restored["actor"] == "tester"
    assert restored["task"] == "Summarize operational risk"
    assert set(restored["columns"]) == {"goal", "evidence", "evaluation", "synthesis"}
    assert restored["outcomes"][-1]["outcome"] == "committed"
    assert restored["outcomes"][-1]["metadata"]["tenant_id"] == "tenant-1"


def test_assembly_runner_escalates_low_context_assembly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    result = AssemblyRunner().run(
        AssemblyRunRequest(
            assembly_id="assembly:runner-2",
            task="Approve risky deployment",
            actor="tester",
            confidence_threshold=0.8,
        )
    )

    assert result.outcome == "escalated"
    assert result.state.commitment is not None
    assert "confidence_below_threshold" in result.state.commitment.blockers
    assert result.persisted["outcomes"][-1]["outcome"] == "escalated"


def test_assembly_runner_blocks_high_risk_commitment_without_human_approval(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()
    audit_events: list[tuple[str, str, dict[str, object]]] = []

    result = AssemblyRunner().run(
        AssemblyRunRequest(
            assembly_id="assembly:high-risk-blocked",
            task="Approve production deployment",
            actor="tester",
            context={"document": "SOC2 evidence"},
            admission_policy=AssemblyAdmissionPolicy(high_risk_commitment_decisions=("commit",)),
            audit_callback=lambda event, _actor, outcome, metadata: audit_events.append(
                (event, outcome, metadata)
            ),
        )
    )

    assert result.outcome == "escalated"
    assert result.state.commitment is not None
    assert result.state.commitment.blockers == ("human_approval_required",)
    assert result.persisted["outcomes"][-1]["outcome"] == "escalated"
    assert ("cognition.commitment.gate", "blocked") in {
        (event, outcome) for event, outcome, _metadata in audit_events
    }


def test_assembly_runner_allows_high_risk_commitment_with_human_approval(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    result = AssemblyRunner().run(
        AssemblyRunRequest(
            assembly_id="assembly:high-risk-approved",
            task="Approve production deployment",
            actor="tester",
            context={"document": "SOC2 evidence"},
            admission_policy=AssemblyAdmissionPolicy(high_risk_commitment_decisions=("commit",)),
            human_approval_granted=True,
        )
    )

    assert result.outcome == "committed"
    assert result.state.commitment is not None
    assert result.state.commitment.is_ready is True


def test_assembly_definition_admission_rejects_missing_required_columns(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    with pytest.raises(AssemblyDefinitionAdmissionError, match="missing required column kinds"):
        AssemblyRunner().run(
            AssemblyRunRequest(
                assembly_id="assembly:missing-required",
                task="Summarize operational risk",
                assembly_definition=AssemblyDefinition(
                    assembly_id="assembly:missing-required",
                    columns=("goal", "evidence", "evaluation"),
                ),
            )
        )

    assert load_assembly_causal_state("assembly:missing-required")["outcomes"] == []


def test_assembly_definition_admission_rejects_too_many_columns(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    with pytest.raises(AssemblyDefinitionAdmissionError, match="too many columns"):
        AssemblyRunner().run(
            AssemblyRunRequest(
                assembly_id="assembly:too-many-columns",
                task="Summarize operational risk",
                assembly_definition=AssemblyDefinition(
                    assembly_id="assembly:too-many-columns",
                    columns=("goal", "evidence", "evaluation", "synthesis", "uncertainty"),
                    budget_constraints=AssemblyBudget(max_columns=5),
                ),
                admission_policy=AssemblyAdmissionPolicy(max_columns=4),
            )
        )

    assert load_assembly_causal_state("assembly:too-many-columns")["columns"] == {}


def test_assembly_definition_admission_rejects_unsupported_column_kind(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    with pytest.raises(AssemblyDefinitionAdmissionError, match="Unsupported column kind"):
        AssemblyRunner().run(
            AssemblyRunRequest(
                assembly_id="assembly:unsupported-kind",
                task="Summarize operational risk",
                assembly_definition=AssemblyDefinition(
                    assembly_id="assembly:unsupported-kind",
                    columns=("goal", "evidence", "evaluation", "synthesis", "mystery"),
                    column_kinds={"mystery": "unsupported"},
                ),
            )
        )

    assert load_assembly_causal_state("assembly:unsupported-kind")["columns"] == {}


def test_assembly_definition_admission_accepts_valid_minimum_assembly(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()

    result = AssemblyRunner().run(
        AssemblyRunRequest(
            assembly_id="assembly:valid-minimum",
            task="Summarize operational risk",
            context={"document": "SOC2 evidence"},
            assembly_definition=AssemblyDefinition(
                assembly_id="assembly:valid-minimum",
                columns=("goal-1", "evidence-1", "evaluation-1", "synthesis-1"),
                column_kinds={
                    "goal-1": "goal",
                    "evidence-1": "evidence",
                    "evaluation-1": "evaluation",
                    "synthesis-1": "synthesis",
                },
            ),
        )
    )

    restored = load_assembly_causal_state("assembly:valid-minimum")
    assert result.outcome == "committed"
    assert set(restored["columns"]) == {"goal-1", "evidence-1", "evaluation-1", "synthesis-1"}


def test_column_runtime_gate_blocks_disallowed_provider_model() -> None:
    with pytest.raises(ColumnRuntimeGateError, match="provider is not allowed"):
        admit_column_runtime_step(
            ColumnRuntimeGateRequest(
                assembly_id="assembly:runtime-provider",
                column_id="goal",
                column_kind=ColumnKind.GOAL,
                capability=ColumnCapability.READ_INPUT,
                actor="tester",
                tenant_id="tenant-1",
                auth_context={"authenticated": True, "tenant": "tenant-1"},
                admission_policy=AssemblyAdmissionPolicy(
                    allowed_providers=("allowed-provider",),
                    allowed_models=("allowed-model",),
                ),
                provider="blocked-provider",
                model="allowed-model",
                budget_counters=ColumnRuntimeBudgetCounters(),
            )
        )

    with pytest.raises(ColumnRuntimeGateError, match="model is not allowed"):
        admit_column_runtime_step(
            ColumnRuntimeGateRequest(
                assembly_id="assembly:runtime-model",
                column_id="goal",
                column_kind=ColumnKind.GOAL,
                capability=ColumnCapability.READ_INPUT,
                actor="tester",
                tenant_id="tenant-1",
                auth_context={"authenticated": True, "tenant": "tenant-1"},
                admission_policy=AssemblyAdmissionPolicy(
                    allowed_providers=("allowed-provider",),
                    allowed_models=("allowed-model",),
                ),
                provider="allowed-provider",
                model="blocked-model",
                budget_counters=ColumnRuntimeBudgetCounters(),
            )
        )
