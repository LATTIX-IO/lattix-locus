from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Protocol, runtime_checkable


BELIEF_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
BELIEF_VALUE_MAX_BYTES = 16_384
BELIEF_METADATA_MAX_BYTES = 8_192
BELIEF_RATIONALE_MAX_LENGTH = 2_048
BELIEF_EVIDENCE_REF_MAX_COUNT = 32
BELIEF_EVIDENCE_REF_MAX_LENGTH = 256
_SENSITIVE_METADATA_TOKENS = (
    "api_key",
    "authorization",
    "auth_header",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "session_id",
    "token",
)


def _now_timestamp() -> float:
    return time.time()


def _clamp_confidence(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError("confidence must be finite")
    return max(0.0, min(1.0, normalized))


class BeliefValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "belief record rejected").strip()
        super().__init__(self.reason)


def _json_size_bytes(value: Any, *, field_name: str) -> int:
    try:
        encoded = json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BeliefValidationError(f"{field_name} must be JSON safe") from exc
    return len(encoded.encode("utf-8"))


def _normalize_belief_key(value: Any) -> str:
    if not isinstance(value, str):
        raise BeliefValidationError("belief key must be a string")
    key = value.strip()
    if not key:
        raise BeliefValidationError("belief key is required")
    if BELIEF_KEY_PATTERN.fullmatch(key) is None:
        raise BeliefValidationError("belief key contains invalid characters")
    return key


def _normalize_evidence_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise BeliefValidationError("evidence refs must be a tuple")
    refs: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise BeliefValidationError(f"evidence_refs[{index}] must be a string")
        ref = value.strip()
        if not ref:
            continue
        if len(ref) > BELIEF_EVIDENCE_REF_MAX_LENGTH:
            raise BeliefValidationError("evidence ref exceeds max length")
        if ref not in refs:
            refs.append(ref)
    if len(refs) > BELIEF_EVIDENCE_REF_MAX_COUNT:
        raise BeliefValidationError("too many evidence refs")
    return tuple(refs)


def _metadata_key_is_sensitive(key: str) -> bool:
    normalized = key.strip().lower()
    return any(token in normalized for token in _SENSITIVE_METADATA_TOKENS)


def _redact_sensitive_metadata(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        redacted_any = False
        for key, item in value.items():
            key_text = str(key)
            if _metadata_key_is_sensitive(key_text):
                redacted[key_text] = "[redacted]"
                redacted_any = True
            else:
                redacted_item = _redact_sensitive_metadata(item, parent_key=key_text)
                redacted[key_text] = redacted_item
                if isinstance(redacted_item, dict) and redacted_item.get("redacted") is True:
                    redacted_any = True
        if redacted_any:
            redacted["redacted"] = True
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_metadata(item, parent_key=parent_key) for item in value]
    if parent_key and _metadata_key_is_sensitive(parent_key):
        return "[redacted]"
    return value


def _normalize_belief_metadata(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BeliefValidationError("belief metadata must be an object")
    redacted = _redact_sensitive_metadata(value)
    if not isinstance(redacted, dict):
        raise BeliefValidationError("belief metadata must be an object")
    if _json_size_bytes(redacted, field_name="belief metadata") > BELIEF_METADATA_MAX_BYTES:
        raise BeliefValidationError("belief metadata exceeds max size")
    return redacted


def validate_belief_record(belief: BeliefRecord) -> BeliefRecord:
    key = _normalize_belief_key(belief.key)
    confidence = _clamp_confidence(belief.confidence)
    evidence_refs = _normalize_evidence_refs(belief.evidence_refs)
    rationale = str(belief.rationale or "")
    if len(rationale) > BELIEF_RATIONALE_MAX_LENGTH:
        raise BeliefValidationError("belief rationale exceeds max length")
    if _json_size_bytes(belief.value, field_name="belief value") > BELIEF_VALUE_MAX_BYTES:
        raise BeliefValidationError("belief value exceeds max size")
    metadata = _normalize_belief_metadata(belief.metadata)
    object.__setattr__(belief, "key", key)
    object.__setattr__(belief, "confidence", confidence)
    object.__setattr__(belief, "evidence_refs", evidence_refs)
    object.__setattr__(belief, "rationale", rationale)
    object.__setattr__(belief, "metadata", metadata)
    return belief


class ColumnKind(str, Enum):
    GOAL = "goal"
    STATE = "state"
    DECOMPOSITION = "decomposition"
    EVIDENCE = "evidence"
    PREDICTION = "prediction"
    EVALUATION = "evaluation"
    UNCERTAINTY = "uncertainty"
    SYNTHESIS = "synthesis"


class ColumnCapability(str, Enum):
    READ_INPUT = "read_input"
    EMIT_GOAL_BELIEF = "emit_goal_belief"
    RETRIEVE = "retrieve"
    SCORE = "score"
    VETO = "veto"
    EMIT_BLOCKER = "emit_blocker"
    PROPOSE_COMMITMENT = "propose_commitment"
    PUBLISH_COMMITMENT = "publish_commitment"
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    MEMORY_WRITE = "memory_write"


class InferenceMode(str, Enum):
    SINGLE_PASS = "single_pass"
    ITERATIVE = "iterative"
    PREDICTIVE_EVALUATIVE = "predictive_evaluative"


class ConsensusPolicy(str, Enum):
    WEIGHTED = "weighted"
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    UNANIMOUS = "unanimous"
    VETO = "veto"


class MessageType(str, Enum):
    BELIEF_UPDATE = "belief_update"
    EVIDENCE_CLAIM = "evidence_claim"
    PREDICTION_BRANCH = "prediction_branch"
    EVALUATION_SCORE = "evaluation_score"
    UNCERTAINTY_SIGNAL = "uncertainty_signal"
    SYNTHESIS_PROPOSAL = "synthesis_proposal"
    DISSENT = "dissent"
    COMMITMENT = "commitment"


class ColumnCapabilityError(ValueError):
    def __init__(
        self,
        *,
        column_id: str,
        column_kind: Any,
        capability: ColumnCapability | str,
    ) -> None:
        self.column_id = str(column_id or "").strip()
        self.column_kind = _column_kind_value(column_kind)
        self.capability = _column_capability_value(capability)
        super().__init__(
            f"Column '{self.column_id or 'unknown'}' of kind '{self.column_kind or 'unknown'}' "
            f"is not permitted to use capability '{self.capability or 'unknown'}'"
        )


class AssemblyDefinitionAdmissionError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "assembly definition rejected").strip()
        super().__init__(self.reason)


class CommitmentValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "commitment rejected").strip()
        super().__init__(self.reason)


def _column_kind_value(value: Any) -> str:
    if isinstance(value, ColumnKind):
        return value.value
    return str(value or "").strip()


def _column_capability_value(value: Any) -> str:
    if isinstance(value, ColumnCapability):
        return value.value
    return str(value or "").strip()


_DEFAULT_COLUMN_CAPABILITIES: dict[ColumnKind, frozenset[ColumnCapability]] = {
    ColumnKind.GOAL: frozenset(
        {
            ColumnCapability.READ_INPUT,
            ColumnCapability.EMIT_GOAL_BELIEF,
        }
    ),
    ColumnKind.EVIDENCE: frozenset({ColumnCapability.RETRIEVE}),
    ColumnKind.EVALUATION: frozenset({ColumnCapability.SCORE, ColumnCapability.VETO}),
    ColumnKind.UNCERTAINTY: frozenset({ColumnCapability.EMIT_BLOCKER}),
    ColumnKind.SYNTHESIS: frozenset({ColumnCapability.PROPOSE_COMMITMENT}),
    ColumnKind.STATE: frozenset(),
    ColumnKind.DECOMPOSITION: frozenset(),
    ColumnKind.PREDICTION: frozenset(),
}


def column_capabilities_for(
    kind: ColumnKind | str,
    *,
    explicit_grants: tuple[ColumnCapability | str, ...] = (),
) -> frozenset[ColumnCapability]:
    try:
        column_kind = ColumnKind(_column_kind_value(kind))
    except ValueError:
        return frozenset()
    grants: set[ColumnCapability] = set(_DEFAULT_COLUMN_CAPABILITIES.get(column_kind, frozenset()))
    for grant in explicit_grants:
        try:
            grants.add(ColumnCapability(_column_capability_value(grant)))
        except ValueError:
            continue
    return frozenset(grants)


def require_column_capability(
    *,
    column_id: str,
    column_kind: ColumnKind | str,
    capability: ColumnCapability | str,
    explicit_grants: tuple[ColumnCapability | str, ...] = (),
) -> None:
    try:
        required_capability = ColumnCapability(_column_capability_value(capability))
        normalized_kind = ColumnKind(_column_kind_value(column_kind))
    except ValueError as exc:
        raise ColumnCapabilityError(
            column_id=column_id,
            column_kind=_column_kind_value(column_kind),
            capability=_column_capability_value(capability),
        ) from exc
    allowed = column_capabilities_for(normalized_kind, explicit_grants=explicit_grants)
    if required_capability not in allowed:
        raise ColumnCapabilityError(
            column_id=column_id,
            column_kind=normalized_kind.value,
            capability=required_capability.value,
        )


@dataclass(frozen=True)
class BeliefRecord:
    key: str
    value: Any
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_belief_record(self)


@dataclass(frozen=True)
class ColumnState:
    column_id: str
    assembly_id: str
    kind: ColumnKind
    belief_set: tuple[BeliefRecord, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    last_updated: float = field(default_factory=_now_timestamp)
    adaptation_metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence))

    def with_beliefs(self, beliefs: tuple[BeliefRecord, ...]) -> ColumnState:
        if beliefs:
            average_confidence = sum(item.confidence for item in beliefs) / len(beliefs)
        else:
            average_confidence = 0.0
        evidence_refs = tuple(
            dict.fromkeys(ref for belief in beliefs for ref in belief.evidence_refs if ref)
        )
        return replace(
            self,
            belief_set=beliefs,
            evidence_refs=evidence_refs,
            confidence=average_confidence,
            last_updated=_now_timestamp(),
        )

    def update_confidence(self, confidence: float, *, metric_key: str | None = None) -> ColumnState:
        metrics = dict(self.adaptation_metrics)
        if metric_key:
            metrics[metric_key] = _clamp_confidence(confidence)
        return replace(
            self,
            confidence=_clamp_confidence(confidence),
            adaptation_metrics=metrics,
            last_updated=_now_timestamp(),
        )


@dataclass(frozen=True)
class AssemblyBudget:
    max_iterations: int = 3
    max_messages: int = 32
    max_columns: int = 8


@dataclass(frozen=True)
class AssemblyAdmissionPolicy:
    max_columns: int = 8
    max_iterations: int = 3
    max_messages: int = 32
    allowed_column_kinds: tuple[ColumnKind | str, ...] = tuple(kind for kind in ColumnKind)
    required_column_kinds: tuple[ColumnKind | str, ...] = (
        ColumnKind.GOAL,
        ColumnKind.EVIDENCE,
        ColumnKind.EVALUATION,
        ColumnKind.SYNTHESIS,
    )
    allowed_tenant_ids: tuple[str, ...] = ()
    allowed_providers: tuple[str, ...] = ()
    allowed_models: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_retrieval_sources: tuple[str, ...] = ()
    allowed_network_hosts: tuple[str, ...] = ()
    high_risk_commitment_decisions: tuple[str, ...] = ()
    require_human_approval_for_high_risk: bool = True


@dataclass(frozen=True)
class AssemblyDefinition:
    assembly_id: str
    columns: tuple[str, ...]
    column_kinds: dict[str, ColumnKind | str] = field(default_factory=dict)
    overlays: tuple[str, ...] = ()
    column_capability_overrides: dict[str, tuple[ColumnCapability | str, ...]] = field(
        default_factory=dict
    )
    consensus_policy: ConsensusPolicy = ConsensusPolicy.WEIGHTED
    inference_mode: InferenceMode = InferenceMode.SINGLE_PASS
    budget_constraints: AssemblyBudget = field(default_factory=AssemblyBudget)
    escalation_policy: str = "escalate_to_human_review"


def _bounded_positive_int(value: int, *, field_name: str, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise AssemblyDefinitionAdmissionError(f"{field_name} must be an integer") from exc
    if normalized < 1:
        raise AssemblyDefinitionAdmissionError(f"{field_name} must be at least 1")
    if normalized > maximum:
        raise AssemblyDefinitionAdmissionError(f"{field_name} exceeds maximum {maximum}")
    return normalized


def _normalize_column_kind_or_raise(value: Any, *, column_id: str) -> ColumnKind:
    try:
        return ColumnKind(_column_kind_value(value))
    except ValueError as exc:
        raise AssemblyDefinitionAdmissionError(
            f"Unsupported column kind for column '{column_id}'"
        ) from exc


def _infer_column_kind(definition: AssemblyDefinition, column_id: str) -> ColumnKind:
    if column_id in definition.column_kinds:
        return _normalize_column_kind_or_raise(
            definition.column_kinds[column_id], column_id=column_id
        )
    prefix = str(column_id or "").strip().lower().replace("_", "-")
    prefix = prefix.split(":", 1)[0].split("-", 1)[0]
    return _normalize_column_kind_or_raise(prefix, column_id=column_id)


def _normalized_text_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(value or "").strip() for value in values if str(value or "").strip())


def validate_assembly_definition(
    definition: AssemblyDefinition,
    *,
    policy: AssemblyAdmissionPolicy | None = None,
    tenant_id: str = "",
    provider: str = "",
    model: str = "",
    tool_ids: tuple[str, ...] = (),
) -> AssemblyDefinition:
    active_policy = policy or AssemblyAdmissionPolicy()
    assembly_id = str(definition.assembly_id or "").strip()
    if not assembly_id:
        raise AssemblyDefinitionAdmissionError("assembly_id is required")

    columns = tuple(str(column_id or "").strip() for column_id in definition.columns)
    columns = tuple(column_id for column_id in columns if column_id)
    if not columns:
        raise AssemblyDefinitionAdmissionError("assembly definition must include columns")
    if len(set(columns)) != len(columns):
        raise AssemblyDefinitionAdmissionError("assembly definition contains duplicate columns")

    _bounded_positive_int(active_policy.max_columns, field_name="policy.max_columns", maximum=128)
    _bounded_positive_int(
        active_policy.max_iterations, field_name="policy.max_iterations", maximum=128
    )
    _bounded_positive_int(
        active_policy.max_messages, field_name="policy.max_messages", maximum=10_000
    )
    max_columns = min(
        active_policy.max_columns,
        _bounded_positive_int(
            definition.budget_constraints.max_columns, field_name="budget.max_columns", maximum=128
        ),
    )
    _bounded_positive_int(
        definition.budget_constraints.max_iterations,
        field_name="budget.max_iterations",
        maximum=active_policy.max_iterations,
    )
    _bounded_positive_int(
        definition.budget_constraints.max_messages,
        field_name="budget.max_messages",
        maximum=active_policy.max_messages,
    )
    if len(columns) > max_columns:
        raise AssemblyDefinitionAdmissionError("assembly definition has too many columns")

    allowed_kind_values = active_policy.allowed_column_kinds or tuple(kind for kind in ColumnKind)
    required_kind_values = active_policy.required_column_kinds or (
        ColumnKind.GOAL,
        ColumnKind.EVIDENCE,
        ColumnKind.EVALUATION,
        ColumnKind.SYNTHESIS,
    )
    allowed_kinds = {
        _normalize_column_kind_or_raise(kind, column_id="policy.allowed_column_kinds")
        for kind in allowed_kind_values
    }
    required_kinds = {
        _normalize_column_kind_or_raise(kind, column_id="policy.required_column_kinds")
        for kind in required_kind_values
    }
    observed_kinds: set[ColumnKind] = set()
    normalized_column_kinds: dict[str, ColumnKind | str] = {}
    for column_id in columns:
        column_kind = _infer_column_kind(definition, column_id)
        if column_kind not in allowed_kinds:
            raise AssemblyDefinitionAdmissionError(
                f"Column kind '{column_kind.value}' is not allowed"
            )
        observed_kinds.add(column_kind)
        normalized_column_kinds[column_id] = column_kind
    missing_kinds = sorted(kind.value for kind in required_kinds - observed_kinds)
    if missing_kinds:
        raise AssemblyDefinitionAdmissionError(
            f"assembly definition missing required column kinds: {', '.join(missing_kinds)}"
        )

    normalized_tenant_id = str(tenant_id or "").strip()
    allowed_tenants = _normalized_text_tuple(active_policy.allowed_tenant_ids)
    if allowed_tenants and normalized_tenant_id not in allowed_tenants:
        raise AssemblyDefinitionAdmissionError("tenant is not allowed for assembly definition")

    normalized_provider = str(provider or "").strip()
    allowed_providers = _normalized_text_tuple(active_policy.allowed_providers)
    if allowed_providers and normalized_provider not in allowed_providers:
        raise AssemblyDefinitionAdmissionError("provider is not allowed for assembly definition")

    normalized_model = str(model or "").strip()
    allowed_models = _normalized_text_tuple(active_policy.allowed_models)
    if allowed_models and normalized_model not in allowed_models:
        raise AssemblyDefinitionAdmissionError("model is not allowed for assembly definition")

    allowed_tools = set(_normalized_text_tuple(active_policy.allowed_tools))
    requested_tools = _normalized_text_tuple(tool_ids)
    if allowed_tools:
        disallowed_tools = [tool_id for tool_id in requested_tools if tool_id not in allowed_tools]
        if disallowed_tools:
            raise AssemblyDefinitionAdmissionError("tool is not allowed for assembly definition")

    return replace(
        definition,
        assembly_id=assembly_id,
        columns=columns,
        column_kinds=normalized_column_kinds,
    )


@dataclass(frozen=True)
class ColumnMessage:
    message_type: MessageType
    assembly_id: str
    source_column: str
    payload_ref: str
    target_column: str | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now_timestamp)

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence))


@runtime_checkable
class CognitiveColumn(Protocol):
    column_id: str
    kind: ColumnKind

    def observe(self, observation: dict[str, Any]) -> ColumnState: ...

    def update_belief(self, state: ColumnState, message: ColumnMessage) -> ColumnState: ...

    def predict(self, state: ColumnState) -> BeliefRecord | None: ...

    def evaluate(self, state: ColumnState) -> BeliefRecord | None: ...

    def emit_message(self, state: ColumnState) -> ColumnMessage | None: ...

    def update_confidence(self, state: ColumnState, feedback: dict[str, Any]) -> ColumnState: ...


class ColumnRegistry:
    def __init__(self) -> None:
        self._columns_by_id: dict[str, CognitiveColumn] = {}

    def register(self, column: CognitiveColumn) -> None:
        if column.column_id in self._columns_by_id:
            raise ValueError(f"Column '{column.column_id}' is already registered")
        self._columns_by_id[column.column_id] = column

    def get(self, column_id: str) -> CognitiveColumn:
        return self._columns_by_id[column_id]

    def list_columns(self) -> list[CognitiveColumn]:
        return list(self._columns_by_id.values())

    def list_by_kind(self, kind: ColumnKind) -> list[CognitiveColumn]:
        return [column for column in self._columns_by_id.values() if column.kind == kind]


@dataclass(frozen=True)
class ColumnVote:
    column_id: str
    decision: str
    confidence: float
    supports_commitment: bool = True
    veto: bool = False
    blockers: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp_confidence(self.confidence))


@dataclass(frozen=True)
class Commitment:
    decision: str
    confidence: float
    supporting_columns: tuple[str, ...]
    dissenting_columns: tuple[str, ...]
    blockers: tuple[str, ...]
    next_actions: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return bool(self.decision) and not self.blockers


@dataclass(frozen=True)
class AssemblyState:
    definition: AssemblyDefinition
    column_states: dict[str, ColumnState] = field(default_factory=dict)
    iteration: int = 0
    messages_emitted: int = 0
    commitment: Commitment | None = None

    def register_column_state(self, state: ColumnState) -> AssemblyState:
        next_states = dict(self.column_states)
        next_states[state.column_id] = state
        return replace(self, column_states=next_states)

    def register_message(self, count: int = 1) -> AssemblyState:
        return replace(self, messages_emitted=self.messages_emitted + max(0, count))

    def next_iteration(self) -> AssemblyState:
        return replace(self, iteration=self.iteration + 1)

    def with_commitment(
        self,
        commitment: Commitment,
        validation_context: CommitmentValidationContext | None = None,
    ) -> AssemblyState:
        if validation_context is not None:
            commitment = validate_commitment_gate(self, validation_context=validation_context)
        elif commitment.is_ready:
            _require_commitment_participation(
                self, votes=(), supporting_columns=commitment.supporting_columns
            )
        return replace(self, commitment=commitment)

    def can_continue(self) -> bool:
        budget = self.definition.budget_constraints
        if len(self.column_states) > budget.max_columns:
            return False
        if self.iteration >= budget.max_iterations:
            return False
        if self.messages_emitted >= budget.max_messages:
            return False
        if self.commitment and self.commitment.is_ready:
            return False
        return True


def build_commitment(
    votes: list[ColumnVote],
    *,
    policy: ConsensusPolicy,
    confidence_threshold: float = 0.6,
    escalation_action: str = "escalate_to_human_review",
) -> Commitment:
    if not votes:
        return Commitment(
            decision="",
            confidence=0.0,
            supporting_columns=(),
            dissenting_columns=(),
            blockers=("no_votes",),
            next_actions=("gather_more_evidence", escalation_action),
        )

    normalized_threshold = _clamp_confidence(confidence_threshold)
    total_weight = sum(vote.confidence for vote in votes)
    decision_weights: dict[str, float] = {}
    for vote in votes:
        decision_weights[vote.decision] = decision_weights.get(vote.decision, 0.0) + vote.confidence

    winning_decision = max(decision_weights.items(), key=lambda item: item[1])[0]
    supporting_votes = [
        vote
        for vote in votes
        if vote.decision == winning_decision and vote.supports_commitment and not vote.veto
    ]
    dissenting_votes = [vote for vote in votes if vote not in supporting_votes]
    confidence = 0.0 if total_weight <= 0 else decision_weights[winning_decision] / total_weight

    blockers: list[str] = []
    next_actions: list[str] = []

    veto_votes = [vote for vote in votes if vote.veto]
    if policy == ConsensusPolicy.VETO and veto_votes:
        blockers.extend(
            blocker
            for vote in veto_votes
            for blocker in vote.blockers
            if blocker and blocker not in blockers
        )
        if not blockers:
            blockers.append("veto_invoked")

    if policy == ConsensusPolicy.UNANIMOUS and dissenting_votes:
        blockers.append("consensus_not_unanimous")

    if policy == ConsensusPolicy.CONFIDENCE_THRESHOLD and confidence < normalized_threshold:
        blockers.append("confidence_below_threshold")

    if confidence < normalized_threshold:
        next_actions.append("gather_more_evidence")
        if escalation_action not in next_actions:
            next_actions.append(escalation_action)

    for vote in dissenting_votes:
        for action in vote.next_actions:
            if action and action not in next_actions:
                next_actions.append(action)
        for blocker in vote.blockers:
            if (
                blocker
                and blocker not in blockers
                and (vote.veto or policy == ConsensusPolicy.UNANIMOUS)
            ):
                blockers.append(blocker)

    return Commitment(
        decision=winning_decision,
        confidence=confidence,
        supporting_columns=tuple(vote.column_id for vote in supporting_votes),
        dissenting_columns=tuple(vote.column_id for vote in dissenting_votes),
        blockers=tuple(blockers),
        next_actions=tuple(next_actions),
    )


@dataclass(frozen=True)
class CommitmentValidationContext:
    commitment: Commitment
    votes: tuple[ColumnVote, ...]
    confidence_threshold: float = 0.6
    decision_trail: tuple[str, ...] = ()
    human_approval_granted: bool = False
    high_risk_decisions: tuple[str, ...] = ()
    require_human_approval_for_high_risk: bool = True


def _column_ids_by_kind(state: AssemblyState, kind: ColumnKind) -> set[str]:
    return {
        column_id
        for column_id, column_state in state.column_states.items()
        if column_state.kind == kind
    }


def _require_commitment_participation(
    state: AssemblyState,
    *,
    votes: tuple[ColumnVote, ...],
    supporting_columns: tuple[str, ...] = (),
) -> None:
    evidence_ids = _column_ids_by_kind(state, ColumnKind.EVIDENCE)
    evaluation_ids = _column_ids_by_kind(state, ColumnKind.EVALUATION)
    if not evidence_ids:
        raise CommitmentValidationError("evidence column participation is required")
    if not evaluation_ids:
        raise CommitmentValidationError("evaluation column participation is required")
    participant_ids = {vote.column_id for vote in votes} if votes else set(supporting_columns)
    if participant_ids:
        if evidence_ids.isdisjoint(participant_ids):
            raise CommitmentValidationError("evidence column vote is required")
        if evaluation_ids.isdisjoint(participant_ids):
            raise CommitmentValidationError("evaluation column vote is required")


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def validate_commitment_gate(
    state: AssemblyState,
    *,
    validation_context: CommitmentValidationContext,
) -> Commitment:
    commitment = validation_context.commitment
    _require_commitment_participation(state, votes=validation_context.votes)
    if not validation_context.decision_trail:
        raise CommitmentValidationError("signed audited decision trail is required")

    blockers = list(commitment.blockers)
    next_actions = list(commitment.next_actions)
    for vote in validation_context.votes:
        if vote.veto:
            if vote.blockers:
                for blocker in vote.blockers:
                    _append_unique(blockers, blocker)
            else:
                _append_unique(blockers, "veto_invoked")
            for action in vote.next_actions:
                _append_unique(next_actions, action)

    if commitment.confidence < _clamp_confidence(validation_context.confidence_threshold):
        _append_unique(blockers, "confidence_below_threshold")
        _append_unique(next_actions, "gather_more_evidence")
        _append_unique(next_actions, "escalate_to_human_review")

    high_risk_decisions = {
        str(decision or "").strip().lower()
        for decision in validation_context.high_risk_decisions
        if str(decision or "").strip()
    }
    if (
        validation_context.require_human_approval_for_high_risk
        and str(commitment.decision or "").strip().lower() in high_risk_decisions
        and not validation_context.human_approval_granted
    ):
        _append_unique(blockers, "human_approval_required")
        _append_unique(next_actions, "request_human_approval")

    if blockers or next_actions:
        return replace(commitment, blockers=tuple(blockers), next_actions=tuple(next_actions))
    return commitment
