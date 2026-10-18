"""LOCUS-415/437: exact-revision engine selection in the existing pod scheduler.

Synthetic trusted-worker adapters here do NOT represent production Codex CLI
or an OS-isolated native AgentRuntime process. They exercise real Gateway child
leases, durable reservations, independent verification and no fallback.
"""
from __future__ import annotations

import threading

import pytest

from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.task_dispatch_driver import (
    EngineBinding,
    GatewayTaskDriver,
    TrustedEnginePins,
    WorkReport,
)
from tests.unit.test_task_dispatch_driver import TrustedVerifier, Worker
from tests.unit.test_task_scheduler import build


def pins(digest: str, *, triage: str = "cli/codex") -> TrustedEnginePins:
    return TrustedEnginePins(
        plan_digest=digest,
        node_engines=(
            ("research", "native/deep-agents"),
            ("triage", triage),
            ("compose", "native/deep-agents"),
            ("publish", "native/deep-agents"),
        ),
    )


def driver(scheduler, native, codex, selector, verifier=None):
    return GatewayTaskDriver(
        scheduler,
        engines=(
            EngineBinding(
                "native/deep-agents", frozenset({"list_issues"}), native,
            ),
            EngineBinding(
                "cli/codex", frozenset({"list_issues"}), codex,
            ),
        ),
        verifier=verifier or TrustedVerifier(),
        engine_pins=selector,
        max_parallel=2,
    )


def test_two_ephemeral_child_leases_use_different_explicit_engines(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    barrier = threading.Barrier(2)
    native, codex = Worker(barrier=barrier), Worker(barrier=barrier)
    verifier = TrustedVerifier()

    outcome = driver(
        scheduler, native, codex, pins(plan.digest), verifier,
    ).run_until_idle(plan, parent)

    assert outcome.status == "done"
    assert outcome.waves == 3
    assert [row[0] for row in codex.calls] == ["triage"]
    assert sorted(row[0] for row in native.calls) == [
        "compose", "publish", "research",
    ]
    all_calls = native.calls + codex.calls
    assert len(all_calls) == 4
    assert len({run_id for _, run_id, _ in all_calls}) == 4
    assert {row["node_id"] for row in verifier.calls} == {
        "research", "triage", "compose", "publish",
    }
    assert {item.engine_id for item in outcome.outcomes} == {
        "native/deep-agents", "cli/codex",
    }
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
    assert ledger.snapshot(parent.caller.run_id).used.actions == 4


@pytest.mark.parametrize("bad", ["cli/unknown", "native/deep-agent"])
def test_missing_pinned_engine_is_denied_before_claim_or_gateway_child(tmp_path, bad):
    scheduler, ledger, plan, parent = build(tmp_path)
    native, codex = Worker(), Worker()
    selector = pins(plan.digest, triage=bad)

    with pytest.raises(AdmissionDenied, match="unavailable or incapable"):
        driver(scheduler, native, codex, selector).run_wave(plan, parent)

    assert native.calls == codex.calls == []
    assert all(state == "pending" for state in scheduler.states(plan, parent).values())
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


def test_pinned_engine_cannot_silently_fallback_after_worker_failure(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    native, codex = Worker(), Worker(fail=True)
    result = driver(scheduler, native, codex, pins(plan.digest)).run_wave(plan, parent)

    assert {(x.node_id, x.engine_id, x.status) for x in result.outcomes} == {
        ("research", "native/deep-agents", "verified"),
        ("triage", "cli/codex", "uncertain"),
    }
    assert [row[0] for row in native.calls] == ["research"]
    assert [row[0] for row in codex.calls] == ["triage"]  # Invocation occurred, but no fallback.
    assert ledger.snapshot(parent.caller.run_id).held.actions > 0
    assert scheduler.ready(plan, parent) == ()


@pytest.mark.parametrize("change", ["digest", "missing", "extra"])
def test_pin_revision_and_exact_node_set_preflight_before_claim(tmp_path, change):
    scheduler, ledger, plan, parent = build(tmp_path)
    native, codex = Worker(), Worker()
    selector = pins(plan.digest)
    if change == "digest":
        selector = pins("f" * 64)
    elif change == "missing":
        selector = TrustedEnginePins(plan.digest, selector.node_engines[:-1])
    else:
        selector = TrustedEnginePins(
            plan.digest, selector.node_engines + (("foreign", "cli/codex"),),
        )

    with pytest.raises(AdmissionDenied, match="trusted engine pins"):
        driver(scheduler, native, codex, selector).run_until_idle(plan, parent)

    assert native.calls == codex.calls == []
    assert all(state == "pending" for state in scheduler.states(plan, parent).values())
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0


def test_capability_incompatible_pinned_engine_fails_without_budget_hold(tmp_path):
    scheduler, ledger, plan, parent = build(tmp_path)
    bad = GatewayTaskDriver(
        scheduler,
        engines=(
            EngineBinding("native/deep-agents", frozenset({"read_file"}), Worker()),
            EngineBinding("cli/codex", frozenset({"list_issues"}), Worker()),
        ),
        verifier=TrustedVerifier(),
        engine_pins=pins(plan.digest),
    )

    with pytest.raises(AdmissionDenied, match="unavailable or incapable"):
        bad.run_wave(plan, parent)

    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
    assert scheduler.ready(plan, parent) == ("research", "triage")


@pytest.mark.parametrize(
    "bad", [
        ("f" * 63, (("research", "cli/codex"),)),
        ("f" * 64, ()),
        ("f" * 64, (("research", "cli/codex"), ("research", "native/deep-agents"))),
        ("f" * 64, (("../bad", "cli/codex"),)),
        ("f" * 64, (("research", "../invalid"),)),
    ],
)
def test_malformed_trusted_selector_fails_closed(bad):
    with pytest.raises(ValueError):
        TrustedEnginePins(*bad)


def test_two_pinned_siblings_have_distinct_gateway_tool_scopes(tmp_path):
    """Each child gets a disjoint tool set; neither may borrow its sibling's."""
    import hashlib

    from locus_runtime.pod_reservations import PodReservationLedger, ResourceBudget
    from locus_runtime.task_plan_compiler import compile_task_plan
    from locus_runtime.task_scheduler import DurableTaskScheduler
    from tests.unit.test_task_scheduler import build as existing_build

    _, _, baseline, parent = existing_build(tmp_path)
    proposal = {
        "contract_version": "1.0",
        "nodes": [
            {
                "node_id": name, "depends_on": [],
                "capabilities": [tool], "risk_ceiling": "R2",
                "budget": {
                    "actions": 3, "tokens": 100,
                    "wall_seconds": 20, "spend_microunits": 0,
                },
                "deliverables": [f"{name} evidence"],
                "acceptance_criteria": ["independent artifact review"],
            }
            for name, tool in (
                ("native_research", "read_file"),
                ("codex_triage", "list_issues"),
            )
        ],
    }
    plan = compile_task_plan(
        proposal,
        intent=baseline.plan.intent,
        parent_authority=baseline.plan.parent_authority,
    )
    ledger = PodReservationLedger(tmp_path / "scoped-pods.db")
    scheduler = DurableTaskScheduler(ledger, max_inflight=2)
    scheduler.bind(plan, parent, max_tool_calls=12)

    class ScopedWorker:
        def __init__(self, allowed, denied):
            self.allowed = allowed
            self.denied = denied
            self.calls = []

        def run(self, lease, *, node, intent):
            allowed = lease.authorize(kind="tool_call", tool=self.allowed).outcome
            refused = lease.authorize(kind="tool_call", tool=self.denied).outcome
            self.calls.append((lease.run_id, node.node_id, allowed, refused))
            assert allowed == "allow", (node.node_id, "own_tool", allowed)
            assert refused == "deny", (node.node_id, "sibling_tool", refused)
            return WorkReport(
                hashlib.sha256(node.node_id.encode()).hexdigest(),
                ResourceBudget(1, 1, 25, 5, 0),
            )

    native = ScopedWorker("read_file", "list_issues")
    codex = ScopedWorker("list_issues", "read_file")
    verifier = TrustedVerifier()
    engine_selector = TrustedEnginePins(
        plan.digest,
        (
            ("native_research", "native/deep-agents"),
            ("codex_triage", "cli/codex"),
        ),
    )
    dispatcher = GatewayTaskDriver(
        scheduler,
        engines=(
            EngineBinding("native/deep-agents", frozenset({"read_file"}), native),
            EngineBinding("cli/codex", frozenset({"list_issues"}), codex),
        ),
        verifier=verifier,
        max_parallel=2,
        engine_pins=engine_selector,
    )

    result = dispatcher.run_until_idle(plan, parent)
    assert result.status == "done", (result.outcomes, native.calls, codex.calls)
    assert result.waves == 1
    assert {n for _, n in native.calls} == {"native_research"}
    assert {n for _, n in codex.calls} == {"codex_triage"}
    assert native.calls[0][0] != codex.calls[0][0]
    assert len(verifier.calls) == 2
    assert ledger.snapshot(parent.caller.run_id).held.actions == 0
