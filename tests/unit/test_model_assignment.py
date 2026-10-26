"""LOCUS-484: primary model binding cannot leak into worker routing."""

from __future__ import annotations

from dataclasses import replace

import pytest

from locus_runtime.model_assignment import (
    ModelCandidate,
    ModelRequirement,
    RouteUnavailable,
    plan_orchestrator_and_workers,
)


def candidate(
    model: str,
    *,
    locality: str = "local",
    capabilities: frozenset[str] = frozenset({"chat", "analysis", "coding"}),
    quality: tuple[tuple[str, int], ...] = (),
    data_classes: frozenset[str] = frozenset({"internal"}),
    cost: int | None = 0,
    latency: int = 80,
) -> ModelCandidate:
    return ModelCandidate(
        model=model,
        compute_id=f"compute-{model.split('/', 1)[0]}",
        locality=locality,  # type: ignore[arg-type]
        admitted=True,
        healthy=True,
        in_quota=True,
        capabilities=capabilities,
        task_scores=quality,
        data_classes=data_classes,
        context_limit=32768,
        memory_available_bytes=32_000_000_000,
        estimated_cost_microusd=cost,
        measured_latency_ms=latency,
    )


def requirement(
    node_id: str,
    *,
    role: str = "worker",
    task_type: str = "analysis",
    explicit_model: str = "",
    data_class: str = "internal",
    local_only: bool = False,
) -> ModelRequirement:
    return ModelRequirement(
        target_id=node_id,
        role=role,  # type: ignore[arg-type]
        task_type=task_type,
        data_class=data_class,
        context_tokens=8192,
        required_capabilities=frozenset({"chat", task_type}),
        explicit_model=explicit_model,
        local_only=local_only,
    )


def plan(
    *,
    default: str = "hosted/astra",
    models: tuple[ModelCandidate, ...] | None = None,
    workers: tuple[ModelRequirement, ...] | None = None,
    worker_routing: str = "auto",
):
    profiles = models or (
        candidate(
            "hosted/astra", locality="hosted",
            capabilities=frozenset({"chat", "planning", "analysis"}),
            quality=(("planning", 95), ("analysis", 87)), cost=1500,
        ),
        candidate("ollama/sol", quality=(("coding", 91), ("analysis", 50))),
        candidate("ollama/terra", quality=(("coding", 40), ("analysis", 94))),
    )
    return plan_orchestrator_and_workers(
        orchestrator_model=default,
        orchestrator=requirement(
            "primary", role="orchestrator", task_type="planning",
        ),
        workers=workers or (
            requirement("implementation", task_type="coding"),
            requirement("assessment", task_type="analysis"),
        ),
        candidates=profiles,
        worker_routing=worker_routing,  # type: ignore[arg-type]
        catalog_snapshot_id="snapshot-1",
        policy_version="policy-8",
    )


def test_explicit_astra_orchestrator_and_independent_worker_routes() -> None:
    routes = plan()
    assert routes.orchestrator.model == "hosted/astra"
    assert routes.orchestrator.reason_code == "explicit_model_binding"
    assert [(node.target_id, node.model) for node in routes.workers] == [
        ("implementation", "ollama/sol"),
        ("assessment", "ollama/terra"),
    ]
    assert not routes.orchestrator.authorized
    assert all(not node.authorized for node in routes.workers)


def test_explicit_orchestrator_cannot_silently_fallback_when_unavailable() -> None:
    available = (candidate("ollama/sol", capabilities=frozenset({"chat", "planning"})),)
    with pytest.raises(RouteUnavailable, match="pinned_model_unavailable"):
        plan(models=available, default="hosted/astra")


def test_an_explicit_worker_pin_does_not_change_primary_model() -> None:
    routes = plan(
        workers=(
            requirement("one", task_type="analysis", explicit_model="ollama/sol"),
        ),
    )
    assert routes.orchestrator.model == "hosted/astra"
    assert routes.workers[0].model == "ollama/sol"
    assert routes.workers[0].reason_code == "explicit_model_binding"


def test_manual_workers_require_per_node_pins() -> None:
    with pytest.raises(RouteUnavailable, match="worker_pin_required"):
        plan(worker_routing="manual")


def test_auto_orchestrator_is_distinct_from_explicit_default() -> None:
    routes = plan(default="auto")
    assert routes.orchestrator.model == "hosted/astra"
    assert routes.orchestrator.reason_code == "ranked_eligible_model"
    assert routes.workers[0].model == "ollama/sol"


def test_model_name_is_not_a_hidden_quality_preference() -> None:
    profiles = (
        candidate("ollama/gpt-oss:20b", quality=(("coding", 60),), latency=60),
        candidate("ollama/new-provider", quality=(("coding", 90),), latency=70),
        candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400),
    )
    routes = plan(
        models=profiles,
        workers=(requirement("node", task_type="coding"),),
    )
    assert routes.workers[0].model == "ollama/new-provider"


def test_unknown_hosted_price_is_not_assumed_free() -> None:
    profiles = (
        candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400),
        candidate("hosted/unknown", locality="hosted", quality=(("coding", 100),), cost=None),
        candidate("ollama/terra", quality=(("coding", 15),)),
    )
    routes = plan(models=profiles, workers=(requirement("node", task_type="coding"),))
    assert routes.workers[0].model == "ollama/terra"


def test_worker_privacy_gate_rejects_hosted_even_if_higher_quality() -> None:
    profiles = (
        candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400),
        candidate("hosted/opus", locality="hosted", quality=(("analysis", 100),), cost=800),
        candidate("ollama/terra", quality=(("analysis", 80),)),
    )
    routes = plan(models=profiles, workers=(requirement("node", local_only=True),))
    assert routes.workers[0].model == "ollama/terra"


def test_worker_no_policy_eligible_candidate_fails_closed() -> None:
    profiles = (
        candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400),
        candidate("ollama/terra", data_classes=frozenset({"public"})),
    )
    with pytest.raises(RouteUnavailable, match="no_eligible_model"):
        plan(models=profiles, workers=(requirement("node", data_class="restricted"),))


def test_health_quota_and_provenance_admission_are_hard_filters() -> None:
    primary = candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400)
    blocked = replace(candidate("ollama/terra", quality=(("coding", 100),)), admitted=False)
    quota = replace(candidate("ollama/sol", quality=(("coding", 99),)), in_quota=False)
    healthy = candidate("ollama/other", quality=(("coding", 30),))
    routes = plan(
        models=(primary, blocked, quota, healthy),
        workers=(requirement("node", task_type="coding"),),
    )
    assert routes.workers[0].model == "ollama/other"


def test_context_or_compute_capacity_is_not_ranked_as_eligible() -> None:
    primary = candidate("hosted/astra", locality="hosted", capabilities=frozenset({"chat", "planning"}), cost=400)
    huge_quality = replace(candidate("ollama/terra", quality=(("coding", 100),)), context_limit=1024)
    feasible = candidate("ollama/sol", quality=(("coding", 50),))
    routes = plan(models=(primary, huge_quality, feasible), workers=(requirement("node", task_type="coding"),))
    assert routes.workers[0].model == "ollama/sol"


def test_duplicate_worker_id_and_unqualified_orchestrator_rejected() -> None:
    with pytest.raises(ValueError, match="uniquely identified"):
        plan(workers=(requirement("duplicate"), requirement("duplicate")))
    with pytest.raises(RouteUnavailable, match="orchestrator_model_not_selected"):
        plan(default="astra")
