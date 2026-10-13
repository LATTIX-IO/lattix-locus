"""LOCUS-413: TDD for durable, dependency-gated, non-replaying supervisor dispatch."""
from __future__ import annotations

import concurrent.futures
import sqlite3
from dataclasses import replace

import pytest

from locus_runtime.gateway import BudgetFigures, Capabilities, Gateway, RiskClass
from locus_runtime.pod_reservations import (
    AdmissionDenied, PodReservationLedger, ReservationRequest, ResourceBudget,
)
from locus_runtime.task_graph_contract import TaskAuthority, TaskBudget, TaskIntent
from locus_runtime.task_plan_compiler import compile_task_plan
from locus_runtime.task_scheduler import DurableTaskScheduler, NodeClaim
from tests.gateway_support import FakeEngine


ARTIFACT = "a" * 64
ALTERNATE = "b" * 64
CONSUMED = ResourceBudget(1, 1, 25, 5, 0)
REQUEST = ResourceBudget(3, 2, 120, 20, 0)


class Verifier:
    def __init__(self, accept=True):
        self.accept = accept
        self.calls = []

    def verify(self, **claims):
        self.calls.append(claims)
        return self.accept


def build(tmp_path, *, max_inflight=2):
    ledger = PodReservationLedger(tmp_path / "pods.db")
    gateway = Gateway(FakeEngine(), lambda _: None)
    parent = gateway.open_session(
        run_id="work-session-001", principal="alice", engine="trusted-supervisor",
        allow_child_delegation=True,
        capabilities=Capabilities(
            allowed_tools=frozenset({"list_issues", "read_file"}),
            max_actions=16, max_tool_calls=12,
            budget=BudgetFigures(
                tokens_used=0, max_tokens=1600,
                duration_used_seconds=0, max_duration_seconds=120,
                cost_used_usd=0, max_cost_usd=0,
            ),
            max_risk_class=RiskClass.R2,
        ),
    )

    def node(name, deps=()):
        return {
            "node_id": name,
            "depends_on": list(deps),
            "capabilities": ["list_issues"],
            "risk_ceiling": "R2",
            "budget": {
                "actions": 3, "tokens": 300,
                "wall_seconds": 20, "spend_microunits": 0,
            },
            "deliverables": [f"{name} evidence"],
            "acceptance_criteria": ["independently verified"],
        }

    plan = compile_task_plan(
        {
            "contract_version": "1.0",
            "nodes": [
                node("research"), node("triage"),
                node("compose", ("research", "triage")),
                node("publish", ("compose",)),
            ],
        },
        intent=TaskIntent(
            "Produce a cross-domain brief", "business", "alice",
            ("all four artifacts independently verified",),
        ),
        parent_authority=TaskAuthority(
            frozenset({"list_issues", "read_file"}), "R2",
            TaskBudget(16, 1600, 120, 0),
        ),
    )
    scheduler = DurableTaskScheduler(ledger, max_inflight=max_inflight)
    scheduler.bind(plan, parent, max_tool_calls=12)
    return scheduler, ledger, plan, parent


def start(scheduler, plan, parent, *, engine="ollama/deep-agents"):
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    hold = scheduler.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
    )
    lease = scheduler.admit(plan, parent, claim, engine=engine)
    assert lease.reservation_id == hold.reservation_id
    return claim, lease


def verified(scheduler, plan, parent, claim, lease, *, digest=ARTIFACT):
    lease.close(total=CONSUMED, evidence=digest)
    verifier = Verifier()
    scheduler.verify_closed(
        plan, parent, claim, artifact_digest=digest, verifier=verifier,
    )
    assert len(verifier.calls) == 1
    assert verifier.calls[0]["child_run_id"].startswith(parent.caller.run_id + ":")
    return verifier


def test_diamond_dependency_gate_parallel_siblings_and_order(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    assert scheduler.ready(plan, parent) == ("research", "triage")
    research, research_lease = start(scheduler, plan, parent)
    triage, triage_lease = start(scheduler, plan, parent, engine="cli/opencode")
    assert (research.node_id, triage.node_id) == ("research", "triage")
    assert scheduler.claim_next(plan, parent) is None
    assert scheduler.ready(plan, parent) == ()
    verified(scheduler, plan, parent, research, research_lease)
    assert scheduler.ready(plan, parent) == ()
    verified(scheduler, plan, parent, triage, triage_lease, digest=ALTERNATE)
    assert scheduler.ready(plan, parent) == ("compose",)
    compose, compose_lease = start(scheduler, plan, parent)
    assert compose.node_id == "compose"
    verified(scheduler, plan, parent, compose, compose_lease)
    assert scheduler.ready(plan, parent) == ("publish",)
    publish, publish_lease = start(scheduler, plan, parent)
    verified(scheduler, plan, parent, publish, publish_lease)
    assert scheduler.ready(plan, parent) == ()
    assert scheduler.claim_next(plan, parent) is None


def test_reservation_is_idempotent_across_process_restart_and_admission_not_replayed(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    first = scheduler.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
    )
    reopened = DurableTaskScheduler(
        PodReservationLedger(tmp_path / "pods.db"), max_inflight=2
    )
    reopened.bind(plan, parent, max_tool_calls=12)
    same = reopened.reserve(
        plan, parent, claim, budget=REQUEST, lease_seconds=30,
    )
    assert same.reservation_id == first.reservation_id
    assert ledger.snapshot(parent.caller.run_id).held.actions == REQUEST.actions
    with pytest.raises(AdmissionDenied, match="idempotency collision"):
        reopened.reserve(
            plan, parent, claim,
            budget=ResourceBudget(2, 1, 100, 20, 0), lease_seconds=30,
        )
    lease = reopened.admit(plan, parent, claim, engine="verified-loop")
    with pytest.raises(AdmissionDenied):
        reopened.admit(plan, parent, claim, engine="verified-loop")
    verified(reopened, plan, parent, claim, lease)


def test_independent_evidence_verification_is_required_before_downstream_admission(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    claim, lease = start(scheduler, plan, parent)
    verifier = Verifier()
    with pytest.raises(AdmissionDenied, match="closed pod evidence"):
        scheduler.verify_closed(
            plan, parent, claim, artifact_digest=ARTIFACT, verifier=verifier,
        )
    assert verifier.calls == []
    lease.close(total=CONSUMED, evidence=ARTIFACT)
    with pytest.raises(AdmissionDenied, match="closed pod evidence"):
        scheduler.verify_closed(
            plan, parent, claim, artifact_digest=ALTERNATE, verifier=verifier,
        )
    assert verifier.calls == []
    verifier.accept = False
    with pytest.raises(AdmissionDenied, match="independent verification"):
        scheduler.verify_closed(
            plan, parent, claim, artifact_digest=ARTIFACT, verifier=verifier,
        )
    assert scheduler.ready(plan, parent) == ("triage",)
    verifier.accept = True
    scheduler.verify_closed(
        plan, parent, claim, artifact_digest=ARTIFACT, verifier=verifier,
    )
    with pytest.raises(AdmissionDenied, match="claim replay"):
        scheduler.verify_closed(
            plan, parent, claim, artifact_digest=ARTIFACT, verifier=verifier,
        )


def test_foreign_claim_plan_and_out_of_order_node_cannot_be_launched(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    forged = replace(claim, node_id="compose")
    with pytest.raises(AdmissionDenied, match="claim"):
        scheduler.reserve(
            plan, parent, forged, budget=REQUEST, lease_seconds=30,
        )
    bad_plan = replace(plan, digest="f" * 64)
    with pytest.raises(AdmissionDenied, match="graph revision"):
        scheduler.ready(bad_plan, parent)
    with pytest.raises(AdmissionDenied, match="graph revision"):
        scheduler.reserve(
            bad_plan, parent, claim, budget=REQUEST, lease_seconds=30,
        )
    foreign = NodeClaim(
        "foreign-run", claim.plan_digest, claim.node_id, claim.attempt_key, "claimed",
    )
    with pytest.raises(AdmissionDenied, match="foreign task"):
        scheduler.reserve(
            plan, parent, foreign, budget=REQUEST, lease_seconds=30,
        )


def test_concurrent_supervisors_claim_only_one_attempt_per_ready_node(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    another = DurableTaskScheduler(PodReservationLedger(tmp_path / "pods.db"), max_inflight=2)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(
                (scheduler if n % 2 else another).claim_next, plan, parent
            )
            for n in range(12)
        ]
        claims = [f.result() for f in futures]
    actual = [c for c in claims if c is not None]
    assert sorted(c.node_id for c in actual) == ["research", "triage"]
    assert len({c.attempt_key for c in actual}) == 2


def test_expired_active_child_is_quarantined_and_not_reissued(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim, lease = start(scheduler, plan, parent)
    with ledger._connect() as db:
        ledger._tx(db)
        db.execute(
            "UPDATE pod_attempt SET expires_at=0 WHERE id=?",
            (lease.reservation_id,),
        )
    assert ledger.recover_expired() == (lease.reservation_id,)
    assert scheduler.reconcile_interrupted(plan, parent) == ("research",)
    assert scheduler.claim_next(plan, parent) is not None  # triage only
    assert scheduler.ready(plan, parent) == ()
    with pytest.raises(AdmissionDenied, match="pod lease"):
        lease.authorize(kind="tool_call", tool="list_issues")


def test_untrusted_flags_and_unregistered_parent_cannot_widen_scope(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    with pytest.raises(ValueError):
        DurableTaskScheduler(ledger, max_inflight=0)
    with pytest.raises(ValueError):
        DurableTaskScheduler(ledger, max_inflight=True)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    with pytest.raises(AdmissionDenied):
        scheduler.admit(plan, parent, claim, engine="opencode")
    with pytest.raises(AdmissionDenied, match="node-specific"):
        scheduler.reserve(
            plan, parent, claim,
            budget=ResourceBudget(4, 2, 120, 20, 0), lease_seconds=30,
        )
    hold = scheduler.reserve(plan, parent, claim, budget=REQUEST, lease_seconds=30)
    assert hold.child_run_id is None
    with pytest.raises(AdmissionDenied, match="invalid agent engine"):
        scheduler.admit(plan, parent, claim, engine="")
    lease = scheduler.admit(plan, parent, claim, engine="claude-code")
    assert lease.run_id.startswith(parent.caller.run_id + ":research:")
    decision = lease.authorize(kind="tool_call", tool="send_email")
    assert decision.outcome == "deny"


def test_rebind_after_restart_keeps_verified_state_and_disallows_plan_mutation(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    claim, lease = start(scheduler, plan, parent)
    verified(scheduler, plan, parent, claim, lease)
    reopened = DurableTaskScheduler(PodReservationLedger(tmp_path / "pods.db"), max_inflight=2)
    reopened.bind(plan, parent, max_tool_calls=12)
    assert reopened.ready(plan, parent) == ("triage",)
    alternate = replace(plan, digest="c" * 64)
    with pytest.raises(AdmissionDenied):
        reopened.bind(alternate, parent, max_tool_calls=12)


def test_structural_digest_substitution_cannot_remove_dependencies_or_widen_authority(tmp_path):
    scheduler, _, plan, parent = build(tmp_path)
    # A forged CompiledTaskPlan can keep the accepted digest while mutating
    # valid graph dependencies or capabilities. The scheduler pins both.
    changed_nodes = tuple(
        replace(node, depends_on=()) if node.node_id == "compose" else node
        for node in plan.plan.nodes
    )
    forged_graph = replace(plan, plan=replace(plan.plan, nodes=changed_nodes))
    with pytest.raises(AdmissionDenied, match="graph revision"):
        scheduler.ready(forged_graph, parent)
    first = plan.plan.nodes[0]
    revised_authority = replace(
        first.authority, capabilities=frozenset({"read_file"}),
    )
    modified = replace(first, authority=revised_authority)
    forged_authority = replace(
        plan,
        plan=replace(plan.plan, nodes=(modified, *plan.plan.nodes[1:])),
    )
    with pytest.raises(AdmissionDenied, match="graph revision"):
        scheduler.claim_next(forged_authority, parent)


def test_restart_with_wider_concurrency_limit_is_rejected(tmp_path):
    scheduler, _, plan, parent = build(tmp_path, max_inflight=2)
    assert scheduler.ready(plan, parent) == ("research", "triage")
    widened = DurableTaskScheduler(
        PodReservationLedger(tmp_path / "pods.db"), max_inflight=64,
    )
    with pytest.raises(AdmissionDenied, match="capacity"):
        widened.ready(plan, parent)
    with pytest.raises(AdmissionDenied, match="concurrency"):
        widened.bind(plan, parent, max_tool_calls=12)



def test_crash_after_claim_is_resumable_without_new_claim_or_duplicate_effect(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    abandoned = scheduler.claim_next(plan, parent)
    assert abandoned is not None and abandoned.node_id == "research"
    restart = DurableTaskScheduler(PodReservationLedger(tmp_path / "pods.db"), max_inflight=2)
    restart.bind(plan, parent, max_tool_calls=12)
    claims = restart.recover_unstarted(plan, parent)
    assert len(claims) == 1 and claims[0].attempt_key == abandoned.attempt_key
    assert restart.recover_unstarted(plan, parent) == claims
    # No new attempt identity was minted; concurrently resuming uses one hold.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        holds = list(pool.map(
            lambda _: restart.reserve(
                plan, parent, claims[0], budget=REQUEST, lease_seconds=30,
            ), range(2),
        ))
    assert holds[0].reservation_id == holds[1].reservation_id
    assert ledger.snapshot(parent.caller.run_id).held == REQUEST
    lease = restart.admit(plan, parent, claims[0], engine="trusted-worker")
    verified(restart, plan, parent, claims[0], lease)
    with pytest.raises(AdmissionDenied):
        restart.admit(plan, parent, claims[0], engine="trusted-worker")


def test_crash_after_reserve_before_dispatch_persist_recovers_same_reservation(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claimed = scheduler.claim_next(plan, parent)
    assert claimed is not None
    hold = ledger.reserve(
        plan, parent, ReservationRequest(
            claimed.node_id, claimed.attempt_key, "v1", REQUEST, 30,
        ),
    )
    # Simulate termination between ledger transaction and scheduler transition.
    recovered = DurableTaskScheduler(PodReservationLedger(tmp_path / "pods.db"), max_inflight=2)
    claims = recovered.recover_unstarted(plan, parent)
    assert len(claims) == 1 and claims[0].state == "reserved"
    assert claims[0].reservation_id == hold.reservation_id
    assert recovered.reserve(
        plan, parent, claims[0], budget=REQUEST, lease_seconds=30,
    ).reservation_id == hold.reservation_id
    lease = recovered.admit(plan, parent, claims[0], engine="trusted-worker")
    verified(recovered, plan, parent, claims[0], lease)


def test_canceled_unadmitted_hold_requeues_with_fresh_identity_and_stale_claim_denied(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    previous = scheduler.claim_next(plan, parent)
    assert previous is not None
    first = scheduler.reserve(plan, parent, previous, budget=REQUEST, lease_seconds=30)
    with ledger._connect() as db:
        ledger._tx(db)
        db.execute("UPDATE pod_attempt SET expires_at=0 WHERE id=?", (first.reservation_id,))
    assert ledger.recover_expired() == (first.reservation_id,)
    assert ledger.get(first.reservation_id).state == "canceled"
    assert scheduler.recover_unstarted(plan, parent) == ()
    assert scheduler.ready(plan, parent) == ("research", "triage")
    replacement = scheduler.claim_next(plan, parent)
    assert replacement is not None
    assert replacement.node_id == previous.node_id
    assert replacement.attempt_key != previous.attempt_key
    with pytest.raises(AdmissionDenied, match="claim replay"):
        scheduler.reserve(plan, parent, previous, budget=REQUEST, lease_seconds=30)
    second = scheduler.reserve(
        plan, parent, replacement, budget=REQUEST, lease_seconds=30,
    )
    assert first.reservation_id != second.reservation_id
    assert ledger.snapshot(parent.caller.run_id).held == REQUEST
    lease = scheduler.admit(plan, parent, replacement, engine="trusted-worker")
    verified(scheduler, plan, parent, replacement, lease)


def test_crash_before_persist_reservation_canceled_never_reuses_terminal_key(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    hold = ledger.reserve(
        plan, parent, ReservationRequest(claim.node_id, claim.attempt_key, "v1", REQUEST, 30),
    )
    assert ledger.cancel_reserved(hold.reservation_id).state == "canceled"
    assert scheduler.recover_unstarted(plan, parent) == ()
    replacement = scheduler.claim_next(plan, parent)
    assert replacement is not None and replacement.attempt_key != claim.attempt_key


def test_ambiguous_admitted_child_never_requeued_by_recovery(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    hold = ledger.reserve(
        plan, parent, ReservationRequest(claim.node_id, claim.attempt_key, "v1", REQUEST, 30),
    )
    child = ledger.admit(plan, parent, hold.reservation_id, engine="sandbox-child")
    assert child.reservation_id == hold.reservation_id
    assert scheduler.recover_unstarted(plan, parent) == ()
    with ledger._connect() as db:
        state = db.execute(
            "SELECT state FROM task_dispatch_node WHERE parent_run_id=? AND node_id=?",
            (parent.caller.run_id, claim.node_id),
        ).fetchone()[0]
    assert state == "uncertain"
    assert ledger.snapshot(parent.caller.run_id).held.actions == REQUEST.actions
    with pytest.raises(AdmissionDenied, match="claim replay"):
        scheduler.reserve(plan, parent, claim, budget=REQUEST, lease_seconds=30)


def test_recovery_key_corruption_is_rejected_and_does_not_release_hold(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    claim = scheduler.claim_next(plan, parent)
    assert claim is not None
    with ledger._connect() as db:
        ledger._tx(db)
        db.execute(
            "UPDATE task_dispatch_node SET attempt_key='forged' WHERE "
            "parent_run_id=? AND node_id=?",
            (parent.caller.run_id, claim.node_id),
        )
    with pytest.raises(AdmissionDenied, match="corrupt durable"):
        scheduler.recover_unstarted(plan, parent)
    assert scheduler.claim_next(plan, parent).node_id == "triage"


def test_bounded_recovery_denies_infinite_attempt_identity_churn(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    for attempt in range(4):
        claimed = scheduler.claim_next(plan, parent)
        assert claimed is not None and claimed.node_id == "research"
        held = scheduler.reserve(
            plan, parent, claimed, budget=REQUEST, lease_seconds=30,
        )
        assert ledger.cancel_reserved(held.reservation_id).state == "canceled"
        assert scheduler.recover_unstarted(plan, parent) == ()
        if attempt < 3:
            assert scheduler.ready(plan, parent) == ("research", "triage")
    assert scheduler.ready(plan, parent) == ("triage",)
    assert scheduler.claim_next(plan, parent).node_id == "triage"
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


def test_legacy_dispatch_schema_upgrade_preserves_claim_format(tmp_path):
    path = tmp_path / "pods.db"
    ledger = PodReservationLedger(path)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE task_dispatch_node (
            parent_run_id TEXT NOT NULL, node_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL, graph_fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, attempt_key TEXT, reservation_id TEXT,
            artifact_digest TEXT, PRIMARY KEY (parent_run_id,node_id)
        )""")
    DurableTaskScheduler(ledger, max_inflight=2)
    with ledger._connect() as db:
        fields = {r["name"] for r in db.execute("PRAGMA table_info(task_dispatch_node)")}
    assert "attempt_epoch" in fields
