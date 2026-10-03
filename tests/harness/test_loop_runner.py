"""LOCUS-338: the self-improvement loop runner end to end, with fakes for Linear,
GitHub and the model; real git (a local bare ``origin``), a real ``Gateway`` over
the FakeEngine, and the real VerifiedLoop + workspace.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, ScriptedChatClient
from locus_runtime.harness.run_envelope import FileCheck
from locus_runtime.loop_runner.delivery import PullRequestInfo
from locus_runtime.loop_runner.linear import (
    CLAIM_MARKER,
    LinearIssue,
    marker,
    parse_claims,
)
from locus_runtime.loop_runner.merge_guard import ChangedFile, GateCheck
from locus_runtime.loop_runner.runner import LoopRunner
from locus_runtime.loop_runner.state import KILL_FILE, Ledger, LoopConfig, today_utc
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tool_response

pytestmark = [requires_bash, requires_git]
REPO_ROOT = Path(__file__).resolve().parents[2]


class Crash(BaseException):
    """Simulates the process dying mid-run (not an Exception: nothing catches it)."""


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeTracker:
    def __init__(
        self, issues: list[LinearIssue], states=("Todo", "In Progress", "In Review")
    ) -> None:
        self.issues = {i.id: i for i in issues}
        self.comments: dict[str, list[str]] = {i.id: [] for i in issues}
        self.states = {s.lower() for s in states}
        self.transitions: list[tuple[str, str]] = []
        self.labels_added: list[tuple[str, str]] = []
        self.links: list[tuple[str, str]] = []
        self.calls = 0
        self.on_comment: Any = None

    def _current(self, issue_id: str) -> LinearIssue:
        return replace(self.issues[issue_id], claims=parse_claims(self.comments[issue_id]))

    def list_candidate_issues(self, project_slug, *, active_states, label="agent:eligible"):
        self.calls += 1
        return [self._current(i) for i in self.issues]

    def get_issue(self, issue_id):
        self.calls += 1
        return self._current(issue_id)

    def has_state(self, issue_id, state_name):
        return state_name.lower() in self.states

    def transition(self, issue_id, state_name):
        self.calls += 1
        self.transitions.append((issue_id, state_name))
        self.issues[issue_id] = replace(self.issues[issue_id], state=state_name)

    def add_label(self, issue_id, label_name):
        self.calls += 1
        self.labels_added.append((issue_id, label_name))
        issue = self.issues[issue_id]
        self.issues[issue_id] = replace(issue, labels=(*issue.labels, label_name))

    def add_comment(self, issue_id, body):
        self.calls += 1
        self.comments[issue_id].append(body)
        if self.on_comment is not None:
            self.on_comment(issue_id, body)

    def attach_link(self, issue_id, url, title=""):
        self.calls += 1
        self.links.append((issue_id, url))


class FakeGitHub:
    def __init__(self) -> None:
        self.opened: list[dict[str, str]] = []
        self.merged: list[tuple[int, str, str]] = []
        self.checks: list[GateCheck] = [GateCheck("ci / test", "completed", "success")]
        self.files: list[ChangedFile] = [
            ChangedFile("fix.txt", "modified", patch="-broken\n+fixed\n")
        ]
        self.codeowners = (REPO_ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        self.state = "OPEN"

    def open_pr(self, branch, base, title, body):
        self.opened.append({"branch": branch, "base": base, "title": title, "body": body})
        return PullRequestInfo(7, "https://github.com/o/r/pull/7", "OPEN", "headsha", "basesha")

    def pr_info(self, number):
        return PullRequestInfo(
            number, f"https://github.com/o/r/pull/{number}", self.state, "headsha", "basesha"
        )

    def pr_checks(self, number):
        return list(self.checks)

    def pr_files(self, number):
        return list(self.files)

    def file_at(self, ref, path):
        return self.codeowners if path == ".github/CODEOWNERS" else None

    def merge_pr(self, number, method, head_sha):
        self.merged.append((number, method, head_sha))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "fix.txt").write_text("broken\n", encoding="utf-8")
    (seed / "README.md").write_text("demo\n", encoding="utf-8")
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "initial")
    _git(tmp_path, "clone", "-q", "--bare", str(seed), "origin.git")
    _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), "repo")
    clone = tmp_path / "repo"
    _git(clone, "config", "user.email", "loop@example.com")
    _git(clone, "config", "user.name", "Locus Loop")
    return clone


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LOCUS_LOOP_DISABLED", "LOCUS_RUN_MAX_STEPS", "LOCUS_AUTONOMY_TIER"):
        monkeypatch.delenv(name, raising=False)


def _issue(**kw: Any) -> LinearIssue:
    base = dict(
        id="iss-1",
        identifier="LOC-1",
        title="Make fix.txt say fixed",
        description="fix.txt should say fixed instead of broken.",
        priority=2,
        url="https://linear.app/x/issue/LOC-1",
        state="Todo",
        labels=("agent:eligible",),
    )
    base.update(kw)
    return LinearIssue(**base)


def _plan() -> ChatResponse:
    return tool_response(
        "p1",
        "update_plan",
        steps=["edit fix.txt", "submit"],
        verification=[{"criterion_id": "fixed", "method": "read fix.txt"}],
    )


def _fix() -> ChatResponse:
    return tool_response(
        "e1",
        "str_replace_editor",
        command="str_replace",
        path="fix.txt",
        old_str="broken",
        new_str="fixed",
    )


def _submit() -> ChatResponse:
    return tool_response("s1", "submit", answer="fix.txt now says fixed")


def _runner(
    repo: Path,
    tmp_path: Path,
    tracker: FakeTracker,
    responses: list[Any],
    *,
    github: FakeGitHub | None = None,
    engine: FakeEngine | None = None,
    clients: list[ScriptedChatClient] | None = None,
    **config: Any,
) -> LoopRunner:
    cfg = LoopConfig(repo_path=repo, home=tmp_path / "home", project_slug="3b160e533200", **config)
    audit: list[gw.GatewayAuditRecord] = []

    def chat_client_factory(session, run_id, on_fallback):
        client = ScriptedChatClient(responses=responses)
        if clients is not None:
            clients.append(client)
        return client

    return LoopRunner(
        config=cfg,
        tracker=tracker,
        github=github or FakeGitHub(),
        gateway_factory=lambda _path: gw.Gateway(engine or FakeEngine(), audit.append),
        executor_factory=lambda root, session: LocalDirectExecutor(root, gateway_session=session),
        chat_client_factory=chat_client_factory,
        egress_hosts=lambda: ("integrate.api.nvidia.com",),
        extra_criteria=(FileCheck(id="fixed", path="fix.txt", contains="fixed"),),
        loop_options={"provider_retry_backoff": 0},
        sleep=lambda _s: None,
    )


def _comments(tracker: FakeTracker) -> list[str]:
    return tracker.comments["iss-1"]


# --------------------------------------------------------------------------- #
# done -> branch, PR with evidence, In Review
# --------------------------------------------------------------------------- #
def test_done_run_opens_pr_with_evidence_and_moves_issue_to_in_review(
    repo: Path, tmp_path: Path
) -> None:
    tracker = FakeTracker([_issue()])
    github = FakeGitHub()
    runner = _runner(repo, tmp_path, tracker, [_plan(), _fix(), _submit()], github=github)

    result = runner.run_once()

    assert result.status == "done", result.detail
    assert result.pr_url == "https://github.com/o/r/pull/7"
    branch = "loop/loc-1-make-fix-txt-say-fixed"
    pr = github.opened[0]
    assert pr["branch"] == branch and pr["base"] == "main"
    body = pr["body"]
    for fragment in (
        "Resolves LOC-1",
        "## Envelope",
        "## Verifier results",
        "`fixed`",
        "## Judge verdicts",
        "Model fallbacks",
        "runs/" + result.run_id + "/trajectory.jsonl",
    ):
        assert fragment in body
    # pushed to origin with the fix
    assert _git(tmp_path / "origin.git", "show", f"{branch}:fix.txt").strip() == "fixed"
    # Linear: claim -> In Progress -> PR comment + link -> In Review
    assert tracker.transitions == [("iss-1", "In Progress"), ("iss-1", "In Review")]
    assert CLAIM_MARKER in _comments(tracker)[0] and result.run_id in _comments(tracker)[0]
    assert github.opened and "pull/7" in _comments(tracker)[-1]
    assert tracker.links == [("iss-1", "https://github.com/o/r/pull/7")]
    status = Ledger.load(tmp_path / "home")
    assert status.active is None and status.data["last_run"]["outcome"] == "done"
    assert status.open_prs[0]["number"] == 7
    assert not (tmp_path / "home" / "worktrees" / result.run_id).exists()
    assert (tmp_path / "home" / "runs" / result.run_id / "trajectory.jsonl").exists()


def test_run_session_carries_envelope_capabilities(repo: Path, tmp_path: Path) -> None:
    engine = FakeEngine()
    runner = _runner(
        repo, tmp_path, FakeTracker([_issue()]), [_plan(), _fix(), _submit()], engine=engine
    )
    assert runner.run_once().status == "done"
    agent_inputs = [payload for policy, payload in engine.calls if policy == "agent_policy"]
    assert agent_inputs, "agent actions must be authorized by the gateway"


# --------------------------------------------------------------------------- #
# blocked / stopped
# --------------------------------------------------------------------------- #
def test_blocked_run_comments_and_labels_for_human_review(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()], states=("Todo", "In Progress", "In Review", "Blocked"))
    blocker = tool_response(
        "b1",
        "report_blocker",
        kind="missing_access",
        blocker="needs the staging DB",
        unblock="grant read access",
    )
    result = _runner(repo, tmp_path, tracker, [_plan(), blocker]).run_once()

    assert result.status == "blocked"
    assert "blocked" in _comments(tracker)[-1] and "grant read access" in _comments(tracker)[-1]
    assert ("iss-1", "agent:human-review-required") in tracker.labels_added
    assert tracker.transitions[-1] == ("iss-1", "Blocked")


def test_stopped_run_comments_and_second_failure_needs_human_review(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_RUN_MAX_STEPS", "2")
    tracker = FakeTracker([_issue()])
    first = _runner(repo, tmp_path, tracker, [_plan(), "still thinking"]).run_once()

    assert first.status == "stopped" and "step budget" in first.detail
    assert "stopped" in _comments(tracker)[-1]
    assert tracker.transitions[-1] == ("iss-1", "Todo")
    assert tracker.labels_added == []

    second = _runner(repo, tmp_path, tracker, [_plan(), "still thinking"]).run_once()
    assert second.status == "stopped" and second.run_id != first.run_id
    assert tracker.labels_added == [("iss-1", "agent:human-review-required")]
    # third tick: the issue is now excluded
    assert _runner(repo, tmp_path, tracker, []).run_once().status == "idle"


# --------------------------------------------------------------------------- #
# selection / claim
# --------------------------------------------------------------------------- #
def test_excluded_and_ineligible_issues_are_never_claimed(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker(
        [
            _issue(id="a", identifier="LOC-2", labels=("agent:eligible", "agent:ineligible")),
            _issue(id="b", identifier="LOC-3", labels=()),
            _issue(id="c", identifier="LOC-4", state="Done"),
        ]
    )
    assert _runner(repo, tmp_path, tracker, []).run_once().status == "idle"
    assert tracker.transitions == [] and all(not c for c in tracker.comments.values())


def test_issue_claimed_by_another_live_run_is_skipped(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue(state="In Progress")])
    tracker.comments["iss-1"].append(
        marker(CLAIM_MARKER, "run-elsewhere", datetime.now(UTC) - timedelta(minutes=1))
    )
    assert _runner(repo, tmp_path, tracker, []).run_once().status == "idle"
    assert len(_comments(tracker)) == 1 and tracker.transitions == []


def test_claim_race_lost_to_an_earlier_claim_releases_and_does_not_run(
    repo: Path, tmp_path: Path
) -> None:
    tracker = FakeTracker([_issue()])

    def competitor(issue_id: str, body: str) -> None:
        if CLAIM_MARKER in body and "run-rival" not in body:
            tracker.comments[issue_id].append(
                marker(CLAIM_MARKER, "run-rival", datetime.now(UTC) - timedelta(seconds=30))
            )

    tracker.on_comment = competitor
    result = _runner(repo, tmp_path, tracker, []).run_once()
    assert result.status == "lost_claim"
    assert "released its duplicate claim" in _comments(tracker)[-1]
    assert tracker.transitions == []


def test_loop_is_single_concurrency(repo: Path, tmp_path: Path) -> None:
    runner = _runner(repo, tmp_path, FakeTracker([_issue()]), [])
    home = tmp_path / "home"
    home.mkdir()
    (home / "loop.lock").write_text(
        '{"owner": "other", "acquired_at": 9999999999}', encoding="utf-8"
    )
    assert runner.run_once().status == "busy"


def test_daily_quota(repo: Path, tmp_path: Path) -> None:
    runner = _runner(repo, tmp_path, FakeTracker([_issue()]), [], max_runs_per_day=1)
    ledger = Ledger.load(tmp_path / "home")
    ledger.count_run(today_utc())
    ledger.save()
    assert runner.run_once().status == "quota"


# --------------------------------------------------------------------------- #
# kill switch / fail closed
# --------------------------------------------------------------------------- #
def test_kill_switch_env_and_file(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = FakeTracker([_issue()])
    runner = _runner(repo, tmp_path, tracker, [])
    monkeypatch.setenv("LOCUS_LOOP_DISABLED", "1")
    assert runner.run_once().status == "disabled"
    monkeypatch.delenv("LOCUS_LOOP_DISABLED")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / KILL_FILE).write_text("x", encoding="utf-8")
    assert runner.run_once().status == "disabled"
    assert runner.serve(0, max_ticks=5).status == "disabled"
    assert tracker.calls == 0


def test_kill_switch_mid_run_stops_without_counting_a_failure(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()])
    home = tmp_path / "home"

    def plan_then_kill(_messages: list[dict[str, Any]]) -> ChatResponse:
        (home / KILL_FILE).write_text("stop", encoding="utf-8")
        return _plan()

    result = _runner(repo, tmp_path, tracker, [plan_then_kill, _fix(), _submit()]).run_once()
    assert result.status == "stopped"
    assert Ledger.load(home).failures("LOC-1") == 0
    assert tracker.labels_added == []


@pytest.mark.parametrize("engine", [FakeEngine(running=False), None])
def test_fails_closed_when_gateway_is_not_enforcing(
    repo: Path, tmp_path: Path, engine: FakeEngine | None
) -> None:
    tracker = FakeTracker([_issue()])
    runner = _runner(repo, tmp_path, tracker, [_plan(), _fix(), _submit()])
    runner.gateway_factory = (
        (lambda _p: gw.Gateway(engine, lambda _r: None)) if engine else (lambda _p: None)
    )
    result = runner.run_once()
    assert result.status == "refused"
    assert tracker.calls == 0
    assert gw.installed_gateway() is None or not isinstance(gw.installed_gateway(), gw.Gateway)


def test_process_gateway_is_restored_after_a_tick(repo: Path, tmp_path: Path) -> None:
    before = gw.installed_gateway()
    _runner(repo, tmp_path, FakeTracker([]), []).run_once()
    assert gw.installed_gateway() is before


# --------------------------------------------------------------------------- #
# resume after crash
# --------------------------------------------------------------------------- #
def test_resume_after_crash_continues_from_checkpoint(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()])

    def crash(_messages: list[dict[str, Any]]) -> ChatResponse:
        raise Crash()

    with pytest.raises(Crash):
        _runner(repo, tmp_path, tracker, [_plan(), crash]).run_once()

    ledger = Ledger.load(tmp_path / "home")
    active = ledger.active
    assert active is not None
    checkpoint = Path(active["run_dir"]) / "checkpoint.json"
    assert checkpoint.exists()
    assert not (tmp_path / "home" / "loop.lock").exists()

    clients: list[ScriptedChatClient] = []
    result = _runner(repo, tmp_path, tracker, [_fix(), _submit()], clients=clients).run_once()

    assert result.status == "done" and result.run_id == active["run_id"]
    # The resumed loop did not re-plan: its first model call already saw the recorded plan.
    first_call = clients[0].calls[0]
    assert any(
        m.get("role") == "tool" and "plan" in str(m.get("content")).lower() for m in first_call
    )
    assert len(clients[0].calls) == 2
    # Claimed once, counted once.
    assert sum(CLAIM_MARKER in c for c in _comments(tracker)) == 1
    assert Ledger.load(tmp_path / "home").runs_today(today_utc()) == 1


def test_resume_abandons_when_issue_was_excluded_meanwhile(repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()])

    def crash(_messages: list[dict[str, Any]]) -> ChatResponse:
        raise Crash()

    with pytest.raises(Crash):
        _runner(repo, tmp_path, tracker, [_plan(), crash]).run_once()
    tracker.add_label("iss-1", "agent:ineligible")
    result = _runner(repo, tmp_path, tracker, []).run_once()
    assert result.status == "abandoned"
    assert Ledger.load(tmp_path / "home").active is None


# --------------------------------------------------------------------------- #
# auto-merge reconcile (D-22)
# --------------------------------------------------------------------------- #
def _with_open_pr(tmp_path: Path) -> None:
    ledger = Ledger.load(tmp_path / "home")
    ledger.add_open_pr(
        {
            "number": 7,
            "url": "u",
            "issue_id": "iss-1",
            "issue_key": "LOC-1",
            "opened_at": "2026-01-01T00:00:00Z",
        }
    )
    ledger.save()


def test_reconcile_merges_green_unprotected_pr_pinned_to_head(repo: Path, tmp_path: Path) -> None:
    github = FakeGitHub()
    tracker = FakeTracker([_issue(state="In Review")])
    _with_open_pr(tmp_path)
    result = _runner(repo, tmp_path, tracker, [], github=github, auto_merge=True).run_once()
    assert result.merges == [{"number": 7, "action": "merged", "detail": ""}]
    assert github.merged == [(7, "squash", "headsha")]
    assert "Auto-merged" in _comments(tracker)[-1]


def test_reconcile_holds_protected_change_and_never_merges(repo: Path, tmp_path: Path) -> None:
    github = FakeGitHub()
    github.files = [ChangedFile("policies/agent_policy.rego", patch="-deny\n+allow\n")]
    tracker = FakeTracker([_issue(state="In Review")])
    _with_open_pr(tmp_path)
    result = _runner(repo, tmp_path, tracker, [], github=github, auto_merge=True).run_once()
    assert result.merges[0]["action"] == "hold"
    assert github.merged == []
    assert "needs principal review" in _comments(tracker)[-1]
    assert Ledger.load(tmp_path / "home").open_prs == []


def test_reconcile_waits_for_pending_checks_and_skips_when_auto_merge_off(
    repo: Path, tmp_path: Path
) -> None:
    github = FakeGitHub()
    github.checks = [GateCheck("ci / test", "in_progress", "")]
    _with_open_pr(tmp_path)
    tracker = FakeTracker([_issue(state="In Review")])
    result = _runner(repo, tmp_path, tracker, [], github=github, auto_merge=True).run_once()
    assert result.merges[0]["action"] == "waiting" and github.merged == []
    github.checks = [GateCheck("ci / test", "completed", "success")]
    off = _runner(repo, tmp_path, tracker, [], github=github, auto_merge=False).run_once()
    assert off.merges == [] and github.merged == []


# --------------------------------------------------------------------------- #
# computer use (LOCUS-346): the toolset comes from the envelope's tools
# --------------------------------------------------------------------------- #
def test_runner_builds_toolset_from_envelope_tools_and_releases_it(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.loop_runner import runner as runner_module

    built: list[dict[str, Any]] = []
    released: list[Any] = []
    real_build = runner_module.build_run_toolset

    def spy_build(**kwargs: Any) -> Any:
        built.append(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(runner_module, "build_run_toolset", spy_build)
    monkeypatch.setattr(runner_module, "release_run_toolset", released.append)
    tracker = FakeTracker([_issue()])
    runner = _runner(repo, tmp_path, tracker, [_plan(), _fix(), _submit()])

    result = runner.run_once()

    assert result.status == "done", result.detail
    assert len(built) == 1 and len(released) == 1
    assert "execute_bash" in built[0]["tools"]
    assert isinstance(built[0]["session"], gw.GatewaySession)
