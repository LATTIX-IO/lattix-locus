"""High-level SWE agent: assemble harness pieces to solve one task.

Given a ``SweTask`` (problem statement + a workspace executor + test command),
runs the agent loop and returns a ``SweAgentResult`` carrying the produced
unified diff (the prediction graded by SWE-bench) and the trajectory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from frontier_runtime.harness.executor import Executor
from frontier_runtime.harness.llm import ChatClient
from frontier_runtime.harness.loop import AgentLoop, LoopBudgets, LoopOutcome
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from frontier_runtime.harness.prompts import (
    BASH_ONLY_SYSTEM_PROMPT,
    SWE_SYSTEM_PROMPT,
    build_task_prompt,
)
from frontier_runtime.harness.tools import CodingToolset
from frontier_runtime.harness.trajectory import TrajectoryRecorder
from frontier_runtime.harness.workspace import Workspace


@dataclass
class SweTask:
    instance_id: str
    problem_statement: str
    executor: Executor
    test_command: str = ""
    base_ref: str = ""
    repo_hint: str = ""
    git_executor: Executor | None = None
    seed: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SweAgentResult:
    instance_id: str
    outcome: LoopOutcome
    patch: str
    answer: str
    steps: int
    telemetry: dict[str, Any]
    elapsed_seconds: float
    trajectory: TrajectoryRecorder
    seed: int | None = None

    @property
    def has_patch(self) -> bool:
        return bool(self.patch and self.patch.strip())


@dataclass
class SweAgent:
    client: ChatClient
    profile: ModelCapabilityProfile | None = None
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    bash_timeout: int = 60
    test_timeout: int = 600
    trajectory_dir: Path | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    system_prompt_override: str | None = None  # e.g. a shipped agent's prompt
    out_of_bounds: str = "ask"  # workspace-boundary policy: ask | deny | allow
    on_escalation: Callable[[dict[str, Any]], None] | None = None
    allow_edits: bool = True  # False => read+exec analyzer (no file mutation)

    def _resolve_profile(self) -> ModelCapabilityProfile:
        if self.profile is not None:
            return self.profile
        return resolve_profile(
            getattr(self.client, "provider", "openai-compatible"),
            getattr(self.client, "model", ""),
        )

    def solve(self, task: SweTask) -> SweAgentResult:
        profile = self._resolve_profile()
        workspace = Workspace(
            run_id=task.instance_id,
            executor=task.executor,
            test_command=task.test_command,
            base_ref=task.base_ref,
            git_executor=task.git_executor,
        )
        toolset = CodingToolset(
            workspace=workspace,
            edit_format=profile.edit_format,
            bash_timeout=self.bash_timeout,
            test_timeout=self.test_timeout,
            out_of_bounds=self.out_of_bounds,
            on_escalation=self.on_escalation,
            allow_edits=self.allow_edits,
        )
        recorder = None
        if self.trajectory_dir is not None:
            recorder = TrajectoryRecorder(
                run_id=task.instance_id,
                file_path=Path(self.trajectory_dir) / f"{task.instance_id}.jsonl",
            )
        else:
            recorder = TrajectoryRecorder(run_id=task.instance_id)

        bash_only = profile.tool_protocol == "bash-only"
        if self.system_prompt_override:
            system_prompt = self.system_prompt_override
        else:
            system_prompt = BASH_ONLY_SYSTEM_PROMPT if bash_only else SWE_SYSTEM_PROMPT
        user_prompt = build_task_prompt(
            task.problem_statement,
            repo_hint=task.repo_hint,
            test_hint=task.test_command,
        )

        loop = AgentLoop(
            client=self.client,
            toolset=toolset,
            profile=profile,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            budgets=self.budgets,
            recorder=recorder,
            on_event=self.on_event,
            agent_id="swe-agent",
            task_meta={
                "run_id": task.instance_id,
                "instance_id": task.instance_id,
                "seed": task.seed,
                "prompt": task.problem_statement[:2000],
            },
        )
        result = loop.run()
        patch = (result.submission or {}).get("patch", "") if result.submission else ""
        # Even on non-submit outcomes, capture any diff for debugging (not graded).
        if not patch and result.outcome != LoopOutcome.SUBMITTED:
            try:
                patch = workspace.diff()
            except Exception:  # noqa: BLE001
                patch = ""
        return SweAgentResult(
            instance_id=task.instance_id,
            outcome=result.outcome,
            patch=patch if result.outcome == LoopOutcome.SUBMITTED else "",
            answer=result.text,
            steps=result.steps,
            telemetry=result.telemetry,
            elapsed_seconds=result.elapsed_seconds,
            trajectory=result.trajectory,
            seed=task.seed,
        )
