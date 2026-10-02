"""End-to-end SWE agent test on a synthetic task (plumbing mode).

Proves the full scaffold works on this machine without a GPU: real git repo,
real file edits, real test execution, real grading. The model is a scripted
client emitting genuine tool calls (explore -> view -> edit -> test -> submit).

The live gpt-oss-20b / DeepSWE path reuses this exact SweAgent against a vLLM
endpoint and SWE-bench Docker instances — see apps/evals.
"""

from __future__ import annotations

from pathlib import Path


from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, ScriptedChatClient
from locus_runtime.harness.loop import LoopBudgets, LoopOutcome
from locus_runtime.harness.model_profiles import resolve_profile
from locus_runtime.harness.swe_agent import SweAgent, SweTask

from tests.harness.conftest import git_init, requires_bash, requires_git, tool_response


def _shell_python() -> str:
    """Find a python interpreter the *shell* can execute (git-bash on Windows
    cannot run the Windows python.exe by path, but `python3` works)."""
    import subprocess

    for cand in ("python3", "python"):
        try:
            r = subprocess.run(["bash", "-c", f"{cand} --version"], capture_output=True, text=True)
            if r.returncode == 0:
                return cand
        except OSError:
            continue
    return "python3"


PYTHON = _shell_python()
# Self-contained assertion runner — no pytest dependency in the target
# interpreter, and self-locating (sys.path from __file__, not cwd) so it works
# regardless of which shell/python resolves the command.
TEST_CMD = f"{PYTHON} runtests.py"
RUNTESTS = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
    "from mathlib.core import add\n"
    "assert add(2, 3) == 5, 'add is broken'\n"
    "print('OK')\n"
)

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED_LINE_OLD = "    return a - b"
FIXED_LINE_NEW = "    return a + b"


def _make_repo(root: Path) -> None:
    (root / "mathlib").mkdir()
    (root / "mathlib" / "__init__.py").write_text("")
    (root / "mathlib" / "core.py").write_text(BUGGY)
    (root / "runtests.py").write_text(RUNTESTS)
    git_init(root)


@requires_bash
@requires_git
def test_swe_agent_fixes_synthetic_bug_and_grades(tmp_path):
    _make_repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)

    # Sanity: the bug really fails before the agent runs, and it fails because
    # of the assertion (not a missing interpreter masking the bug).
    pre = executor.run_shell(TEST_CMD, timeout=120)
    assert "not found" not in pre.stderr and "No such file" not in pre.stderr, (
        f"interpreter must be runnable in the workspace: {pre.combined()}"
    )
    assert pre.exit_code != 0, "synthetic test should fail before the fix"

    client = ScriptedChatClient(
        provider="scripted",
        model="scripted-fixer",
        responses=[
            tool_response("c1", "execute_bash", command="ls -R mathlib"),
            tool_response("c2", "str_replace_editor", command="view", path="mathlib/core.py"),
            tool_response(
                "c3",
                "str_replace_editor",
                command="str_replace",
                path="mathlib/core.py",
                old_str=FIXED_LINE_OLD,
                new_str=FIXED_LINE_NEW,
            ),
            tool_response("c4", "run_tests"),
            tool_response("c5", "submit", answer="Fixed sign error in add()."),
        ],
    )

    agent = SweAgent(
        client=client,
        profile=resolve_profile("scripted", "scripted-fixer", profile_id="local-32b-class"),
        budgets=LoopBudgets(max_steps=10, max_seconds=300),
        test_timeout=120,
    )
    task = SweTask(
        instance_id="synthetic-add",
        problem_statement="add(2, 3) returns -1 instead of 5; fix the addition.",
        executor=executor,
        test_command=TEST_CMD,
    )
    result = agent.solve(task)

    assert result.outcome == LoopOutcome.SUBMITTED
    assert result.has_patch, "submission must carry a unified diff"
    assert "return a + b" in result.patch
    assert result.telemetry["edits_applied"] >= 1

    # GRADE: apply happened in-place; tests must now pass.
    post = executor.run_shell(TEST_CMD, timeout=120)
    assert post.exit_code == 0, f"tests should pass after the fix:\n{post.combined()}"


@requires_bash
@requires_git
def test_budget_exhaustion_yields_zero_credit(tmp_path):
    _make_repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)
    # A client that never submits — just keeps running cheap bash.
    client = ScriptedChatClient(
        responses=[
            tool_response(f"c{i}", "execute_bash", command="echo working") for i in range(20)
        ],
    )
    agent = SweAgent(
        client=client,
        profile=resolve_profile("scripted", "x", profile_id="local-32b-class"),
        budgets=LoopBudgets(max_steps=4, max_seconds=300),
    )
    result = agent.solve(
        SweTask(
            instance_id="never-submits",
            problem_statement="do something",
            executor=executor,
            test_command=TEST_CMD,
        )
    )
    assert result.outcome == LoopOutcome.BUDGET_EXHAUSTED
    assert result.patch == "", "no submission => no graded patch (submit-or-zero)"


@requires_bash
@requires_git
def test_malformed_tool_call_triggers_reask(tmp_path):
    _make_repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)
    # First a malformed call (bad JSON), then a valid submit.
    client = ScriptedChatClient(
        responses=[
            ChatResponse(
                tool_calls=[
                    __import__("locus_runtime.harness.llm", fromlist=["ToolCall"]).ToolCall(
                        id="bad", name="execute_bash", arguments="{not valid json"
                    )
                ]
            ),
            tool_response("ok", "submit", answer="done"),
        ]
    )
    agent = SweAgent(
        client=client,
        profile=resolve_profile("scripted", "x", profile_id="local-32b-class"),
        budgets=LoopBudgets(max_steps=10),
    )
    result = agent.solve(
        SweTask(
            instance_id="reask",
            problem_statement="x",
            executor=executor,
            test_command=TEST_CMD,
        )
    )
    assert result.telemetry["reasks"] == 1
    assert result.telemetry["tool_calls_malformed"] == 1
    assert result.outcome == LoopOutcome.SUBMITTED


@requires_bash
@requires_git
def test_trajectory_is_written(tmp_path):
    _make_repo(tmp_path)
    executor = LocalDirectExecutor(tmp_path)
    traj_dir = tmp_path / "traj"
    client = ScriptedChatClient(responses=[tool_response("s", "submit", answer="trivial")])
    agent = SweAgent(
        client=client,
        profile=resolve_profile("scripted", "x", profile_id="local-32b-class"),
        trajectory_dir=traj_dir,
    )
    result = agent.solve(
        SweTask(
            instance_id="traj-test",
            problem_statement="x",
            executor=executor,
            test_command=TEST_CMD,
        )
    )
    path = traj_dir / "traj-test.jsonl"
    assert path.exists()
    # replayable: messages reconstruct, ends with an outcome line
    records = result.trajectory.parse(path.read_text(encoding="utf-8"))
    assert records[0]["kind"] == "meta"
    assert records[-1]["kind"] == "outcome"
    assert records[-1]["status"] == "submitted"
