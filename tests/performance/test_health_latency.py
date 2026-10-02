from __future__ import annotations

from statistics import quantiles
from time import perf_counter


def test_health_endpoint_p95_latency(test_client) -> None:
    """Guard the documented local-first control-plane latency budget."""
    for _ in range(5):
        assert test_client.get("/health").status_code == 200

    samples_ms: list[float] = []
    for _ in range(100):
        started = perf_counter()
        response = test_client.get("/health")
        samples_ms.append((perf_counter() - started) * 1_000)
        assert response.status_code == 200

    p95_ms = quantiles(samples_ms, n=20)[18]
    assert p95_ms < 100, f"health endpoint p95 {p95_ms:.2f} ms exceeds 100 ms"
