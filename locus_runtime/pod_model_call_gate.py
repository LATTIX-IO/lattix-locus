"""LOCUS-484: child PodLease-bound model_call admission.

This is a host-side adapter for an *already trusted, registered* worker.
It is never serialized into an untrusted LLM/CLI prompt. Each inference
attempt authorizes the exact selected model, run and egress through the
child Gateway session; the model routing proposal cannot grant authority.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from locus_runtime.model_client import (
    ModelCall,
    ModelCallDenied,
    ModelUsage,
)
from locus_runtime.pod_reservations import PodLease
from locus_runtime.task_model_binding import PinnedWorkerModel


class PodModelCallGate:
    """ModelCallGate for exactly one pinned specialist model and PodLease.

    Usage receipts and the Gateway audit ID are generated independently of
    a model's text; no prompt or completion content is stored in this gate.
    A caller must create one gate per admitted child attempt and pass it to a
    ModelClient whose endpoint was resolved against its trusted compute_id.
    """

    def __init__(
        self,
        lease: PodLease,
        *,
        binding: PinnedWorkerModel,
        egress_host: str,
        usage_sink: Callable[[ModelUsage], None] | None = None,
    ) -> None:
        if not isinstance(lease, PodLease):
            raise TypeError("a durable child PodLease is required")
        if not isinstance(binding, PinnedWorkerModel) or binding.authorized:
            raise ValueError("a non-privileged pinned worker binding is required")
        if not binding.model or "/" not in binding.model:
            raise ValueError("provider-qualified model is required")
        if not binding.compute_id or not binding.engine_adapter_id:
            raise ValueError("exact compute and registered engine identity are required")
        if not egress_host or egress_host.strip() != egress_host:
            raise ValueError("a canonical expected egress host is required")
        self._lease = lease
        self._binding = binding
        self._host = egress_host
        self._usage_sink = usage_sink
        self._lock = threading.Lock()
        self._approved: dict[str, tuple[str, str, str, str]] = {}

    def _require_selected(self, call: ModelCall) -> None:
        selected = self._binding.model
        if (f"{call.provider}/{call.model}" != selected
                or call.run_id != self._lease.run_id
                or call.egress_host != self._host
                or call.operation != "chat"):
            raise ModelCallDenied(
                provider=call.provider,
                model=call.model,
                reason="model/compute, run, egress or operation differs from admitted worker binding",
            )

    def authorize(self, call: ModelCall) -> str:
        self._require_selected(call)
        # require_active() happens inside the durable PodLease, before the
        # independent Gateway action, on *every* inference request.
        decision = self._lease.authorize(**call.gateway_kwargs())
        if not decision.allowed:
            raise ModelCallDenied(
                provider=call.provider,
                model=call.model,
                reason="child Gateway model_call was not allowed",
                audit_id=decision.audit_id,
                outcome=decision.outcome,
            )
        if not decision.audit_id:
            raise ModelCallDenied(
                provider=call.provider, model=call.model,
                reason="Gateway admission has no audit receipt",
            )
        with self._lock:
            self._approved[decision.audit_id] = (
                call.provider, call.model, call.egress_host, call.run_id,
            )
        return decision.audit_id

    def record(self, call: ModelCall, usage: ModelUsage) -> None:
        self._require_selected(call)
        if not isinstance(usage, ModelUsage):
            raise ValueError("model usage must have a typed receipt")
        with self._lock:
            approved = self._approved.pop(usage.audit_id, None)
        if (
            approved != (call.provider, call.model, call.egress_host, call.run_id)
            or usage.provider != call.provider
            or usage.model != call.model
            or usage.run_id != call.run_id
        ):
            raise ValueError("model usage does not match a one-use Gateway audit receipt")
        if self._usage_sink is not None:
            self._usage_sink(usage)
