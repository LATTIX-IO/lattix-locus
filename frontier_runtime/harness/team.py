"""Multi-agent dev team orchestration.

A spec goes in; a reviewed, gated patch comes out. The team:

  architect  -> plan the change from the spec
  implementer (SweAgent) -> implement + test against the plan
  review panel (code / security / performance) -> challenge the change
  moderator  -> synthesize the panel, decide ship / send-back
  (loop)     -> on send-back, the implementer addresses the required changes

This is where the agents "challenge each other, moderate, ensure quality". The
implementer runs the full coding loop (tools, tests, submit); the architect,
reviewers, and moderator are single-shot judges over the spec + diff, which
keeps the panel cheap and deterministic to test. Reviewers report findings —
they never edit — so only the implementer changes code.

Role prompts come from the shipped agent definitions (`examples/agents/`), so
the team you run is the team in the modeler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from frontier_runtime.harness.agent_library import AgentSpec, load_agent_spec
from frontier_runtime.harness.llm import ChatClient
from frontier_runtime.harness.loop import LoopBudgets, LoopOutcome
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from frontier_runtime.harness.swe_agent import SweAgent, SweAgentResult, SweTask

# role -> shipped agent id
TEAM_ROLE_AGENTS: dict[str, str] = {
    "architect": "spec-architect-agent",
    "implementer": "sdet-swe-agent",
    "code-review": "code-reviewer-agent",
    "security": "security-auditor-agent",
    "performance": "performance-engineer-agent",
    "moderator": "quality-moderator-agent",
}
REVIEW_ROLES = ("code-review", "security", "performance")


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model response (tolerant of fences)."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    candidates = []
    if fence:
        candidates.append(fence.group(1))
    # also try the largest brace-balanced span
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue
    return None


@dataclass
class Review:
    role: str
    verdict: str  # "approve" | "request_changes"
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    raw: str = ""

    @property
    def requests_changes(self) -> bool:
        return self.verdict == "request_changes"

    def blocking(self) -> list[dict[str, Any]]:
        sev = {"critical", "major", "high"}
        return [f for f in self.findings if str(f.get("severity", "")).lower() in sev]


@dataclass
class ModeratorVerdict:
    decision: str  # "approve" | "request_changes"
    required_changes: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    rationale: str = ""
    raw: str = ""

    @property
    def approved(self) -> bool:
        return self.decision == "approve"


@dataclass
class RoundResult:
    index: int
    implement: SweAgentResult
    reviews: list[Review]
    verdict: ModeratorVerdict


@dataclass
class TeamResult:
    spec: str
    approved: bool
    final_patch: str
    rounds: list[RoundResult]
    plan: str = ""

    @property
    def round_count(self) -> int:
        return len(self.rounds)


def _single_shot(
    client: ChatClient, system_prompt: str, user_prompt: str, profile: ModelCapabilityProfile
) -> str:
    resp = client.complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=None,
        temperature=profile.temperature,
        top_p=profile.top_p,
    )
    return resp.text or ""


@dataclass
class ReviewAgent:
    role: str
    client: ChatClient
    system_prompt: str
    profile: ModelCapabilityProfile

    def review(self, spec: str, diff: str) -> Review:
        user = (
            f"Specification:\n{spec}\n\n"
            f"Proposed change (unified diff):\n{diff or '(no changes)'}\n\n"
            "Review it and respond with the JSON object described in your instructions."
        )
        text = _single_shot(self.client, self.system_prompt, user, self.profile)
        data = extract_json(text) or {}
        verdict = str(data.get("verdict") or "approve").strip().lower()
        if verdict not in ("approve", "request_changes"):
            verdict = "request_changes" if data.get("findings") else "approve"
        findings = data.get("findings") if isinstance(data.get("findings"), list) else []
        return Review(
            role=self.role,
            verdict=verdict,
            findings=findings,
            summary=str(data.get("summary") or ""),
            raw=text,
        )


@dataclass
class TeamFlow:
    """Orchestrate the multi-agent dev team over a single task/spec."""

    client_for: Callable[[str], ChatClient]
    prompts: dict[str, str]
    profiles: dict[str, ModelCapabilityProfile]
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    max_rounds: int = 3
    use_architect: bool = True
    trajectory_dir: Path | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def run(self, task: SweTask, spec: str | None = None) -> TeamResult:
        spec = spec or task.problem_statement
        plan = ""
        if self.use_architect and "architect" in self.prompts:
            self._emit("phase", phase="architect")
            plan = _single_shot(
                self.client_for("architect"),
                self.prompts["architect"],
                f"Specification:\n{spec}\n\nProduce the implementation plan.",
                self.profiles.get("architect", resolve_profile("", "")),
            )

        rounds: list[RoundResult] = []
        directives = spec if not plan else f"{spec}\n\n## Implementation plan\n{plan}"
        approved = False

        for i in range(self.max_rounds):
            self._emit("phase", phase="implement", round=i)
            impl_task = SweTask(
                instance_id=f"{task.instance_id}-r{i}",
                problem_statement=directives,
                executor=task.executor,
                test_command=task.test_command,
                base_ref=task.base_ref,
                repo_hint=task.repo_hint,
                git_executor=task.git_executor,
                seed=task.seed,
                metadata=task.metadata,
            )
            implementer = SweAgent(
                client=self.client_for("implementer"),
                profile=self.profiles.get("implementer", resolve_profile("", "")),
                budgets=self.budgets,
                trajectory_dir=self.trajectory_dir,
                system_prompt_override=self.prompts.get("implementer"),
            )
            impl = implementer.solve(impl_task)
            diff = impl.patch or ""

            self._emit("phase", phase="review", round=i)
            reviews: list[Review] = []
            for role in REVIEW_ROLES:
                if role not in self.prompts:
                    continue
                agent = ReviewAgent(
                    role=role,
                    client=self.client_for(role),
                    system_prompt=self.prompts[role],
                    profile=self.profiles.get(role, resolve_profile("", "")),
                )
                reviews.append(agent.review(spec, diff))
                self._emit("review", role=role, verdict=reviews[-1].verdict)

            verdict = self._moderate(spec, diff, impl, reviews)
            rounds.append(RoundResult(index=i, implement=impl, reviews=reviews, verdict=verdict))
            self._emit("verdict", round=i, decision=verdict.decision)

            if verdict.approved and impl.outcome == LoopOutcome.SUBMITTED:
                approved = True
                break
            # prepare the next round: fold the required changes into the directives
            if verdict.required_changes:
                changes = "\n".join(f"- {c}" for c in verdict.required_changes)
                directives = (
                    f"{spec}\n\n## Plan\n{plan}\n\n"
                    f"## Required changes from review (address all)\n{changes}"
                )

        final_patch = ""
        if rounds:
            final_patch = rounds[-1].implement.patch if approved else ""
        return TeamResult(
            spec=spec, approved=approved, final_patch=final_patch, rounds=rounds, plan=plan
        )

    def _moderate(
        self, spec: str, diff: str, impl: SweAgentResult, reviews: list[Review]
    ) -> ModeratorVerdict:
        self._emit("phase", phase="moderate")
        reviews_blob = "\n\n".join(
            f"### {r.role} review (verdict: {r.verdict})\n"
            f"summary: {r.summary}\nfindings: {json.dumps(r.findings, ensure_ascii=False)}"
            for r in reviews
        )
        test_state = "submitted" if impl.outcome == LoopOutcome.SUBMITTED else impl.outcome.value
        user = (
            f"Specification:\n{spec}\n\n"
            f"Implementer outcome: {test_state}\n\n"
            f"Proposed change (unified diff):\n{diff or '(no changes)'}\n\n"
            f"Reviews:\n{reviews_blob}\n\n"
            "Synthesize and decide. Respond with the JSON object from your instructions."
        )
        prompt = self.prompts.get("moderator", "")
        text = _single_shot(
            self.client_for("moderator"),
            prompt,
            user,
            self.profiles.get("moderator", resolve_profile("", "")),
        )
        data = extract_json(text) or {}
        decision = str(data.get("decision") or "").strip().lower()
        if decision not in ("approve", "request_changes"):
            # safe default: if any reviewer blocks or no submission, request changes
            blocking = any(r.requests_changes for r in reviews)
            decision = (
                "request_changes"
                if blocking or impl.outcome != LoopOutcome.SUBMITTED
                else "approve"
            )
        req = data.get("required_changes")
        deferred = data.get("deferred")
        return ModeratorVerdict(
            decision=decision,
            required_changes=req if isinstance(req, list) else [],
            deferred=deferred if isinstance(deferred, list) else [],
            rationale=str(data.get("rationale") or ""),
            raw=text,
        )


def build_team_from_shipped(
    client_for: Callable[[str], ChatClient],
    *,
    repo_root: Path | None = None,
    budgets: LoopBudgets | None = None,
    max_rounds: int = 3,
    profile_overrides: dict[str, Any] | None = None,
    trajectory_dir: Path | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> TeamFlow:
    """Construct a TeamFlow from the shipped agent definitions in examples/agents/."""
    prompts: dict[str, str] = {}
    profiles: dict[str, ModelCapabilityProfile] = {}
    for role, agent_id in TEAM_ROLE_AGENTS.items():
        try:
            spec: AgentSpec = load_agent_spec(agent_id, repo_root)
        except FileNotFoundError:
            continue
        prompts[role] = spec.system_prompt
        profiles[role] = spec.profile(overrides=profile_overrides)
    return TeamFlow(
        client_for=client_for,
        prompts=prompts,
        profiles=profiles,
        budgets=budgets or LoopBudgets(),
        max_rounds=max_rounds,
        trajectory_dir=trajectory_dir,
        on_event=on_event,
    )
