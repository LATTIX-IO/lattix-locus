"""LOCUS-351: the loop runner's RSI scorecard gate (off / advisory / required).

End to end with real git and the real VerifiedLoop (the LOCUS-339 harness), a
fake scorecard runner standing in for the suite (the suite itself is covered by
``tests/evals/test_rsi_suite.py``), and the real variant archive and merge guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.loop_runner.report import load_report, render_text
from locus_runtime.loop_runner.runner import LoopRunner
from locus_runtime.loop_runner.scorecard_gate import (
    ScorecardRequest,
    ScorecardUnavailable,
    scorecard_merge_hold_reason,
)
from locus_runtime.loop_runner.state import Ledger, read_run_history
from locus_runtime.rsi.scorecard import SampleRecord, Scorecard, TamperCheck, build_scorecard
from locus_runtime.rsi.variants import VariantArchive
from tests.harness.conftest import requires_bash, requires_git
from tests.harness.test_loop_quality_gates import (
    _edit,
    _issue,
    _plan,
    _runner,
    _submit,
    py_repo,  # noqa: F401 - pytest fixture
)
from tests.harness.test_loop_runner import FakeGitHub, FakeTracker, _git

pytestmark = [requires_bash, requires_git]

MODEL = "ollama/gpt-oss:20b-ctx32k"
DIGESTS = {"dev": "d1", "heldout": "h1"}


def _card(sha: str, branch: str, heldout_passes: int, *, n: int = 20) -> Scorecard:
    records = [
        SampleRecord.model_validate(
            {
                "task_id": f"{split}-{i}",
                "split": split,
                "status": "pass" if i < passes else "fail",
                "tokens": 1000,
                "wall_seconds": 10.0,
                "model_coverage": 1.0,
                "injection": "resisted" if i == 0 else None,
            }
        )
        for split, passes in (("dev", 10), ("heldout", heldout_passes))
        for i in range(n)
    ]
    return build_scorecard(
        records,
        tamper=TamperCheck(verified_before=True, verified_after=True, manifest_digest="m"),
        gate_failures=[],
        meta={"git_sha": sha, "branch": branch, "model": MODEL, "split_digests": DIGESTS},
    )


class FakeScorecard:
    """Stands in for the suite: records the request and checks it sees the commit."""

    def __init__(self, heldout_passes: int = 19, *, fail: Exception | None = None) -> None:
        self.heldout_passes = heldout_passes
        self.fail = fail
        self.requests: list[ScorecardRequest] = []
        self.seen_code: list[str] = []

    def __call__(self, request: ScorecardRequest) -> Scorecard:
        self.requests.append(request)
        self.seen_code.append((request.candidate_checkout / "calc.py").read_text(encoding="utf-8"))
        if self.fail is not None:
            raise self.fail
        return _card(request.git_sha, request.branch, self.heldout_passes)


def _seed_baseline(home: Path, heldout_passes: int = 5) -> None:
    VariantArchive(home).record(_card("b" * 40, "main", heldout_passes), source="test")


def _loop(
    repo: Path,
    tmp_path: Path,
    scorecard: FakeScorecard,
    *,
    mode: str,
    github: FakeGitHub | None = None,
    **kw: Any,
) -> tuple[LoopRunner, FakeTracker, FakeGitHub]:
    tracker = FakeTracker([_issue()])
    gh = github or FakeGitHub()
    responses = [_plan(), _edit("calc.py", "a - b", "a + b"), _submit()]
    runner = _runner(repo, tmp_path, tracker, responses, github=gh, scorecard_mode=mode, **kw)
    runner.scorecard_runner = scorecard
    return runner, tracker, gh


def test_advisory_scorecard_on_the_commit_is_attached_archived_and_reported(
    py_repo: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    _seed_baseline(home)
    scorecard = FakeScorecard(heldout_passes=19)
    runner, _tracker, github = _loop(py_repo, tmp_path, scorecard, mode="advisory")
    result = runner.run_once()
    assert result.status == "done", result.detail

    (request,) = scorecard.requests
    # The candidate is the run's committed working copy; the exam comes from the runner's repo.
    assert "a + b" in scorecard.seen_code[0]
    assert request.repo_path == py_repo.resolve()
    assert request.candidate_checkout.is_relative_to(home / "worktrees")
    # Scored before the commit (the same tree); archived under the commit sha.
    assert request.git_sha == "" and request.branch.startswith("loop/")
    assert request.gate_failures == [] and request.splits == ("dev", "heldout")
    assert request.output_dir == home / "scorecards" / result.run_id
    pushed = _git(tmp_path / "origin.git", "rev-parse", request.branch).strip()

    body = github.opened[0]["body"]
    assert "## RSI scorecard" in body and "Decision: **promote**" in body
    assert "heldout: 19/20" in body and "pass_rate[heldout]" in body
    assert Ledger.load(home).open_prs[0]["scorecard_status"] == "promote"
    (run,) = read_run_history(home)
    assert run["scorecard"]["status"] == "promote" and run["scorecard"]["heldout_pass_rate"] == 0.95
    history = [
        json.loads(line)
        for line in (home / "scorecard-history.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert history[0]["status"] == "promote" and history[0]["issue"] == "LOC-1"
    entries = VariantArchive(home).entries()
    assert [e["branch"] for e in entries] == ["main", request.branch]
    # The variant is the pushed commit: what was scored is what was pushed.
    assert entries[-1]["decision"] == "promote" and entries[-1]["git_sha"] == pushed
    assert run["scorecard"]["git_sha"] == pushed

    report = load_report(home)
    assert report["scorecard"]["runs"] == 1
    assert report["scorecard"]["latest_heldout_pass_rate"] == 0.95
    assert "RSI scorecard: latest held-out 95.0%" in render_text(report)


def test_an_advisory_hold_does_not_block_the_pr(py_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    _seed_baseline(tmp_path / "home", heldout_passes=19)
    runner, _t, github = _loop(py_repo, tmp_path, FakeScorecard(heldout_passes=5), mode="advisory")
    assert runner.run_once().status == "done"
    body = github.opened[0]["body"]
    assert "Decision: **hold**" in body and "regression: pass_rate[heldout]" in body


def test_a_skipped_scorecard_does_not_block_the_pr(py_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    skip = FakeScorecard(fail=ScorecardUnavailable("model endpoint unreachable (ConnectError)"))
    runner, _t, github = _loop(py_repo, tmp_path, skip, mode="advisory")
    assert runner.run_once().status == "done"
    assert "- skipped: model endpoint unreachable" in github.opened[0]["body"]
    assert Ledger.load(tmp_path / "home").open_prs[0]["scorecard_status"] == "skipped"


def test_a_crashing_scorecard_is_an_error_never_a_promote(py_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    runner, _t, github = _loop(
        py_repo, tmp_path, FakeScorecard(fail=RuntimeError("boom")), mode="required"
    )
    assert runner.run_once().status == "done"
    assert "- error: the scorecard run failed (RuntimeError)" in github.opened[0]["body"]
    assert Ledger.load(tmp_path / "home").open_prs[0]["scorecard_status"] == "error"


def test_off_runs_nothing(py_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    scorecard = FakeScorecard()
    runner, _t, github = _loop(py_repo, tmp_path, scorecard, mode="off")
    assert runner.run_once().status == "done"
    assert scorecard.requests == []
    assert (
        "## RSI scorecard (LOCUS-351: candidate vs the base branch's baseline)\n- off"
        in github.opened[0]["body"]
    )


def test_variant_tags_are_local(py_repo: Path, tmp_path: Path) -> None:  # noqa: F811
    _seed_baseline(tmp_path / "home")
    scorecard = FakeScorecard()
    runner, _t, _gh = _loop(py_repo, tmp_path, scorecard, mode="advisory", tag_variants=True)
    assert runner.run_once().status == "done"
    sha = VariantArchive(tmp_path / "home").entries()[-1]["git_sha"]
    assert _git(py_repo, "rev-parse", f"refs/tags/variant/{sha[:12]}").strip() == sha
    # Never pushed: the remote has no variant tags.
    assert _git(tmp_path / "origin.git", "tag", "--list", "variant/*").strip() == ""


@pytest.mark.parametrize(
    ("mode", "status", "merged"),
    [
        ("required", "hold", False),
        ("required", "skipped", False),
        ("required", None, False),
        ("required", "promote", True),
        ("advisory", "hold", True),
        ("off", None, True),
    ],
)
def test_required_scorecard_holds_the_d22_auto_merge(
    py_repo: Path,  # noqa: F811
    tmp_path: Path,
    mode: str,
    status: str | None,
    merged: bool,
) -> None:
    github = FakeGitHub()
    ledger = Ledger.load(tmp_path / "home")
    ledger.add_open_pr(
        {
            "number": 7,
            "url": "u",
            "issue_id": "iss-1",
            "issue_key": "LOC-1",
            "opened_at": "2026-01-01T00:00:00Z",
            "eval_status": "pass",
            "scorecard_status": status,
        }
    )
    ledger.save()
    tracker = FakeTracker([_issue(state="In Review")])
    runner = _runner(
        py_repo, tmp_path, tracker, [], github=github, auto_merge=True, scorecard_mode=mode
    )
    result = runner.run_once()
    assert (github.merged != []) is merged
    if not merged:
        assert result.merges[0]["action"] == "hold"
        assert "RSI scorecard is required but did not promote" in tracker.comments["iss-1"][-1]


@pytest.mark.parametrize(
    ("mode", "status", "holds"),
    [
        ("off", None, False),
        ("advisory", "hold", False),
        ("required", "promote", False),
        ("required", "hold", True),
        ("required", "error", True),
    ],
)
def test_scorecard_merge_hold_reason(mode: str, status: str | None, holds: bool) -> None:
    assert bool(scorecard_merge_hold_reason(mode, status)) is holds
