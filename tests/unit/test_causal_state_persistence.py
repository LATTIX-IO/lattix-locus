from pathlib import Path

import pytest

from frontier_runtime.cognition import (
    AssemblyDefinition,
    AssemblyState,
    BeliefRecord,
    BeliefValidationError,
    ColumnKind,
    ColumnState,
    Commitment,
)
from frontier_runtime.persistence import (
    load_assembly_causal_state,
    load_causal_state,
    persist_assembly_state,
    persist_column_state,
    record_assembly_outcome,
    reset_shared_state_backend,
)


def _configured_state_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FRONTIER_STATE_STORE", str(tmp_path / "runtime-state.json"))
    reset_shared_state_backend()


def test_persist_column_state_records_snapshot_and_histories(monkeypatch, tmp_path: Path) -> None:
    _configured_state_store(monkeypatch, tmp_path)

    state = ColumnState(
        column_id="evidence-1",
        assembly_id="assembly-1",
        kind=ColumnKind.EVIDENCE,
        confidence=0.2,
    ).with_beliefs(
        (
            BeliefRecord(
                key="source_doc",
                value="doc-17",
                confidence=0.8,
                evidence_refs=("doc-17",),
                rationale="Primary evidence source",
            ),
        )
    )

    persisted = persist_column_state(state, cause={"message_id": "msg-1"})
    restored = load_assembly_causal_state("assembly-1")
    causal_state = load_causal_state()

    assert persisted["columns"]["evidence-1"]["kind"] == "evidence"
    assert persisted["columns"]["evidence-1"]["belief_set"][0]["key"] == "source_doc"
    assert persisted["belief_history"]["evidence-1"][0]["cause"] == {"message_id": "msg-1"}
    assert persisted["confidence_history"]["evidence-1"][0]["confidence"] == 0.8
    assert restored["columns"]["evidence-1"]["evidence_refs"] == ["doc-17"]
    assert "assembly-1" in causal_state["assemblies"]


def test_persist_column_state_rejects_unsafe_mutated_belief_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    _configured_state_store(monkeypatch, tmp_path)
    belief = BeliefRecord(key="source_doc", value="doc-17", confidence=0.8)
    object.__setattr__(belief, "metadata", {"bad": object()})
    state = ColumnState(
        column_id="evidence-unsafe",
        assembly_id="assembly-unsafe",
        kind=ColumnKind.EVIDENCE,
    ).with_beliefs((belief,))

    with pytest.raises(BeliefValidationError, match="metadata must be JSON safe"):
        persist_column_state(state, cause={"message_id": "msg-unsafe"})

    assert load_assembly_causal_state("assembly-unsafe")["columns"] == {}


def test_persist_assembly_state_appends_confidence_history_and_commitment_outcome(
    monkeypatch, tmp_path: Path
) -> None:
    _configured_state_store(monkeypatch, tmp_path)

    initial_state = ColumnState(
        column_id="synthesis-1",
        assembly_id="assembly-2",
        kind=ColumnKind.SYNTHESIS,
    ).with_beliefs((BeliefRecord(key="decision", value="revise", confidence=0.4),))
    persist_column_state(initial_state, cause={"phase": "draft"})

    updated_state = initial_state.with_beliefs(
        (BeliefRecord(key="decision", value="approve", confidence=0.9),)
    )
    commitment = Commitment(
        decision="approve",
        confidence=0.91,
        supporting_columns=("evidence-1", "evaluation-1", "synthesis-1"),
        dissenting_columns=(),
        blockers=(),
        next_actions=("publish",),
    )
    assembly_state = (
        AssemblyState(
            definition=AssemblyDefinition(
                assembly_id="assembly-2",
                columns=("evidence-1", "evaluation-1", "synthesis-1"),
            )
        )
        .register_column_state(
            ColumnState(
                column_id="evidence-1",
                assembly_id="assembly-2",
                kind=ColumnKind.EVIDENCE,
                confidence=0.88,
            )
        )
        .register_column_state(
            ColumnState(
                column_id="evaluation-1",
                assembly_id="assembly-2",
                kind=ColumnKind.EVALUATION,
                confidence=0.86,
            )
        )
        .register_column_state(updated_state)
        .with_commitment(commitment)
    )

    persisted = persist_assembly_state(
        assembly_state,
        cause={"phase": "consensus"},
        outcome="committed",
        outcome_metadata={"trigger": "test"},
        recorded_at=999.0,
        tenant_id="tenant-1",
        actor="tester",
        task="Approve deployment",
    )

    confidence_history = persisted["confidence_history"]["synthesis-1"]
    assert persisted["tenant_id"] == "tenant-1"
    assert persisted["actor"] == "tester"
    assert persisted["task"] == "Approve deployment"
    assert len(confidence_history) == 2
    assert [item["cause"]["phase"] for item in confidence_history] == ["draft", "consensus"]
    assert persisted["columns"]["synthesis-1"]["belief_set"][0]["value"] == "approve"
    assert persisted["outcomes"][-1]["outcome"] == "committed"
    assert persisted["outcomes"][-1]["commitment"]["decision"] == "approve"
    assert persisted["outcomes"][-1]["metadata"] == {"trigger": "test"}


def test_causal_history_limits_keep_recent_entries(monkeypatch, tmp_path: Path) -> None:
    _configured_state_store(monkeypatch, tmp_path)
    monkeypatch.setenv("FRONTIER_CAUSAL_BELIEF_HISTORY_LIMIT", "2")
    monkeypatch.setenv("FRONTIER_CAUSAL_CONFIDENCE_HISTORY_LIMIT", "2")

    for confidence in (0.2, 0.5, 0.9):
        state = ColumnState(
            column_id="evaluation-1",
            assembly_id="assembly-3",
            kind=ColumnKind.EVALUATION,
        ).with_beliefs((BeliefRecord(key="score", value=confidence, confidence=confidence),))
        persist_column_state(state, cause={"confidence": confidence})

    restored = load_assembly_causal_state("assembly-3")

    belief_history = restored["belief_history"]["evaluation-1"]
    confidence_history = restored["confidence_history"]["evaluation-1"]
    assert len(belief_history) == 2
    assert len(confidence_history) == 2
    assert [item["confidence"] for item in confidence_history] == [0.5, 0.9]


def test_record_assembly_outcome_supports_outcome_only_entries(monkeypatch, tmp_path: Path) -> None:
    _configured_state_store(monkeypatch, tmp_path)

    persisted = record_assembly_outcome(
        "assembly-4",
        outcome="escalated",
        metadata={"reason": "confidence_below_threshold"},
        recorded_at=321.0,
        tenant_id="tenant-2",
        actor="reviewer",
        task="Review risk",
    )

    assert persisted["tenant_id"] == "tenant-2"
    assert persisted["actor"] == "reviewer"
    assert persisted["task"] == "Review risk"
    assert persisted["outcomes"] == [
        {
            "at": 321.0,
            "metadata": {"reason": "confidence_below_threshold"},
            "outcome": "escalated",
        }
    ]
