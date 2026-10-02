from __future__ import annotations

import pytest

from frontier_runtime.cognitive import (
    AssemblyDefinition,
    AssemblyRuntime,
    CognitiveMessage,
    ConsensusEngine,
    EvidenceColumn,
    GoalColumn,
    SynthesisColumn,
    run_mvp_cognitive_assembly,
)


def test_goal_column_tracks_intent_and_confidence() -> None:
    column = GoalColumn()

    state = column.observe(
        assembly_id="assembly-1",
        config={
            "intent": "Prepare a customer-ready remediation plan",
            "success_criteria": ["Actionable steps", "Named owners"],
            "constraints": ["No destructive actions"],
        },
        run_input={"message": "fallback"},
    )

    assert state.belief_set["intent"] == "Prepare a customer-ready remediation plan"
    assert state.confidence >= 0.8
    assert column.emit_message(state).message_type == "belief_update"


def test_evidence_column_reports_missing_required_evidence() -> None:
    column = EvidenceColumn()

    state = column.observe(
        assembly_id="assembly-1",
        config={
            "required_evidence": ["SOC 2 report", "customer impact"],
            "allowed_sources": ["kb://default"],
        },
        run_input={"message": "Need a remediation plan"},
        incoming_context=[{"evidence": ["SOC 2 report was reviewed"]}],
    )

    blockers = state.belief_set["blockers"]
    assert blockers == ["Missing required evidence: customer impact"]
    assert state.evidence_refs == ["evidence:1", "evidence:2"]
    assert column.emit_message(state).message_type == "evidence_claim"


def test_consensus_engine_escalates_low_confidence_or_missing_evidence() -> None:
    goal_state = GoalColumn().observe(
        assembly_id="assembly-2",
        config={"intent": "Ship the change", "success_criteria": ["No regressions"]},
        run_input={"message": "ship it"},
    )
    evidence_state = EvidenceColumn().observe(
        assembly_id="assembly-2",
        config={"required_evidence": ["test pass"]},
        run_input={"message": "ship it"},
        incoming_context=[{"evidence": ["deployment notes only"]}],
    )
    synthesis_state = SynthesisColumn().observe(
        assembly_id="assembly-2",
        goal_state=goal_state,
        evidence_state=evidence_state,
    )

    commitment = ConsensusEngine().fuse(
        goal_state=goal_state,
        evidence_state=evidence_state,
        synthesis_state=synthesis_state,
        confidence_threshold=0.75,
    )

    assert commitment.blockers == ["Missing required evidence: test pass"]
    assert "evidence" in commitment.dissenting_columns
    assert any("Escalate" in action for action in commitment.next_actions)


def test_cognitive_message_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="Unsupported cognitive message type"):
        CognitiveMessage(
            message_type="prediction_branch",  # type: ignore[arg-type]
            column_id="goal",
            assembly_id="assembly-1",
            payload={},
        )


def test_mvp_assembly_builds_commitment() -> None:
    result = run_mvp_cognitive_assembly(
        assembly_id="assembly-3",
        goal_config={
            "intent": "Prepare a constrained release recommendation",
            "success_criteria": ["Explain risk", "Give next step"],
        },
        evidence_config={
            "required_evidence": ["test results"],
            "allowed_sources": ["kb://release"],
        },
        run_input={"message": "Recommend whether we should release"},
        incoming_context=[{"evidence": ["test results show no critical regressions"]}],
        confidence_threshold=0.55,
    )

    assert result["assembly"]["assembly_id"] == "assembly-3"
    assert result["commitment"]["confidence"] >= 0.55
    assert result["commitment"]["blockers"] == []
    assert "Proceed" in result["commitment"]["decision"]
    assert len(result["messages"]) == 3


def test_assembly_runtime_uses_declared_definition() -> None:
    runtime = AssemblyRuntime(
        AssemblyDefinition(
            assembly_id="assembly-4",
            columns=["goal", "evidence", "synthesis"],
            consensus_policy="weighted-support",
        )
    )

    result = runtime.run(
        goal_config={"intent": "Document the decision"},
        evidence_config={},
        run_input={"message": "Document the decision"},
        incoming_context=[{"response": "Operator approved the approach."}],
    )

    assert result["assembly"]["consensus_policy"] == "weighted-support"
    assert result["states"]["goal"]["column_id"] == "goal"
    assert result["states"]["synthesis"]["column_id"] == "synthesis"
