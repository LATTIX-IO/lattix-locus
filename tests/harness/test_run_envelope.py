"""LOCUS-337: run envelope schema and the builder (task text + repository defaults)."""

from __future__ import annotations

import json

import pytest

from locus_runtime.harness.run_envelope import (
    AcceptanceCriterion,
    CommandCheck,
    FileCheck,
    RunBudget,
    RunEnvelope,
    build_envelope,
    detect_repo_checks,
    parse_task_criteria,
)


class _Repo:
    """A RepoReader over an in-memory file map."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read_file(self, path: str) -> str | None:
        return self.files.get(path)

    def exists(self, path: str) -> bool:
        return path in self.files


ISSUE = """Fix the add() sign bug

add(2, 3) returns -1.

## Acceptance criteria
- `python -m pytest -q tests/test_math.py` passes
- add() is documented with a docstring
- [ ] No other module changes

## Notes
- not a criterion
"""


def test_detects_python_and_npm_defaults() -> None:
    repo = _Repo(
        {
            "pyproject.toml": "[tool.pytest.ini_options]\n[tool.ruff]\nline-length = 100\n",
            "mypy.ini": "",
            "package.json": json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."}}),
        }
    )
    checks = detect_repo_checks(repo)
    assert [(c.id, c.command) for c in checks] == [
        ("tests", "python -m pytest -q"),
        ("lint", "ruff check ."),
        ("typecheck", "mypy ."),
        ("npm-tests", "npm test"),
    ]


def test_npm_placeholder_test_script_is_not_a_check() -> None:
    repo = _Repo(
        {
            "package.json": json.dumps(
                {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}
            )
        }
    )
    assert detect_repo_checks(repo) == []


def test_task_criteria_become_command_and_judged_checks() -> None:
    criteria = parse_task_criteria(ISSUE)
    assert criteria == [
        CommandCheck(
            id="task-cmd-1",
            command="python -m pytest -q tests/test_math.py",
            description="`python -m pytest -q tests/test_math.py` passes",
        ),
        AcceptanceCriterion(id="ac-1", text="add() is documented with a docstring"),
        AcceptanceCriterion(id="ac-2", text="No other module changes"),
    ]


def test_build_envelope_merges_repo_defaults_task_criteria_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCUS_RUN_MAX_ACTIONS", "42")
    monkeypatch.setenv("LOCUS_AUTONOMY_TIER", "supervised")
    repo = _Repo({"pyproject.toml": "[tool.pytest.ini_options]\n[tool.ruff]\n"})
    env = build_envelope(ISSUE, repo=repo, workspace_root="/ws")

    assert env.goal == "Fix the add() sign bug"
    assert [c.id for c in env.done_criteria] == ["tests", "lint", "task-cmd-1", "ac-1", "ac-2"]
    assert env.budget.max_actions == 42
    assert env.autonomy_tier == "supervised"
    caps = env.gateway_capabilities()
    assert caps.write_roots == ("/ws",) and caps.autonomy_tier == "supervised"
    assert {"process_exec", "write_file", "read_file"} <= caps.allowed_tools
    assert caps.budget is not None and caps.budget.max_tokens == env.budget.max_tokens


def test_explicit_test_command_wins_and_goal_only_task_still_has_a_criterion() -> None:
    env = build_envelope("Make it faster", test_command="make test")
    assert [(c.id, getattr(c, "command", "")) for c in env.done_criteria] == [
        ("tests", "make test")
    ]
    bare = build_envelope("Write the release notes")
    assert bare.done_criteria == (
        AcceptanceCriterion(id="ac-goal", text="The change accomplishes: Write the release notes"),
    )


def test_envelope_round_trips_through_json_and_validates() -> None:
    env = RunEnvelope(
        goal="g",
        done_criteria=(
            CommandCheck(id="t", command="pytest", expected_exit_code=0),
            FileCheck(id="f", path="README.md", contains="Usage"),
            AcceptanceCriterion(id="a", text="clear"),
        ),
        budget=RunBudget(max_steps=5, max_cost_usd=1.5),
        autonomy_tier="envelope-autonomous",
    )
    assert RunEnvelope.from_dict(json.loads(env.to_json())) == env
    with pytest.raises(ValueError, match="at least one done criterion"):
        RunEnvelope(goal="g", done_criteria=())
    with pytest.raises(ValueError, match="unique"):
        RunEnvelope(
            goal="g", done_criteria=(AcceptanceCriterion("x", "a"), AcceptanceCriterion("x", "b"))
        )
    with pytest.raises(ValueError, match="must be positive"):
        RunBudget(max_actions=0)
