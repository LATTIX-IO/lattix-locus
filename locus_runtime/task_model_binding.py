"""LOCUS-484: seal separately selected worker models against an admitted task DAG.

No inference, shell execution, model credentials, lease creation or Gateway grant
is performed here. Existing TrustedEnginePins binds the registered *engine*
adapter for each child. Model identities remain separately immutable and MUST
be checked again by ModelClient/Gateway at each actual model_call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from locus_runtime.model_assignment import ModelRoutingPlan, SelectedRoute
from locus_runtime.task_dispatch_driver import TrustedEnginePins
from locus_runtime.task_graph_contract import validate_task_plan
from locus_runtime.task_plan_compiler import CompiledTaskPlan

_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_ENGINE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class PinnedWorkerModel:
    node_id: str
    model: str
    compute_id: str
    engine_adapter_id: str
    #: These are *proposed* model/compute routes, never inference grants.
    authorized: bool = False


@dataclass(frozen=True, slots=True)
class BoundTaskModels:
    """Unambiguous exact-DAG revision with distinct orchestrator/child choices."""

    plan_digest: str
    orchestrator: SelectedRoute
    workers: tuple[PinnedWorkerModel, ...]
    waves: tuple[tuple[str, ...], ...]
    catalog_snapshot_id: str
    policy_version: str
    engine_pins: TrustedEnginePins


class InvalidModelBinding(ValueError):
    """A route cannot be sealed against this accepted DAG/adapter inventory."""


def bind_models_to_task_plan(
    *,
    compiled: CompiledTaskPlan,
    routes: ModelRoutingPlan,
    registered_adapters: Mapping[tuple[str, str], str],
) -> BoundTaskModels:
    """Validate the complete immutable model selection before a child lease.

    The server supplies registered_adapters as (exact model ID, exact compute
    ID) -> trusted engine adapter ID; it MUST NOT be model-provided input.
    The existing dispatcher checks model-independent tool capabilities and
    Gateway authority. A separate model_call Gateway check is still required.
    """
    if not isinstance(compiled, CompiledTaskPlan) or not isinstance(routes, ModelRoutingPlan):
        raise InvalidModelBinding("typed task plan and routing plan are required")
    if not _DIGEST.fullmatch(compiled.digest):
        raise InvalidModelBinding("invalid or absent canonical task plan digest")
    waves = validate_task_plan(compiled.plan)
    if waves != compiled.waves:
        raise InvalidModelBinding("task waves differ from admitted canonical dependency graph")
    if not routes.catalog_snapshot_id or not routes.policy_version:
        raise InvalidModelBinding("catalog and policy revisions must be recorded")
    if routes.orchestrator.role != "orchestrator" or routes.orchestrator.authorized:
        raise InvalidModelBinding("orchestrator binding is an unprivileged selection")
    if not routes.orchestrator.model or not routes.orchestrator.compute_id:
        raise InvalidModelBinding("orchestrator model and compute are mandatory")

    tasks = {node.node_id for node in compiled.plan.nodes}
    assignments: dict[str, SelectedRoute] = {}
    for route in routes.workers:
        if route.role != "worker" or route.authorized or route.target_id in assignments:
            raise InvalidModelBinding("duplicate, privileged, or non-worker model route")
        assignments[route.target_id] = route
    if set(assignments) != tasks:
        raise InvalidModelBinding("worker route coverage must match exact admitted DAG")

    pins: list[tuple[str, str]] = []
    chosen: list[PinnedWorkerModel] = []
    for node_id in sorted(tasks):
        route = assignments[node_id]
        engine_id = registered_adapters.get((route.model, route.compute_id))
        if not isinstance(engine_id, str) or not _ENGINE.fullmatch(engine_id):
            raise InvalidModelBinding("worker model/compute has no trusted registered engine")
        if not route.model or not route.compute_id:
            raise InvalidModelBinding("worker requires a model and compute ID")
        pins.append((node_id, engine_id))
        chosen.append(
            PinnedWorkerModel(
                node_id=node_id,
                model=route.model,
                compute_id=route.compute_id,
                engine_adapter_id=engine_id,
            )
        )

    return BoundTaskModels(
        plan_digest=compiled.digest,
        orchestrator=routes.orchestrator,
        workers=tuple(chosen),
        waves=waves,
        catalog_snapshot_id=routes.catalog_snapshot_id,
        policy_version=routes.policy_version,
        engine_pins=TrustedEnginePins(
            plan_digest=compiled.digest,
            node_engines=tuple(pins),
        ),
    )
