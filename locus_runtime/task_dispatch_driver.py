"""Trusted runtime-driver port for executing admitted task DAG waves (LOCUS-413).

This driver never spawns a process, authorizes an action, imports a vendor SDK,
or decides that model output is verified. It connects the accepted DAG and
Gateway-issued PodLease to administrator-registered engine adapters. External
CLIs and models must be implemented by separately audited, mediated adapters.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Protocol

from locus_runtime.gateway import GatewaySession
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, ResourceBudget
from locus_runtime.task_graph_contract import TaskIntent, TaskNode
from locus_runtime.task_plan_compiler import CompiledTaskPlan
from locus_runtime.task_scheduler import (
    DurableTaskScheduler,
    IndependentVerifier,
    NodeClaim,
    VerificationRejected,
    VerificationUnavailable,
)

_ENGINE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}\Z", re.ASCII)
_ARTIFACT = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


_NODE_PIN_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}\\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class TrustedEnginePins:
    """Supervisor-owned engine choices for one immutable DAG revision.

    This is not an authorization grant or a model-supplied plan field. Only
    trusted control-plane code may construct and pass it to the dispatcher.
    The Gateway still owns all execution and tool authority.
    """

    plan_digest: str
    node_engines: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan_digest, str) or _ARTIFACT.fullmatch(self.plan_digest) is None:
            raise ValueError("engine pins require a canonical plan digest")
        if not isinstance(self.node_engines, tuple) or not self.node_engines:
            raise ValueError("engine pins require a nonempty immutable node mapping")
        nodes: set[str] = set()
        for entry in self.node_engines:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("engine pin entries must be immutable node/engine pairs")
            node_id, engine_id = entry
            if (
                not isinstance(node_id, str)
                or _NODE_PIN_ID.fullmatch(node_id) is None
                or not isinstance(engine_id, str)
                or _ENGINE_ID.fullmatch(engine_id) is None
            ):
                raise ValueError("invalid trusted engine pin identifier")
            if node_id in nodes:
                raise ValueError("duplicate trusted engine pin")
            nodes.add(node_id)


@dataclass(frozen=True, slots=True)
class WorkReport:
    """Worker output is an assertion until a separate trusted verifier passes."""

    artifact_sha256: str
    consumed: ResourceBudget


class TrustedWorker(Protocol):
    """Administrator-registered adapter using existing Gateway/pod tool broker.

    Implementations must keep the untrusted model or CLI in its admitted OS
    jail. Never give the untrusted process this Python object or a raw
    GatewaySession: it may submit only bounded calls to the admitted broker.
    """

    def run(
        self, lease: PodLease, *, node: TaskNode, intent: TaskIntent,
    ) -> WorkReport: ...


@dataclass(frozen=True, slots=True)
class EngineBinding:
    """Explicit operator-installed engine adapter; not an LLM-proposed command."""

    engine_id: str
    supported_tools: frozenset[str]
    worker: TrustedWorker

    def __post_init__(self) -> None:
        if not isinstance(self.engine_id, str) or _ENGINE_ID.fullmatch(self.engine_id) is None:
            raise ValueError("engine ID must be a bounded canonical identifier")
        if not isinstance(self.supported_tools, frozenset) or any(
            not isinstance(name, str) or not name.strip() for name in self.supported_tools
        ):
            raise ValueError("engine capabilities must be canonical strings")
        if not callable(getattr(self.worker, "run", None)):
            raise ValueError("registered engine must provide a worker")


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    node_id: str
    engine_id: str
    # verified / blocked / uncertain; never translate these into success.
    status: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class DispatchWave:
    outcomes: tuple[DispatchOutcome, ...]
    ready_after: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchSummary:
    # done / blocked / waiting / stopped / budget
    status: str
    waves: int
    outcomes: tuple[DispatchOutcome, ...]
    node_states: dict[str, str]


class GatewayTaskDriver:
    """Bounded local coordinator. Agent engine/provider remains an adapter choice.

    Each wave prepares at most max_parallel child leases using the existing
    ledger and Gateway, then invokes registered trusted adapters concurrently.
    The adapter owns OS-level cancellation/timeout and mediated tools; a
    Python thread is NOT a security sandbox or process timeout.
    """

    def __init__(
        self,
        scheduler: DurableTaskScheduler,
        *,
        engines: tuple[EngineBinding, ...],
        verifier: IndependentVerifier,
        max_parallel: int = 2,
        engine_pins: TrustedEnginePins | None = None,
    ) -> None:
        if type(max_parallel) is not int or not 1 <= max_parallel <= scheduler.max_inflight:
            raise ValueError("parallelism must fit the durable supervisor limit")
        if not engines or len({engine.engine_id for engine in engines}) != len(engines):
            raise ValueError("registered engine IDs must be nonempty and distinct")
        if not callable(getattr(verifier, "verify", None)):
            raise ValueError("independent verifier must be supplied by trusted supervisor")
        if engine_pins is not None and not isinstance(engine_pins, TrustedEnginePins):
            raise ValueError("engine pins require a trusted immutable contract")
        self.scheduler = scheduler
        self.engines = engines
        self.verifier = verifier
        self.max_parallel = max_parallel
        self.engine_pins = engine_pins

    def _preflight_engine_pins(self, plan: CompiledTaskPlan) -> None:
        """Refuse stale, incomplete or unavailable pins before any node claim."""
        pins = self.engine_pins
        if pins is None:
            return  # Existing unpinned internal callers keep their behavior.
        if pins.plan_digest != plan.digest:
            raise AdmissionDenied("trusted engine pins target a stale task revision")
        node_by_id = {node.node_id: node for node in plan.plan.nodes}
        assigned = dict(pins.node_engines)
        if set(assigned) != set(node_by_id):
            raise AdmissionDenied("trusted engine pins do not cover exact DAG nodes")
        registered = {binding.engine_id: binding for binding in self.engines}
        for node_id, engine_id in assigned.items():
            binding = registered.get(engine_id)
            if (
                binding is None
                or not node_by_id[node_id].authority.capabilities.issubset(
                    binding.supported_tools
                )
            ):
                raise AdmissionDenied("trusted engine pin is unavailable or incapable")

    def _select(self, node: TaskNode) -> EngineBinding | None:
        if self.engine_pins is not None:
            pinned = dict(self.engine_pins.node_engines)[node.node_id]
            # No fallback, including to another engine with identical tools.
            return next((b for b in self.engines if b.engine_id == pinned), None)
        # Existing registration order is retained for unpinned callers.
        return next(
            (binding for binding in self.engines
             if node.authority.capabilities.issubset(binding.supported_tools)),
            None,
        )

    @staticmethod
    def _budget(node: TaskNode) -> ResourceBudget:
        limit = node.authority.budget
        # The Gateway/broker enforces the run's actual elapsed time and calls.
        # This upper bound reserves every possible action as a tool call.
        if (
            limit.actions <= 0
            or limit.tokens <= 0
            or not 1 <= limit.wall_seconds <= 86400
        ):
            raise AdmissionDenied("task requires finite executable resource bounds")
        return ResourceBudget(
            actions=limit.actions,
            tool_calls=limit.actions,
            tokens=limit.tokens,
            wall_seconds=limit.wall_seconds,
            spend_microunits=limit.spend_microunits,
        )

    def _finish(
        self,
        plan: CompiledTaskPlan,
        parent: GatewaySession,
        node: TaskNode,
        claim: NodeClaim,
        lease: PodLease,
        adapter: EngineBinding,
    ) -> DispatchOutcome:
        # Do not log/return exception content, which may contain model input,
        # credentials, filesystem paths or browser/computer observations.
        try:
            report = adapter.worker.run(lease, node=node, intent=plan.plan.intent)
        except Exception:
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "worker_interrupted")
        if (
            not isinstance(report, WorkReport)
            or not isinstance(report.artifact_sha256, str)
            or _ARTIFACT.fullmatch(report.artifact_sha256) is None
            or not isinstance(report.consumed, ResourceBudget)
        ):
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "worker_report_invalid")
        try:
            lease.close(total=report.consumed, evidence=report.artifact_sha256)
        except Exception:
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "worker_settlement_failed")
        try:
            self.scheduler.verify_closed(
                plan, parent, claim,
                artifact_digest=report.artifact_sha256,
                verifier=self.verifier,
            )
        except VerificationRejected:
            # Closed result is terminal and has been refused by verification;
            # never allow an automatic retry of this potentially effectful pod.
            try:
                self.scheduler.reject_closed(
                    plan, parent, claim, artifact_digest=report.artifact_sha256,
                )
            except Exception:
                return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "artifact_reconciliation")
            return DispatchOutcome(node.node_id, adapter.engine_id, "blocked", "artifact_not_verified")
        except VerificationUnavailable:
            # No verdict: preserve closed evidence for manual reconciliation,
            # rather than turning a transient outage into a false rejection.
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "verifier_unavailable")
        except AdmissionDenied:
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "verification_integrity")
        except Exception:
            return DispatchOutcome(node.node_id, adapter.engine_id, "uncertain", "verifier_error")
        return DispatchOutcome(node.node_id, adapter.engine_id, "verified", "evidence_accepted")

    def run_wave(
        self,
        plan: CompiledTaskPlan,
        parent: GatewaySession,
        *,
        should_stop: Callable[[], bool] | None = None,
        recover_unstarted: bool = False,
    ) -> DispatchWave:
        """Execute one bounded set of independent nodes.

        If an admitted worker crashes or exceeds its resource limits, retain
        its existing reservation and report uncertainty. A separate recovery
        controller must reconcile; this driver never retries side effects.
        """
        if type(recover_unstarted) is not bool:
            raise ValueError("recovery flag must be boolean")
        self._preflight_engine_pins(plan)
        resumable = (
            list(self.scheduler.recover_unstarted(plan, parent))
            if recover_unstarted else []
        )
        nodes = {node.node_id: node for node in plan.plan.nodes}
        outcomes: list[DispatchOutcome] = []
        tasks: list[tuple[TaskNode, NodeClaim, PodLease, EngineBinding]] = []
        for _ in range(self.max_parallel):
            if should_stop is not None:
                try:
                    if should_stop():
                        break
                except Exception:
                    break
            claim = (
                resumable.pop(0) if resumable
                else self.scheduler.claim_next(plan, parent)
            )
            if claim is None:
                break
            node = nodes[claim.node_id]
            binding = self._select(node)
            if binding is None:
                if claim.state == "claimed":
                    self.scheduler.block_unstarted(plan, parent, claim)
                outcomes.append(DispatchOutcome(
                    claim.node_id, "",
                    "blocked" if claim.state == "claimed" else "uncertain",
                    "engine_capability_unavailable",
                ))
                continue
            try:
                budget = self._budget(node)
            except (AdmissionDenied, ValueError):
                if claim.state == "claimed":
                    self.scheduler.block_unstarted(plan, parent, claim)
                outcomes.append(DispatchOutcome(
                    claim.node_id, binding.engine_id,
                    "blocked" if claim.state == "claimed" else "uncertain",
                    "invalid_node_budget",
                ))
                continue
            try:
                if claim.state == "claimed":
                    self.scheduler.reserve(
                        plan, parent, claim,
                        budget=budget, lease_seconds=budget.wall_seconds,
                    )
                elif claim.state != "reserved":
                    raise AdmissionDenied("recovered attempt is not unstarted")
                lease = self.scheduler.admit(
                    plan, parent, claim, engine=binding.engine_id,
                )
            except Exception:
                # The reservation may have been durably admitted just before
                # the failure. Never guess it is safe to cancel or retry.
                outcomes.append(DispatchOutcome(
                    claim.node_id, binding.engine_id, "uncertain", "child_admission_failed",
                ))
                continue
            tasks.append((node, claim, lease, binding))
        if tasks:
            with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
                futures = [
                    pool.submit(self._finish, plan, parent, node, claim, lease, adapter)
                    for node, claim, lease, adapter in tasks
                ]
                outcomes.extend(future.result() for future in futures)
        return DispatchWave(
            outcomes=tuple(sorted(outcomes, key=lambda item: item.node_id)),
            ready_after=self.scheduler.ready(plan, parent),
        )

    def run_until_idle(
        self,
        plan: CompiledTaskPlan,
        parent: GatewaySession,
        *,
        max_waves: int = 256,
        should_stop: Callable[[], bool] | None = None,
        recover_unstarted: bool = True,
    ) -> DispatchSummary:
        """Drive deterministically until verified, blocked, waiting or bounded stop.

        An externally running pod is not assumed to have finished merely
        because there is no newly eligible work.
        """
        if type(max_waves) is not int or not 1 <= max_waves <= 256:
            raise ValueError("max_waves must be in 1..256")
        if type(recover_unstarted) is not bool:
            raise ValueError("recovery flag must be boolean")
        outcomes: list[DispatchOutcome] = []
        for index in range(max_waves):
            if should_stop is not None:
                try:
                    if should_stop():
                        return DispatchSummary(
                            "stopped", index, tuple(outcomes),
                            self.scheduler.states(plan, parent),
                        )
                except Exception:
                    return DispatchSummary(
                        "stopped", index, tuple(outcomes),
                        self.scheduler.states(plan, parent),
                    )
            wave = self.run_wave(
                plan, parent, should_stop=should_stop,
                recover_unstarted=recover_unstarted,
            )
            outcomes.extend(wave.outcomes)
            states = self.scheduler.states(plan, parent)
            if all(state == "verified" for state in states.values()):
                return DispatchSummary("done", index + 1, tuple(outcomes), states)
            if (
                any(state in {"blocked", "uncertain"} for state in states.values())
                or any(item.status == "uncertain" for item in outcomes)
            ):
                return DispatchSummary("blocked", index + 1, tuple(outcomes), states)
            if not wave.outcomes:
                return DispatchSummary("waiting", index + 1, tuple(outcomes), states)
        return DispatchSummary(
            "budget", max_waves, tuple(outcomes), self.scheduler.states(plan, parent),
        )
