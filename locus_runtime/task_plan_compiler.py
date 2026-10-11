"""Strict admission of untrusted model-proposed task DAGs (LOCUS-411).

The model proposes only nodes and constraints. Requester/area/goal and parent
budget/capabilities come exclusively from the trusted caller, never model JSON.
This module cannot authorize or execute any action.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from locus_runtime.task_graph_contract import (
    CONTRACT_VERSION,
    MAX_NODES,
    InvalidTaskPlan,
    TaskAuthority,
    TaskBudget,
    TaskIntent,
    TaskNode,
    TaskPlan,
    validate_task_plan,
)

MAX_PROPOSAL_BYTES = 65_536
MAX_CRITERION_CHARS = 4_096
_ROOT_FIELDS = frozenset({"contract_version", "nodes"})
_NODE_FIELDS = frozenset(
    {"node_id", "depends_on", "capabilities", "risk_ceiling", "budget", "deliverables", "acceptance_criteria"}
)
_BUDGET_FIELDS = frozenset({"actions", "tokens", "wall_seconds", "spend_microunits"})


@dataclass(frozen=True, slots=True)
class CompiledTaskPlan:
    plan: TaskPlan
    waves: tuple[tuple[str, ...], ...]
    digest: str


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidTaskPlan("duplicate JSON key")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise InvalidTaskPlan(f"nonfinite JSON value {value} is prohibited")


def _fields(value: Any, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise InvalidTaskPlan(f"{label} requires exactly {sorted(expected)}")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise InvalidTaskPlan(f"{label} must be a nonempty array of strings")
    if any(not isinstance(item, str) or not item.strip() or len(item) > MAX_CRITERION_CHARS for item in value):
        raise InvalidTaskPlan(f"{label} contains an invalid string")
    return tuple(value)


def compile_task_plan(
    proposal: str | Mapping[str, Any], *, intent: TaskIntent, parent_authority: TaskAuthority
) -> CompiledTaskPlan:
    """Parse a bounded untrusted proposal into the existing side-effect-free DAG contract.

    All node budgets are *reservations*, not grant tokens; risk ceilings below
    R3 are not yet enforceable by the existing gateway and may NOT be launched.
    """
    if isinstance(proposal, str):
        raw = proposal
    elif isinstance(proposal, Mapping):
        try:
            raw = json.dumps(dict(proposal), allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise InvalidTaskPlan("proposal must be JSON-compatible") from exc
    else:
        raise InvalidTaskPlan("proposal must be a JSON object or JSON string")
    try:
        payload_size = len(raw.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise InvalidTaskPlan("invalid proposal text encoding") from exc
    if payload_size > MAX_PROPOSAL_BYTES:
        raise InvalidTaskPlan("proposal exceeds size limit")
    try:
        obj = json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_bad_constant)
    except InvalidTaskPlan:
        raise
    except (ValueError, TypeError, RecursionError) as exc:
        raise InvalidTaskPlan("invalid task proposal JSON") from exc
    root = _fields(obj, _ROOT_FIELDS, "proposal")
    if root["contract_version"] != CONTRACT_VERSION:
        raise InvalidTaskPlan("unsupported task contract version")
    proposed_nodes = root["nodes"]
    if not isinstance(proposed_nodes, list) or not 1 <= len(proposed_nodes) <= MAX_NODES:
        raise InvalidTaskPlan("proposal must have 1..256 nodes")
    nodes: list[TaskNode] = []
    for item in proposed_nodes:
        entry = _fields(item, _NODE_FIELDS, "node")
        budget = _fields(entry["budget"], _BUDGET_FIELDS, "budget")
        if not isinstance(entry["node_id"], str):
            raise InvalidTaskPlan("node ID must be a string")
        if not isinstance(entry["risk_ceiling"], str):
            raise InvalidTaskPlan("risk ceiling must be a string")
        for key in _BUDGET_FIELDS:
            if type(budget[key]) is not int:
                raise InvalidTaskPlan("budget values must be integers")
        dependencies = entry["depends_on"]
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            raise InvalidTaskPlan("dependencies must be an array of strings")
        capabilities = entry["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(cap, str) for cap in capabilities):
            raise InvalidTaskPlan("capabilities must be an array of strings")
        if len(capabilities) != len(set(capabilities)):
            raise InvalidTaskPlan("duplicate proposed capability")
        nodes.append(TaskNode(
            node_id=entry["node_id"],
            depends_on=tuple(dependencies),
            authority=TaskAuthority(
                capabilities=frozenset(capabilities),
                risk_ceiling=entry["risk_ceiling"],
                budget=TaskBudget(**budget),
            ),
            deliverables=_strings(entry["deliverables"], "deliverables"),
            acceptance_criteria=_strings(entry["acceptance_criteria"], "acceptance criteria"),
        ))
    plan = TaskPlan(intent=intent, parent_authority=parent_authority, nodes=tuple(nodes))
    waves = validate_task_plan(plan)
    # Bind the revision identity to the trusted context. Identical suggested
    # steps in two areas/principals must never share an admission identity.
    context = {
        "proposal": root,
        "intent": asdict(intent),
        "parent_authority": {
            "capabilities": sorted(parent_authority.capabilities),
            "risk_ceiling": parent_authority.risk_ceiling,
            "budget": asdict(parent_authority.budget),
        },
    }
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return CompiledTaskPlan(plan, waves, hashlib.sha256(canonical.encode("utf-8")).hexdigest())
