from __future__ import annotations

import pytest

from frontier_runtime.cognition import (
    AssemblyBudget,
    AssemblyDefinition,
    AssemblyState,
    BeliefRecord,
    BeliefValidationError,
    ColumnCapability,
    ColumnCapabilityError,
    ColumnKind,
    ColumnMessage,
    ColumnRegistry,
    ColumnState,
    ColumnVote,
    Commitment,
    CommitmentValidationContext,
    CommitmentValidationError,
    ConsensusPolicy,
    InferenceMode,
    MessageType,
    build_commitment,
    require_column_capability,
)


def _commitment_gate_state() -> AssemblyState:
    definition = AssemblyDefinition(
        assembly_id="assembly-1",
        columns=("goal-1", "evidence-1", "evaluation-1", "synthesis-1"),
    )
    state = AssemblyState(definition=definition)
    for column_id, kind, confidence in (
        ("goal-1", ColumnKind.GOAL, 0.9),
        ("evidence-1", ColumnKind.EVIDENCE, 0.85),
        ("evaluation-1", ColumnKind.EVALUATION, 0.8),
        ("synthesis-1", ColumnKind.SYNTHESIS, 0.82),
    ):
        state = state.register_column_state(
            ColumnState(
                column_id=column_id, assembly_id="assembly-1", kind=kind, confidence=confidence
            )
        )
    return state


def _commitment_votes(*, confidence: float = 0.8, veto: bool = False) -> tuple[ColumnVote, ...]:
    return (
        ColumnVote(column_id="evidence-1", decision="approve", confidence=confidence),
        ColumnVote(
            column_id="evaluation-1",
            decision="approve",
            confidence=confidence,
            veto=veto,
            blockers=("policy_violation",) if veto else (),
        ),
        ColumnVote(column_id="synthesis-1", decision="approve", confidence=confidence),
    )


class StubColumn:
    def __init__(self, column_id: str, kind: ColumnKind) -> None:
        self.column_id = column_id
        self.kind = kind

    def observe(self, observation: dict[str, object]) -> ColumnState:
        belief = BeliefRecord(key="observation", value=observation, confidence=0.7)
        return ColumnState(
            column_id=self.column_id,
            assembly_id="assembly-1",
            kind=self.kind,
            belief_set=(belief,),
            confidence=belief.confidence,
        )

    def update_belief(self, state: ColumnState, message: ColumnMessage) -> ColumnState:
        return state

    def predict(self, state: ColumnState) -> BeliefRecord | None:
        return None

    def evaluate(self, state: ColumnState) -> BeliefRecord | None:
        return None

    def emit_message(self, state: ColumnState) -> ColumnMessage | None:
        return None

    def update_confidence(self, state: ColumnState, feedback: dict[str, object]) -> ColumnState:
        return state


def test_belief_and_column_state_clamp_confidence() -> None:
    belief = BeliefRecord(key="risk", value="high", confidence=1.4)
    state = ColumnState(
        column_id="goal-1", assembly_id="assembly-1", kind=ColumnKind.GOAL, confidence=-0.5
    )

    assert belief.confidence == 1.0
    assert state.confidence == 0.0


def test_belief_record_rejects_oversized_value() -> None:
    with pytest.raises(BeliefValidationError, match="value exceeds max size"):
        BeliefRecord(key="large_value", value={"content": "x" * 20_000}, confidence=0.8)


def test_belief_record_rejects_non_finite_confidence() -> None:
    with pytest.raises(ValueError, match="confidence must be finite"):
        BeliefRecord(key="bad_confidence", value="risk", confidence=float("nan"))


def test_belief_record_rejects_too_many_evidence_refs() -> None:
    with pytest.raises(BeliefValidationError, match="too many evidence refs"):
        BeliefRecord(
            key="many_refs",
            value="risk",
            confidence=0.7,
            evidence_refs=tuple(f"doc-{index}" for index in range(33)),
        )


def test_belief_record_rejects_unserializable_metadata() -> None:
    with pytest.raises(BeliefValidationError, match="metadata must be JSON safe"):
        BeliefRecord(
            key="bad_metadata",
            value="risk",
            confidence=0.7,
            metadata={"bad": object()},
        )


def test_belief_record_redacts_sensitive_metadata_hooks() -> None:
    belief = BeliefRecord(
        key="safe_metadata",
        value="risk",
        confidence=0.7,
        metadata={"api_key": "secret-value", "nested": {"refresh_token": "token-value"}},
    )

    assert belief.metadata["api_key"] == "[redacted]"
    assert belief.metadata["nested"]["refresh_token"] == "[redacted]"
    assert belief.metadata["redacted"] is True


def test_column_state_with_beliefs_updates_confidence_and_evidence() -> None:
    state = ColumnState(column_id="evidence-1", assembly_id="assembly-1", kind=ColumnKind.EVIDENCE)

    next_state = state.with_beliefs(
        (
            BeliefRecord(key="source_a", value="doc-1", confidence=0.6, evidence_refs=("doc-1",)),
            BeliefRecord(
                key="source_b", value="doc-2", confidence=0.8, evidence_refs=("doc-2", "doc-1")
            ),
        )
    )

    assert next_state.confidence == 0.7
    assert next_state.evidence_refs == ("doc-1", "doc-2")


def test_column_registry_groups_columns_by_kind() -> None:
    registry = ColumnRegistry()
    goal = StubColumn("goal-1", ColumnKind.GOAL)
    evidence = StubColumn("evidence-1", ColumnKind.EVIDENCE)

    registry.register(goal)
    registry.register(evidence)

    assert registry.get("goal-1") is goal
    assert registry.list_by_kind(ColumnKind.EVIDENCE) == [evidence]


def test_column_registry_rejects_duplicate_column_ids() -> None:
    registry = ColumnRegistry()
    registry.register(StubColumn("goal-1", ColumnKind.GOAL))

    try:
        registry.register(StubColumn("goal-1", ColumnKind.GOAL))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("Expected duplicate column registration to fail")


def test_assembly_state_honors_budget_constraints() -> None:
    definition = AssemblyDefinition(
        assembly_id="assembly-1",
        columns=("goal-1", "evidence-1"),
        consensus_policy=ConsensusPolicy.WEIGHTED,
        inference_mode=InferenceMode.ITERATIVE,
        budget_constraints=AssemblyBudget(max_iterations=2, max_messages=2, max_columns=2),
    )
    state = AssemblyState(definition=definition)
    state = state.register_column_state(
        ColumnState(column_id="goal-1", assembly_id="assembly-1", kind=ColumnKind.GOAL)
    )
    state = state.register_column_state(
        ColumnState(column_id="evidence-1", assembly_id="assembly-1", kind=ColumnKind.EVIDENCE)
    )

    assert state.can_continue() is True

    state = state.register_message().next_iteration()
    assert state.can_continue() is True

    state = state.register_message().next_iteration()
    assert state.can_continue() is False


def test_weighted_commitment_tracks_support_and_dissent() -> None:
    commitment = build_commitment(
        [
            ColumnVote(column_id="goal-1", decision="approve", confidence=0.9),
            ColumnVote(column_id="evidence-1", decision="approve", confidence=0.8),
            ColumnVote(
                column_id="evaluation-1",
                decision="revise",
                confidence=0.4,
                supports_commitment=False,
            ),
        ],
        policy=ConsensusPolicy.WEIGHTED,
        confidence_threshold=0.6,
    )

    assert commitment.decision == "approve"
    assert round(commitment.confidence, 2) == 0.81
    assert commitment.supporting_columns == ("goal-1", "evidence-1")
    assert commitment.dissenting_columns == ("evaluation-1",)
    assert commitment.blockers == ()
    assert commitment.is_ready is True


def test_confidence_threshold_commitment_escalates_when_uncertain() -> None:
    commitment = build_commitment(
        [
            ColumnVote(column_id="goal-1", decision="approve", confidence=0.45),
            ColumnVote(column_id="evidence-1", decision="approve", confidence=0.15),
            ColumnVote(
                column_id="uncertainty-1",
                decision="revise",
                confidence=0.4,
                supports_commitment=False,
            ),
        ],
        policy=ConsensusPolicy.CONFIDENCE_THRESHOLD,
        confidence_threshold=0.75,
    )

    assert commitment.decision == "approve"
    assert commitment.blockers == ("confidence_below_threshold",)
    assert commitment.next_actions == ("gather_more_evidence", "escalate_to_human_review")
    assert commitment.is_ready is False


def test_veto_commitment_carries_blockers_and_next_actions() -> None:
    commitment = build_commitment(
        [
            ColumnVote(column_id="goal-1", decision="approve", confidence=0.85),
            ColumnVote(
                column_id="evaluation-1",
                decision="approve",
                confidence=0.4,
                veto=True,
                blockers=("policy_violation",),
                next_actions=("request_human_review",),
            ),
        ],
        policy=ConsensusPolicy.VETO,
        confidence_threshold=0.6,
    )

    assert commitment.decision == "approve"
    assert commitment.blockers == ("policy_violation",)
    assert commitment.next_actions == ("request_human_review",)
    assert commitment.is_ready is False


def test_synthesis_only_commitment_is_rejected() -> None:
    definition = AssemblyDefinition(assembly_id="assembly-1", columns=("synthesis-1",))
    state = AssemblyState(definition=definition).register_column_state(
        ColumnState(
            column_id="synthesis-1",
            assembly_id="assembly-1",
            kind=ColumnKind.SYNTHESIS,
            confidence=0.9,
        )
    )
    commitment = Commitment(
        decision="approve",
        confidence=0.9,
        supporting_columns=("synthesis-1",),
        dissenting_columns=(),
        blockers=(),
        next_actions=(),
    )

    with pytest.raises(CommitmentValidationError, match="evidence column participation"):
        state.with_commitment(commitment)


def test_commitment_gate_blocks_veto() -> None:
    state = _commitment_gate_state()
    votes = _commitment_votes(veto=True)
    commitment = build_commitment(votes, policy=ConsensusPolicy.CONFIDENCE_THRESHOLD)

    gated = state.with_commitment(
        commitment,
        validation_context=CommitmentValidationContext(
            commitment=commitment,
            votes=votes,
            decision_trail=("runtime-gate:evidence-1", "runtime-gate:evaluation-1"),
        ),
    ).commitment

    assert gated is not None
    assert gated.blockers == ("policy_violation",)
    assert gated.is_ready is False


def test_commitment_gate_escalates_low_confidence() -> None:
    state = _commitment_gate_state()
    votes = (
        ColumnVote(column_id="evidence-1", decision="approve", confidence=0.3),
        ColumnVote(column_id="evaluation-1", decision="approve", confidence=0.3),
        ColumnVote(
            column_id="synthesis-1", decision="revise", confidence=0.5, supports_commitment=False
        ),
    )
    commitment = build_commitment(
        votes,
        policy=ConsensusPolicy.CONFIDENCE_THRESHOLD,
        confidence_threshold=0.75,
    )

    gated = state.with_commitment(
        commitment,
        validation_context=CommitmentValidationContext(
            commitment=commitment,
            votes=votes,
            confidence_threshold=0.75,
            decision_trail=("runtime-gate:evidence-1", "runtime-gate:evaluation-1"),
        ),
    ).commitment

    assert gated is not None
    assert gated.blockers == ("confidence_below_threshold",)
    assert gated.next_actions == ("gather_more_evidence", "escalate_to_human_review")
    assert gated.is_ready is False


def test_commitment_gate_requires_human_approval_for_high_risk_decision() -> None:
    state = _commitment_gate_state()
    votes = _commitment_votes()
    commitment = build_commitment(votes, policy=ConsensusPolicy.CONFIDENCE_THRESHOLD)

    gated = state.with_commitment(
        commitment,
        validation_context=CommitmentValidationContext(
            commitment=commitment,
            votes=votes,
            decision_trail=("runtime-gate:evidence-1", "runtime-gate:evaluation-1"),
            high_risk_decisions=("approve",),
        ),
    ).commitment

    assert gated is not None
    assert gated.blockers == ("human_approval_required",)
    assert gated.next_actions == ("request_human_approval",)
    assert gated.is_ready is False


def test_valid_commitment_gate_succeeds() -> None:
    state = _commitment_gate_state()
    votes = _commitment_votes()
    commitment = build_commitment(votes, policy=ConsensusPolicy.CONFIDENCE_THRESHOLD)

    committed = state.with_commitment(
        commitment,
        validation_context=CommitmentValidationContext(
            commitment=commitment,
            votes=votes,
            decision_trail=("runtime-gate:evidence-1", "runtime-gate:evaluation-1"),
        ),
    )

    assert committed.commitment is not None
    assert committed.commitment.is_ready is True
    assert committed.can_continue() is False


def test_assembly_state_stops_after_ready_commitment() -> None:
    definition = AssemblyDefinition(
        assembly_id="assembly-1",
        columns=("goal-1", "evidence-1", "evaluation-1"),
    )
    commitment = Commitment(
        decision="approve",
        confidence=0.92,
        supporting_columns=("goal-1", "evidence-1", "evaluation-1"),
        dissenting_columns=(),
        blockers=(),
        next_actions=(),
    )
    state = AssemblyState(definition=definition)
    for column_id, kind in (
        ("goal-1", ColumnKind.GOAL),
        ("evidence-1", ColumnKind.EVIDENCE),
        ("evaluation-1", ColumnKind.EVALUATION),
    ):
        state = state.register_column_state(
            ColumnState(column_id=column_id, assembly_id="assembly-1", kind=kind)
        )
    state = state.with_commitment(commitment)

    assert state.can_continue() is False


def test_column_message_clamps_confidence() -> None:
    message = ColumnMessage(
        message_type=MessageType.BELIEF_UPDATE,
        assembly_id="assembly-1",
        source_column="goal-1",
        payload_ref="belief:goal-1:1",
        confidence=1.8,
    )

    assert message.confidence == 1.0


def test_evidence_column_cannot_publish_commitment() -> None:
    with pytest.raises(ColumnCapabilityError, match="propose_commitment"):
        require_column_capability(
            column_id="evidence-1",
            column_kind=ColumnKind.EVIDENCE,
            capability=ColumnCapability.PROPOSE_COMMITMENT,
        )


def test_synthesis_column_cannot_retrieve_unless_explicitly_granted() -> None:
    with pytest.raises(ColumnCapabilityError, match="retrieve"):
        require_column_capability(
            column_id="synthesis-1",
            column_kind=ColumnKind.SYNTHESIS,
            capability=ColumnCapability.RETRIEVE,
        )

    require_column_capability(
        column_id="synthesis-1",
        column_kind=ColumnKind.SYNTHESIS,
        capability=ColumnCapability.RETRIEVE,
        explicit_grants=(ColumnCapability.RETRIEVE,),
    )


def test_evaluation_column_can_score_veto_and_block() -> None:
    require_column_capability(
        column_id="evaluation-1",
        column_kind=ColumnKind.EVALUATION,
        capability=ColumnCapability.SCORE,
    )
    require_column_capability(
        column_id="evaluation-1",
        column_kind=ColumnKind.EVALUATION,
        capability=ColumnCapability.VETO,
    )
    commitment = build_commitment(
        [
            ColumnVote(column_id="goal-1", decision="approve", confidence=0.9),
            ColumnVote(
                column_id="evaluation-1",
                decision="approve",
                confidence=0.6,
                veto=True,
                blockers=("policy_violation",),
            ),
        ],
        policy=ConsensusPolicy.VETO,
    )

    assert commitment.blockers == ("policy_violation",)
    assert commitment.is_ready is False


def test_unknown_column_kind_is_rejected_by_default() -> None:
    with pytest.raises(ColumnCapabilityError, match="unknown"):
        require_column_capability(
            column_id="unknown-1",
            column_kind="unregistered-kind",
            capability=ColumnCapability.READ_INPUT,
        )
