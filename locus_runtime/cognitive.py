from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MVP_COGNITIVE_MESSAGE_TYPES = {
    "belief_update",
    "evidence_claim",
    "synthesis_proposal",
    "dissent",
    "commitment",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in output:
            output.append(text)
    return output


def _estimate_confidence(completeness_score: float) -> float:
    bounded = max(0.0, min(1.0, float(completeness_score)))
    return round(bounded, 3)


@dataclass(frozen=True)
class CognitiveMessage:
    message_type: Literal[
        "belief_update", "evidence_claim", "synthesis_proposal", "dissent", "commitment"
    ]
    column_id: str
    assembly_id: str
    payload: dict[str, Any]
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.message_type not in MVP_COGNITIVE_MESSAGE_TYPES:
            raise ValueError(f"Unsupported cognitive message type: {self.message_type}")
        if not str(self.column_id or "").strip():
            raise ValueError("column_id is required")
        if not str(self.assembly_id or "").strip():
            raise ValueError("assembly_id is required")


@dataclass
class ColumnState:
    column_id: str
    assembly_id: str
    belief_set: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    last_updated: str = field(default_factory=_now_iso)
    adaptation_metrics: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "column_id": self.column_id,
            "assembly_id": self.assembly_id,
            "belief_set": dict(self.belief_set),
            "evidence_refs": list(self.evidence_refs),
            "confidence": self.confidence,
            "last_updated": self.last_updated,
            "adaptation_metrics": dict(self.adaptation_metrics),
        }


@dataclass(frozen=True)
class Commitment:
    decision: str
    confidence: float
    supporting_columns: list[str]
    dissenting_columns: list[str]
    blockers: list[str]
    next_actions: list[str]
    evidence_refs: list[str] = field(default_factory=list)
    rationale: str = ""

    def model_dump(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "confidence": self.confidence,
            "supporting_columns": list(self.supporting_columns),
            "dissenting_columns": list(self.dissenting_columns),
            "blockers": list(self.blockers),
            "next_actions": list(self.next_actions),
            "evidence_refs": list(self.evidence_refs),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class AssemblyDefinition:
    assembly_id: str
    columns: list[str]
    consensus_policy: str = "weighted-support"
    inference_mode: str = "bounded"
    budget_constraints: dict[str, Any] = field(default_factory=dict)
    escalation_policy: dict[str, Any] = field(default_factory=dict)


class GoalColumn:
    def observe(
        self, *, assembly_id: str, config: dict[str, Any], run_input: dict[str, Any]
    ) -> ColumnState:
        intent = str(config.get("intent") or run_input.get("message") or "").strip()
        success_criteria = _coerce_string_list(config.get("success_criteria"))
        constraints = _coerce_string_list(config.get("constraints"))
        priorities = _coerce_string_list(config.get("priorities"))
        completeness = 0.3
        if intent:
            completeness += 0.4
        if success_criteria:
            completeness += 0.2
        if constraints or priorities:
            completeness += 0.1
        belief_set = {
            "intent": intent,
            "success_criteria": success_criteria,
            "constraints": constraints,
            "priorities": priorities,
            "output_contract": str(config.get("output_contract") or "").strip(),
        }
        return ColumnState(
            column_id="goal",
            assembly_id=assembly_id,
            belief_set=belief_set,
            confidence=_estimate_confidence(completeness),
        )

    def emit_message(self, state: ColumnState) -> CognitiveMessage:
        return CognitiveMessage(
            message_type="belief_update",
            column_id=state.column_id,
            assembly_id=state.assembly_id,
            payload=state.model_dump(),
            confidence=state.confidence,
        )


class EvidenceColumn:
    def observe(
        self,
        *,
        assembly_id: str,
        config: dict[str, Any],
        run_input: dict[str, Any],
        incoming_context: list[Any] | None = None,
    ) -> ColumnState:
        required_evidence = _coerce_string_list(config.get("required_evidence"))
        allowed_sources = _coerce_string_list(config.get("allowed_sources"))
        incoming_context = incoming_context or []
        collected_claims: list[str] = []
        for item in incoming_context:
            if isinstance(item, dict):
                for key in ("evidence", "documents", "grounding_context", "response", "message"):
                    candidate = item.get(key)
                    if isinstance(candidate, list):
                        for subitem in candidate:
                            text = str(subitem or "").strip()
                            if text:
                                collected_claims.append(text)
                    else:
                        text = str(candidate or "").strip()
                        if text:
                            collected_claims.append(text)
            else:
                text = str(item or "").strip()
                if text:
                    collected_claims.append(text)
        fallback_message = str(run_input.get("message") or "").strip()
        if fallback_message:
            collected_claims.append(fallback_message)

        normalized_claims: list[str] = []
        for claim in collected_claims:
            if claim and claim not in normalized_claims:
                normalized_claims.append(claim)

        blockers: list[str] = []
        lowered_claims = " ".join(normalized_claims).lower()
        for required in required_evidence:
            if required.lower() not in lowered_claims:
                blockers.append(f"Missing required evidence: {required}")

        completeness = 0.2
        if normalized_claims:
            completeness += 0.4
        if allowed_sources:
            completeness += 0.1
        if required_evidence and not blockers:
            completeness += 0.3

        belief_set = {
            "evidence": normalized_claims,
            "allowed_sources": allowed_sources,
            "required_evidence": required_evidence,
            "blockers": blockers,
        }
        evidence_refs = [f"evidence:{index + 1}" for index in range(len(normalized_claims))]
        return ColumnState(
            column_id="evidence",
            assembly_id=assembly_id,
            belief_set=belief_set,
            evidence_refs=evidence_refs,
            confidence=_estimate_confidence(completeness),
        )

    def emit_message(self, state: ColumnState) -> CognitiveMessage:
        return CognitiveMessage(
            message_type="evidence_claim",
            column_id=state.column_id,
            assembly_id=state.assembly_id,
            payload=state.model_dump(),
            confidence=state.confidence,
            evidence_refs=state.evidence_refs,
        )


class SynthesisColumn:
    def observe(
        self,
        *,
        assembly_id: str,
        goal_state: ColumnState,
        evidence_state: ColumnState,
    ) -> ColumnState:
        goal_intent = str(goal_state.belief_set.get("intent") or "").strip()
        evidence_items = evidence_state.belief_set.get("evidence")
        evidence_count = len(evidence_items) if isinstance(evidence_items, list) else 0
        blockers = evidence_state.belief_set.get("blockers")
        blocker_list = blockers if isinstance(blockers, list) else []

        if blocker_list:
            decision = f"Escalate goal '{goal_intent or 'unspecified goal'}' for human review"
            rationale = "Evidence is incomplete for a safe autonomous commitment."
        elif evidence_count:
            decision = f"Proceed on goal '{goal_intent or 'unspecified goal'}' with evidence-backed execution"
            rationale = f"Found {evidence_count} evidence item(s) supporting the current goal."
        else:
            decision = f"Pause goal '{goal_intent or 'unspecified goal'}' pending evidence"
            rationale = "No supporting evidence was available to synthesize a commitment."

        confidence = _estimate_confidence((goal_state.confidence + evidence_state.confidence) / 2)
        belief_set = {
            "decision": decision,
            "rationale": rationale,
            "goal_intent": goal_intent,
            "evidence_count": evidence_count,
            "blockers": blocker_list,
        }
        return ColumnState(
            column_id="synthesis",
            assembly_id=assembly_id,
            belief_set=belief_set,
            evidence_refs=list(evidence_state.evidence_refs),
            confidence=confidence,
        )

    def emit_message(self, state: ColumnState) -> CognitiveMessage:
        return CognitiveMessage(
            message_type="synthesis_proposal",
            column_id=state.column_id,
            assembly_id=state.assembly_id,
            payload=state.model_dump(),
            confidence=state.confidence,
            evidence_refs=state.evidence_refs,
        )


class ConsensusEngine:
    def fuse(
        self,
        *,
        goal_state: ColumnState,
        evidence_state: ColumnState,
        synthesis_state: ColumnState,
        confidence_threshold: float = 0.6,
    ) -> Commitment:
        blockers = list(evidence_state.belief_set.get("blockers") or [])
        dissenting_columns: list[str] = []
        next_actions: list[str] = []
        confidence = _estimate_confidence(
            (goal_state.confidence + evidence_state.confidence + synthesis_state.confidence) / 3
        )
        if blockers:
            dissenting_columns.append("evidence")
            next_actions.append(
                "Collect the missing required evidence before autonomous execution."
            )
            next_actions.append(
                "Escalate to a human checkpoint because required evidence is missing."
            )
        if confidence < max(0.0, min(1.0, float(confidence_threshold))):
            if "synthesis" not in dissenting_columns:
                dissenting_columns.append("synthesis")
            next_actions.append(
                "Escalate to a human checkpoint because confidence is below threshold."
            )

        if not next_actions:
            next_actions.append("Proceed with the commitment and monitor the outcome.")

        rationale = str(synthesis_state.belief_set.get("rationale") or "").strip()
        decision = str(
            synthesis_state.belief_set.get("decision") or "No commitment produced"
        ).strip()
        return Commitment(
            decision=decision,
            confidence=confidence,
            supporting_columns=[
                goal_state.column_id,
                evidence_state.column_id,
                synthesis_state.column_id,
            ],
            dissenting_columns=dissenting_columns,
            blockers=blockers,
            next_actions=next_actions,
            evidence_refs=list(evidence_state.evidence_refs),
            rationale=rationale,
        )


class AssemblyRuntime:
    def __init__(self, definition: AssemblyDefinition) -> None:
        self.definition = definition
        self.goal_column = GoalColumn()
        self.evidence_column = EvidenceColumn()
        self.synthesis_column = SynthesisColumn()
        self.consensus_engine = ConsensusEngine()

    def run(
        self,
        *,
        goal_config: dict[str, Any],
        evidence_config: dict[str, Any],
        run_input: dict[str, Any],
        incoming_context: list[Any] | None = None,
        confidence_threshold: float = 0.6,
    ) -> dict[str, Any]:
        goal_state = self.goal_column.observe(
            assembly_id=self.definition.assembly_id,
            config=goal_config,
            run_input=run_input,
        )
        evidence_state = self.evidence_column.observe(
            assembly_id=self.definition.assembly_id,
            config=evidence_config,
            run_input=run_input,
            incoming_context=incoming_context,
        )
        synthesis_state = self.synthesis_column.observe(
            assembly_id=self.definition.assembly_id,
            goal_state=goal_state,
            evidence_state=evidence_state,
        )
        commitment = self.consensus_engine.fuse(
            goal_state=goal_state,
            evidence_state=evidence_state,
            synthesis_state=synthesis_state,
            confidence_threshold=confidence_threshold,
        )
        return {
            "assembly": {
                "assembly_id": self.definition.assembly_id,
                "columns": list(self.definition.columns),
                "consensus_policy": self.definition.consensus_policy,
                "inference_mode": self.definition.inference_mode,
            },
            "states": {
                "goal": goal_state.model_dump(),
                "evidence": evidence_state.model_dump(),
                "synthesis": synthesis_state.model_dump(),
            },
            "messages": [
                self.goal_column.emit_message(goal_state).__dict__,
                self.evidence_column.emit_message(evidence_state).__dict__,
                self.synthesis_column.emit_message(synthesis_state).__dict__,
            ],
            "commitment": commitment.model_dump(),
        }


def run_mvp_cognitive_assembly(
    *,
    assembly_id: str,
    goal_config: dict[str, Any],
    evidence_config: dict[str, Any],
    run_input: dict[str, Any],
    incoming_context: list[Any] | None = None,
    confidence_threshold: float = 0.6,
) -> dict[str, Any]:
    runtime = AssemblyRuntime(
        AssemblyDefinition(
            assembly_id=assembly_id,
            columns=["goal", "evidence", "synthesis"],
        )
    )
    return runtime.run(
        goal_config=goal_config,
        evidence_config=evidence_config,
        run_input=run_input,
        incoming_context=incoming_context,
        confidence_threshold=confidence_threshold,
    )
