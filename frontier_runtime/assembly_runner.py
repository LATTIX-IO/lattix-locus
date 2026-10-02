from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from frontier_runtime.cognition import (
    AssemblyAdmissionPolicy,
    AssemblyBudget,
    AssemblyDefinition,
    AssemblyState,
    BeliefRecord,
    ColumnKind,
    ColumnVote,
    CommitmentValidationContext,
    CommitmentValidationError,
    ConsensusPolicy,
    InferenceMode,
    build_commitment,
    ColumnCapability,
    require_column_capability,
    validate_assembly_definition,
)
from frontier_runtime.persistence import persist_assembly_state


class ColumnRuntimeGateError(PermissionError):
    def __init__(self, reason: str, *, metadata: dict[str, Any] | None = None) -> None:
        self.reason = str(reason or "column runtime admission denied").strip()
        self.metadata = dict(metadata or {})
        super().__init__(self.reason)


ColumnRuntimeAuditCallback = Callable[
    [str, str, Literal["allowed", "blocked", "error"], dict[str, Any]], None
]


@dataclass
class ColumnRuntimeBudgetCounters:
    admitted_steps: int = 0


@dataclass(frozen=True)
class ColumnRuntimeGateRequest:
    assembly_id: str
    column_id: str
    column_kind: ColumnKind
    capability: ColumnCapability
    actor: str
    tenant_id: str = ""
    auth_context: dict[str, Any] | None = None
    require_tenant_context: bool = False
    definition: AssemblyDefinition | None = None
    admission_policy: AssemblyAdmissionPolicy = field(default_factory=AssemblyAdmissionPolicy)
    provider: str = ""
    model: str = ""
    tool_ids: tuple[str, ...] = ()
    retrieval_sources: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()
    budget: AssemblyBudget = field(default_factory=AssemblyBudget)
    budget_counters: ColumnRuntimeBudgetCounters = field(
        default_factory=ColumnRuntimeBudgetCounters
    )
    audit_callback: ColumnRuntimeAuditCallback | None = None


def _normalized_runtime_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(value or "").strip() for value in values if str(value or "").strip())


def _runtime_gate_audit(
    request: ColumnRuntimeGateRequest,
    outcome: Literal["allowed", "blocked", "error"],
    metadata: dict[str, Any],
) -> None:
    if request.audit_callback is None:
        return
    request.audit_callback(
        "cognition.column.runtime_gate",
        request.actor,
        outcome,
        {
            "assembly_id": request.assembly_id,
            "tenant_id": request.tenant_id,
            "column_id": request.column_id,
            "column_kind": request.column_kind.value,
            "capability": request.capability.value,
            **metadata,
        },
    )


def _deny_column_runtime(request: ColumnRuntimeGateRequest, reason: str) -> None:
    metadata = {"reason": reason}
    _runtime_gate_audit(request, "blocked", metadata)
    raise ColumnRuntimeGateError(reason, metadata=metadata)


def admit_column_runtime_step(request: ColumnRuntimeGateRequest) -> None:
    auth_context = request.auth_context if isinstance(request.auth_context, dict) else None
    if auth_context is not None and auth_context.get("authenticated") is not True:
        _deny_column_runtime(request, "authenticated runtime context required")

    tenant_id = str(request.tenant_id or "").strip()
    auth_tenant = str(auth_context.get("tenant") or "").strip() if auth_context else ""
    if request.require_tenant_context and not tenant_id:
        _deny_column_runtime(request, "tenant context required")
    if tenant_id and auth_tenant and tenant_id != auth_tenant:
        _deny_column_runtime(request, "tenant ownership mismatch")

    try:
        require_column_capability(
            column_id=request.column_id,
            column_kind=request.column_kind,
            capability=request.capability,
            explicit_grants=(
                request.definition.column_capability_overrides.get(request.column_id, ())
                if request.definition is not None
                else ()
            ),
        )
    except Exception as exc:
        _deny_column_runtime(request, str(exc))

    allowed_providers = set(_normalized_runtime_values(request.admission_policy.allowed_providers))
    provider = str(request.provider or "").strip()
    if allowed_providers and provider not in allowed_providers:
        _deny_column_runtime(request, "provider is not allowed for column runtime")

    allowed_models = set(_normalized_runtime_values(request.admission_policy.allowed_models))
    model = str(request.model or "").strip()
    if allowed_models and model not in allowed_models:
        _deny_column_runtime(request, "model is not allowed for column runtime")

    allowed_tools = set(_normalized_runtime_values(request.admission_policy.allowed_tools))
    if allowed_tools:
        disallowed_tools = [
            tool_id
            for tool_id in _normalized_runtime_values(request.tool_ids)
            if tool_id not in allowed_tools
        ]
        if disallowed_tools:
            _deny_column_runtime(request, "tool is not allowed for column runtime")

    allowed_retrieval_sources = set(
        _normalized_runtime_values(request.admission_policy.allowed_retrieval_sources)
    )
    if allowed_retrieval_sources:
        disallowed_sources = [
            source
            for source in _normalized_runtime_values(request.retrieval_sources)
            if source not in allowed_retrieval_sources
        ]
        if disallowed_sources:
            _deny_column_runtime(request, "retrieval source is not allowed for column runtime")

    allowed_network_hosts = set(
        _normalized_runtime_values(request.admission_policy.allowed_network_hosts)
    )
    if allowed_network_hosts:
        disallowed_hosts = [
            host
            for host in _normalized_runtime_values(request.network_hosts)
            if host not in allowed_network_hosts
        ]
        if disallowed_hosts:
            _deny_column_runtime(request, "network host is not allowed for column runtime")

    if request.budget_counters.admitted_steps >= request.budget.max_messages:
        _deny_column_runtime(request, "column runtime budget exceeded")
    request.budget_counters.admitted_steps += 1
    _runtime_gate_audit(
        request,
        "allowed",
        {"admitted_steps": request.budget_counters.admitted_steps},
    )


@dataclass(frozen=True)
class AssemblyRunRequest:
    assembly_id: str
    task: str
    actor: str = "system"
    tenant_id: str = ""
    auth_context: dict[str, Any] | None = None
    require_tenant_context: bool = False
    assembly_definition: AssemblyDefinition | None = None
    admission_policy: AssemblyAdmissionPolicy = field(default_factory=AssemblyAdmissionPolicy)
    provider: str = ""
    model: str = ""
    tool_ids: tuple[str, ...] = ()
    retrieval_sources: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()
    audit_callback: ColumnRuntimeAuditCallback | None = None
    human_approval_granted: bool = False
    confidence_threshold: float = 0.6
    consensus_policy: ConsensusPolicy = ConsensusPolicy.WEIGHTED
    inference_mode: InferenceMode = InferenceMode.SINGLE_PASS
    budget: AssemblyBudget = field(default_factory=AssemblyBudget)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssemblyRunResult:
    assembly_id: str
    state: AssemblyState
    outcome: str
    persisted: dict[str, Any]


class AssemblyRunner:
    def run(self, request: AssemblyRunRequest) -> AssemblyRunResult:
        task = str(request.task or "").strip()
        if not task:
            raise ValueError("task is required")

        definition = validate_assembly_definition(
            request.assembly_definition
            or AssemblyDefinition(
                assembly_id=str(request.assembly_id or "").strip(),
                columns=("goal", "evidence", "evaluation", "synthesis"),
                consensus_policy=request.consensus_policy,
                inference_mode=request.inference_mode,
                budget_constraints=request.budget,
            ),
            policy=request.admission_policy,
            tenant_id=request.tenant_id,
            provider=request.provider,
            model=request.model,
            tool_ids=request.tool_ids,
        )
        if not definition.assembly_id:
            raise ValueError("assembly_id is required")
        if (
            str(request.assembly_id or "").strip()
            and definition.assembly_id != str(request.assembly_id or "").strip()
        ):
            raise ValueError("assembly_definition assembly_id must match request assembly_id")

        column_by_kind = {kind: column_id for column_id, kind in definition.column_kinds.items()}
        goal_id = column_by_kind[ColumnKind.GOAL]
        evidence_id = column_by_kind[ColumnKind.EVIDENCE]
        evaluation_id = column_by_kind[ColumnKind.EVALUATION]
        synthesis_id = column_by_kind[ColumnKind.SYNTHESIS]

        context_keys = tuple(sorted(str(key) for key in request.context if str(key).strip()))
        budget_counters = ColumnRuntimeBudgetCounters()
        decision_trail: list[str] = []
        self._admit_column_runtime_step(
            request,
            definition=definition,
            budget_counters=budget_counters,
            decision_trail=decision_trail,
            column_id=goal_id,
            kind=ColumnKind.GOAL,
            capability=ColumnCapability.READ_INPUT,
        )
        self._admit_column_runtime_step(
            request,
            definition=definition,
            budget_counters=budget_counters,
            decision_trail=decision_trail,
            column_id=goal_id,
            kind=ColumnKind.GOAL,
            capability=ColumnCapability.EMIT_GOAL_BELIEF,
        )
        goal_state = self._column_state(
            definition.assembly_id,
            goal_id,
            ColumnKind.GOAL,
            BeliefRecord(
                key="task_goal",
                value=task,
                confidence=0.9,
                rationale="Assembly objective supplied by caller.",
                metadata={"actor": request.actor, "tenant_id": request.tenant_id},
            ),
        )
        self._admit_column_runtime_step(
            request,
            definition=definition,
            budget_counters=budget_counters,
            decision_trail=decision_trail,
            column_id=evidence_id,
            kind=ColumnKind.EVIDENCE,
            capability=ColumnCapability.RETRIEVE,
        )
        evidence_state = self._column_state(
            definition.assembly_id,
            evidence_id,
            ColumnKind.EVIDENCE,
            BeliefRecord(
                key="available_context",
                value={"context_keys": list(context_keys), "has_context": bool(context_keys)},
                confidence=0.65 if context_keys else 0.45,
                evidence_refs=tuple(f"context:{key}" for key in context_keys),
                rationale="Evidence column reflects caller-provided structured context.",
            ),
        )
        evaluation_confidence = 0.78 if context_keys else 0.58
        self._admit_column_runtime_step(
            request,
            definition=definition,
            budget_counters=budget_counters,
            decision_trail=decision_trail,
            column_id=evaluation_id,
            kind=ColumnKind.EVALUATION,
            capability=ColumnCapability.SCORE,
        )
        if evaluation_confidence < request.confidence_threshold:
            self._admit_column_runtime_step(
                request,
                definition=definition,
                budget_counters=budget_counters,
                decision_trail=decision_trail,
                column_id=evaluation_id,
                kind=ColumnKind.EVALUATION,
                capability=ColumnCapability.VETO,
            )
        evaluation_state = self._column_state(
            definition.assembly_id,
            evaluation_id,
            ColumnKind.EVALUATION,
            BeliefRecord(
                key="readiness_score",
                value={"ready": evaluation_confidence >= request.confidence_threshold},
                confidence=evaluation_confidence,
                rationale="Initial deterministic readiness score for the first execution slice.",
            ),
        )

        ready_for_commitment = evaluation_confidence >= request.confidence_threshold
        decision = "commit"
        self._admit_column_runtime_step(
            request,
            definition=definition,
            budget_counters=budget_counters,
            decision_trail=decision_trail,
            column_id=synthesis_id,
            kind=ColumnKind.SYNTHESIS,
            capability=ColumnCapability.PROPOSE_COMMITMENT,
        )
        synthesis_state = self._column_state(
            definition.assembly_id,
            synthesis_id,
            ColumnKind.SYNTHESIS,
            BeliefRecord(
                key="proposed_decision",
                value=decision,
                confidence=max(goal_state.confidence, evaluation_state.confidence),
                rationale="Synthesis column combines the goal and evaluation signals.",
            ),
        )

        votes = [
            ColumnVote(goal_id, decision, goal_state.confidence),
            ColumnVote(evidence_id, decision, evidence_state.confidence),
            ColumnVote(
                evaluation_id,
                decision if ready_for_commitment else "escalate",
                evaluation_state.confidence,
                supports_commitment=ready_for_commitment,
                blockers=() if ready_for_commitment else ("insufficient_context",),
                next_actions=() if ready_for_commitment else ("gather_more_evidence",),
            ),
            ColumnVote(synthesis_id, decision, synthesis_state.confidence),
        ]
        commitment = build_commitment(
            votes,
            policy=ConsensusPolicy.CONFIDENCE_THRESHOLD,
            confidence_threshold=request.confidence_threshold,
            escalation_action="escalate_to_human_review",
        )

        state = AssemblyState(definition=definition)
        for column_state in (goal_state, evidence_state, evaluation_state, synthesis_state):
            state = state.register_column_state(column_state)
        validation_context = CommitmentValidationContext(
            commitment=commitment,
            votes=tuple(votes),
            confidence_threshold=request.confidence_threshold,
            decision_trail=tuple(decision_trail),
            human_approval_granted=request.human_approval_granted,
            high_risk_decisions=request.admission_policy.high_risk_commitment_decisions,
            require_human_approval_for_high_risk=(
                request.admission_policy.require_human_approval_for_high_risk
            ),
        )
        try:
            state = state.register_message(len(votes)).with_commitment(
                commitment,
                validation_context=validation_context,
            )
        except CommitmentValidationError as exc:
            if request.audit_callback is not None:
                request.audit_callback(
                    "cognition.commitment.gate",
                    request.actor,
                    "blocked",
                    {
                        "assembly_id": request.assembly_id,
                        "tenant_id": request.tenant_id,
                        "reason": str(exc),
                    },
                )
            raise
        if request.audit_callback is not None:
            gated_commitment = state.commitment
            request.audit_callback(
                "cognition.commitment.gate",
                request.actor,
                "allowed"
                if gated_commitment is not None and gated_commitment.is_ready
                else "blocked",
                {
                    "assembly_id": request.assembly_id,
                    "tenant_id": request.tenant_id,
                    "blockers": list(
                        gated_commitment.blockers if gated_commitment is not None else ()
                    ),
                    "confidence": gated_commitment.confidence
                    if gated_commitment is not None
                    else 0.0,
                },
            )

        gated_commitment = state.commitment
        outcome = (
            "committed"
            if gated_commitment is not None and gated_commitment.is_ready
            else "escalated"
        )
        persisted = persist_assembly_state(
            state,
            cause={"runner": "assembly_runner", "actor": request.actor},
            outcome=outcome,
            outcome_metadata={"tenant_id": request.tenant_id, "task": task},
            tenant_id=request.tenant_id,
            actor=request.actor,
            task=task,
        )
        return AssemblyRunResult(
            assembly_id=definition.assembly_id,
            state=state,
            outcome=outcome,
            persisted=persisted,
        )

    def _column_state(
        self,
        assembly_id: str,
        column_id: str,
        kind: ColumnKind,
        belief: BeliefRecord,
    ) -> Any:
        from frontier_runtime.cognition import ColumnState

        return ColumnState(column_id=column_id, assembly_id=assembly_id, kind=kind).with_beliefs(
            (belief,)
        )

    def _admit_column_runtime_step(
        self,
        request: AssemblyRunRequest,
        *,
        definition: AssemblyDefinition,
        budget_counters: ColumnRuntimeBudgetCounters,
        decision_trail: list[str],
        column_id: str,
        kind: ColumnKind,
        capability: ColumnCapability,
    ) -> None:
        admit_column_runtime_step(
            ColumnRuntimeGateRequest(
                assembly_id=definition.assembly_id,
                column_id=column_id,
                column_kind=kind,
                capability=capability,
                actor=request.actor,
                tenant_id=request.tenant_id,
                auth_context=request.auth_context,
                require_tenant_context=request.require_tenant_context,
                definition=definition,
                admission_policy=request.admission_policy,
                provider=request.provider,
                model=request.model,
                tool_ids=request.tool_ids,
                retrieval_sources=request.retrieval_sources,
                network_hosts=request.network_hosts,
                budget=definition.budget_constraints,
                budget_counters=budget_counters,
                audit_callback=request.audit_callback,
            )
        )
        decision_trail.append(f"{column_id}:{kind.value}:{capability.value}:allowed")

    def _require_column_capability(
        self,
        definition: AssemblyDefinition,
        *,
        column_id: str,
        kind: ColumnKind,
        capability: ColumnCapability,
    ) -> None:
        require_column_capability(
            column_id=column_id,
            column_kind=kind,
            capability=capability,
            explicit_grants=definition.column_capability_overrides.get(column_id, ()),
        )
