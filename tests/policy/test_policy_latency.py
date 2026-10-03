"""Decision latency through the OPA sidecar (LOCUS-328). Requires OPA."""

from __future__ import annotations

import logging
import statistics
import time
from typing import Any

from locus_runtime.policy_engine import OpaSidecarEngine

logger = logging.getLogger(__name__)

AGENT_INPUT: dict[str, Any] = {
    "agent_id": "backend",
    "tool": "read_file",
    "allowed_tools": ["read_file"],
    "resource": "docs/report.txt",
    "budget": {"tokens_used": 0, "max_tokens": 10},
    "action": "read_file",
    "classification": "internal",
    "provider": "local",
}


def measure_decision_latency(
    engine: OpaSidecarEngine, policy: str, payload: dict[str, Any], samples: int = 200
) -> dict[str, float]:
    """Return p50/p95/max decision latency in milliseconds over ``samples`` calls."""
    for _ in range(10):  # warm the connection and OPA's compiled query cache
        engine.decide(policy, payload)
    timings: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        engine.decide(policy, payload)
        timings.append((time.perf_counter() - started) * 1000.0)
    cuts = statistics.quantiles(timings, n=100)
    return {"p50_ms": cuts[49], "p95_ms": cuts[94], "max_ms": max(timings), "samples": samples}


def test_decision_latency_is_recorded(opa_engine: OpaSidecarEngine, record_property: Any) -> None:
    stats = measure_decision_latency(opa_engine, "agent_policy", AGENT_INPUT)
    for key, value in stats.items():
        record_property(key, value)
    message = (
        f"policy_engine latency agent_policy: p50={stats['p50_ms']:.2f}ms "
        f"p95={stats['p95_ms']:.2f}ms max={stats['max_ms']:.2f}ms n={int(stats['samples'])}"
    )
    logger.warning(message)
    print(message)
    # Generous ceiling: catches a broken sidecar, not a performance regression gate.
    assert stats["p95_ms"] < 500.0
