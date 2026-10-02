"""Resolve-rate statistics with SEM across seeds (SWE-rebench protocol)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SeedSummary:
    seed: int
    resolved: int
    total: int

    @property
    def resolve_rate(self) -> float:
        return 0.0 if self.total == 0 else self.resolved / self.total


@dataclass
class EvalSummary:
    n_seeds: int
    n_instances: int
    resolve_rate_mean: float
    resolve_rate_sem: float
    per_seed_rates: list[float]
    pass_at_k: float  # fraction of instances resolved by at least one seed

    def meets_threshold(self, threshold: float) -> bool:
        return self.resolve_rate_mean >= threshold


def summarize(seed_summaries: list[SeedSummary], per_instance_pass: dict[str, int]) -> EvalSummary:
    rates = [s.resolve_rate for s in seed_summaries]
    n = len(rates)
    mean = sum(rates) / n if n else 0.0
    if n > 1:
        variance = sum((r - mean) ** 2 for r in rates) / (n - 1)
        sem = math.sqrt(variance) / math.sqrt(n)
    else:
        sem = 0.0
    n_instances = len(per_instance_pass)
    pass_at_k = (
        sum(1 for v in per_instance_pass.values() if v > 0) / n_instances if n_instances else 0.0
    )
    return EvalSummary(
        n_seeds=n,
        n_instances=n_instances,
        resolve_rate_mean=round(mean, 4),
        resolve_rate_sem=round(sem, 4),
        per_seed_rates=[round(r, 4) for r in rates],
        pass_at_k=round(pass_at_k, 4),
    )
