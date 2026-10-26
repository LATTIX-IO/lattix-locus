"""LOCUS-484: selected specialist models bind to the *dependency graph*."""

from __future__ import annotations

from dataclasses import replace

import pytest

from locus_runtime.model_assignment import (
    ModelCandidate,
    ModelRequirement,
    plan_orchestrator_and_workers,
)
from locus_runtime.task_graph_contract import (
    TaskAuthority,
    TaskBudget,
    TaskIntent,
    TaskNode,
    TaskPlan,
    validate_task_plan,
)
from locus_runtime.task_model_binding import (
    InvalidModelBinding,
    bind_models_to_task_plan,
)
from locus_runtime.task_plan_compiler import CompiledTaskPlan


def admitted_plan() -> CompiledTaskPlan:
    root = TaskAuthority(
        capabilities=frozenset({"read_artifact", "write_artifact"}),
        risk_ceiling="R3",
        budget=TaskBudget(actions=12, tokens=12000, wall_seconds=900, spend_microunits=24000),
    )
    node_rights = TaskAuthority(
        capabilities=frozenset({"read_artifact"}),
        risk_ceiling="R3",
        budget=TaskBudget(actions=2, tokens=2000, wall_seconds=180, spend_microunits=1500),
    )
    plan = TaskPlan(
        intent=TaskIntent(
            goal="Design and verify a distributed architecture",
            area="work",
            requester_id="operator-1",
            acceptance_criteria=("Verified combined architecture",),
        ),
        parent_authority=root,
        nodes=(
            TaskNode("implement", (), node_rights, ("Implementation report",), ("Tests pass",)),
            TaskNode("security", (), node_rights, ("Threat report",), ("Threats identified",)),
            TaskNode("synthesize", ("implement", "security"), node_rights, ("Final architecture",), ("Both reports verified",)),
        ),
    )
    return CompiledTaskPlan(plan=plan, waves=validate_task_plan(plan), digest="a" * 64)


def routes():
    profiles = (
        ModelCandidate(
            model="openai/astra", compute_id="host-a", locality="hosted",
            admitted=True, healthy=True, in_quota=True,
            capabilities=frozenset({"chat", "planning"}),
            task_scores=(("planning", 95),),
            data_classes=frozenset({"internal"}),
            context_limit=100_000, memory_available_bytes=100_000_000_000,
            estimated_cost_microusd=1000,
        ),
        ModelCandidate(
            model="ollama/sol", compute_id="gpu-1", locality="local",
            admitted=True, healthy=True, in_quota=True,
            capabilities=frozenset({"chat", "coding", "analysis"}),
            task_scores=(("coding", 97), ("analysis", 72)),
            data_classes=frozenset({"internal"}),
            context_limit=32_768, memory_available_bytes=32_000_000_000,
            estimated_cost_microusd=0,
        ),
        ModelCandidate(
            model="ollama/terra", compute_id="gpu-2", locality="local",
            admitted=True, healthy=True, in_quota=True,
            capabilities=frozenset({"chat", "coding", "analysis"}),
            task_scores=(("coding", 62), ("analysis", 96)),
            data_classes=frozenset({"internal"}),
            context_limit=32_768, memory_available_bytes=32_000_000_000,
            estimated_cost_microusd=0,
        ),
    )
    def requirement(node: str, kind: str, role: str = "worker") -> ModelRequirement:
        return ModelRequirement(
            target_id=node,
            role=role,  # type: ignore[arg-type]
            task_type=kind,
            data_class="internal",
            context_tokens=4000,
            required_capabilities=frozenset({"chat", kind}),
        )
    return plan_orchestrator_and_workers(
        orchestrator_model="openai/astra",
        orchestrator=requirement("primary", "planning", "orchestrator"),
        workers=(
            requirement("implement", "coding"),
            requirement("security", "analysis"),
            requirement("synthesize", "analysis"),
        ),
        candidates=profiles,
        catalog_snapshot_id="inventory-3",
        policy_version="policy-12",
    )


REGISTERED = {
    ("ollama/sol", "gpu-1"): "engine-local-gpu-1",
    ("ollama/terra", "gpu-2"): "engine-local-gpu-2",
}


def test_two_independent_nodes_route_to_distinct_models_and_merge_after_verification() -> None:
    bound = bind_models_to_task_plan(
        compiled=admitted_plan(), routes=routes(), registered_adapters=REGISTERED,
        current_catalog_snapshot_id="inventory-3",
        current_policy_version="policy-12",
        expected_orchestrator_model="openai/astra",
    )
    assert bound.waves == (("implement", "security"), ("synthesize",))
    assert bound.orchestrator.model == "openai/astra"
    assert {worker.node_id: worker.model for worker in bound.workers} == {
        "implement": "ollama/sol",
        "security": "ollama/terra",
        "synthesize": "ollama/terra",
    }
    assert bound.engine_pins.plan_digest == "a" * 64
    assert dict(bound.engine_pins.node_engines) == {
        "implement": "engine-local-gpu-1",
        "security": "engine-local-gpu-2",
        "synthesize": "engine-local-gpu-2",
    }
    assert all(not worker.authorized for worker in bound.workers)


def test_missing_registered_adapter_fails_before_worker_leases() -> None:
    with pytest.raises(InvalidModelBinding, match="trusted registered engine"):
        bind_models_to_task_plan(
            compiled=admitted_plan(), routes=routes(),
            registered_adapters={("ollama/sol", "gpu-1"): "engine-local-gpu-1"},
            current_catalog_snapshot_id="inventory-3",
            current_policy_version="policy-12",
            expected_orchestrator_model="openai/astra",
        )


def test_missing_or_duplicate_worker_route_cannot_be_sealed() -> None:
    current = routes()
    for malformed in (
        replace(current, workers=current.workers[:-1]),
        replace(current, workers=current.workers + (current.workers[0],)),
        replace(current, workers=(replace(current.workers[0], authorized=True),) + current.workers[1:]),
    ):
        with pytest.raises(InvalidModelBinding):
            bind_models_to_task_plan(
                compiled=admitted_plan(), routes=malformed, registered_adapters=REGISTERED,
        current_catalog_snapshot_id="inventory-3",
        current_policy_version="policy-12",
        expected_orchestrator_model="openai/astra",
            )


def test_stale_or_modified_task_waves_cannot_be_sealed() -> None:
    compiled = admitted_plan()
    with pytest.raises(InvalidModelBinding, match="task waves differ"):
        bind_models_to_task_plan(
            compiled=replace(compiled, waves=(("security",), ("implement", "synthesize"))),
            routes=routes(), registered_adapters=REGISTERED,
        current_catalog_snapshot_id="inventory-3",
        current_policy_version="policy-12",
        expected_orchestrator_model="openai/astra",
        )


def test_no_binding_is_a_model_call_authorization() -> None:
    bound = bind_models_to_task_plan(
        compiled=admitted_plan(), routes=routes(), registered_adapters=REGISTERED,
        current_catalog_snapshot_id="inventory-3",
        current_policy_version="policy-12",
        expected_orchestrator_model="openai/astra",
    )
    assert bound.orchestrator.authorized is False
    assert all(worker.authorized is False for worker in bound.workers)


def test_rejects_stale_catalog_even_when_worker_adapter_is_registered() -> None:
    """Model capability, health and attestation changes invalidate old plans."""
    with pytest.raises(InvalidModelBinding, match="stale catalog or policy"):
        bind_models_to_task_plan(
            compiled=admitted_plan(), routes=routes(),
            registered_adapters=REGISTERED,
            current_catalog_snapshot_id="inventory-4",
            current_policy_version="policy-12",
            expected_orchestrator_model="openai/astra",
        )


def test_rejects_stale_policy_after_provider_or_data_ceiling_change() -> None:
    with pytest.raises(InvalidModelBinding, match="stale catalog or policy"):
        bind_models_to_task_plan(
            compiled=admitted_plan(), routes=routes(),
            registered_adapters=REGISTERED,
            current_catalog_snapshot_id="inventory-3",
            current_policy_version="policy-13",
            expected_orchestrator_model="openai/astra",
        )


def test_rejects_replaced_primary_orchestrator_after_user_pin_change() -> None:
    with pytest.raises(InvalidModelBinding, match="orchestrator route"):
        bind_models_to_task_plan(
            compiled=admitted_plan(), routes=routes(),
            registered_adapters=REGISTERED,
            current_catalog_snapshot_id="inventory-3",
            current_policy_version="policy-12",
            expected_orchestrator_model="ollama/other",
        )


def test_cannot_seal_without_current_trusted_revision_identifiers() -> None:
    with pytest.raises(InvalidModelBinding, match="current catalog"):
        bind_models_to_task_plan(
            compiled=admitted_plan(), routes=routes(),
            registered_adapters=REGISTERED,
            current_catalog_snapshot_id="",
            current_policy_version="policy-12",
            expected_orchestrator_model="openai/astra",
        )
