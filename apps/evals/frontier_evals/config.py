"""Evaluation configuration + the local-fleet guardrail."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class EvalConfig:
    mode: str = "plumbing"  # plumbing | live
    dataset: str = "synthetic-mini"
    instance_ids: list[str] = field(default_factory=list)
    seeds: list[int] = field(default_factory=lambda: [0])
    # model serving (live mode)
    api_base_url: str = ""  # OpenAI-compatible endpoint, e.g. http://runner:8000/v1
    model: str = ""
    api_key: str = "not-needed"
    provider: str = "vllm"
    profile_id: str = ""  # force a model profile, else auto
    agent_id: str = ""  # shipped agent (examples/agents/<id>) to drive the run
    # execution
    docker_host: str = ""  # tcp://runner:2376 or ssh://runner
    max_steps: int = 40
    max_seconds: float = 1800.0
    allow_local: bool = False
    output_dir: str = "eval-results"
    threshold: float = 0.30  # DeepSWE acceptance gate for gpt-oss-20b

    @classmethod
    def from_env(cls) -> "EvalConfig":
        seeds_raw = os.getenv("FRONTIER_EVALS_SEEDS", "0")
        seeds = [int(s) for s in seeds_raw.split(",") if s.strip()]
        return cls(
            mode=os.getenv("FRONTIER_EVALS_MODE", "plumbing"),
            dataset=os.getenv("FRONTIER_EVALS_DATASET", "synthetic-mini"),
            seeds=seeds or [0],
            api_base_url=os.getenv("FRONTIER_EVALS_API_BASE_URL", ""),
            model=os.getenv("FRONTIER_EVALS_MODEL", ""),
            api_key=os.getenv("FRONTIER_EVALS_API_KEY", "not-needed"),
            provider=os.getenv("FRONTIER_EVALS_PROVIDER", "vllm"),
            profile_id=os.getenv("FRONTIER_EVALS_PROFILE", ""),
            docker_host=os.getenv("FRONTIER_EVALS_DOCKER_HOST", os.getenv("DOCKER_HOST", "")),
            allow_local=os.getenv("FRONTIER_EVALS_ALLOW_LOCAL", "0").strip()
            in {"1", "true", "yes"},
            output_dir=os.getenv("FRONTIER_EVALS_OUTPUT_DIR", "eval-results"),
            threshold=float(os.getenv("FRONTIER_EVALS_THRESHOLD", "0.30")),
        )

    def enforce_remote_guardrail(self, n_instances: int) -> None:
        """Refuse to run a real benchmark fleet against localhost.

        Encodes the resource-constrained-local-testing rule as code: live runs
        of more than a couple of instances must target a remote runner box.
        """
        if self.mode != "live" or self.allow_local:
            return
        if n_instances <= 2:
            return
        for label, url in (("api_base_url", self.api_base_url), ("docker_host", self.docker_host)):
            host = _host_of(url)
            if host in {"localhost", "127.0.0.1", "::1", ""} and url:
                raise RuntimeError(
                    f"Refusing to run a {n_instances}-instance live eval against a local "
                    f"{label} ({url!r}). Point it at a remote runner/GPU box, or pass "
                    f"--allow-local to override (not recommended on dev machines)."
                )


def _host_of(url: str) -> str:
    if not url:
        return ""
    if "://" not in url:
        url = "//" + url
    return (urlparse(url).hostname or "").lower()
