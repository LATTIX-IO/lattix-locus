"""High-level SWE agent: assemble harness pieces to solve one task.

Given a ``SweTask`` (problem statement + a workspace executor + test command),
runs the agent loop and returns a ``SweAgentResult`` carrying the produced
unified diff (the prediction graded by SWE-bench) and the trajectory.

Verified by default (LOCUS-337): when the task has a test command (or an
explicit ``envelope`` is given) the agent runs the
:class:`~locus_runtime.harness.verified_loop.VerifiedLoop`, whose default
envelope's done criterion is "the test command exits 0". ``submit`` is only
accepted once the tests pass; ``outcome`` maps the end state back to
:class:`LoopOutcome` (done -> SUBMITTED) and ``run`` carries the full
:class:`~locus_runtime.harness.verified_loop.RunResult`. Tasks with nothing
checkable (no test command, e.g. SWE-bench where the official harness grades)
keep the unverified :class:`AgentLoop`, and say so: ``end_state`` is empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from locus_runtime.harness.executor import Executor
from locus_runtime.harness.llm import ChatClient
from locus_runtime.harness.loop import AgentLoop, LoopBudgets, LoopOutcome
from locus_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from locus_runtime.harness.run_envelope import (
    CommandCheck,
    EnvelopeCapabilities,
    RunBudget,
    RunEnvelope,
    goal_from_task,
)
from locus_runtime.harness.prompts import (
    BASH_ONLY_SYSTEM_PROMPT,
    SWE_SYSTEM_PROMPT,
    build_task_prompt,
)
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.trajectory import TrajectoryRecorder
from locus_runtime.harness.verified_loop import ModelPricing, PlanMode, RunResult, VerifiedLoop
from locus_runtime.harness.workspace import Workspace


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
    #: done | blocked | stopped for a verified run; "" when the unverified loop ran.
    end_state: str = ""
    run: RunResult | None = None

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
    # Verified loop (LOCUS-337). ``envelope`` overrides the default "tests pass" one.
    verify: bool = True
    envelope: RunEnvelope | None = None
    judge_client: ChatClient | None = None
    pricing: ModelPricing | None = None
    # "optional": a plan is requested and recorded, not enforced (weak models,
    # legacy scripted flows). Use "required" for the native runner.
    plan_mode: PlanMode = "optional"
    run_dir: Path | None = None  # checkpoint location (defaults to trajectory_dir)

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

        envelope = self._envelope_for(task)
        if envelope is not None:
            return self._solve_verified(
                task, envelope, profile, workspace, toolset, recorder, system_prompt, user_prompt
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

    # -- verified path (LOCUS-337) ---------------------------------------------
    def _envelope_for(self, task: SweTask) -> RunEnvelope | None:
        if self.envelope is not None:
            return self.envelope
        # Analyzers (allow_edits=False) report findings; "tests pass" is not their
        # done criterion, so they keep the unverified loop unless given an envelope.
        if not self.verify or not self.allow_edits or not task.test_command.strip():
            return None
        defaults = RunBudget.from_env()
        budget = RunBudget.from_env(
            max_steps=self.budgets.max_steps,
            max_seconds=self.budgets.max_seconds,
            max_context_tokens=self.budgets.max_context_tokens,
            # Never tighter than the legacy loop: a full context on every step fits.
            max_tokens=max(
                defaults.max_tokens, self.budgets.max_steps * self.budgets.max_context_tokens
            ),
        )
        return RunEnvelope(
            goal=goal_from_task(task.problem_statement) or task.instance_id,
            done_criteria=(
                CommandCheck(
                    id="tests",
                    command=task.test_command,
                    timeout_seconds=self.test_timeout,
                    description="the test command passes",
                ),
            ),
            capabilities=EnvelopeCapabilities(
                read_roots=(task.executor.workdir(),), write_roots=(task.executor.workdir(),)
            ),
            budget=budget,
        )

    def _solve_verified(
        self,
        task: SweTask,
        envelope: RunEnvelope,
        profile: ModelCapabilityProfile,
        workspace: Workspace,
        toolset: CodingToolset,
        recorder: TrajectoryRecorder,
        system_prompt: str,
        user_prompt: str,
    ) -> SweAgentResult:
        run_dir = self.run_dir or self.trajectory_dir
        checkpoint = (
            Path(run_dir) / f"{task.instance_id}.checkpoint.json" if run_dir is not None else None
        )
        loop = VerifiedLoop(
            client=self.client,
            toolset=toolset,
            profile=profile,
            envelope=envelope,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            run_id=task.instance_id,
            judge_client=self.judge_client,
            pricing=self.pricing or ModelPricing(),
            plan_mode=self.plan_mode,
            checkpoint_path=checkpoint,
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
        run = loop.run()
        submission = run.submission or {}
        return SweAgentResult(
            instance_id=task.instance_id,
            outcome=run.legacy_outcome,
            patch=str(submission.get("patch") or "") if run.done else "",
            answer=str(submission.get("answer") or "") if run.done else "",
            steps=run.steps,
            telemetry=run.telemetry,
            elapsed_seconds=run.usage.elapsed_seconds,
            trajectory=recorder,
            seed=task.seed,
            end_state=run.end_state.value,
            run=run,
        )
