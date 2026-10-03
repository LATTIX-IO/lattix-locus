"""Locus self-improvement loop: native Linear intake + delivery runner (LOCUS-338).

See ``docs/development/self-improvement-loop.md`` and :mod:`.runner`.
"""

from __future__ import annotations

from typing import Any

from locus_runtime.loop_runner.merge_guard import (
    ChangedFile,
    GateCheck,
    MergeDecision,
    evaluate_auto_merge,
    parse_codeowners,
)
from locus_runtime.loop_runner.state import LoopConfig, kill_switch_reason, loop_status

__all__ = [
    "ChangedFile",
    "GateCheck",
    "LoopConfig",
    "MergeDecision",
    "build_runner",
    "evaluate_auto_merge",
    "kill_switch_reason",
    "loop_status",
    "parse_codeowners",
]


def build_runner(repo_path: str = ".", **overrides: Any) -> Any:
    """The production runner: Linear over httpx, ``gh`` CLI, OPA gateway, NIM → Ollama."""
    from pathlib import Path

    from locus_runtime.loop_runner.delivery import GhCliLoopGitHub, default_gh_runner
    from locus_runtime.loop_runner.linear import LinearClient
    from locus_runtime.loop_runner.runner import LoopRunner

    config = LoopConfig.load(Path(repo_path))
    return LoopRunner(
        config=config,
        tracker=LinearClient(),
        github=GhCliLoopGitHub(default_gh_runner(config.repo_path)),
        **overrides,
    )
