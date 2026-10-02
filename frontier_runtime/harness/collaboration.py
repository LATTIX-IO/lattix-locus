"""A genuine collaborative cross-functional engineering team.

Not a pipeline. A shared working thread where each engineer reasons through the
spec in their own discipline (with visible chain-of-thought), reads and responds
to teammates, and the Tech Lead facilitates the group to a consensus design —
then the team builds and tests against that design, verifies it across
disciplines, and hands a completed, intent-matching feature back to the human.

Flow (facilitated round-table):

  1. OPEN      Tech Lead frames the spec, acceptance criteria, key questions.
  2. DISCUSS   each engineer (backend, frontend, SDET, security, devops, perf)
               posts independent reasoning + a proposal + concerns; later
               speakers see earlier ones, so it is a real discussion. The Tech
               Lead facilitates each round and decides when the team has reached
               a workable consensus, then synthesizes the AGREED DESIGN.
  3. BUILD     the implementer writes the code + tests against the agreed design
               (full coding loop), iterating with the team on send-back.
  4. VERIFY    each discipline reviews the change against the design + intent.
  5. GATE      Tech Lead approves only if functional + intent-matching.
  6. HANDBACK  a completed feature + the team's discussion + a human-facing summary.

Each agent's ``thinking`` field is its chain-of-thought, captured in the
conversation so the human can see how the team reasoned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from frontier_runtime.harness.integrations import Spec
from frontier_runtime.harness.llm import ChatClient
from frontier_runtime.harness.loop import LoopBudgets, LoopOutcome
from frontier_runtime.harness.model_profiles import ModelCapabilityProfile, resolve_profile
from frontier_runtime.harness.swe_agent import SweAgent, SweAgentResult, SweTask
from frontier_runtime.harness.team import _single_shot, extract_json

COLLAB_ROLE_AGENTS: dict[str, str] = {
    "tech-lead": "tech-lead-agent",
    "backend": "backend-engineer-agent",
    "frontend": "frontend-engineer-agent",
    "sdet": "sdet-swe-agent",
    "security": "security-auditor-agent",
    "devops": "azure-cloud-engineer-agent",
    "performance": "performance-engineer-agent",
}
SPEAKERS = {
    "tech-lead": "Tech Lead",
    "backend": "Backend Engineer",
    "frontend": "Frontend Engineer",
    "sdet": "SDET / QA",
    "security": "Security Engineer",
    "devops": "DevOps / Cloud Engineer",
    "performance": "Performance Engineer",
    "system": "System",
}
FACILITATOR = "tech-lead"
IMPLEMENTER = "sdet"
DEFAULT_PARTICIPANTS = ("backend", "frontend", "sdet", "security", "devops", "performance")


@dataclass
class Contribution:
    role: str
    speaker: str
    phase: str  # open | discuss | facilitate | verify | gate | system
    round: int
    thinking: str = ""  # chain of thought
    message: str = ""  # what they say to the team
    proposal: str = ""
    concerns: list[str] = field(default_factory=list)
    verdict: str = ""  # verify/gate: approve | request_changes


@dataclass
class Conversation:
    turns: list[Contribution] = field(default_factory=list)

    def add(self, c: Contribution) -> None:
        self.turns.append(c)

    def visible(self, max_chars: int = 6000) -> str:
        """The message stream teammates read (no private chain-of-thought)."""
        lines = []
        for t in self.turns:
            head = f"{t.speaker}"
            if t.message:
                lines.append(f"{head}: {t.message}")
            if t.proposal:
                lines.append(f"  proposal: {t.proposal}")
            if t.concerns:
                lines.append("  concerns: " + "; ".join(t.concerns))
        text = "\n".join(lines)
        return text[-max_chars:] if len(text) > max_chars else text

    def transcript(self) -> str:
        """Full record including each engineer's chain-of-thought."""
        out = []
        last_phase = None
        for t in self.turns:
            if t.phase != last_phase:
                out.append(f"\n=== {t.phase.upper()} ===")
                last_phase = t.phase
            out.append(f"\n[{t.speaker}]")
            if t.thinking:
                out.append(f"  (thinking) {t.thinking}")
            if t.message:
                out.append(f"  {t.message}")
            if t.proposal:
                out.append(f"  proposal: {t.proposal}")
            if t.concerns:
                out.append("  concerns: " + "; ".join(t.concerns))
            if t.verdict:
                out.append(f"  verdict: {t.verdict}")
        return "\n".join(out).strip()


@dataclass
class CollaborationResult:
    spec: Spec
    approved: bool
    agreed_design: str
    conversation: Conversation
    final_patch: str
    discussion_rounds: int
    build_rounds: int
    implement: SweAgentResult | None = None
    handback: str = ""

    def chat(self) -> str:
        body = self.conversation.transcript()
        verdict = "FEATURE COMPLETE ✅" if self.approved else "NOT COMPLETE ❌"
        return f"{body}\n\n=== {verdict} ===\n{self.handback}".strip()


_ENGINEER_TURN = (
    "You are participating in the team's design discussion. Read the spec and the "
    "discussion so far, then contribute from your discipline. Build on what others "
    "said; agree, disagree (with reasons), or raise what they missed.\n\n"
    "Respond ONLY with JSON:\n"
    '{{"thinking": "your private step-by-step reasoning about the spec and the '
    'discussion", "message": "what you say to the team (concise, specific, builds on '
    'others)", "proposal": "your concrete proposal for your part of the approach", '
    '"concerns": ["risks or objections you are raising"]}}'
)
_FACILITATE_TURN = (
    "You are facilitating this round. Read the whole discussion and decide whether the "
    "team has reached a workable consensus on the approach.\n\n"
    "Respond ONLY with JSON:\n"
    '{{"thinking": "your assessment of agreements, disagreements, and gaps", '
    '"consensus": true or false, "message": "what you tell the team now", '
    '"agreed_design": "if consensus: a concrete minimal plan the team supports — '
    "approach, files/components to change, test strategy, and how it handles the "
    'security/performance/deployment risks raised", "open_questions": ["if not '
    'consensus: the specific unresolved questions for the next round"]}}'
)
_VERIFY_TURN = (
    "The change below was implemented against the team's agreed design. Review it from "
    "your discipline against the design and the spec intent.\n\n"
    "Respond ONLY with JSON:\n"
    '{{"thinking": "your review reasoning", "message": "your assessment to the team", '
    '"verdict": "approve" or "request_changes", "concerns": ["specific blocking issues, '
    'if any"]}}'
)
_GATE_TURN = (
    "You are the Tech Lead deciding whether this feature is done and matches the human's "
    "intent. Consider the spec, the agreed design, the implementation outcome (tests), and "
    "the team's verification.\n\n"
    "Respond ONLY with JSON:\n"
    '{{"thinking": "the decisive factors", "message": "your decision to the team", '
    '"decision": "approve" or "request_changes", "required_changes": ["if requesting '
    'changes: one prioritized, deduplicated list"]}}'
)


@dataclass
class CollaborativeTeam:
    client_for: Callable[[str], ChatClient]
    prompts: dict[str, str]
    profiles: dict[str, ModelCapabilityProfile]
    budgets: LoopBudgets = field(default_factory=LoopBudgets)
    participants: tuple[str, ...] = DEFAULT_PARTICIPANTS
    max_discussion_rounds: int = 2
    max_build_rounds: int = 2
    out_of_bounds: str = "ask"  # workspace-boundary policy for the implementer
    on_escalation: Callable[[dict[str, Any]], None] | None = None
    trajectory_dir: Path | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def _profile(self, role: str) -> ModelCapabilityProfile:
        return self.profiles.get(role) or resolve_profile("", "")

    def _ask(self, role: str, instruction: str, context: str) -> dict[str, Any]:
        persona = self.prompts.get(role, "")
        text = _single_shot(
            self.client_for(role), persona, f"{context}\n\n{instruction}", self._profile(role)
        )
        return extract_json(text) or {"message": text.strip()[:1000]}

    def run(self, task: SweTask, spec: Spec | str) -> CollaborationResult:
        spec_obj = (
            spec
            if isinstance(spec, Spec)
            else Spec(id=task.instance_id, title="", body=str(spec), source="inline")
        )
        conv = Conversation()
        conv.add(
            Contribution(
                "system",
                SPEAKERS["system"],
                "open",
                0,
                message=f"Spec [{spec_obj.source}:{spec_obj.id}] {spec_obj.title}\n{spec_obj.body}",
            )
        )

        # 1. Tech Lead opens the discussion
        self._emit("phase", phase="open")
        opening = self._ask(
            "tech-lead", _FACILITATE_OPEN, f"Specification:\n{spec_obj.as_prompt()}"
        )
        conv.add(
            Contribution(
                "tech-lead",
                SPEAKERS["tech-lead"],
                "open",
                0,
                thinking=str(opening.get("thinking", "")),
                message=str(opening.get("message", "")),
            )
        )

        # 2. Discussion rounds -> consensus design
        agreed_design = ""
        disc_round = 0
        for r in range(1, self.max_discussion_rounds + 1):
            disc_round = r
            self._emit("phase", phase="discuss", round=r)
            for role in self.participants:
                ctx = (
                    f"Specification:\n{spec_obj.as_prompt()}\n\n"
                    f"Discussion so far:\n{conv.visible()}"
                )
                data = self._ask(role, _ENGINEER_TURN, ctx)
                conv.add(
                    Contribution(
                        role,
                        SPEAKERS.get(role, role),
                        "discuss",
                        r,
                        thinking=str(data.get("thinking", "")),
                        message=str(data.get("message", "")),
                        proposal=str(data.get("proposal", "")),
                        concerns=_as_list(data.get("concerns")),
                    )
                )
                self._emit("contribution", role=role, round=r)
            facil = self._ask(
                "tech-lead",
                _FACILITATE_TURN,
                f"Specification:\n{spec_obj.as_prompt()}\n\nFull discussion:\n{conv.visible()}",
            )
            consensus = bool(facil.get("consensus"))
            conv.add(
                Contribution(
                    "tech-lead",
                    SPEAKERS["tech-lead"],
                    "facilitate",
                    r,
                    thinking=str(facil.get("thinking", "")),
                    message=str(facil.get("message", "")),
                    concerns=_as_list(facil.get("open_questions")),
                )
            )
            self._emit("facilitate", round=r, consensus=consensus)
            if consensus and str(facil.get("agreed_design", "")).strip():
                agreed_design = str(facil.get("agreed_design"))
                break
            agreed_design = str(facil.get("agreed_design", "")).strip() or agreed_design

        if not agreed_design:
            agreed_design = self._fallback_design(conv)

        # 3-5. Build -> verify -> gate (bounded fix loop)
        approved = False
        impl: SweAgentResult | None = None
        directives = f"{spec_obj.as_prompt()}\n\n## Agreed design (from the team)\n{agreed_design}"
        build_round = 0
        for b in range(1, self.max_build_rounds + 1):
            build_round = b
            self._emit("phase", phase="build", round=b)
            impl = self._implement(task, directives, b)
            diff = impl.patch or ""

            self._emit("phase", phase="verify", round=b)
            verify_roles = [
                r
                for r in ("backend", "frontend", "sdet", "security", "performance")
                if r in self.participants
            ]
            for role in verify_roles:
                ctx = (
                    f"Specification:\n{spec_obj.as_prompt()}\n\n"
                    f"Agreed design:\n{agreed_design}\n\n"
                    f"Implemented change (unified diff):\n{diff or '(no changes)'}"
                )
                data = self._ask(role, _VERIFY_TURN, ctx)
                conv.add(
                    Contribution(
                        role,
                        SPEAKERS.get(role, role),
                        "verify",
                        b,
                        thinking=str(data.get("thinking", "")),
                        message=str(data.get("message", "")),
                        verdict=str(data.get("verdict", "")),
                        concerns=_as_list(data.get("concerns")),
                    )
                )

            outcome = "submitted" if impl.outcome == LoopOutcome.SUBMITTED else impl.outcome.value
            gate = self._ask(
                "tech-lead",
                _GATE_TURN,
                f"Specification:\n{spec_obj.as_prompt()}\n\n"
                f"Agreed design:\n{agreed_design}\n\n"
                f"Implementation outcome: {outcome}; tests run: "
                f"{impl.telemetry.get('test_runs', 0)}\n\n"
                f"Change (unified diff):\n{diff or '(no changes)'}\n\n"
                f"Team verification:\n{conv.visible()}",
            )
            decision = str(gate.get("decision", "")).strip().lower()
            required = _as_list(gate.get("required_changes"))
            if decision not in ("approve", "request_changes"):
                decision = (
                    "approve"
                    if impl.outcome == LoopOutcome.SUBMITTED and not required
                    else "request_changes"
                )
            conv.add(
                Contribution(
                    "tech-lead",
                    SPEAKERS["tech-lead"],
                    "gate",
                    b,
                    thinking=str(gate.get("thinking", "")),
                    message=str(gate.get("message", "")),
                    verdict=decision,
                    concerns=required,
                )
            )
            self._emit("gate", round=b, decision=decision)
            if decision == "approve" and impl.outcome == LoopOutcome.SUBMITTED:
                approved = True
                break
            if required:
                changes = "\n".join(f"- {c}" for c in required)
                directives = (
                    f"{spec_obj.as_prompt()}\n\n## Agreed design\n{agreed_design}\n\n"
                    f"## Required changes from the team (address all)\n{changes}"
                )

        handback = self._handback(spec_obj, agreed_design, impl, approved)
        return CollaborationResult(
            spec=spec_obj,
            approved=approved,
            agreed_design=agreed_design,
            conversation=conv,
            final_patch=(impl.patch if (impl and approved) else ""),
            discussion_rounds=disc_round,
            build_rounds=build_round,
            implement=impl,
            handback=handback,
        )

    def _implement(self, task: SweTask, directives: str, build_round: int) -> SweAgentResult:
        impl_task = SweTask(
            instance_id=f"{task.instance_id}-b{build_round}",
            problem_statement=directives,
            executor=task.executor,
            test_command=task.test_command,
            base_ref=task.base_ref,
            repo_hint=task.repo_hint,
            git_executor=task.git_executor,
            seed=task.seed,
            metadata=task.metadata,
        )
        agent = SweAgent(
            client=self.client_for(IMPLEMENTER),
            profile=self._profile(IMPLEMENTER),
            budgets=self.budgets,
            trajectory_dir=self.trajectory_dir,
            system_prompt_override=self.prompts.get(IMPLEMENTER),
            out_of_bounds=self.out_of_bounds,
            on_escalation=self.on_escalation,
        )
        return agent.solve(impl_task)

    def _fallback_design(self, conv: Conversation) -> str:
        proposals = [f"- {t.speaker}: {t.proposal}" for t in conv.turns if t.proposal]
        return "Consensus not explicitly reached; proceeding on the team's proposals:\n" + (
            "\n".join(proposals) or "- implement the spec minimally and test it"
        )

    def _handback(
        self, spec: Spec, design: str, impl: SweAgentResult | None, approved: bool
    ) -> str:
        status = "complete and approved by the team" if approved else "NOT complete"
        tests = impl.telemetry.get("test_runs", 0) if impl else 0
        return (
            f"Feature for [{spec.source}:{spec.id}] {spec.title} is {status}.\n"
            f"Agreed approach:\n{design[:1200]}\n"
            f"Implementation: {'patch produced' if (impl and impl.has_patch and approved) else 'no final patch'}; "
            f"tests run: {tests}.\n"
            + (
                "Ready for your review/merge."
                if approved
                else "Returned to you for direction — the team could not converge on a passing solution."
            )
        )


_FACILITATE_OPEN = (
    "Open the team's design discussion for this spec. Respond ONLY with JSON:\n"
    '{{"thinking": "your read of the problem", "message": "frame the intent, the key '
    'questions for the team, and the acceptance criteria; invite each discipline to weigh in"}}'
)


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def build_collaborative_team(
    client_for: Callable[[str], ChatClient],
    *,
    repo_root: Path | None = None,
    budgets: LoopBudgets | None = None,
    participants: tuple[str, ...] = DEFAULT_PARTICIPANTS,
    max_discussion_rounds: int = 2,
    max_build_rounds: int = 2,
    out_of_bounds: str = "ask",
    on_escalation: Callable[[dict[str, Any]], None] | None = None,
    profile_overrides: dict[str, Any] | None = None,
    trajectory_dir: Path | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> CollaborativeTeam:
    """Assemble the collaborative team from the shipped agent definitions."""
    from frontier_runtime.harness.agent_library import load_agent_spec

    prompts: dict[str, str] = {}
    profiles: dict[str, ModelCapabilityProfile] = {}
    needed = {"tech-lead", IMPLEMENTER, *participants}
    for role in needed:
        agent_id = COLLAB_ROLE_AGENTS.get(role)
        if not agent_id:
            continue
        try:
            spec = load_agent_spec(agent_id, repo_root)
        except FileNotFoundError:
            continue
        prompts[role] = spec.system_prompt
        profiles[role] = spec.profile(overrides=profile_overrides)
    return CollaborativeTeam(
        client_for=client_for,
        prompts=prompts,
        profiles=profiles,
        budgets=budgets or LoopBudgets(),
        participants=participants,
        max_discussion_rounds=max_discussion_rounds,
        max_build_rounds=max_build_rounds,
        out_of_bounds=out_of_bounds,
        on_escalation=on_escalation,
        trajectory_dir=trajectory_dir,
        on_event=on_event,
    )
