"""LOCUS-459 A: backend-owned, non-executable TaskPlan review projection."""

from dataclasses import replace

import pytest

from locus_runtime.task_graph_contract import (
    InvalidTaskPlan,
    TaskAuthority,
    TaskBudget,
    TaskIntent,
)
from locus_runtime.task_plan_review import (
    InvalidPlanReviewContext,
    TrustedReviewContext,
    build_plan_review,
    matches_current_review,
)


def _context(**overrides):
    base = TrustedReviewContext(
        intent=TaskIntent(
            "Research and publish a grounded policy brief",
            "professional",
            "user-42",
            ("Verify primary sources", "No unsupported conclusions"),
        ),
        parent_authority=TaskAuthority(
            frozenset({"sources.read", "document.write"}),
            "R2",
            TaskBudget(8, 4000, 600, 500000),
        ),
        actor_id="user-42",
        tenant_id="org-1",
        project_id="project-99",
        project_classification_floor="internal",
        effective_classification="confidential",
        policy_digest="a" * 64,
        catalog_digest="b" * 64,
        policy_epoch=17,
        revision=1,
    )
    return replace(base, **overrides)


def _proposal():
    return {
        "contract_version": "1.0",
        "nodes": [
            {
                "node_id": "brief",
                "depends_on": ["sources"],
                "capabilities": ["document.write"],
                "risk_ceiling": "R2",
                "budget": {
                    "actions": 2,
                    "tokens": 1000,
                    "wall_seconds": 180,
                    "spend_microunits": 100000,
                },
                "deliverables": ["source-linked policy brief"],
                "acceptance_criteria": ["Evidence citations independently reviewed"],
            },
            {
                "node_id": "sources",
                "depends_on": [],
                "capabilities": ["sources.read"],
                "risk_ceiling": "R1",
                "budget": {
                    "actions": 3,
                    "tokens": 1500,
                    "wall_seconds": 240,
                    "spend_microunits": 150000,
                },
                "deliverables": ["verifiable source notes"],
                "acceptance_criteria": ["Primary sources recorded"],
            },
        ],
    }


def test_review_contains_honest_display_data_not_authorization():
    result = build_plan_review(_proposal(), context=_context()).to_dict()
    assert result["schema_version"] == "locus.task-plan-review/1.0"
    assert result["waves"] == [["sources"], ["brief"]]
    assert result["requester_id"] == "user-42"
    assert result["project_namespace"] == "tenants/org-1/projects/project-99"
    assert result["classification"] == "confidential"
    assert result["requested_reservations"] == {
        "actions": 5, "tokens": 2500, "spend_microunits": 250000
    }
    assert result["nodes"][0]["agent_definition"] is None
    assert result["nodes"][1]["policy_decision"] == "not_evaluated"
    assert result["admission"] == {
        "structural_validation": "passed",
        "policy_evaluation": "not_performed",
        "agent_and_engine_binding": "not_performed",
        "gateway_authorization": "not_granted",
        "human_review": "not_recorded",
        "execution_admitted": False,
    }


def test_same_revision_recomputes_exact_digest_from_same_proposal_and_context():
    context = _context()
    result = build_plan_review(_proposal(), context=context)
    assert matches_current_review(result.review_digest, _proposal(), context=context)
    assert result.review_digest != result.plan_digest


@pytest.mark.parametrize(
    "change",
    [
        {"revision": 2},
        {"policy_epoch": 18},
        {"policy_digest": "c" * 64},
        {"catalog_digest": "d" * 64},
        {"project_id": "other"},
        {"tenant_id": "foreign"},
        {"effective_classification": "restricted"},
    ],
)
def test_policy_catalog_tenant_and_revision_changes_invalidate_preview(change):
    proposal = _proposal()
    old = build_plan_review(proposal, context=_context())
    new_context = _context(**change)
    assert not matches_current_review(old.review_digest, proposal, context=new_context)


def test_new_requester_and_area_invalidate_and_bind_to_server():
    old = build_plan_review(_proposal(), context=_context())
    next_intent = replace(_context().intent, requester_id="user-43")
    new_actor = _context(intent=next_intent, actor_id="user-43")
    assert not matches_current_review(old.review_digest, _proposal(), context=new_actor)
    new_area = _context(intent=replace(_context().intent, area="personal"))
    assert not matches_current_review(old.review_digest, _proposal(), context=new_area)


def test_any_material_plan_change_invalidates_previous_review():
    original = build_plan_review(_proposal(), context=_context())
    edited = _proposal()
    edited["nodes"][0]["deliverables"] = ["different artifact"]
    assert not matches_current_review(
        original.review_digest, edited, context=_context()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": "foreign"},
        {"tenant_id": "../other"},
        {"project_id": "x/../y"},
        {"project_classification_floor": "restricted"},
        {"effective_classification": "secret"},
        {"policy_digest": "wrong"},
        {"catalog_digest": "f" * 63},
        {"policy_epoch": 0},
        {"policy_epoch": True},
        {"revision": 0},
        {"revision": False},
    ],
)
def test_untrusted_or_inconsistent_server_context_rejected(change):
    with pytest.raises(InvalidPlanReviewContext):
        _context(**change)


def test_model_cannot_override_trusted_authority_or_policy_information():
    proposal = _proposal()
    proposal["approved"] = True
    with pytest.raises(InvalidTaskPlan, match="requires exactly"):
        build_plan_review(proposal, context=_context())
    proposal = _proposal()
    proposal["nodes"][0]["tenant_id"] = "foreign"
    with pytest.raises(InvalidTaskPlan, match="requires exactly"):
        build_plan_review(proposal, context=_context())


def test_cycles_and_budget_escalations_are_still_refused():
    proposal = _proposal()
    proposal["nodes"][1]["depends_on"] = ["brief"]
    with pytest.raises(InvalidTaskPlan, match="cycle"):
        build_plan_review(proposal, context=_context())
    proposal = _proposal()
    proposal["nodes"][1]["budget"]["spend_microunits"] = 900000
    with pytest.raises(InvalidTaskPlan, match="reservations"):
        build_plan_review(proposal, context=_context())


def test_display_digest_is_never_a_capability_grant():
    review = build_plan_review(_proposal(), context=_context()).to_dict()
    assert "grants" not in review
    assert "approve" not in review
    assert review["admission"]["execution_admitted"] is False


def test_malformed_digest_and_missing_trusted_context_fail_closed():
    with pytest.raises(InvalidPlanReviewContext, match="review_digest"):
        matches_current_review("not-a-digest", _proposal(), context=_context())
    with pytest.raises(InvalidPlanReviewContext, match="server-owned"):
        build_plan_review(_proposal(), context=None)


def test_cross_domain_task_does_not_depend_on_software_engine():
    personal = _context(
        intent=TaskIntent(
            "Build a weekly household grocery research brief",
            "personal",
            "user-42",
            ("Record ingredient constraints and sources",),
        )
    )
    review = build_plan_review(_proposal(), context=personal).to_dict()
    assert review["area"] == "personal"
    assert all(node["engine"] is None for node in review["nodes"])
