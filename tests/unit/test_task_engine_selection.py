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
