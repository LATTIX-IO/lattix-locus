"""LOCUS-484: model_call on child leases is independent and exact-identity gated."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from locus_runtime.model_client import ModelCall, ModelCallDenied, ModelUsage
from locus_runtime.pod_model_call_gate import PodModelCallGate
from locus_runtime.pod_reservations import AdmissionDenied
from locus_runtime.task_model_binding import PinnedWorkerModel
from tests.gateway_support import FakeEngine
from tests.unit.test_task_scheduler import build, start


def admitted_child(tmp_path):
    scheduler, _ledger, plan, parent = build(tmp_path)
    _claim, lease = start(scheduler, plan, parent, engine="engine/sol")
    binding = PinnedWorkerModel(
        node_id="research",
        model="ollama/sol",
        compute_id="gpu-local-1",
        engine_adapter_id="engine/sol",
    )
    call = ModelCall(
        provider="ollama", model="sol",
        egress_host="127.0.0.1", run_id=lease.run_id,
    )
    return lease, binding, call


def sample_usage(call: ModelCall, audit_id: str) -> ModelUsage:
    return ModelUsage(
        provider=call.provider,
        model=call.model,
        tokens_in=12, tokens_out=8, est_cost_usd=0.0,
        cost_known=True, usage_reported=True,
        audit_id=audit_id, duration_ms=40, ok=True,
        run_id=call.run_id, stream=False,
    )


@pytest.mark.parametrize("change", [
    {"model": "terra"},
    {"provider": "openai"},
    {"egress_host": "api.openai.com"},
    {"run_id": "wrong-run"},
    {"operation": "embeddings"},
])
def test_mismatch_never_reaches_child_gateway(tmp_path, monkeypatch, change):
    lease, selected, model_call = admitted_child(tmp_path)
    def forbidden_authorize(**kwargs):
        raise AssertionError("mismatched route must not even reach gateway")
    monkeypatch.setattr(lease, "authorize", forbidden_authorize)
    gate = PodModelCallGate(
        lease, binding=selected, egress_host="127.0.0.1",
    )
    with pytest.raises(ModelCallDenied) as denial:
        gate.authorize(replace(model_call, **change))
    assert denial.value.code == "model_call_denied"


def test_gateway_is_called_for_every_matching_child_inference(tmp_path, monkeypatch):
    lease, selected, call = admitted_child(tmp_path)
    seen = []
    seq = iter(("gw-accepted-1", "gw-accepted-2"))

    def trusted_authorizer(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(allowed=True, audit_id=next(seq), outcome="allow")

    monkeypatch.setattr(lease, "authorize", trusted_authorizer)
    usages = []
    gate = PodModelCallGate(
        lease, binding=selected, egress_host="127.0.0.1",
        usage_sink=usages.append,
    )
    ids = [gate.authorize(call), gate.authorize(call)]
    assert ids == ["gw-accepted-1", "gw-accepted-2"]
    assert len(seen) == 2
    assert all(action["kind"] == "model_call" for action in seen)
    assert all(action["target"] == "ollama/sol" for action in seen)
    assert all(action["egress_host"] == "127.0.0.1" for action in seen)
    gate.record(call, sample_usage(call, ids[0]))
    gate.record(call, sample_usage(call, ids[1]))
    assert [usage.audit_id for usage in usages] == ids
    with pytest.raises(ValueError, match="one-use"):
        gate.record(call, sample_usage(call, ids[0]))


def test_real_child_gateway_denial_prevents_model_inference(tmp_path):
    lease, selected, call = admitted_child(tmp_path)
    gate = PodModelCallGate(lease, binding=selected, egress_host="127.0.0.1")
    # Keep the real child Gateway and durable PodLease, but force the policy
    # engine to deny the action; this is deterministic even when local-model
    # egress defaults change in a later product release.
    lease._session.gateway._engine = FakeEngine(default=False)
    with pytest.raises(ModelCallDenied) as error:
        gate.authorize(call)
    assert error.value.code == "model_call_denied"


def test_expired_or_closed_child_lease_never_authorizes_model_call(tmp_path):
    lease, selected, call = admitted_child(tmp_path)
    gate = PodModelCallGate(lease, binding=selected, egress_host="127.0.0.1")
    with lease._ledger._connect() as db:
        lease._ledger._tx(db)
        db.execute(
            "UPDATE pod_attempt SET state = 'revoked' WHERE id = ?",
            (lease.reservation_id,),
        )
    with pytest.raises(AdmissionDenied, match="revoked"):
        gate.authorize(call)
