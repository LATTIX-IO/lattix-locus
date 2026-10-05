"""LOCUS-339: the loop runner's pre-PR verifier suite, eval gate, skill proposals,
failure-pattern filing and history -- end to end with real git and the real
VerifiedLoop, fakes for Linear / GitHub / the model, a Gateway over the FakeEngine
and the local direct executor (the gate commands really run)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, ScriptedChatClient
from locus_runtime.harness.run_envelope import FileCheck
from locus_runtime.loop_runner.eval_gate import EvalGateResult, EvalRequest, default_eval_runner
from locus_runtime.loop_runner.feedback import FAILURE_MARKER
from locus_runtime.loop_runner.linear import LinearIssue, parse_claims
from locus_runtime.loop_runner.runner import LoopRunner
from locus_runtime.loop_runner.state import Ledger, LoopConfig, read_run_history
from locus_runtime.skills import SkillStore
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git, tool_response
from tests.harness.test_loop_runner import FakeGitHub, FakeTracker, _git

pytestmark = [requires_bash, requires_git]

CALC = "def add(a, b):\n    return a - b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
PERF_STUB = (
    "import json\n\nfrom locus_runtime.x import DELAY_MS\n\n"
    'print("LOCUS_PERF_RESULT " + json.dumps({"metrics": {"health_ms": DELAY_MS}}))\n'
)


class FilingTracker(FakeTracker):
    """FakeTracker plus the two Linear calls failure filing uses."""

    def __init__(self, issues: list[LinearIssue], **kw: Any) -> None:
        super().__init__(issues, **kw)
        self.created: list[dict[str, str]] = []
        self.searched: list[str] = []

    def find_issue_with_text(self, text: str) -> str | None:
        self.searched.append(text)
        for i, issue in enumerate(self.created):
            if text in issue["description"]:
                return f"LOC-{100 + i}"
        return None

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        project_slug: str = "",
        state_name: str = "",
        label_name: str = "",
    ) -> str:
        self.created.append(
            {
                "team_id": team_id,
                "title": title,
                "description": description,
                "slug": project_slug,
                "state_name": state_name,
                "label_name": label_name,
            }
        )
        return f"LOC-{100 + len(self.created) - 1}"


@pytest.fixture()
def py_repo(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    files = {
        "calc.py": CALC,
        "tests/test_calc.py": TEST_CALC,
        "locus_runtime/__init__.py": "",
        "locus_runtime/loop_runner/__init__.py": "",
        "locus_runtime/loop_runner/perf_budget.py": PERF_STUB,
        "locus_runtime/x.py": "DELAY_MS = 1.0\n",
    }
    for rel, text in files.items():
        target = seed / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))  # LF on every platform
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
    for name in ("LOCUS_LOOP_PERF_TOLERANCE", "LOCUS_LOOP_PERF_MIN_DELTA_MS"):
        monkeypatch.delenv(name, raising=False)


def _issue(**kw: Any) -> LinearIssue:
    base: dict[str, Any] = dict(
        id="iss-1",
        identifier="LOC-1",
        title="Fix add",
        description="add() should add.",
        priority=2,
        url="https://linear.app/x/issue/LOC-1",
        state="Todo",
        labels=("agent:eligible",),
        team_id="team-1",
    )
    base.update(kw)
    return LinearIssue(**base)


def _plan() -> ChatResponse:
    return tool_response(
        "p1",
        "update_plan",
        steps=["edit the code", "submit"],
        verification=[{"criterion_id": "edited", "method": "read the file"}],
    )


def _edit(path: str, old: str, new: str) -> ChatResponse:
    return tool_response(
        "e1", "str_replace_editor", command="str_replace", path=path, old_str=old, new_str=new
    )


def _submit() -> ChatResponse:
    return tool_response("s1", "submit", answer="done")


class Recorder:
    def __init__(self, result: EvalGateResult | None = None) -> None:
        self.requests: list[EvalRequest] = []
        self.result = result or EvalGateResult(
            "pass", resolve_rate=0.67, n_instances=3, model="nim/test"
        )

    def __call__(self, request: EvalRequest) -> EvalGateResult:
        self.requests.append(request)
        return self.result


def _runner(
    repo: Path,
    tmp_path: Path,
    tracker: FakeTracker,
    responses: list[Any],
    *,
    github: FakeGitHub | None = None,
    criterion: FileCheck | None = None,
    eval_runner: Any = None,
    **config: Any,
) -> LoopRunner:
    settings: dict[str, Any] = dict(
        gate_python=sys.executable,
        typecheck_roots=(),
        eval_gate="advisory",
        propose_skills=True,
        file_failure_issues=True,
    )
    settings.update(config)
    cfg = LoopConfig(repo_path=repo, home=tmp_path / "home", project_slug="slug", **settings)

    def chat_client_factory(session: Any, run_id: str, on_fallback: Any) -> ScriptedChatClient:
        return ScriptedChatClient(responses=list(responses))

    return LoopRunner(
        config=cfg,
        tracker=tracker,
        github=github or FakeGitHub(),
        gateway_factory=lambda _path: gw.Gateway(FakeEngine(), lambda _r: None),
        executor_factory=lambda root, session: LocalDirectExecutor(root, gateway_session=session),
        chat_client_factory=chat_client_factory,
        egress_hosts=lambda: ("integrate.api.nvidia.com",),
        extra_criteria=(criterion or FileCheck(id="edited", path="calc.py", contains="return a"),),
        loop_options={"provider_retry_backoff": 0},
        sleep=lambda _s: None,
        eval_runner=eval_runner or Recorder(),
        skill_store_factory=lambda: SkillStore(tmp_path / "skills"),
    )


# --------------------------------------------------------------------------- #
# Gate pass -> PR with gate + eval evidence, skill proposal, history
# --------------------------------------------------------------------------- #
def test_green_gates_open_a_pr_with_gate_and_eval_evidence(py_repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()])
    github = FakeGitHub()
    recorder = Recorder()
    responses = [_plan(), _edit("calc.py", "a - b", "a + b"), _submit()]
    result = _runner(
        py_repo, tmp_path, tracker, responses, github=github, eval_runner=recorder
    ).run_once()

    assert result.status == "done", result.detail
    body = github.opened[0]["body"]
    assert "## Quality gates" in body
    assert "`tests` **pass**" in body and "tests/test_calc.py" in body
    assert "`lint` **pass**" in body
    assert "`frontend` skipped" in body and "`policy` skipped" in body
    assert "## Eval gate" in body and "resolve rate 67.0%" in body
    home = tmp_path / "home"
    gate_file = json.loads(
        (home / "runs" / result.run_id / "quality-gate.json").read_text(encoding="utf-8")
    )
    assert gate_file["report"]["passed"] is True
    # eval: requested once, recorded, carried to the open PR for the merge guard
    assert len(recorder.requests) == 1 and recorder.requests[0].threshold == 0.30
    assert Ledger.load(home).open_prs[0]["eval_status"] == "pass"
    assert json.loads((home / "eval-history.jsonl").read_text(encoding="utf-8"))["status"] == "pass"
    # history line for the report
    (run,) = read_run_history(home)
    assert run["outcome"] == "done" and run["eval"]["status"] == "pass"
    assert run["gate_failures"] == [] and run["usage"]["steps"] >= 1
    # P24: a quarantined proposal with provenance, never trusted
    (skill,) = SkillStore(tmp_path / "skills").list()
    assert skill.state == "quarantined" and not skill.trusted
    md = SkillStore(tmp_path / "skills").read_file(skill.id, "SKILL.md").decode("utf-8")
    assert result.pr_url in md and "LOC-1" in md and result.run_id in md


# --------------------------------------------------------------------------- #
# Gate failure -> no PR, stopped, counted, gate failure recorded
# --------------------------------------------------------------------------- #
def test_failing_mapped_test_stops_the_run_before_any_pr(py_repo: Path, tmp_path: Path) -> None:
    tracker = FakeTracker([_issue()])
    github = FakeGitHub()
    recorder = Recorder()
    responses = [_plan(), _edit("calc.py", "a - b", "a * b"), _submit()]
    result = _runner(
        py_repo, tmp_path, tracker, responses, github=github, eval_runner=recorder
    ).run_once()

    assert result.status == "stopped" and "pre-PR verifier suite failed" in result.detail
    assert github.opened == [] and recorder.requests == []
    assert _git(tmp_path / "origin.git", "branch", "--list", "loop/*").strip() == ""
    comment = tracker.comments["iss-1"][-1]
    assert "quality_gate" in comment and "tests: fail" in comment
    home = tmp_path / "home"
    assert Ledger.load(home).failures("LOC-1") == 1
    (run,) = read_run_history(home)
    assert run["outcome"] == "stopped" and run["kind"] == "quality_gate"
    assert run["gate_failures"] == ["tests"]
    assert SkillStore(tmp_path / "skills").list() == []


# --------------------------------------------------------------------------- #
# Eval skipped honestly
# --------------------------------------------------------------------------- #
def test_unreachable_model_chain_skips_the_eval_and_still_opens_the_pr(
    py_repo: Path, tmp_path: Path
) -> None:
    github = FakeGitHub()
    responses = [_plan(), _edit("calc.py", "a - b", "a + b"), _submit()]
    runner = _runner(
        py_repo,
        tmp_path,
        FakeTracker([_issue()]),
        responses,
        github=github,
        eval_runner=default_eval_runner,
    )
    base_factory = runner.chat_client_factory

    def factory(session: Any, run_id: str, on_fallback: Any) -> Any:
        if run_id.endswith("-eval"):
            assert MODEL_CALL in session.capabilities.allowed_tools
            raise ConnectionError("no tier reachable")
        return base_factory(session, run_id, on_fallback)

    runner.chat_client_factory = factory
    result = runner.run_once()

    assert result.status == "done", result.detail
    assert "skipped: no model in the chain is reachable" in github.opened[0]["body"]
    assert "**pass**: resolve rate" not in github.opened[0]["body"]
    assert Ledger.load(tmp_path / "home").open_prs[0]["eval_status"] == "skipped"


MODEL_CALL = "llm_call"


def test_eval_gate_off_is_not_run(py_repo: Path, tmp_path: Path) -> None:
    github = FakeGitHub()
    recorder = Recorder()
    responses = [_plan(), _edit("calc.py", "a - b", "a + b"), _submit()]
    result = _runner(
        py_repo,
        tmp_path,
        FakeTracker([_issue()]),
        responses,
        github=github,
        eval_runner=recorder,
        eval_gate="off",
    ).run_once()
    assert result.status == "done" and recorder.requests == []
    assert "## Eval gate (synthetic DeepSWE on the model chain)\n- off" in github.opened[0]["body"]


# --------------------------------------------------------------------------- #
# Eval required -> D-22 merge holds unless the eval passed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("status", "merged"), [("skipped", False), ("pass", True)])
def test_required_eval_gate_holds_auto_merge(
    py_repo: Path, tmp_path: Path, status: str, merged: bool
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
            "eval_status": status,
        }
    )
    ledger.save()
    tracker = FakeTracker([_issue(state="In Review")])
    result = _runner(
        py_repo, tmp_path, tracker, [], github=github, auto_merge=True, eval_gate="required"
    ).run_once()
    assert (github.merged != []) is merged
    if not merged:
        assert result.merges[0]["action"] == "hold"
        assert "eval gate is required" in tracker.comments["iss-1"][-1]


# --------------------------------------------------------------------------- #
# Performance budget gate: first run records the baseline, a regression fails
# --------------------------------------------------------------------------- #
def test_perf_gate_records_a_baseline_then_fails_a_regression(
    py_repo: Path, tmp_path: Path
) -> None:
    first_issue = _issue(priority=1)
    second_issue = _issue(id="iss-2", identifier="LOC-2", url="u2", priority=3)
    tracker = FakeTracker([first_issue, second_issue])
    github = FakeGitHub()
    criterion = FileCheck(id="edited", path="locus_runtime/x.py", contains="DELAY_MS")
    home = tmp_path / "home"

    faster = [_plan(), _edit("locus_runtime/x.py", "1.0", "1.5"), _submit()]
    first = _runner(
        py_repo, tmp_path, tracker, faster, github=github, criterion=criterion
    ).run_once()
    assert first.status == "done", first.detail
    assert "baseline recorded" in github.opened[0]["body"]
    assert json.loads((home / "perf-baseline.json").read_text(encoding="utf-8"))["metrics"] == {
        "health_ms": 1.5
    }

    slower = [_plan(), _edit("locus_runtime/x.py", "1.0", "50.0"), _submit()]
    second = _runner(
        py_repo, tmp_path, tracker, slower, github=github, criterion=criterion
    ).run_once()
    assert second.status == "stopped" and "perf" in second.detail
    assert len(github.opened) == 1
    assert read_run_history(home)[-1]["gate_failures"] == ["perf"]
    statuses = [
        json.loads(line)["status"]
        for line in (home / "perf-history.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert statuses == ["pass", "fail"]


# --------------------------------------------------------------------------- #
# Failure patterns -> one Linear issue per new pattern
# --------------------------------------------------------------------------- #
def test_repeated_failure_files_exactly_one_issue(
    py_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_RUN_MAX_STEPS", "2")
    tracker = FilingTracker([_issue()])
    for _ in range(3):
        result = _runner(
            py_repo, tmp_path, tracker, [_plan(), "still thinking"], max_failures=10
        ).run_once()
        assert result.status == "stopped"

    assert len(tracker.created) == 1
    created = tracker.created[0]
    assert created["team_id"] == "team-1" and created["slug"] == "slug"
    assert created["state_name"] == "Triage" and created["label_name"] == ""
    assert "stopped/budget" in created["title"]
    assert f"{FAILURE_MARKER}: " in created["description"]
    assert "3 time(s)" not in created["description"]  # filed at the 2nd occurrence
    assert parse_claims([created["description"]]) == ()
    assert tracker.searched  # deduped against Linear before creating
    registry = json.loads((tmp_path / "home" / "failure-patterns.json").read_text(encoding="utf-8"))
    assert list(registry["patterns"].values())[0]["issue"] == "LOC-100"


def test_failure_filing_off_and_user_stops_never_file(py_repo: Path, tmp_path: Path) -> None:
    tracker = FilingTracker([_issue()])
    home = tmp_path / "home"

    def kill_then_plan(_messages: list[dict[str, Any]]) -> ChatResponse:
        home.mkdir(parents=True, exist_ok=True)
        (home / "DISABLED").write_text("stop", encoding="utf-8")
        return _plan()

    for _ in range(2):
        _runner(py_repo, tmp_path, tracker, [kill_then_plan], max_failures=10).run_once()
        (home / "DISABLED").unlink(missing_ok=True)
    assert tracker.created == []
