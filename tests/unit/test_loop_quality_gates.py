"""LOCUS-339: the loop's pre-PR verifier suite -- selection from the diff (pure) and
execution through an executor (fakes; no subprocess)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from pathlib import Path

from locus_runtime.harness.executor import GATEWAY_BLOCKED_EXIT_CODE, ExecResult
from locus_runtime.loop_runner.perf_budget import RESULT_PREFIX
from locus_runtime.loop_runner.state import LoopConfig
from locus_runtime.loop_runner.quality_gates import (
    PERF_MODULE,
    PERF_MODULE_PATH,
    GateSelection,
    GateSettings,
    GateSpec,
    gate_executables,
    map_tests,
    normalize_path,
    parse_command,
    run_gate_check,
    run_gate_suite,
    select_gate_checks,
)

TRACKED = [
    "locus_runtime/gateway.py",
    "locus_runtime/loop_runner/runner.py",
    PERF_MODULE_PATH,
    "locus_tooling/cli.py",
    "apps/backend/app/main.py",
    "apps/frontend/src/page.tsx",
    "policies/agent_policy.rego",
    "tests/unit/test_gateway.py",
    "tests/harness/test_runner.py",
    "tests/unit/test_cli.py",
    "tests/policy/test_agent_policy.py",
    "docs/readme.md",
]
SETTINGS = GateSettings(python="py", opa="opa", npm="npm")


def _ids(selection: GateSelection) -> list[str]:
    return [c.id for c in selection.checks]


def _check(selection: GateSelection, check_id: str) -> GateSpec:
    return next(c for c in selection.checks if c.id == check_id)


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def test_docs_only_change_selects_nothing_and_explains_why() -> None:
    selection = select_gate_checks(["docs/readme.md"], TRACKED, settings=SETTINGS)
    assert selection.checks == ()
    skipped = dict(selection.skipped)
    assert skipped["tests"] == "no Python change"
    assert {"lint", "typecheck", "frontend", "policy", "perf"} <= set(skipped)


def test_runtime_change_selects_mapped_tests_lint_typecheck_and_perf() -> None:
    selection = select_gate_checks(["locus_runtime/gateway.py"], TRACKED, settings=SETTINGS)
    assert _ids(selection) == ["tests", "lint", "typecheck", "perf"]
    assert _check(selection, "tests").argv == (
        "py",
        "-m",
        "pytest",
        "-q",
        "tests/unit/test_gateway.py",
    )
    assert _check(selection, "lint").argv == (
        "py",
        "-m",
        "ruff",
        "check",
        "locus_runtime/gateway.py",
    )
    assert _check(selection, "typecheck").argv == ("py", "-m", "mypy", "locus_runtime")
    perf = _check(selection, "perf")
    assert perf.kind == "perf" and perf.argv[:3] == ("py", "-m", PERF_MODULE)


def test_selection_is_deterministic_and_order_independent() -> None:
    changed = ["locus_tooling/cli.py", "locus_runtime/gateway.py", "./locus_runtime/gateway.py"]
    first = select_gate_checks(changed, TRACKED, settings=SETTINGS)
    second = select_gate_checks(list(reversed(changed)), list(reversed(TRACKED)), settings=SETTINGS)
    assert first == second
    assert _check(first, "typecheck").argv[3:] == ("locus_runtime", "locus_tooling")


def test_frontend_gate_only_when_frontend_changed() -> None:
    selection = select_gate_checks(["apps/frontend/src/page.tsx"], TRACKED, settings=SETTINGS)
    assert _ids(selection) == ["frontend-lint", "frontend-test"]
    assert _check(selection, "frontend-test").argv == (
        "npm",
        "--prefix",
        "apps/frontend",
        "run",
        "test",
    )
    other = select_gate_checks(["locus_tooling/cli.py"], TRACKED, settings=SETTINGS)
    assert not any(i.startswith("frontend") for i in _ids(other))


def test_policy_suite_only_when_policies_changed() -> None:
    selection = select_gate_checks(["policies/agent_policy.rego"], TRACKED, settings=SETTINGS)
    assert _check(selection, "policy").argv == ("opa", "test", "policies")
    assert _check(selection, "policy-tests").argv == ("py", "-m", "pytest", "-q", "tests/policy")
    assert "perf" in _ids(selection)  # policy decisions are a measured path
    other = select_gate_checks(["locus_tooling/cli.py"], TRACKED, settings=SETTINGS)
    assert "policy" not in _ids(other)


def test_overrides_replace_never_remove_and_unknown_override_is_kept() -> None:
    overrides = {
        "tests": parse_command("python -m pytest tests/unit -q"),
        "typecheck": ("python", "-m", "mypy", "locus_runtime/loop_runner"),
        "custom": ("make", "verify"),
    }
    # A docs-only diff still runs every overridden check (an override never removes one).
    selection = select_gate_checks(
        ["docs/readme.md"], TRACKED, settings=SETTINGS, overrides=overrides
    )
    assert _check(selection, "tests").argv == ("python", "-m", "pytest", "tests/unit", "-q")
    assert _check(selection, "typecheck").argv[-1] == "locus_runtime/loop_runner"
    assert _check(selection, "custom").argv == ("make", "verify")
    # With a matching diff the override replaces the derived argv.
    replaced = select_gate_checks(
        ["locus_runtime/gateway.py"], TRACKED, settings=SETTINGS, overrides=overrides
    )
    assert _check(replaced, "tests").argv[-2:] == ("tests/unit", "-q")
    assert "overridden" in _check(replaced, "tests").reason


def test_python_change_without_mapped_tests_is_reported_as_skipped() -> None:
    selection = select_gate_checks(["apps/backend/app/main.py"], TRACKED, settings=SETTINGS)
    assert dict(selection.skipped)["tests"] == "no test file maps to the changed Python files"
    assert "lint" in _ids(selection)


def test_deleted_files_are_not_linted_or_run() -> None:
    tracked = [p for p in TRACKED if p != "tests/unit/test_cli.py"]
    selection = select_gate_checks(["tests/unit/test_cli.py"], tracked, settings=SETTINGS)
    assert "tests" not in _ids(selection) and "lint" not in _ids(selection)


def test_perf_gate_disabled_or_absent() -> None:
    off = select_gate_checks(
        ["locus_runtime/gateway.py"], TRACKED, settings=GateSettings(perf_enabled=False)
    )
    assert "perf" not in _ids(off) and "disabled" in dict(off.skipped)["perf"]
    absent = select_gate_checks(
        ["locus_runtime/gateway.py"], [p for p in TRACKED if p != PERF_MODULE_PATH]
    )
    assert "perf" not in _ids(absent)


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../outside.py", "a/../../b.py", "C:/x.py", "x\x00.py", ""]
)
def test_unsafe_paths_are_ignored(path: str) -> None:
    assert normalize_path(path) == ""
    assert select_gate_checks([path], TRACKED, settings=SETTINGS).checks == ()


def test_map_tests_maps_modules_packages_and_changed_tests() -> None:
    tracked = ["pkg/__init__.py", "tests/test_pkg.py", "tests/test_mod.py", "tests/test_other.py"]
    assert map_tests(["pkg/__init__.py", "pkg/mod.py", "tests/test_other.py"], tracked) == [
        "tests/test_pkg.py",
        "tests/test_mod.py",
        "tests/test_other.py",
    ]
    assert map_tests(["pkg/conftest.py", "README.md"], tracked) == []


def test_parse_command_is_argv_and_rejects_bad_quoting() -> None:
    assert parse_command("ruff check 'a b.py'") == ("ruff", "check", "a b.py")
    assert parse_command("echo 'unterminated") == ()
    assert parse_command("rm -rf / ; echo pwned") == ("rm", "-rf", "/", ";", "echo", "pwned")


def test_gate_executables_are_argv0_basenames() -> None:
    selection = select_gate_checks(
        ["apps/frontend/x.ts", "policies/a.rego"],
        TRACKED,
        settings=GateSettings(python="/usr/bin/python3", opa="C:\\tools\\opa.exe"),
    )
    assert set(gate_executables(selection)) == {"npm", "opa.exe", "python3"}


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
@dataclass
class FakeExecutor:
    results: dict[str, ExecResult] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)
    raises: bool = False
    backend: str = "fake"

    def run(self, command: list[str], *, timeout: int = 60) -> ExecResult:
        self.calls.append(list(command))
        if self.raises:
            raise RuntimeError("sandbox broke")
        return self.results.get(command[0], ExecResult(0, "ok", "", 0.1))


@dataclass
class _Decision:
    allowed: bool = False
    outcome: str = "deny"
    reasons: tuple[str, ...] = ("tool_jail.deny",)


def _spec(check_id: str = "tests", argv: tuple[str, ...] = ("py", "-m", "pytest")) -> GateSpec:
    return GateSpec(check_id, argv, "why", timeout_seconds=5)


def test_check_statuses() -> None:
    executor = FakeExecutor(
        results={
            "ok": ExecResult(0, "", "", 0.1),
            "bad": ExecResult(1, "1 failed", "", 0.1),
            "slow": ExecResult(-1, "", "", 5.0, timed_out=True),
            "missing": ExecResult(127, "", "not found", 0.0),
            "denied": ExecResult(
                GATEWAY_BLOCKED_EXIT_CODE,
                "",
                "[denied by policy] x",
                0.0,
                gateway=_Decision(),  # type: ignore[arg-type]
            ),
        }
    )
    status = {
        name: run_gate_check(executor, _spec(name, (name,))).status
        for name in ("ok", "bad", "slow", "missing", "denied")
    }
    assert status == {
        "ok": "pass",
        "bad": "fail",
        "slow": "fail",
        "missing": "blocked",
        "denied": "blocked",
    }
    assert run_gate_check(FakeExecutor(raises=True), _spec()).status == "blocked"


def test_check_output_is_redacted_and_bounded() -> None:
    secret = "ghp_" + "a" * 36
    executor = FakeExecutor(results={"x": ExecResult(1, f"token={secret}\n" + "y" * 9000, "", 0)})
    result = run_gate_check(executor, _spec("x", ("x",)))
    assert secret not in result.output_tail and len(result.output_tail) < 2100


def test_perf_check_uses_the_evaluator_and_fails_closed_without_one() -> None:
    out = RESULT_PREFIX + '{"metrics": {"health_ms": 1.0}}'
    executor = FakeExecutor(results={"py": ExecResult(0, out, "", 0.1)})
    spec = GateSpec("perf", ("py", "-m", "x"), "why", kind="perf")
    seen: list[str] = []

    def evaluator(stdout: str) -> Any:
        seen.append(stdout)
        return "fail", "regression: health_ms", {"metrics": {}}

    result = run_gate_check(executor, spec, perf_evaluator=evaluator)
    assert (result.status, result.detail, seen) == ("fail", "regression: health_ms", [out])
    assert run_gate_check(executor, spec).status == "blocked"


def test_suite_runs_every_check_and_reports_failures() -> None:
    selection = GateSelection(
        (_spec("tests", ("bad",)), _spec("lint", ("ok",))), skipped=(("perf", "off"),)
    )
    executor = FakeExecutor(results={"bad": ExecResult(1, "", "", 0)})
    report = run_gate_suite(executor, selection)
    assert [r.status for r in report.results] == ["fail", "pass"]
    assert not report.passed and report.failing_ids == ["tests"]
    assert "`perf` skipped: off" in "\n".join(report.markdown())
    assert report.to_dict()["passed"] is False


def test_suite_stops_running_commands_when_the_kill_switch_is_set() -> None:
    selection = GateSelection((_spec("tests", ("ok",)), _spec("lint", ("ok",))))
    executor = FakeExecutor()
    report = run_gate_suite(executor, selection, should_stop=lambda: True)
    assert executor.calls == [] and all(r.status == "blocked" for r in report.results)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[2]


def test_loop_config_gate_and_feedback_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "LOCUS_LOOP_QUALITY_GATES",
        "LOCUS_LOOP_PERF_GATE",
        "LOCUS_LOOP_EVAL_GATE",
        "LOCUS_LOOP_PROPOSE_SKILLS",
        "LOCUS_LOOP_FILE_FAILURE_ISSUES",
        "LOCUS_LOOP_TYPECHECK_ROOTS",
        "LOCUS_LOOP_EVAL_THRESHOLD",
    ]:
        monkeypatch.delenv(name, raising=False)
    defaults = LoopConfig.load(REPO, home=REPO / ".no-loop-home")
    assert defaults.quality_gates and defaults.perf_gate
    assert defaults.eval_gate == "advisory" and defaults.eval_threshold == 0.30
    assert defaults.propose_skills and defaults.file_failure_issues
    assert defaults.typecheck_roots == ("locus_runtime", "locus_tooling")
    # A directly constructed config (tests, embedding) writes nothing outside the loop home.
    bare = LoopConfig(repo_path=REPO, home=REPO, project_slug="x")
    assert bare.eval_gate == "off" and not bare.propose_skills and not bare.file_failure_issues

    monkeypatch.setenv("LOCUS_LOOP_QUALITY_GATES", "0")
    monkeypatch.setenv("LOCUS_LOOP_EVAL_GATE", "required")
    monkeypatch.setenv("LOCUS_LOOP_PROPOSE_SKILLS", "off")
    monkeypatch.setenv("LOCUS_LOOP_TYPECHECK_ROOTS", "locus_runtime/loop_runner")
    monkeypatch.setenv("LOCUS_LOOP_EVAL_THRESHOLD", "7")  # out of range -> default
    tuned = LoopConfig.load(REPO, home=REPO / ".no-loop-home")
    assert not tuned.quality_gates and tuned.eval_gate == "required"
    assert not tuned.propose_skills and tuned.typecheck_roots == ("locus_runtime/loop_runner",)
    assert tuned.eval_threshold == 0.30
