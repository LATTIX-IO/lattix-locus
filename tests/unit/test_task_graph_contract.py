"""Admission-contract tests: no model, tools, engine or gateway instantiated."""

from dataclasses import replace

import pytest

from locus_runtime.task_graph_contract import (
    CONTRACT_VERSION,
    InvalidTaskPlan,
    TaskAuthority,
    TaskBudget,
    TaskIntent,
    TaskNode,
    TaskPlan,
    eligible_nodes,
    validate_task_plan,
)


def _authority(*, capabilities=frozenset({"search", "write:workspace"}), risk="R2", actions=10,
               tokens=1000, wall_seconds=120, spend=100) -> TaskAuthority:
    return TaskAuthority(frozenset(capabilities), risk, TaskBudget(actions, tokens, wall_seconds, spend))


def _node(name: str, *dependencies: str, authority: TaskAuthority | None = None) -> TaskNode:
    return TaskNode(name, tuple(dependencies), authority or _authority(actions=2, tokens=200, spend=20),
                    (f"artifact:{name}",), ("independent reviewer accepts output",))


def _plan(*nodes: TaskNode, parent: TaskAuthority | None = None) -> TaskPlan:
    return TaskPlan(TaskIntent("Research and summarize", "personal", "principal-1", ("citations checked",)),
                    parent or _authority(), tuple(nodes))


def test_contract_version_and_parallel_diamond_order_are_deterministic() -> None:
    plan = _plan(_node("final", "a", "b"), _node("b", "root"),
                 _node("root"), _node("a", "root"))
    assert plan.contract_version == CONTRACT_VERSION
    assert validate_task_plan(plan) == (("root",), ("a", "b"), ("final",))
    assert eligible_nodes(plan, set()) == ("root",)
    assert eligible_nodes(plan, {"root"}) == ("a", "b")
    assert eligible_nodes(plan, {"root", "a"}) == ("b",)
    assert eligible_nodes(plan, {"root", "a", "b"}) == ("final",)


@pytest.mark.parametrize("nodes,error", [
    (("cycle",), "cycle"),
    (("unknown",), "unknown node"),
    (("duplicate",), "duplicate task"),
    (("self",), "itself"),
])
def test_reject_invalid_graph(nodes: tuple[str, ...], error: str) -> None:
    kind = nodes[0]
    candidates = {
        "cycle": (_node("a", "b"), _node("b", "a")),
        "unknown": (_node("a", "missing"),),
        "duplicate": (_node("a"), _node("a")),
        "self": (_node("a", "a"),),
    }
    with pytest.raises(InvalidTaskPlan, match=error):
        validate_task_plan(_plan(*candidates[kind]))


def test_reject_capability_and_risk_escalation() -> None:
    with pytest.raises(InvalidTaskPlan, match="capabilities outside"):
        validate_task_plan(_plan(_node("a", authority=_authority(capabilities={"send:email"}))))
    with pytest.raises(InvalidTaskPlan, match="risk ceiling"):
        validate_task_plan(_plan(_node("a"), parent=_authority(risk="R0")))
    with pytest.raises(InvalidTaskPlan, match="R4 is forbidden"):
        _authority(risk="R4")


def test_parent_budget_is_not_oversubscribed_by_parallel_pods() -> None:
    with pytest.raises(InvalidTaskPlan, match="reservations"):
        validate_task_plan(_plan(_node("a"), _node("b"), parent=_authority(actions=3)))
    with pytest.raises(InvalidTaskPlan, match="lifetime exceeds"):
        validate_task_plan(_plan(_node("a"), parent=_authority(wall_seconds=60)))
    with pytest.raises(InvalidTaskPlan, match="nonnegative integers"):
        TaskBudget(1, 1, 2, True)


def test_reject_unknown_verified_predecessor_and_unrecognized_version() -> None:
    p = _plan(_node("a"))
    with pytest.raises(InvalidTaskPlan, match="unknown verified"):
        eligible_nodes(p, {"untrusted"})
    with pytest.raises(InvalidTaskPlan, match="unsupported task contract"):
        validate_task_plan(replace(p, contract_version="2.0"))


def test_nonempty_goal_verifier_and_deliverables_are_required() -> None:
    with pytest.raises(InvalidTaskPlan, match="acceptance criteria"):
        TaskIntent("goal", "area", "who", ())
    with pytest.raises(InvalidTaskPlan, match="declare deliverables"):
        validate_task_plan(_plan(replace(_node("a"), deliverables=())))
