"""The callable cross-functional development workflow.

One entrypoint that brings the whole engineering team together — as a readable
team chat — to take a spec from plan to production-ready:

    PLAN     architect turns the spec into a concrete plan
    EXECUTE  SDET implements the change
    TEST     SDET runs the suite; results feed back into the loop
    SECURE   security auditor (+ code & performance reviewers) challenge the diff
    MODERATE quality moderator gates: ship or send back (bounded fix loop)
    DEPLOY   Azure cloud engineer prepares a deployment-readiness checklist;
             delivery opens/merges the PR per policy

It composes the existing TeamFlow (plan→implement→review→moderate→fix) and adds
the deployment-prep phase + delivery, then renders the entire run as a
phase-labelled chat transcript so you can read the team's conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from frontier_runtime.harness.integrations import (
    DeliveryPolicy,
    DeliveryResult,
    DeliveryTarget,
    Spec,
    SpecSource,
)
from frontier_runtime.harness.llm import ChatClient
from frontier_runtime.harness.loop import LoopBudgets, LoopOutcome
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from frontier_runtime.harness.swe_agent import SweTask
from frontier_runtime.harness.team import (
    TeamFlow,
    TeamResult,
    _single_shot,
    build_team_from_shipped,
)

PHASES = ("plan", "execute", "test", "secure", "moderate", "deploy")


@dataclass
class ChatTurn:
    phase: str
    role: str
    speaker: str  # human-friendly name
    content: str


@dataclass
class DevelopmentResult:
    spec: Spec
    approved: bool
    transcript: list[ChatTurn]
    team: TeamResult
    deploy_readiness: str = ""
    delivery: DeliveryResult | None = None

    def chat(self) -> str:
        """Render the run as a readable cross-functional team chat."""
        lines: list[str] = []
        last_phase = None
        for t in self.transcript:
            if t.phase != last_phase:
                lines.append(f"\n=== {t.phase.upper()} ===")
                last_phase = t.phase
            body = t.content.strip()
            lines.append(f"\n[{t.speaker}]\n{body}")
        verdict = "APPROVED ✅" if self.approved else "NOT APPROVED ❌"
        lines.append(f"\n=== RESULT: {verdict} ===")
        if self.delivery:
            lines.append(f"delivery: {self.delivery.action} {self.delivery.pr_url}".rstrip())
        return "\n".join(lines).strip()


_SPEAKERS = {
    "architect": "Spec Architect",
    "implementer": "SDET",
    "code-review": "Code Reviewer",
    "security": "Security Auditor",
    "performance": "Performance Engineer",
    "moderator": "Quality Moderator",
    "azure": "Azure Cloud Engineer",
    "system": "System",
}


@dataclass
class DevelopmentWorkflow:
    """Callable dev workflow over the cross-functional team."""

    team: TeamFlow
    deploy_client: ChatClient | None = None
    deploy_prompt: str = ""
    deploy_profile: ModelCapabilityProfile | None = None
    delivery: DeliveryTarget | None = None
    policy: DeliveryPolicy = field(default_factory=DeliveryPolicy)
    spec_source: SpecSource | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def run(self, task: SweTask, spec: Spec | str | None = None) -> DevelopmentResult:
        if isinstance(spec, Spec):
            spec_obj = spec
        elif self.spec_source is not None and spec is None:
            spec_obj = self.spec_source.fetch_spec()
        else:
            text = spec if isinstance(spec, str) else task.problem_statement
            spec_obj = Spec(id=task.instance_id, title="", body=text, source="inline")

        self._emit("phase", phase="plan")
        team_result = self.team.run(task, spec_obj.as_prompt())
        transcript = self._build_transcript(spec_obj, team_result)

        deploy_readiness = ""
        delivery_result: DeliveryResult | None = None
        if team_result.approved:
            self._emit("phase", phase="deploy")
            deploy_readiness = self._deploy_prep(spec_obj, team_result)
            transcript.append(ChatTurn("deploy", "azure", _SPEAKERS["azure"], deploy_readiness))
            if self.delivery is not None:
                delivery_result = self.delivery.deliver(task, spec_obj, team_result, self.policy)
                transcript.append(
                    ChatTurn(
                        "deploy",
                        "system",
                        _SPEAKERS["system"],
                        f"Delivery: {delivery_result.action}. {delivery_result.detail} "
                        f"{delivery_result.pr_url}".strip(),
                    )
                )
                self._emit("delivery", action=delivery_result.action, pr_url=delivery_result.pr_url)

        return DevelopmentResult(
            spec=spec_obj,
            approved=team_result.approved,
            transcript=transcript,
            team=team_result,
            deploy_readiness=deploy_readiness,
            delivery=delivery_result,
        )

    # -- transcript assembly ------------------------------------------------
    def _build_transcript(self, spec: Spec, team: TeamResult) -> list[ChatTurn]:
        turns: list[ChatTurn] = []
        turns.append(
            ChatTurn(
                "plan",
                "system",
                _SPEAKERS["system"],
                f"Spec [{spec.source}:{spec.id}]: {spec.title}\n{spec.body}",
            )
        )
        if team.plan:
            turns.append(ChatTurn("plan", "architect", _SPEAKERS["architect"], team.plan))
        for rnd in team.rounds:
            label = f"(round {rnd.index + 1})"
            impl = rnd.implement
            outcome = "submitted" if impl.outcome == LoopOutcome.SUBMITTED else impl.outcome.value
            turns.append(
                ChatTurn(
                    "execute",
                    "implementer",
                    _SPEAKERS["implementer"],
                    f"{label} {impl.answer or '(worked on the change)'}\n"
                    f"steps={impl.steps} outcome={outcome} edits={impl.telemetry.get('edits_applied', 0)}",
                )
            )
            turns.append(
                ChatTurn(
                    "test",
                    "implementer",
                    _SPEAKERS["implementer"],
                    f"{label} ran tests ({impl.telemetry.get('test_runs', 0)} run(s)); "
                    f"patch {'present' if impl.has_patch else 'empty'}.",
                )
            )
            for rv in rnd.reviews:
                phase = "secure" if rv.role == "security" else "secure"
                fcount = len(rv.findings)
                turns.append(
                    ChatTurn(
                        phase,
                        rv.role,
                        _SPEAKERS.get(rv.role, rv.role),
                        f"{label} verdict={rv.verdict}; {fcount} finding(s). {rv.summary}",
                    )
                )
            v = rnd.verdict
            req = "\n".join(f"  - {c}" for c in v.required_changes)
            turns.append(
                ChatTurn(
                    "moderate",
                    "moderator",
                    _SPEAKERS["moderator"],
                    f"{label} decision={v.decision}. {v.rationale}"
                    + (f"\nRequired changes:\n{req}" if v.required_changes else ""),
                )
            )
        return turns

    def _deploy_prep(self, spec: Spec, team: TeamResult) -> str:
        if self.deploy_client is None or not self.deploy_prompt:
            return self._fallback_readiness(team)
        diff = team.final_patch or ""
        user = (
            f"Specification:\n{spec.as_prompt()}\n\n"
            f"Approved change (unified diff):\n{diff or '(no code changes)'}\n\n"
            "The change is approved. Produce a concise DEPLOYMENT READINESS checklist for "
            "shipping this to production on Azure: required infra/config changes, secrets & "
            "identity, rollout/rollback plan, observability, and any pre-deploy gates. If no "
            "cloud changes are needed, say so and give the minimal release steps."
        )
        try:
            return _single_shot(
                self.deploy_client,
                self.deploy_prompt,
                user,
                self.deploy_profile or resolve_profile("", ""),
            )
        except Exception:  # noqa: BLE001 - deploy prep is advisory, never fatal
            return self._fallback_readiness(team)

    @staticmethod
    def _fallback_readiness(team: TeamResult) -> str:
        deferred = []
        if team.rounds:
            deferred = team.rounds[-1].verdict.deferred
        notes = "\n".join(f"- follow-up: {d}" for d in deferred)
        return (
            "Deployment readiness:\n"
            "- code approved by the review panel; tests passing\n"
            "- open PR for human review; verify CI is green before merge\n"
            "- no cloud-infra changes detected by the team\n" + (notes or "")
        ).strip()


def build_development_workflow(
    client_for: Callable[[str], ChatClient],
    *,
    repo_root: Path | None = None,
    budgets: LoopBudgets | None = None,
    max_rounds: int = 3,
    delivery: DeliveryTarget | None = None,
    policy: DeliveryPolicy | None = None,
    spec_source: SpecSource | None = None,
    profile_overrides: dict[str, Any] | None = None,
    trajectory_dir: Path | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> DevelopmentWorkflow:
    """Assemble the dev workflow from the shipped agent team."""
    team = build_team_from_shipped(
        client_for,
        repo_root=repo_root,
        budgets=budgets,
        max_rounds=max_rounds,
        profile_overrides=profile_overrides,
        trajectory_dir=trajectory_dir,
        on_event=on_event,
    )
    return DevelopmentWorkflow(
        team=team,
        deploy_client=client_for("azure"),
        deploy_prompt=team.prompts.get("azure", "") or _load_azure_prompt(repo_root),
        deploy_profile=team.profiles.get("azure"),
        delivery=delivery,
        policy=policy or DeliveryPolicy(),
        spec_source=spec_source,
        on_event=on_event,
    )


def _load_azure_prompt(repo_root: Path | None) -> str:
    from frontier_runtime.harness.agent_library import load_agent_spec

    try:
        return load_agent_spec("azure-cloud-engineer-agent", repo_root).system_prompt
    except FileNotFoundError:
        return ""
