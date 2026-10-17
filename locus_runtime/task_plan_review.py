"""Version-bound, non-executable TaskPlan review projection (LOCUS-459 A).

This adapter compiles a bounded untrusted proposal against server-owned context.
It never approves a plan, evaluates live Gateway policy, creates a run, or
issues a capability grant. Treat every display field as data, not authority.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from locus_runtime.task_graph_contract import TaskAuthority, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan

REVIEW_SCHEMA_VERSION = "locus.task-plan-review/1.0"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


class InvalidPlanReviewContext(ValueError):
    """The server context cannot safely describe this plan review."""


def _identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidPlanReviewContext(f"invalid {label}")


def _fingerprint(value: str, label: str) -> None:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise InvalidPlanReviewContext(f"invalid {label}")


@dataclass(frozen=True, slots=True)
class TrustedReviewContext:
    """Supplied ONLY by the authenticated backend/policy snapshot, not model JSON.

    This type is not a principal attestation. Constructing it cannot prove
    membership, fresh policy, resource scope, or permission to dispatch.
    """

    intent: TaskIntent
    parent_authority: TaskAuthority
    actor_id: str
    tenant_id: str
    project_id: str
    project_classification_floor: str
    effective_classification: str
    policy_digest: str
    catalog_digest: str
    policy_epoch: int
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.intent, TaskIntent) or not isinstance(self.parent_authority, TaskAuthority):
            raise InvalidPlanReviewContext("trusted intent and parent envelope are required")
        for value, label in (
            (self.actor_id, "actor_id"),
            (self.tenant_id, "tenant_id"),
            (self.project_id, "project_id"),
        ):
            _identifier(value, label)
        if self.intent.requester_id != self.actor_id:
            raise InvalidPlanReviewContext("authenticated actor differs from task requester")
        for value, label in (
            (self.policy_digest, "policy_digest"),
            (self.catalog_digest, "catalog_digest"),
        ):
            _fingerprint(value, label)
        floor = _CLASSIFICATION_RANK.get(self.project_classification_floor)
        effective = _CLASSIFICATION_RANK.get(self.effective_classification)
        if floor is None or effective is None or effective < floor:
            raise InvalidPlanReviewContext("effective classification is below project floor")
        if type(self.policy_epoch) is not int or self.policy_epoch < 1:
            raise InvalidPlanReviewContext("policy_epoch must be positive")
        if type(self.revision) is not int or self.revision < 1:
            raise InvalidPlanReviewContext("revision must be positive")

    @property
    def namespace(self) -> str:
        """Canonical display namespace assembled from trusted opaque IDs."""
        return f"tenants/{self.tenant_id}/projects/{self.project_id}"


@dataclass(frozen=True, slots=True)
class PlanReviewProjection:
    """Immutable structural preview. No field carries execution authority."""

    review_digest: str
    plan_digest: str
    context: TrustedReviewContext
    waves: tuple[tuple[str, ...], ...]
    nodes: tuple[dict[str, Any], ...]
    requested_budget: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "revision": self.context.revision,
            "review_digest": self.review_digest,
            "plan_digest": self.plan_digest,
            "requester_id": self.context.actor_id,
            "area": self.context.intent.area,
            "goal": self.context.intent.goal,
            "acceptance_criteria": list(self.context.intent.acceptance_criteria),
            "tenant_id": self.context.tenant_id,
            "project_namespace": self.context.namespace,
            "classification": self.context.effective_classification,
            "project_classification_floor": self.context.project_classification_floor,
            "policy_epoch": self.context.policy_epoch,
            "policy_digest": self.context.policy_digest,
            "catalog_digest": self.context.catalog_digest,
            "parent_envelope": {
                "requested_capabilities": sorted(self.context.parent_authority.capabilities),
                "requested_risk_ceiling": self.context.parent_authority.risk_ceiling,
                "budget": asdict(self.context.parent_authority.budget),
            },
            "requested_reservations": dict(self.requested_budget),
            "waves": [list(wave) for wave in self.waves],
            "nodes": [dict(node) for node in self.nodes],
            "admission": {
                "structural_validation": "passed",
                "policy_evaluation": "not_performed",
                "agent_and_engine_binding": "not_performed",
                "gateway_authorization": "not_granted",
                "human_review": "not_recorded",
                "execution_admitted": False,
            },
        }


def build_plan_review(
    proposal: str | dict[str, Any], *, context: TrustedReviewContext
) -> PlanReviewProjection:
    """Compile strict, untrusted node JSON for display; do not authorize it.

    Caller must have independently authenticated the actor and retrieved
    current tenant/project policy, budget, definition catalog and revision.
    """
    if not isinstance(context, TrustedReviewContext):
        raise InvalidPlanReviewContext("a server-owned review context is required")
    compiled = compile_task_plan(
        proposal, intent=context.intent, parent_authority=context.parent_authority
    )
    nodes: list[dict[str, Any]] = []
    requested_budget = {
        "actions": 0,
        "tokens": 0,
        "spend_microunits": 0,
    }
    for node in compiled.plan.nodes:
        budget = asdict(node.authority.budget)
        for key in requested_budget:
            requested_budget[key] += budget[key]
        nodes.append({
            "node_id": node.node_id,
            "depends_on": list(node.depends_on),
            "requested_capabilities": sorted(node.authority.capabilities),
            "requested_risk_ceiling": node.authority.risk_ceiling,
            "requested_budget": budget,
            "deliverables": list(node.deliverables),
            "acceptance_criteria": list(node.acceptance_criteria),
            "agent_definition": None,
            "engine": None,
            "policy_decision": "not_evaluated",
        })
    # A review digest binds immutable, trusted server context and a monotonically
    # allocated revision to the full compiled plan. It is NOT a MAC/signature.
    review_binding = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "plan_digest": compiled.digest,
        "actor_id": context.actor_id,
        "area": context.intent.area,
        "tenant_id": context.tenant_id,
        "project_namespace": context.namespace,
        "effective_classification": context.effective_classification,
        "project_floor": context.project_classification_floor,
        "policy_digest": context.policy_digest,
        "policy_epoch": context.policy_epoch,
        "catalog_digest": context.catalog_digest,
        "revision": context.revision,
    }
    canonical = json.dumps(
        review_binding, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return PlanReviewProjection(
        review_digest=digest,
        plan_digest=compiled.digest,
        context=context,
        waves=compiled.waves,
        nodes=tuple(nodes),
        requested_budget=requested_budget,
    )


def matches_current_review(
    expected_review_digest: str,
    proposal: str | dict[str, Any],
    *,
    context: TrustedReviewContext,
) -> bool:
    """Compare display revisions ONLY. A match is never execution permission."""
    _fingerprint(expected_review_digest, "review_digest")
    current = build_plan_review(proposal, context=context)
    return hmac.compare_digest(expected_review_digest, current.review_digest)
