"""Red/green validation of untrusted model plan proposals and parent ownership."""

import json
from dataclasses import replace

import pytest

from locus_runtime.task_graph_contract import InvalidTaskPlan, TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan


@pytest.fixture
def args():
    return {
        "intent": TaskIntent("Produce market brief", "professional", "human-42", ("Review references",)),
        "parent_authority": TaskAuthority(frozenset({"search", "write:workspace"}), "R3", TaskBudget(10, 2000, 600, 10000)),
    }


def proposal():
    def n(name, deps):
        return {"node_id": name, "depends_on": deps, "capabilities": ["search"], "risk_ceiling": "R1", "budget": {"actions": 2, "tokens": 300, "wall_seconds": 120, "spend_microunits": 100}, "deliverables": [f"artifact:{name}"], "acceptance_criteria": ["Independent citations pass"]}
    return {"contract_version": "1.0", "nodes": [n("summary", ["sources", "metrics"]), n("metrics", []), n("sources", [])]}


def test_cross_domain_plan_is_deterministic_and_principal_is_not_from_model(args):
    document = proposal()
    one = compile_task_plan(document, **args)
    two = compile_task_plan(json.dumps(document, sort_keys=True), **args)
    assert one.digest == two.digest
    assert one.waves == (("metrics", "sources"), ("summary",))
    assert one.plan.intent.requester_id == "human-42"
    assert one.plan.intent.area == "professional"


@pytest.mark.parametrize("extra", [{"requester_id": "admin"}, {"area": "another"}, {"verified": True}, {"model": "untrusted"}])
def test_model_cannot_override_trusted_root_fields(args, extra):
    payload = proposal() | extra
    with pytest.raises(InvalidTaskPlan, match="proposal requires exactly"):
        compile_task_plan(payload, **args)


def test_model_cannot_smuggle_node_or_budget_fields(args):
    payload = proposal()
    payload["nodes"][0]["trusted"] = True
    with pytest.raises(InvalidTaskPlan, match="node requires exactly"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][0]["budget"]["unknown"] = 0
    with pytest.raises(InvalidTaskPlan, match="budget requires exactly"):
        compile_task_plan(payload, **args)


def test_json_duplicate_keys_nonfinite_and_payload_limits_are_refused(args):
    with pytest.raises(InvalidTaskPlan, match="duplicate JSON key"):
        compile_task_plan('{"contract_version":"1.0","contract_version":"1.0","nodes":[]}', **args)
    payload = proposal()
    payload["nodes"][0]["budget"]["tokens"] = float("nan")
    with pytest.raises(InvalidTaskPlan, match="JSON-compatible"):
        compile_task_plan(payload, **args)
    with pytest.raises(InvalidTaskPlan, match="size limit"):
        compile_task_plan(" " * 65537, **args)


def test_capability_budget_cycle_and_dependency_are_checked(args):
    payload = proposal()
    payload["nodes"][0]["capabilities"] = ["secrets.read"]
    with pytest.raises(InvalidTaskPlan, match="capabilities outside"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][0]["budget"]["actions"] = 10000
    with pytest.raises(InvalidTaskPlan, match="reservations"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][1]["depends_on"] = ["summary"]
    with pytest.raises(InvalidTaskPlan, match="cycle"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][0]["depends_on"] = ["not-there"]
    with pytest.raises(InvalidTaskPlan, match="unknown node"):
        compile_task_plan(payload, **args)


def test_reject_nonstring_dependency_unsafe_type_and_empty_criteria(args):
    payload = proposal()
    payload["nodes"][0]["depends_on"] = [3]
    with pytest.raises(InvalidTaskPlan, match="dependencies"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][0]["budget"]["actions"] = True
    with pytest.raises(InvalidTaskPlan, match="integers"):
        compile_task_plan(payload, **args)
    payload = proposal()
    payload["nodes"][0]["acceptance_criteria"] = []
    with pytest.raises(InvalidTaskPlan, match="nonempty"):
        compile_task_plan(payload, **args)


def test_plan_digest_is_bound_to_principal_area_and_parent_budget(args):
    proposal_data = proposal()
    initial = compile_task_plan(proposal_data, **args).digest
    moved_principal = replace(args["intent"], requester_id="other-human")
    moved_area = replace(args["intent"], area="personal")
    smaller = replace(args["parent_authority"], budget=TaskBudget(9, 2000, 600, 10000))
    assert initial != compile_task_plan(proposal_data, intent=moved_principal, parent_authority=args["parent_authority"]).digest
    assert initial != compile_task_plan(proposal_data, intent=moved_area, parent_authority=args["parent_authority"]).digest
    assert initial != compile_task_plan(proposal_data, intent=args["intent"], parent_authority=smaller).digest


def test_invalid_unicode_and_nested_json_duplicate_key_are_denied(args):
    with pytest.raises(InvalidTaskPlan, match="encoding"):
        compile_task_plan("\ud800", **args)
    bad = json.dumps(proposal())
    bad = bad.replace('"actions": 2', '"actions": 2, "actions": 2', 1)
    with pytest.raises(InvalidTaskPlan, match="duplicate JSON key"):
        compile_task_plan(bad, **args)
