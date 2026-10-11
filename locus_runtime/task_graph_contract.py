"""Versioned, side-effect-free task graph admission contract (LOCUS-411).

This module is not an orchestrator or an authorization decision point. Admission
checks only structural validity and that proposed pod authority is no broader
than the enclosing run. Every action still requires an independent gateway
policy decision and its current grant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

CONTRACT_VERSION = "1.0"
MAX_NODES = 256
MAX_EDGES = 2048
_NODE_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,63}\Z")
_RISK_RANK = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}


class InvalidTaskPlan(ValueError):
    """A task plan cannot be admitted without correction."""


@dataclass(frozen=True, slots=True)
class TaskBudget:
    """Integer resource reservations. Spend is in micro-units of local currency."""

    actions: int
    tokens: int
    wall_seconds: int
    spend_microunits: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.actions, self.tokens, self.wall_seconds, self.spend_microunits)
        ):
            raise InvalidTaskPlan("budgets must contain nonnegative integers")
        if self.wall_seconds == 0:
            raise InvalidTaskPlan("wall_seconds must be positive")


@dataclass(frozen=True, slots=True)
class TaskAuthority:
    """An upper bound, not an authorization grant."""

    capabilities: frozenset[str]
    risk_ceiling: str
    budget: TaskBudget

    def __post_init__(self) -> None:
        if self.risk_ceiling not in _RISK_RANK:
            raise InvalidTaskPlan("risk ceiling must be R0..R3 (R4 is forbidden)")
        if any(not c or not c.strip() for c in self.capabilities):
            raise InvalidTaskPlan("capabilities must be nonblank")


@dataclass(frozen=True, slots=True)
class TaskIntent:
    goal: str
    area: str
    requester_id: str
    acceptance_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.goal.strip() or not self.area.strip() or not self.requester_id.strip():
            raise InvalidTaskPlan("goal, area and requester identity are required")
        if not self.acceptance_criteria or any(not x.strip() for x in self.acceptance_criteria):
            raise InvalidTaskPlan("acceptance criteria must be explicit")


@dataclass(frozen=True, slots=True)
class TaskNode:
    node_id: str
    depends_on: tuple[str, ...]
    authority: TaskAuthority
    deliverables: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TaskPlan:
    intent: TaskIntent
    parent_authority: TaskAuthority
    nodes: tuple[TaskNode, ...]
    contract_version: str = CONTRACT_VERSION


def validate_task_plan(plan: TaskPlan) -> tuple[tuple[str, ...], ...]:
    """Return deterministic topological waves or refuse an unsafe/invalid plan.

    Resource allocations are conservatively reserved across all pods. Runtime
    admission must *also* enforce aggregate live consumption and concurrency.
    """
    if plan.contract_version != CONTRACT_VERSION:
        raise InvalidTaskPlan("unsupported task contract version")
    if not plan.nodes or len(plan.nodes) > MAX_NODES:
        raise InvalidTaskPlan("task plan must have 1..256 nodes")
    nodes: dict[str, TaskNode] = {}
    totals = {key: 0 for key in ("actions", "tokens", "spend_microunits")}
    edge_count = 0
    for node in plan.nodes:
        if not _NODE_ID.fullmatch(node.node_id):
            raise InvalidTaskPlan("invalid task node identifier")
        if node.node_id in nodes:
            raise InvalidTaskPlan("duplicate task node identifier")
        if not node.deliverables or any(not item.strip() for item in node.deliverables):
            raise InvalidTaskPlan("every node must declare deliverables")
        if not node.acceptance_criteria or any(not item.strip() for item in node.acceptance_criteria):
            raise InvalidTaskPlan("every node needs independent acceptance criteria")
        if len(node.depends_on) != len(set(node.depends_on)):
            raise InvalidTaskPlan("duplicate dependency")
        if not node.authority.capabilities.issubset(plan.parent_authority.capabilities):
            raise InvalidTaskPlan("node requested capabilities outside parent envelope")
        if _RISK_RANK[node.authority.risk_ceiling] > _RISK_RANK[plan.parent_authority.risk_ceiling]:
            raise InvalidTaskPlan("node risk ceiling widens parent envelope")
        if node.authority.budget.wall_seconds > plan.parent_authority.budget.wall_seconds:
            raise InvalidTaskPlan("node lifetime exceeds parent envelope")
        for key in totals:
            totals[key] += getattr(node.authority.budget, key)
        edge_count += len(node.depends_on)
        nodes[node.node_id] = node
    if edge_count > MAX_EDGES:
        raise InvalidTaskPlan("too many task dependencies")
    for key, reserved in totals.items():
        if reserved > getattr(plan.parent_authority.budget, key):
            raise InvalidTaskPlan(f"pod {key} reservations exceed parent envelope")
    successors: dict[str, set[str]] = {name: set() for name in nodes}
    indegree: dict[str, int] = {name: 0 for name in nodes}
    for node in plan.nodes:
        for predecessor in node.depends_on:
            if predecessor not in nodes:
                raise InvalidTaskPlan("dependency references unknown node")
            if predecessor == node.node_id:
                raise InvalidTaskPlan("node cannot depend on itself")
            successors[predecessor].add(node.node_id)
            indegree[node.node_id] += 1
    wave = sorted(name for name, degree in indegree.items() if degree == 0)
    waves: list[tuple[str, ...]] = []
    visited = 0
    while wave:
        waves.append(tuple(wave))
        visited += len(wave)
        following: list[str] = []
        for name in wave:
            for consumer in successors[name]:
                indegree[consumer] -= 1
                if indegree[consumer] == 0:
                    following.append(consumer)
        wave = sorted(following)
    if visited != len(nodes):
        raise InvalidTaskPlan("cycle in task dependency graph")
    return tuple(waves)


def eligible_nodes(plan: TaskPlan, verified_node_ids: Iterable[str]) -> tuple[str, ...]:
    """Identify ready work from *externally verified* predecessors only.

    This helper deliberately does not mark outputs verified, accept artifacts,
    lease pods or trigger actions. Those require future orchestrator services.
    """
    validate_task_plan(plan)
    known = {node.node_id for node in plan.nodes}
    verified = set(verified_node_ids)
    if not verified.issubset(known):
        raise InvalidTaskPlan("unknown verified node")
    return tuple(
        sorted(
            node.node_id
            for node in plan.nodes
            if node.node_id not in verified and set(node.depends_on).issubset(verified)
        )
    )
