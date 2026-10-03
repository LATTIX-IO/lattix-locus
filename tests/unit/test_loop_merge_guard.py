"""LOCUS-338: the D-22 auto-merge guard (pure) -- merge only when every gate is green
and no protected path or gate definition changed. Adversarial cases included."""

from __future__ import annotations

from pathlib import Path

import pytest

from locus_runtime.loop_runner.merge_guard import (
    ChangedFile,
    GateCheck,
    UnsafePath,
    compile_owner_pattern,
    evaluate_auto_merge,
    normalize_path,
    parse_codeowners,
)

REPO = Path(__file__).resolve().parents[2]
CODEOWNERS = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
GREEN = [
    GateCheck("ci / test", "completed", "success"),
    GateCheck("ci / lint", "completed", "success"),
]
SAFE = ChangedFile("locus_runtime/harness/prompts.py", "modified", patch="+x = 1\n-x = 0\n")


def _decide(files: list[ChangedFile], checks: list[GateCheck] | None = None, **kwargs):
    kwargs.setdefault("codeowners_text", CODEOWNERS)
    return evaluate_auto_merge(files, GREEN if checks is None else checks, **kwargs)


def _held_for(decision, fragment: str) -> bool:
    return decision.action == "hold" and any(fragment in r for r in decision.reasons)


def test_green_unprotected_change_merges() -> None:
    decision = _decide([SAFE])
    assert decision.merge and decision.reasons == ()


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("check", "fragment"),
    [
        (GateCheck("ci / test", "completed", "failure"), "concluded failure"),
        (GateCheck("ci / test", "in_progress", ""), "is in_progress"),
        (GateCheck("ci / test", "completed", "cancelled"), "concluded cancelled"),
        (GateCheck("ci / test", "completed", "timed_out"), "concluded timed_out"),
        (GateCheck("ci / test", "completed", "action_required"), "concluded action_required"),
    ],
)
def test_any_non_green_check_holds(check: GateCheck, fragment: str) -> None:
    assert _held_for(_decide([SAFE], [GREEN[1], check]), fragment)


def test_no_checks_holds() -> None:
    assert _held_for(_decide([SAFE], []), "no CI checks")


def test_required_check_missing_or_skipped_holds() -> None:
    assert _held_for(_decide([SAFE], required_checks=["ci / policy"]), "did not run")
    skipped = [*GREEN, GateCheck("ci / policy", "completed", "skipped")]
    assert _held_for(
        _decide([SAFE], skipped, required_checks=["CI / Policy"]), "required check 'ci / policy'"
    )


def test_skipped_optional_check_is_not_a_failure() -> None:
    checks = [*GREEN, GateCheck("docs preview", "completed", "skipped")]
    assert _decide([SAFE], checks).merge


# --------------------------------------------------------------------------- #
# Protected paths (CODEOWNERS + baseline)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path",
    [
        "policies/agent_policy.rego",
        "locus_runtime/gateway.py",
        "locus_tooling/native_secrets.py",
        "apps/backend/app/control_status.py",
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
        "AGENTS.md",
        "docs/product/20-roadmap-and-decisions.md",
        "locus_runtime/loop_runner/merge_guard.py",
    ],
)
def test_protected_paths_hold(path: str) -> None:
    assert _held_for(_decide([ChangedFile(path, patch="+a\n")]), "protected path changed")


@pytest.mark.parametrize(
    "path",
    [
        "POLICIES/agent_policy.rego",
        "Locus_Runtime/Gateway.py",
        "agents.md",
        ".GitHub/Workflows/ci.yml",
        "policies./x.rego",
    ],
)
def test_case_and_windows_name_variations_hold(path: str) -> None:
    assert _held_for(_decide([ChangedFile(path, patch="+a\n")]), "protected path changed")


def test_rename_of_protected_file_holds_on_either_side() -> None:
    out_of = ChangedFile("locus_runtime/gateway_old.py", "renamed", "locus_runtime/gateway.py")
    into = ChangedFile("policies/new.rego", "renamed", "notes/new.rego")
    assert _held_for(_decide([out_of]), "locus_runtime/gateway.py")
    assert _held_for(_decide([into]), "policies/new.rego")


@pytest.mark.parametrize(
    "path",
    [
        "src/../policies/agent_policy.rego",
        "./locus_runtime/./gateway.py",
        "docs\\..\\AGENTS.md",
    ],
)
def test_path_traversal_is_normalized_then_protected(path: str) -> None:
    assert _held_for(_decide([ChangedFile(path, patch="+a\n")]), "protected path changed")


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/etc/passwd",
        "C:/Windows/x.py",
        "a/\x00b.py",
        "AGENTS.md::$DATA",
        "",
        "a/../..",
    ],
)
def test_unsafe_paths_hold(path: str) -> None:
    with pytest.raises(UnsafePath):
        normalize_path(path)
    assert _decide([ChangedFile(path, patch="+a\n")]).action == "hold"


def test_codeowners_edit_or_deletion_holds_even_without_codeowners_text() -> None:
    edit = ChangedFile(".github/CODEOWNERS", patch="-/policies/ @jmsbooth\n")
    assert _held_for(_decide([edit]), "protected path changed")
    # A PR that deletes CODEOWNERS: the base text is gone -> fail closed + baseline still applies.
    gone = _decide([ChangedFile("CODEOWNERS", "removed")], codeowners_text=None)
    assert _held_for(gone, "CODEOWNERS is missing")
    assert _held_for(gone, "protected path changed: codeowners")


def test_baseline_protects_even_if_codeowners_is_emptied() -> None:
    decision = _decide(
        [ChangedFile("locus_runtime/sandbox.py", patch="+a\n")], codeowners_text="# none\n"
    )
    assert _held_for(decision, "protected path changed")
    assert _held_for(decision, "CODEOWNERS is missing or empty")


def test_codeowners_glob_semantics() -> None:
    assert compile_owner_pattern("/docs/").match("docs/a/b.md")
    assert not compile_owner_pattern("/docs/").match("x/docs/a.md")
    assert compile_owner_pattern("*.rego").match("deep/dir/p.rego")
    assert compile_owner_pattern("apps/**/secrets.py").match("apps/a/b/secrets.py")
    assert compile_owner_pattern("/AGENTS.md").match("agents.md")
    assert not compile_owner_pattern("/AGENTS.md").match("docs/agents.md")
    assert len(parse_codeowners(CODEOWNERS)) >= 10


# --------------------------------------------------------------------------- #
# Gate definitions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "apps/backend/tests/conftest.py",
        "pytest.ini",
        "setup.cfg",
        "ruff.toml",
        "scripts/run_opa.py",
        ".pre-commit-config.yaml",
    ],
)
def test_gate_config_files_hold(path: str) -> None:
    assert _decide([ChangedFile(path, patch="+a\n")]).action == "hold"


PYPROJECT = """[project]
name = "x"
version = "0.1.0"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F"]

[tool.pytest.ini_options]
addopts = "-q"
"""


def test_pyproject_gate_table_change_holds_but_other_tables_merge() -> None:
    weakened = PYPROJECT.replace('select = ["E", "F"]', 'select = ["E"]')
    file = ChangedFile("pyproject.toml", patch="-select\n+select\n")
    held = _decide([file], config_versions={"pyproject.toml": (PYPROJECT, weakened)})
    assert _held_for(held, "[tool.ruff] changed")
    bumped = PYPROJECT.replace('version = "0.1.0"', 'version = "0.1.1"')
    assert _decide([file], config_versions={"pyproject.toml": (PYPROJECT, bumped)}).merge


def test_pyproject_without_contents_or_unparseable_holds() -> None:
    file = ChangedFile("pyproject.toml", patch="+a\n")
    assert _held_for(_decide([file]), "contents were not provided")
    assert _held_for(
        _decide([file], config_versions={"pyproject.toml": (PYPROJECT, "[[[")}),
        "could not be parsed",
    )


MAKEFILE = """PYTHON ?= python
PYTEST ?= pytest

test:           ## Run all tests
\t$(PYTEST) tests -v

lint:           ## Lint
\t$(PYTHON) -m ruff check .

docs:
\techo docs
"""


def test_makefile_gate_target_or_variable_change_holds() -> None:
    file = ChangedFile("Makefile", patch="+x\n")
    weakened = MAKEFILE.replace("$(PYTEST) tests -v", "$(PYTEST) tests -v -k 'not slow'")
    assert _held_for(
        _decide([file], config_versions={"Makefile": (MAKEFILE, weakened)}), "target 'test' changed"
    )
    var = MAKEFILE.replace("PYTEST ?= pytest", "PYTEST ?= true")
    assert _held_for(_decide([file], config_versions={"Makefile": (MAKEFILE, var)}), "variables")


def test_makefile_non_gate_target_change_merges() -> None:
    file = ChangedFile("Makefile", patch="+x\n")
    docs = MAKEFILE.replace("echo docs", "echo documentation")
    assert _decide([file], config_versions={"Makefile": (MAKEFILE, docs)}).merge


# --------------------------------------------------------------------------- #
# Tests weakened
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "line",
    [
        "@pytest.mark.skip(reason='flaky')",
        "@pytest.mark.skipif(True, reason='x')",
        "@pytest.mark.xfail",
        "    pytest.skip('later')",
        "pytest.importorskip('nope')",
        "@unittest.skip('x')",
        "  it.skip('renders', () => {})",
    ],
)
def test_added_skip_markers_hold(line: str) -> None:
    file = ChangedFile("tests/unit/test_x.py", patch=f"+{line}\n")
    assert _held_for(_decide([file]), "skip/xfail marker added")


def test_skip_marker_in_non_test_file_also_holds() -> None:
    file = ChangedFile("locus_runtime/harness/tools.py", patch="+pytestmark = pytest.mark.skip\n")
    assert _held_for(_decide([file]), "skip/xfail marker added")


def test_deleting_test_functions_or_assertions_holds() -> None:
    patch = "-def test_a() -> None:\n-    assert add(1, 2) == 3\n+def helper() -> None:\n"
    decision = _decide([ChangedFile("tests/unit/test_math.py", patch=patch)])
    assert _held_for(decision, "net deletion of test functions")
    assert _held_for(decision, "net deletion of assertions")


def test_deleted_or_moved_out_test_file_holds() -> None:
    assert _held_for(
        _decide([ChangedFile("tests/unit/test_math.py", "removed", patch="-x\n")]),
        "test file deleted",
    )
    moved = ChangedFile("scratch/math_cases.py", "renamed", "tests/unit/test_math.py")
    assert _held_for(_decide([moved]), "moved out of the test tree")


def test_test_file_without_diff_holds() -> None:
    assert _held_for(_decide([ChangedFile("tests/unit/test_math.py")]), "diff was not provided")


def test_adding_tests_merges() -> None:
    patch = "+def test_b() -> None:\n+    assert 1 == 1\n"
    assert _decide([SAFE, ChangedFile("tests/unit/test_new.py", "added", patch=patch)]).merge


def test_empty_pr_holds() -> None:
    assert _held_for(_decide([]), "changes no files")
