"""LOCUS-484: real scheduler dispatch conveys independently pinned specialist models.

These are deterministic registered-worker doubles, not live LLM inference.
They exercise existing Gateway child leases and independent DAG verification.
"""

from __future__ import annotations

import hashlib

import pytest

from locus_runtime.model_assignment import SelectedRoute
from locus_runtime.pod_reservations import AdmissionDenied, ResourceBudget
from locus_runtime.task_dispatch_driver import (
    EngineBinding,
    GatewayTaskDriver,
    TrustedEnginePins,
    WorkReport,
)
from locus_runtime.task_model_binding import BoundTaskModels, PinnedWorkerModel
from tests.unit.test_task_scheduler import build


class ModelBoundWorker:
    """The trusted adapter, not the LLM, receives selected model/compute IDs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def run(self, lease, *, node, intent) -> WorkReport:
        # The real worker must perform each model inference through the
        # Gateway-bound ModelClient. This test exercises lease/tool gating;
        # it does not pretend that a model call occurred.
        allowed = lease.authorize(kind="tool_call", tool="list_issues")
        assert allowed.allowed
        self.calls.append((node.node_id, "unbound", "unbound"))
        return WorkReport(
            hashlib.sha256(node.node_id.encode()).hexdigest(),
            ResourceBudget(1, 1, 25, 5, 0),
        )

    def run_bound(self, lease, *, node, intent, model_id, compute_id) -> WorkReport:
        allowed = lease.authorize(kind="tool_call", tool="list_issues")
        assert allowed.allowed
        self.calls.append((node.node_id, model_id, compute_id))
        return WorkReport(
            hashlib.sha256(node.node_id.encode()).hexdigest(),
            ResourceBudget(1, 1, 25, 5, 0),
        )


class IndependentVerifier:
    def verify(self, **_kwargs) -> bool:
        return True


def bound_models(plan) -> BoundTaskModels:
    chosen = (
        PinnedWorkerModel("research", "ollama/sol", "gpu-1", "engine/sol"),
        PinnedWorkerModel("triage", "ollama/terra", "gpu-2", "engine/terra"),
        PinnedWorkerModel("compose", "ollama/sol", "gpu-1", "engine/sol"),
        PinnedWorkerModel("publish", "ollama/terra", "gpu-2", "engine/terra"),
    )
    return BoundTaskModels(
        plan_digest=plan.digest,
        orchestrator=SelectedRoute(
            role="orchestrator", target_id="primary", model="openai/astra",
            compute_id="host-a", eligible_models=("openai/astra",),
            reason_code="explicit_model_binding",
        ),
        workers=chosen,
        waves=plan.waves,
        catalog_snapshot_id="inventory-accepted",
        policy_version="gateway-policy-12",
        engine_pins=TrustedEnginePins(
            plan_digest=plan.digest,
            node_engines=tuple((w.node_id, w.engine_adapter_id) for w in chosen),
        ),
    )


def test_selected_subtask_models_are_preserved_through_gateway_child_dispatch(tmp_path) -> None:
    scheduler, ledger, plan, parent = build(tmp_path)
    sol, terra = ModelBoundWorker(), ModelBoundWorker()
    subject = GatewayTaskDriver(
        scheduler,
        engines=(
            EngineBinding("engine/sol", frozenset({"list_issues"}), sol),
            EngineBinding("engine/terra", frozenset({"list_issues"}), terra),
        ),
        verifier=IndependentVerifier(),
        model_routes=bound_models(plan),
    )
    result = subject.run_until_idle(plan, parent)
    assert result.status == "done"
    assert result.waves == 3
    assert sorted(sol.calls + terra.calls) == sorted([
        ("research", "ollama/sol", "gpu-1"),
        ("triage", "ollama/terra", "gpu-2"),
        ("compose", "ollama/sol", "gpu-1"),
        ("publish", "ollama/terra", "gpu-2"),
    ])
    assert result.outcomes
    assert {o.model_id for o in result.outcomes} == {"ollama/sol", "ollama/terra"}
    assert {o.compute_id for o in result.outcomes} == {"gpu-1", "gpu-2"}
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
    assert subject.run_wave(plan, parent).outcomes == ()


def test_missing_model_aware_adapter_blocks_before_child_lease(tmp_path) -> None:
    scheduler, ledger, plan, parent = build(tmp_path)
    # Registered adapter has the legacy run() method but cannot accept a
    # model-bound instruction. No fallback to a different installed engine.
    class LegacyWorker:
        def run(self, lease, *, node, intent):
            raise AssertionError("must never be invoked")

    with pytest.raises(AdmissionDenied, match="model-bound execution"):
        GatewayTaskDriver(
            scheduler,
            engines=(
                EngineBinding("engine/sol", frozenset({"list_issues"}), LegacyWorker()),
                EngineBinding("engine/terra", frozenset({"list_issues"}), ModelBoundWorker()),
            ),
            verifier=IndependentVerifier(),
            model_routes=bound_models(plan),
        ).run_wave(plan, parent)
    snap = ledger.snapshot(parent.caller.run_id)
    assert snap.held.actions == snap.used.actions == 0
    assert scheduler.ready(plan, parent) == ("research", "triage")


def test_model_routes_and_engine_pins_must_match_at_construction(tmp_path) -> None:
    scheduler, _, plan, _ = build(tmp_path)
    chosen = bound_models(plan)
    with pytest.raises(ValueError, match="same DAG"):
        GatewayTaskDriver(
            scheduler,
            engines=(
                EngineBinding("engine/sol", frozenset({"list_issues"}), ModelBoundWorker()),
                EngineBinding("engine/terra", frozenset({"list_issues"}), ModelBoundWorker()),
            ),
            verifier=IndependentVerifier(),
            engine_pins=TrustedEnginePins(
                plan_digest=plan.digest,
                node_engines=(("research", "engine/terra"),),
            ),
            model_routes=chosen,
        )
