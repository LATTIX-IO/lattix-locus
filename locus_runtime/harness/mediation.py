"""Mediation measurement for agent runtimes (LOCUS-348, P6/P9 evidence).

:class:`MediationMonitor` answers one question for a run: *did anything the
agent caused happen without a gateway decision allowing it first?*

* It is the gateway's audit sink (chained to the real one), so it sees every
  decision, in the thread that asked for it.
* It wraps the executor's side-effect sinks (process spawn, file read, file
  write). Each sink call must be immediately preceded, in the same thread, by
  an ``allow`` decision of the matching kind; otherwise it counts as
  unmediated.
* It wraps the run's chat client and counts model turns; each must have a
  ``model_call`` decision.

Coverage is ``mediated / observed`` per channel; a run is fully mediated when
both are 1.0. Measurement only: it never changes a decision.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from locus_runtime.gateway import GatewayAuditRecord

_SINK_KIND = {"_spawn": "process_exec", "_read_text": "file_read", "_write_bytes": "file_write"}


@dataclass
class MediationReport:
    model_calls_observed: int = 0
    model_call_decisions: int = 0
    side_effects_observed: int = 0
    side_effects_mediated: int = 0
    unmediated: list[str] = field(default_factory=list)
    decisions: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def model_coverage(self) -> float:
        if not self.model_calls_observed:
            return 1.0
        return min(1.0, self.model_call_decisions / self.model_calls_observed)

    @property
    def side_effect_coverage(self) -> float:
        if not self.side_effects_observed:
            return 1.0
        return self.side_effects_mediated / self.side_effects_observed

    @property
    def complete(self) -> bool:
        return self.model_coverage == 1.0 and self.side_effect_coverage == 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_calls_observed": self.model_calls_observed,
            "model_call_decisions": self.model_call_decisions,
            "side_effects_observed": self.side_effects_observed,
            "side_effects_mediated": self.side_effects_mediated,
            "model_coverage": round(self.model_coverage, 4),
            "side_effect_coverage": round(self.side_effect_coverage, 4),
            "unmediated": list(self.unmediated[:20]),
            "decisions": dict(self.decisions),
            "outcomes": dict(self.outcomes),
        }


class _CountingClient:
    """ChatClient pass-through that counts turns."""

    def __init__(self, inner: Any, on_call: Callable[[], None]) -> None:
        self._inner = inner
        self._on_call = on_call

    @property
    def provider(self) -> str:
        return str(getattr(self._inner, "provider", ""))

    @property
    def model(self) -> str:
        return str(getattr(self._inner, "model", ""))

    def complete(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        self._on_call()
        return self._inner.complete(messages, **kwargs)


class MediationMonitor:
    def __init__(self, inner_sink: Callable[[GatewayAuditRecord], None] | None = None) -> None:
        self._inner = inner_sink
        self._local = threading.local()
        self._lock = threading.Lock()
        self.records: list[GatewayAuditRecord] = []
        self._model_calls = 0
        self._side_effects = 0
        self._mediated = 0
        self._unmediated: list[str] = []

    # -- gateway side ------------------------------------------------------------
    def audit_sink(self, record: GatewayAuditRecord) -> None:
        with self._lock:
            self.records.append(record)
        self._local.last = (record.action_kind, record.outcome)
        if self._inner is not None:
            self._inner(record)

    # -- observed side -------------------------------------------------------------
    def wrap_client(self, client: Any) -> Any:
        def counted() -> None:
            with self._lock:
                self._model_calls += 1

        return _CountingClient(client, counted)

    def attach(self, executor: Any) -> None:
        """Spy on the side-effect sinks of a harness executor (and its host file ops)."""
        targets = [executor]
        direct = getattr(executor, "_direct", None)
        if direct is not None:
            targets.append(direct)
        for target in targets:
            for sink, kind in _SINK_KIND.items():
                original = getattr(target, sink, None)
                if original is not None and not getattr(original, "_mediation_spy", False):
                    setattr(target, sink, self._spy(original, sink, kind))

    def _spy(self, original: Callable[..., Any], sink: str, kind: str) -> Callable[..., Any]:
        def spy(*args: Any, **kwargs: Any) -> Any:
            last = getattr(self._local, "last", None)
            self._local.last = None  # one decision authorizes one sink call
            with self._lock:
                self._side_effects += 1
                if last == (kind, "allow"):
                    self._mediated += 1
                else:
                    self._unmediated.append(f"{sink} without a preceding {kind} allow")
            return original(*args, **kwargs)

        spy._mediation_spy = True  # type: ignore[attr-defined]
        return spy

    # -- report ----------------------------------------------------------------------
    def report(self) -> MediationReport:
        with self._lock:
            kinds = Counter(r.action_kind for r in self.records)
            outcomes = Counter(str(r.outcome) for r in self.records)
            return MediationReport(
                model_calls_observed=self._model_calls,
                model_call_decisions=kinds.get("model_call", 0),
                side_effects_observed=self._side_effects,
                side_effects_mediated=self._mediated,
                unmediated=list(self._unmediated),
                decisions=dict(kinds),
                outcomes=dict(outcomes),
            )
