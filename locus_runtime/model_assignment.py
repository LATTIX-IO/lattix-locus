"""LOCUS-484: deterministic orchestrator/worker inference bindings.

This is a trusted-control-plane route *proposal*, not a permission grant, an
LLM, or an executor. The caller MUST populate Candidate from an authenticated,
policy-filtered runtime snapshot and authorize EACH model call with the Gateway.
No user-supplied model claim can weaken data/egress, provenance, or cost policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence

_ROLE = Literal["orchestrator", "worker"]
_QUALIFIED = re.compile(r"[a-z][a-z0-9_-]{0,31}/[^\s]{1,256}\Z", re.ASCII)
_NODE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)


class RouteUnavailable(ValueError):
    """The user or an authorized operator must supply another eligible route."""

    def __init__(self, code: str, target_id: str) -> None:
        self.code = code
        self.target_id = target_id
        super().__init__(f"{code}: {target_id}")


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """One capability profile from the server-owned, *pre-admitted* inventory.

    'admitted' is input evidence from policy, not authorization by this planner.
    A Gateway check is still mandatory for the resulting execution attempt.
    """

    model: str
    compute_id: str
    locality: Literal["local", "hosted"]
    admitted: bool
    healthy: bool
    in_quota: bool
    capabilities: frozenset[str]
    task_scores: tuple[tuple[str, int], ...]
    data_classes: frozenset[str]
    context_limit: int
    memory_available_bytes: int
    estimated_cost_microusd: int | None
    measured_latency_ms: int | None = None

    def __post_init__(self) -> None:
        if not _QUALIFIED.fullmatch(self.model):
            raise ValueError("model must be provider-qualified and bounded")
        if not self.compute_id or len(self.compute_id) > 128:
            raise ValueError("compute identifier must be nonempty and bounded")
        if self.locality not in ("local", "hosted"):
            raise ValueError("invalid locality")
        if self.context_limit <= 0 or self.memory_available_bytes < 0:
            raise ValueError("invalid model resource envelope")
        if self.estimated_cost_microusd is not None and self.estimated_cost_microusd < 0:
            raise ValueError("invalid cost quote")
        if self.measured_latency_ms is not None and self.measured_latency_ms < 0:
            raise ValueError("invalid measured latency")
        if len(set(name for name, _score in self.task_scores)) != len(self.task_scores):
            raise ValueError("duplicate quality rubric")
        if any(not name or type(score) is not int or not 0 <= score <= 100 for name, score in self.task_scores):
            raise ValueError("quality scores must be bounded measured rubric values")

    def quality(self, task_type: str) -> int:
        return dict(self.task_scores).get(task_type, -1)


@dataclass(frozen=True, slots=True)
class ModelRequirement:
    """The trusted orchestrator's required shape, not its proposed authority."""

    target_id: str
    role: _ROLE
    task_type: str
    data_class: str
    context_tokens: int
    required_capabilities: frozenset[str]
    allowed_models: frozenset[str] = frozenset()
    local_only: bool = False
    required_memory_bytes: int = 0
    explicit_model: str = ""

    def __post_init__(self) -> None:
        if not _NODE.fullmatch(self.target_id):
            raise ValueError("invalid model-routing target identifier")
        if self.role not in ("orchestrator", "worker"):
            raise ValueError("invalid routing role")
        if not self.task_type or not self.data_class:
            raise ValueError("task type and data class are required")
        if self.context_tokens <= 0 or self.required_memory_bytes < 0:
            raise ValueError("invalid task resource requirement")
        if self.explicit_model not in ("", "auto") and not _QUALIFIED.fullmatch(self.explicit_model):
            raise ValueError("explicit model must be provider-qualified")
        if any(not _QUALIFIED.fullmatch(value) for value in self.allowed_models):
            raise ValueError("allowed models must be provider-qualified")


@dataclass(frozen=True, slots=True)
class SelectedRoute:
    role: _ROLE
    target_id: str
    model: str
    compute_id: str
    eligible_models: tuple[str, ...]
    reason_code: str
    #: Always false. A Gateway-issued model_call grant is needed at use time.
    authorized: bool = False


@dataclass(frozen=True, slots=True)
class ModelRoutingPlan:
    orchestrator: SelectedRoute
    workers: tuple[SelectedRoute, ...]
    catalog_snapshot_id: str
    policy_version: str


def _eligible(candidate: ModelCandidate, req: ModelRequirement) -> bool:
    if not (candidate.admitted and candidate.healthy and candidate.in_quota):
        return False
    if not req.required_capabilities.issubset(candidate.capabilities):
        return False
    if req.data_class not in candidate.data_classes:
        return False
    if req.local_only and candidate.locality != "local":
        return False
    if req.allowed_models and candidate.model not in req.allowed_models:
        return False
    if candidate.context_limit < req.context_tokens:
        return False
    if candidate.memory_available_bytes < req.required_memory_bytes:
        return False
    # No unknown-price hosted model can be automatically ranked as free.
    if candidate.locality == "hosted" and candidate.estimated_cost_microusd is None:
        return False
    return True


def _rank(candidate: ModelCandidate, task_type: str) -> tuple[int, int, int, str]:
    """Quality is evidence-backed; known latency/cost break ties only."""
    return (
        -candidate.quality(task_type),
        candidate.measured_latency_ms if candidate.measured_latency_ms is not None else 2**31,
        candidate.estimated_cost_microusd if candidate.estimated_cost_microusd is not None else 2**31,
        candidate.model,
    )


def _choose(
    requirement: ModelRequirement,
    models: Sequence[ModelCandidate],
    *,
    mode: Literal["auto", "manual"] = "auto",
) -> SelectedRoute:
    eligible = sorted(
        (c for c in models if _eligible(c, requirement)),
        key=lambda c: _rank(c, requirement.task_type),
    )
    candidate_ids = tuple(c.model for c in eligible)
    pinned = requirement.explicit_model.strip()
    if pinned and pinned != "auto":
        selected = next((c for c in eligible if c.model == pinned), None)
        if selected is None:
            raise RouteUnavailable("pinned_model_unavailable", requirement.target_id)
        reason = "explicit_model_binding"
    else:
        if mode == "manual":
            raise RouteUnavailable("worker_pin_required", requirement.target_id)
        if not eligible:
            raise RouteUnavailable("no_eligible_model", requirement.target_id)
        selected = eligible[0]
        reason = "ranked_eligible_model"
    return SelectedRoute(
        role=requirement.role,
        target_id=requirement.target_id,
        model=selected.model,
        compute_id=selected.compute_id,
        eligible_models=candidate_ids,
        reason_code=reason,
    )


def plan_orchestrator_and_workers(
    *,
    orchestrator_model: str,
    orchestrator: ModelRequirement,
    workers: Sequence[ModelRequirement],
    candidates: Sequence[ModelCandidate],
    worker_routing: Literal["auto", "manual"] = "auto",
    catalog_snapshot_id: str,
    policy_version: str,
) -> ModelRoutingPlan:
    """Independently bind the primary orchestrator and every worker.

    No percentage allocation, model-name bias, worker-to-orchestrator rewrite,
    or implicit hosted fallback. All-or-nothing planning: callers must preserve
    the user message in a blocked/queued state if any required model is absent.
    """
    if orchestrator.role != "orchestrator" or any(node.role != "worker" for node in workers):
        raise ValueError("orchestrator and worker roles are not interchangeable")
    if not catalog_snapshot_id or not policy_version:
        raise ValueError("catalog/policy revision is mandatory")
    if not orchestrator_model or orchestrator_model != "auto" and not _QUALIFIED.fullmatch(orchestrator_model):
        raise RouteUnavailable("orchestrator_model_not_selected", orchestrator.target_id)
    if worker_routing not in ("auto", "manual"):
        raise ValueError("invalid worker routing mode")
    names = [node.target_id for node in workers]
    if orchestrator.target_id in names or len(set(names)) != len(names):
        raise ValueError("each task target must be uniquely identified")
    # An explicit orchestrator choice is binding, even if the caller supplied
    # an unrelated req.explicit_model. Never let workers reassign this engine.
    orchestrator_req = ModelRequirement(
        target_id=orchestrator.target_id,
        role="orchestrator",
        task_type=orchestrator.task_type,
        data_class=orchestrator.data_class,
        context_tokens=orchestrator.context_tokens,
        required_capabilities=orchestrator.required_capabilities,
        allowed_models=orchestrator.allowed_models,
        local_only=orchestrator.local_only,
        required_memory_bytes=orchestrator.required_memory_bytes,
        explicit_model=orchestrator_model,
    )
    primary = _choose(orchestrator_req, candidates)
    assigned = tuple(_choose(node, candidates, mode=worker_routing) for node in workers)
    return ModelRoutingPlan(
        orchestrator=primary,
        workers=assigned,
        catalog_snapshot_id=catalog_snapshot_id,
        policy_version=policy_version,
    )
