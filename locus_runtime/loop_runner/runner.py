"""The self-improvement loop runner: Linear intake → verified run → PR (LOCUS-338).

Locus's native equivalent of Symphony (11 §8 "Symphony generalized"): a tracker
trigger (Linear issues labelled ``agent:eligible``) + the coding playbook (the
verified run loop, LOCUS-337) + the unified gated model client (NIM → Ollama,
D-21, LOCUS-336) + delivery (branch, PR, auto-merge guard, D-22).

One tick (:meth:`LoopRunner.run_once`)::

    kill switch? ── yes ─▶ disabled
    lock (max concurrent = 1, TTL) ── held ─▶ busy
    gateway posture (real Gateway, engine running) ── no ─▶ refused (fail closed)
    reconcile open loop PRs through the D-22 merge guard (if auto-merge is on)
    crashed run in the ledger? ── yes ─▶ resume it from its checkpoint
    runs today ≥ max ─▶ quota
    pick the highest-priority eligible issue ── none ─▶ idle
    claim (comment marker + In Progress; earliest live claim wins)
    worktree from <remote>/<base> → envelope → gateway session → VerifiedLoop
    done    ─▶ commit, push, PR with evidence, issue → In Review
    blocked ─▶ comment + agent:human-review-required, issue → Blocked | Todo
    stopped ─▶ comment, issue → Todo (second failure → agent:human-review-required)

Trust boundaries:

* Issue text is untrusted (P8). It sets the run's goal and done criteria only.
  The run's capabilities (workspace root, tools, executables, egress hosts)
  come from this runner's configuration, never from the issue.
* Every agent action and every model call goes through the run's gateway
  session (P6). Git, ``gh`` and Linear write-back are the runner's own
  delivery steps (see :mod:`locus_runtime.loop_runner.delivery`); they are not
  reachable by the agent.
* The loop never runs unless the gateway is enforcing (a real ``Gateway`` whose
  policy engine is running). It never merges unless the D-22 guard says so.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from locus_runtime.gateway import (
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
    host_of,
    install_gateway,
    installed_gateway,
    redact_text,
)
from locus_runtime.harness.executor import Executor, default_executor
from locus_runtime.harness.llm import ChatClient, GatedChatClient
from locus_runtime.harness.model_profiles import resolve_profile
from locus_runtime.harness.prompts import SWE_SYSTEM_PROMPT, build_task_prompt
from locus_runtime.harness.run_envelope import (
    CommandCheck,
    DoneCriterion,
    RunBudget,
    RunEnvelope,
    build_envelope,
    detect_repo_checks,
)
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.trajectory import TrajectoryRecorder
from locus_runtime.harness.verified_loop import (
    EndState,
    RunResult,
    VerifiedLoop,
    read_checkpoint,
)
from locus_runtime.harness.workspace import Workspace
from locus_runtime.loop_runner.delivery import (
    DeliveryError,
    GitOps,
    LoopGitHub,
    PullRequestInfo,
    branch_name,
)
from locus_runtime.loop_runner.linear import (
    CLAIM_MARKER,
    HUMAN_REVIEW_LABEL,
    RELEASE_MARKER,
    LinearError,
    LinearIssue,
    LinearNotConfigured,
    eligible_issues,
    live_claim,
    marker,
    with_retry,
)
from locus_runtime.loop_runner.merge_guard import evaluate_auto_merge
from locus_runtime.loop_runner.state import (
    Ledger,
    LoopBusy,
    LoopConfig,
    RunLock,
    kill_switch_reason,
    today_utc,
)
from locus_runtime.model_client import (
    EnvProviderSettings,
    FallbackEvent,
    GatewayModelGate,
    ModelRouter,
    build_client,
    default_agent_chain,
)

logger = logging.getLogger(__name__)

PRINCIPAL = "locus-self-improvement-loop"
ENGINE = "locus-loop"
#: The gateway operation the run's model calls use (agent_policy ``allowed_tools``).
MODEL_CALL_TOOL = "llm_call"
_EMPTY_CHECKS_GRACE_SECONDS = 1800.0
_COMMENT_MAX = 600


# --------------------------------------------------------------------------- #
# Seams
# --------------------------------------------------------------------------- #
class Tracker(Protocol):
    """What the runner needs from Linear (:class:`~.linear.LinearClient` satisfies it)."""

    def list_candidate_issues(
        self, project_slug: str, *, active_states: Any, label: str = ...
    ) -> list[LinearIssue]: ...
    def get_issue(self, issue_id: str) -> LinearIssue: ...
    def has_state(self, issue_id: str, state_name: str) -> bool: ...
    def transition(self, issue_id: str, state_name: str) -> None: ...
    def add_label(self, issue_id: str, label_name: str) -> None: ...
    def add_comment(self, issue_id: str, body: str) -> None: ...
    def attach_link(self, issue_id: str, url: str, title: str = "") -> None: ...


GatewayFactory = Callable[[Path], "Gateway | None"]
ExecutorFactory = Callable[[Path, GatewaySession], Executor]
ChatClientFactory = Callable[[GatewaySession, str, Callable[[FallbackEvent], None]], ChatClient]


class _JsonlAudit:
    """Gateway audit sink: one JSON line per decision in the run home."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, record: GatewayAuditRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.as_metadata(), sort_keys=True, default=str) + "\n")


def default_gateway_factory(audit_path: Path) -> Gateway | None:
    """The configured policy engine (OPA by default) behind a real Gateway.

    Returns ``None`` when no engine can be built; an engine that cannot start
    leaves ``gateway.healthy`` false. Either way the runner refuses to run.
    """
    from locus_runtime.policy_engine import build_policy_engine

    try:
        engine: Any = build_policy_engine()
    except Exception:  # noqa: BLE001 - no engine means no run (fail closed)
        logger.exception("loop.policy_engine_unavailable")
        return None
    start = getattr(engine, "start", None)
    if callable(start):
        try:
            start()
        except Exception:  # noqa: BLE001 - gateway.healthy stays false -> refused
            logger.exception("loop.policy_engine_start_failed")
    return Gateway(engine, _JsonlAudit(audit_path))


def default_executor_factory(root: Path, session: GatewaySession) -> Executor:
    return default_executor(root, gateway_session=session)


def default_chat_client_factory(
    session: GatewaySession, run_id: str, on_fallback: Callable[[FallbackEvent], None]
) -> ChatClient:
    """D-21: hosted NIM, then local Ollama; every call a gated ``model_call``."""
    gate = GatewayModelGate(session=session)
    router = ModelRouter(
        default_agent_chain(),
        client_factory=lambda tier: build_client(tier, gate=gate, run_id=run_id),
        on_fallback=on_fallback,
    )
    # GatedChatClient exposes provider/model as read-only properties (ChatClient-compatible).
    return cast(ChatClient, GatedChatClient(router))


def model_egress_hosts() -> tuple[str, ...]:
    """Egress hosts of the configured model tiers (the run may reach only these)."""
    settings = EnvProviderSettings()
    hosts: list[str] = []
    for tier in default_agent_chain(settings):
        host = host_of(settings.value(tier.provider, "base_url"))
        if host and host not in hosts:
            hosts.append(host)
    return tuple(hosts)


class _HostRepoReader:
    """Reads marker files (pyproject.toml, package.json, ...) for check detection.

    Detection only reads files inside the fresh worktree before the run starts;
    it never executes anything (see ``detect_repo_checks``)."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, path: str) -> Path | None:
        candidate = (self.root / path).resolve()
        return candidate if candidate.is_relative_to(self.root) else None

    def read_file(self, path: str) -> str | None:
        p = self._path(path)
        try:
            return p.read_text(encoding="utf-8") if p is not None and p.is_file() else None
        except (OSError, UnicodeDecodeError):
            return None

    def exists(self, path: str) -> bool:
        p = self._path(path)
        return p is not None and p.exists()


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class TickResult:
    status: str  # disabled|refused|busy|quota|idle|done|blocked|stopped|error|lost_claim|abandoned
    detail: str = ""
    issue: str = ""
    run_id: str = ""
    pr_url: str = ""
    merges: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(text: Any, limit: int = _COMMENT_MAX) -> str:
    """Redacted, bounded text for comments and PR bodies (never raw tool output)."""
    return redact_text(str(text or ""), limit=limit).replace("<!--", "&lt;!--")


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def pr_body(
    issue: LinearIssue,
    result: RunResult,
    *,
    fallbacks: list[FallbackEvent],
    model: str,
    trajectory_ref: str,
) -> str:
    env = result.envelope
    evidence = result.evidence or {}
    usage = result.usage
    lines = [
        f"Resolves {issue.identifier}: {issue.url}",
        "",
        f"Opened by the Locus self-improvement loop (run `{result.run_id}`). "
        "Merge only per D-22: every gate green and no protected path changed.",
        "",
        "## Envelope",
        f"- Goal: {_safe(env.goal, 300)}",
        f"- Autonomy tier: `{env.autonomy_tier}`",
        f"- Budget: steps {env.budget.max_steps}, seconds {env.budget.max_seconds:g}, "
        f"tokens {env.budget.max_tokens}, cost ${env.budget.max_cost_usd:g}, "
        f"actions {env.budget.max_actions}",
        "- Done criteria:",
        *[f"  - `{c.id}` ({c.kind}): {_safe(c.label(), 200)}" for c in env.done_criteria],
        "",
        "## Verifier results",
    ]
    for cmd in evidence.get("commands") or []:
        lines.append(
            f"- `{_safe(cmd.get('command'), 120)}` exit {cmd.get('exit_code')} "
            f"(expected {cmd.get('expected_exit_code')})"
        )
    for item in evidence.get("files") or []:
        lines.append(
            f"- file `{item.get('id')}`: {_safe(item.get('label'), 160)} -> {item.get('status')}"
        )
    lines += ["", "## Judge verdicts"]
    verdicts = evidence.get("judge") or []
    if verdicts:
        for verdict in verdicts:
            status = "pass" if verdict.get("pass") else "fail"
            lines.append(f"- `{verdict.get('id')}` {status}: {_safe(verdict.get('reason'), 300)}")
    else:
        lines.append("- (no free-text criteria)")
    lines += [
        "",
        "## Usage",
        f"- Model: `{model}`",
        f"- Steps {usage.steps}, actions {usage.actions}, model calls {usage.model_calls}, "
        f"judge calls {usage.judge_calls}",
        f"- Tokens {usage.tokens} (estimated: {usage.tokens_estimated}), "
        f"cost ${usage.cost_usd:.4f}, elapsed {usage.elapsed_seconds:.0f}s",
        f"- Verification attempts: {evidence.get('verification_attempts', len(result.verification))}",
        "- Model fallbacks:",
    ]
    if fallbacks:
        lines += [
            f"  - {e.from_provider}/{e.from_model} -> {e.to_provider}/{e.to_model} "
            f"({e.reason_code})"
            for e in fallbacks
        ]
    else:
        lines.append("  - none")
    lines += [
        "",
        "## Trajectory",
        f"- `{trajectory_ref}` (under `LOCUS_LOOP_HOME` on the runner host)",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
@dataclass
class LoopRunner:
    config: LoopConfig
    tracker: Tracker
    github: LoopGitHub
    gateway_factory: GatewayFactory = default_gateway_factory
    executor_factory: ExecutorFactory = default_executor_factory
    chat_client_factory: ChatClientFactory = default_chat_client_factory
    git: GitOps = field(default_factory=GitOps)
    egress_hosts: Callable[[], tuple[str, ...]] = model_egress_hosts
    extra_criteria: tuple[DoneCriterion, ...] = ()
    loop_options: dict[str, Any] = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], datetime] = _now

    # ------------------------------------------------------------------ tick
    def run_once(self) -> TickResult:
        home = self.config.home
        reason = kill_switch_reason(home)
        if reason:
            return TickResult("disabled", reason)
        if not self.config.project_slug:
            return TickResult("refused", "no Linear project slug (WORKFLOW.md tracker.provider)")
        lock = RunLock(home / "loop.lock", self.config.lock_ttl_seconds)
        try:
            lock.acquire(owner=f"tick-{secrets.token_hex(4)}")
        except LoopBusy as exc:
            return TickResult("busy", str(exc))
        previous_gateway = installed_gateway()
        gateway: Gateway | None = None
        try:
            gateway = self.gateway_factory(home / "gateway-audit.jsonl")
            refusal = self._posture_refusal(gateway)
            if refusal:
                return TickResult("refused", refusal)
            assert gateway is not None
            install_gateway(gateway)
            ledger = Ledger.load(home)
            merges = self._reconcile_prs(ledger) if self.config.auto_merge else []
            result = self._tick(ledger, gateway, lock)
            result.merges = merges
            return result
        except LinearNotConfigured as exc:
            return TickResult("refused", str(exc))
        except LinearError as exc:
            return TickResult("error", f"Linear: {_safe(exc, 300)}")
        finally:
            install_gateway(previous_gateway)
            if gateway is not None:
                try:
                    gateway.engine.close()
                except Exception:  # noqa: BLE001 - cleanup
                    logger.exception("loop.engine_close_error")
            lock.release()

    def serve(
        self, poll_interval: float | None = None, *, max_ticks: int | None = None
    ) -> TickResult:
        """Tick until the kill switch is set (or ``max_ticks``). Returns the last tick."""
        interval = poll_interval if poll_interval is not None else self.config.poll_interval_seconds
        ticks = 0
        last = TickResult("idle")
        while max_ticks is None or ticks < max_ticks:
            last = self.run_once()
            ticks += 1
            logger.info("loop.tick", extra={"status": last.status, "issue": last.issue})
            if last.status == "disabled":
                break
            self.sleep(max(1.0, float(interval)))
        return last

    @staticmethod
    def _posture_refusal(gateway: Gateway | None) -> str:
        if not isinstance(gateway, Gateway):
            return "no policy gateway could be built; the loop never runs ungated"
        if not gateway.healthy:
            return "the policy engine is not running; the gateway would deny every action"
        return ""

    def _tick(self, ledger: Ledger, gateway: Gateway, lock: RunLock) -> TickResult:
        active = ledger.active
        if active is not None:
            return self._resume(ledger, gateway, lock, active)
        today = today_utc(self.clock())
        if ledger.runs_today(today) >= self.config.max_runs_per_day:
            return TickResult(
                "quota",
                f"{ledger.runs_today(today)} runs today (max {self.config.max_runs_per_day})",
            )
        candidates = with_retry(
            lambda: self.tracker.list_candidate_issues(
                self.config.project_slug,
                active_states=self.config.active_states,
                label=self.config.required_label,
            ),
            sleep=self.sleep,
        )
        picked = eligible_issues(
            candidates,
            active_states=self.config.active_states,
            exclude_labels=self.config.exclude_labels,
            required_label=self.config.required_label,
            now=self.clock(),
            claim_ttl_seconds=self.config.lock_ttl_seconds,
        )
        if not picked:
            return TickResult("idle", "no eligible issues")
        issue = picked[0]
        # Issue keys come from Linear; keep only [a-z0-9-] since the run id names directories.
        key = re.sub(r"[^a-z0-9]+", "-", issue.identifier.lower()).strip("-")[:24] or "issue"
        run_id = f"loop-{key}-{self.clock().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
        claimed = self._claim(issue, run_id)
        if claimed is None:
            return TickResult("lost_claim", "another run claimed the issue first", issue.identifier)
        issue = claimed
        ledger.count_run(today)
        branch = branch_name(issue.identifier, issue.title)
        record = {
            "run_id": run_id,
            "issue_id": issue.id,
            "issue_key": issue.identifier,
            "title": issue.title,
            "url": issue.url,
            "branch": branch,
            "run_dir": str(self.config.runs_dir / run_id),
            "worktree": str(self.config.worktrees_dir / run_id),
            "started_at": _iso(self.clock()),
            "base_sha": "",
        }
        ledger.set_active(record)
        ledger.save()
        return self._run(ledger, gateway, lock, issue, record)

    # ------------------------------------------------------------------ claim
    def _claim(self, issue: LinearIssue, run_id: str) -> LinearIssue | None:
        """Idempotent claim: marker comment + In Progress; the earliest live claim wins."""
        fresh = with_retry(lambda: self.tracker.get_issue(issue.id), sleep=self.sleep)
        now = self.clock()
        if not eligible_issues(
            [fresh],
            active_states=self.config.active_states,
            exclude_labels=self.config.exclude_labels,
            required_label=self.config.required_label,
            now=now,
            claim_ttl_seconds=self.config.lock_ttl_seconds,
        ):
            return None
        self.tracker.add_comment(
            issue.id,
            f"Locus self-improvement loop claimed this issue (run `{run_id}`).\n\n"
            + marker(CLAIM_MARKER, run_id, now),
        )
        after = with_retry(lambda: self.tracker.get_issue(issue.id), sleep=self.sleep)
        released = {c.run_id for c in after.claims if c.kind == "release"}
        live = [
            c
            for c in after.claims
            if c.kind == "claim"
            and c.run_id not in released
            and (now - c.at).total_seconds() < self.config.lock_ttl_seconds
        ]
        winner = min(live, key=lambda c: (c.at, c.run_id)) if live else None
        if winner is None or winner.run_id != run_id:
            self.tracker.add_comment(
                issue.id,
                "Locus loop released its duplicate claim.\n\n"
                + marker(RELEASE_MARKER, run_id, now),
            )
            return None
        if after.state.strip().lower() != self.config.in_progress_state.lower():
            self.tracker.transition(issue.id, self.config.in_progress_state)
        return after

    # ------------------------------------------------------------------ resume
    def _resume(
        self, ledger: Ledger, gateway: Gateway, lock: RunLock, record: dict[str, Any]
    ) -> TickResult:
        issue = with_retry(
            lambda: self.tracker.get_issue(str(record["issue_id"])), sleep=self.sleep
        )
        claim = live_claim(issue, now=self.clock(), ttl_seconds=self.config.lock_ttl_seconds * 2)
        still_ours = claim is not None and claim.run_id == record["run_id"]
        excluded = issue.label_set & {s.lower() for s in self.config.exclude_labels}
        active = issue.state.strip().lower() in {s.lower() for s in self.config.active_states}
        if not still_ours or excluded or not active:
            # A human moved, relabelled or re-claimed the issue meanwhile: do not resume.
            self._cleanup_worktree(record)
            ledger.set_active(None)
            ledger.save()
            if still_ours:
                self.tracker.add_comment(
                    issue.id,
                    f"Locus loop abandoned run `{record['run_id']}` after a restart: the issue "
                    "is no longer eligible.\n\n"
                    + marker(RELEASE_MARKER, record["run_id"], self.clock()),
                )
            return TickResult(
                "abandoned", "issue no longer eligible", issue.identifier, record["run_id"]
            )
        return self._run(ledger, gateway, lock, issue, record)

    # ------------------------------------------------------------------ run
    def _envelope(self, issue: LinearIssue, worktree: Path) -> RunEnvelope:
        overrides = dict(self.config.check_commands)
        checks: list[DoneCriterion] = []
        for check in detect_repo_checks(_HostRepoReader(worktree)):
            command = overrides.pop(check.id, "")
            checks.append(replace(check, command=command) if command else check)
        # An override for a check the repo does not declare adds it (never removes one).
        for check_id, command in overrides.items():
            checks.append(
                CommandCheck(id=check_id, command=command, description=f"{check_id} passes")
            )
        return build_envelope(
            issue.task_text(),
            workspace_root=str(worktree),
            extra_criteria=(*checks, *self.extra_criteria),
            budget=RunBudget.from_env(),
            egress_hosts=self.egress_hosts(),
        )

    def _run(
        self,
        ledger: Ledger,
        gateway: Gateway,
        lock: RunLock,
        issue: LinearIssue,
        record: dict[str, Any],
    ) -> TickResult:
        run_dir = Path(record["run_dir"])
        worktree = Path(record["worktree"])
        run_dir.mkdir(parents=True, exist_ok=True)
        fallbacks: list[FallbackEvent] = []
        try:
            if not worktree.exists():
                self.git.fetch(self.config.repo_path, self.config.remote, self.config.base_branch)
                record["base_sha"] = self.git.add_worktree(
                    self.config.repo_path,
                    worktree,
                    str(record["branch"]),
                    f"{self.config.remote}/{self.config.base_branch}",
                    remote=self.config.remote,
                )
                ledger.set_active(record)
                ledger.save()
            result, model = self._execute(gateway, lock, issue, record, fallbacks)
            return self._deliver(ledger, issue, record, result, fallbacks, model)
        except (DeliveryError, LinearError, OSError, ValueError, RuntimeError) as exc:
            return self._fail(ledger, issue, record, exc)

    def _execute(
        self,
        gateway: Gateway,
        lock: RunLock,
        issue: LinearIssue,
        record: dict[str, Any],
        fallbacks: list[FallbackEvent],
    ) -> tuple[RunResult, str]:
        run_id = str(record["run_id"])
        run_dir = Path(record["run_dir"])
        worktree = Path(record["worktree"])
        checkpoint = run_dir / "checkpoint.json"
        resuming = checkpoint.exists()
        envelope = (
            RunEnvelope.from_dict(read_checkpoint(checkpoint)["envelope"])
            if resuming
            else self._envelope(issue, worktree)
        )
        caps = envelope.gateway_capabilities()
        caps = replace(caps, allowed_tools=caps.allowed_tools | {MODEL_CALL_TOOL})
        session = gateway.open_session(
            run_id=run_id, principal=PRINCIPAL, engine=ENGINE, capabilities=caps
        )
        try:
            executor = self.executor_factory(worktree, session)
            client = self.chat_client_factory(session, run_id, fallbacks.append)
            profile = resolve_profile(
                str(getattr(client, "provider", "")), str(getattr(client, "model", ""))
            )
            workspace = Workspace(
                run_id=run_id, executor=executor, base_ref=str(record.get("base_sha") or "HEAD")
            )
            toolset = CodingToolset(workspace=workspace, edit_format=profile.edit_format)
            owner = f"run-{run_id}"

            def on_event(kind: str, data: dict[str, Any]) -> None:
                if kind == "model_step":
                    lock.refresh(owner)

            options: dict[str, Any] = {
                "on_event": on_event,
                "should_stop": lambda: bool(kill_switch_reason(self.config.home)),
                **self.loop_options,
            }
            if resuming:
                loop = VerifiedLoop.resume(
                    checkpoint, client=client, toolset=toolset, profile=profile, **options
                )
            else:
                loop = VerifiedLoop(
                    client=client,
                    toolset=toolset,
                    profile=profile,
                    envelope=envelope,
                    system_prompt=SWE_SYSTEM_PROMPT,
                    user_prompt=build_task_prompt(issue.task_text()),
                    run_id=run_id,
                    plan_mode="required",
                    checkpoint_path=checkpoint,
                    recorder=TrajectoryRecorder(
                        run_id=run_id, file_path=run_dir / "trajectory.jsonl"
                    ),
                    agent_id="locus-loop",
                    task_meta={"issue": issue.identifier, "url": issue.url, "run_id": run_id},
                    **options,
                )
            result = loop.run()
            model = f"{getattr(client, 'provider', '')}/{getattr(client, 'model', '')}"
            return result, model
        finally:
            session.close()

    # ------------------------------------------------------------------ outcomes
    def _deliver(
        self,
        ledger: Ledger,
        issue: LinearIssue,
        record: dict[str, Any],
        result: RunResult,
        fallbacks: list[FallbackEvent],
        model: str,
    ) -> TickResult:
        run_id = str(record["run_id"])
        worktree = Path(record["worktree"])
        if result.end_state is EndState.DONE:
            if not self.git.has_changes(worktree):
                return self._blocked(
                    ledger,
                    issue,
                    record,
                    "no_changes",
                    "the done criteria passed but the run produced no change",
                    "confirm whether the issue is already resolved, then close or re-scope it",
                )
            branch = str(record["branch"])
            title = f"{issue.identifier}: {issue.title}"[:120]
            self.git.commit_all(
                worktree,
                f"chore(loop): {title}\n\nResolves {issue.identifier}\nLocus-Run: {run_id}",
            )
            self._retry(lambda: self.git.push(worktree, self.config.remote, branch))
            body = pr_body(
                issue,
                result,
                fallbacks=fallbacks,
                model=model,
                trajectory_ref=f"runs/{run_id}/trajectory.jsonl",
            )
            pr = self._retry(
                lambda: self.github.open_pr(branch, self.config.base_branch, title, body)
            )
            self._after_pr(ledger, issue, record, pr)
            return TickResult("done", "PR opened", issue.identifier, run_id, pr.url)
        if result.end_state is EndState.BLOCKED and result.blocker is not None:
            b = result.blocker
            return self._blocked(ledger, issue, record, b.kind, b.detail, b.unblock)
        stop = result.stop
        return self._stopped(
            ledger,
            issue,
            record,
            stop.kind if stop else "unknown",
            stop.detail if stop else "stopped",
        )

    def _after_pr(
        self, ledger: Ledger, issue: LinearIssue, record: dict[str, Any], pr: PullRequestInfo
    ) -> None:
        run_id = str(record["run_id"])
        self.tracker.add_comment(
            issue.id,
            f"Locus loop run `{run_id}` is done: every done criterion verified.\n\n"
            f"PR: {pr.url}\n\n" + marker(RELEASE_MARKER, run_id, self.clock()),
        )
        self.tracker.attach_link(issue.id, pr.url, f"PR #{pr.number}")
        if self.tracker.has_state(issue.id, self.config.review_state):
            self.tracker.transition(issue.id, self.config.review_state)
        ledger.clear_failures(issue.identifier)
        ledger.add_open_pr(
            {
                "number": pr.number,
                "url": pr.url,
                "issue_id": issue.id,
                "issue_key": issue.identifier,
                "opened_at": _iso(self.clock()),
            }
        )
        self._finish(ledger, record, "done", pr_url=pr.url)

    def _blocked(
        self,
        ledger: Ledger,
        issue: LinearIssue,
        record: dict[str, Any],
        kind: str,
        detail: str,
        unblock: str,
    ) -> TickResult:
        run_id = str(record["run_id"])
        ledger.record_failure(issue.identifier)
        self.tracker.add_comment(
            issue.id,
            f"Locus loop run `{run_id}` is **blocked** ({_safe(kind, 40)}).\n\n"
            f"Blocker: {_safe(detail)}\n\nWhat would unblock it: {_safe(unblock)}\n\n"
            + marker(RELEASE_MARKER, run_id, self.clock()),
        )
        self.tracker.add_label(issue.id, HUMAN_REVIEW_LABEL)
        target = (
            self.config.blocked_state
            if self.tracker.has_state(issue.id, self.config.blocked_state)
            else self.config.todo_state
        )
        self.tracker.transition(issue.id, target)
        self._finish(ledger, record, "blocked", detail=f"{kind}: {detail}")
        return TickResult("blocked", _safe(detail, 300), issue.identifier, run_id)

    def _stopped(
        self, ledger: Ledger, issue: LinearIssue, record: dict[str, Any], kind: str, detail: str
    ) -> TickResult:
        run_id = str(record["run_id"])
        # A kill-switch / user stop is not the issue's failure; budget and policy stops are.
        failures = ledger.failures(issue.identifier)
        if kind != "user":
            failures = ledger.record_failure(issue.identifier)
        self.tracker.add_comment(
            issue.id,
            f"Locus loop run `{run_id}` **stopped** ({_safe(kind, 40)}): {_safe(detail)}\n\n"
            + marker(RELEASE_MARKER, run_id, self.clock()),
        )
        if failures >= self.config.max_failures:
            self.tracker.add_label(issue.id, HUMAN_REVIEW_LABEL)
        self.tracker.transition(issue.id, self.config.todo_state)
        self._finish(ledger, record, "stopped", detail=f"{kind}: {detail}")
        return TickResult("stopped", _safe(detail, 300), issue.identifier, run_id)

    def _fail(
        self, ledger: Ledger, issue: LinearIssue, record: dict[str, Any], exc: BaseException
    ) -> TickResult:
        run_id = str(record["run_id"])
        logger.exception("loop.run_error", extra={"run_id": run_id})
        failures = ledger.record_failure(issue.identifier)
        detail = f"{type(exc).__name__}: {_safe(exc, 300)}"
        try:
            self.tracker.add_comment(
                issue.id,
                f"Locus loop run `{run_id}` failed: {detail}\n\n"
                + marker(RELEASE_MARKER, run_id, self.clock()),
            )
            if failures >= self.config.max_failures:
                self.tracker.add_label(issue.id, HUMAN_REVIEW_LABEL)
            self.tracker.transition(issue.id, self.config.todo_state)
        except LinearError:
            logger.exception("loop.report_failure_error", extra={"run_id": run_id})
        self._finish(ledger, record, "error", detail=detail)
        return TickResult("error", detail, issue.identifier, run_id)

    def _finish(
        self,
        ledger: Ledger,
        record: dict[str, Any],
        outcome: str,
        *,
        detail: str = "",
        pr_url: str = "",
    ) -> None:
        self._save_diff(record)
        self._cleanup_worktree(record)
        ledger.set_active(None)
        ledger.set_last(
            {
                "run_id": record["run_id"],
                "issue": record["issue_key"],
                "outcome": outcome,
                "detail": _safe(detail, 300),
                "pr_url": pr_url,
                "finished_at": _iso(self.clock()),
            }
        )
        ledger.save()

    def _save_diff(self, record: dict[str, Any]) -> None:
        worktree = Path(record["worktree"])
        if not worktree.exists():
            return
        try:
            diff = self.git.run(worktree, "diff", "HEAD")
        except DeliveryError:
            return
        if diff.strip():
            (Path(record["run_dir"]) / "final.diff").write_text(diff, encoding="utf-8")

    def _cleanup_worktree(self, record: dict[str, Any]) -> None:
        worktree = Path(record["worktree"])
        if worktree.exists():
            self.git.remove_worktree(self.config.repo_path, worktree)

    def _retry(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(1, 4):
            try:
                return operation()
            except DeliveryError as exc:
                if attempt == 3:
                    raise
                logger.warning(
                    "loop.delivery_retry", extra={"attempt": attempt, "error": str(exc)[:200]}
                )
                self.sleep(2.0**attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ merges
    def _reconcile_prs(self, ledger: Ledger) -> list[dict[str, Any]]:
        """Apply the D-22 guard to every open loop PR; merge only on ``merge``."""
        outcomes: list[dict[str, Any]] = []
        for pr in ledger.open_prs:
            number = int(pr["number"])
            try:
                outcomes.append(self._reconcile_one(ledger, pr, number))
            except (DeliveryError, LinearError) as exc:
                outcomes.append({"number": number, "action": "error", "detail": _safe(exc, 200)})
        ledger.save()
        return outcomes

    def _reconcile_one(self, ledger: Ledger, pr: dict[str, Any], number: int) -> dict[str, Any]:
        info = self.github.pr_info(number)
        if info.state.upper() != "OPEN":
            ledger.remove_open_pr(number)
            return {"number": number, "action": "closed", "detail": info.state}
        checks = self.github.pr_checks(number)
        if any(c.status.lower() != "completed" for c in checks):
            return {"number": number, "action": "waiting", "detail": "checks pending"}
        if not checks:
            opened = datetime.strptime(str(pr.get("opened_at")), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
            if (self.clock() - opened).total_seconds() < _EMPTY_CHECKS_GRACE_SECONDS:
                return {"number": number, "action": "waiting", "detail": "no checks yet"}
        files = self.github.pr_files(number)
        versions: dict[str, tuple[str | None, str | None]] = {}
        for item in files:
            name = item.path.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if name in {"pyproject.toml", "makefile"}:
                base_path = item.previous_path or item.path
                versions[item.path] = (
                    self.github.file_at(info.base_sha, base_path)
                    if item.status != "added"
                    else None,
                    self.github.file_at(info.head_sha, item.path)
                    if item.status not in {"removed", "deleted"}
                    else None,
                )
        decision = evaluate_auto_merge(
            files,
            checks,
            codeowners_text=self.github.file_at(info.base_sha, ".github/CODEOWNERS"),
            required_checks=self.config.required_checks,
            config_versions=versions,
        )
        issue_id = str(pr.get("issue_id") or "")
        if decision.merge:
            # The head must still be the one evaluated (merge_pr also pins it).
            if self.github.pr_info(number).head_sha != info.head_sha:
                return {"number": number, "action": "waiting", "detail": "head changed"}
            self.github.merge_pr(number, self.config.merge_method, info.head_sha)
            ledger.remove_open_pr(number)
            if issue_id:
                self.tracker.add_comment(
                    issue_id,
                    f"Auto-merged {info.url} per D-22: every gate green, no protected path changed.",
                )
            return {"number": number, "action": "merged", "detail": ""}
        ledger.remove_open_pr(number)
        if issue_id:
            reasons = "\n".join(f"- {_safe(r, 200)}" for r in decision.reasons[:20])
            self.tracker.add_comment(
                issue_id,
                f"{info.url} needs principal review (not auto-merged, D-22):\n\n{reasons}",
            )
        return {"number": number, "action": "hold", "detail": "; ".join(decision.reasons)[:500]}
