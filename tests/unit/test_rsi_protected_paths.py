"""LOCUS-351: the RSI suite and scorecard code are D-22 protected gate definitions.

LOCUS-382 adds the private held-out split's sync and pinned source
(``locus_tooling/evals_sync.py``, ``locus_tooling/evals_heldout.py``).

One list (``locus_runtime/gate_definitions.py``) feeds the merge guard and the
gateway; the Rego mirror is asserted equal in ``tests/policy/test_policy_parity.py``
(needs OPA) and ``policies/tests/filesystem_access_test.rego``. CODEOWNERS and the
merge guard's built-in baseline both name the paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from locus_runtime import gateway as gw
from locus_runtime.gate_definitions import (
    GATE_CONFIG_PATHS,
    GATE_WRITE_PATHS,
    gate_config_reason,
    gate_write_reason,
)
from locus_runtime.loop_runner.merge_guard import (
    BASELINE_PROTECTED,
    ChangedFile,
    GateCheck,
    evaluate_auto_merge,
    parse_codeowners,
)

REPO = Path(__file__).resolve().parents[2]
CODEOWNERS = (REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
RSI_PATHS = (
    "apps/evals/locus_evals/suite/",
    "locus_runtime/rsi/",
    "locus_tooling/evals_sync.py",
    "locus_tooling/evals_heldout.py",
)
ROOT = "/workspace/project"
PROTECTED_FILES = (
    "apps/evals/locus_evals/suite/tasks/heldout/any-task.yaml",
    "apps/evals/locus_evals/suite/tasks/dev/loc-injection.yaml",
    "apps/evals/locus_evals/suite/graders.py",
    "apps/evals/locus_evals/suite/store.py",
    "apps/evals/locus_evals/suite/runner.py",
    "locus_runtime/rsi/scorecard.py",
    "locus_runtime/rsi/candidate.py",
    "locus_runtime/rsi/metering.py",
    "locus_runtime/rsi/readonly.py",
    "locus_tooling/evals_sync.py",
    "locus_tooling/evals_heldout.py",
    "locus_runtime/loop_runner/scorecard_gate.py",
)


def test_rsi_paths_are_gate_definitions_for_the_guard_and_the_gateway() -> None:
    for prefix in RSI_PATHS:
        assert prefix in GATE_CONFIG_PATHS and prefix in GATE_WRITE_PATHS
        assert f"/{prefix}" in BASELINE_PROTECTED
    for path in PROTECTED_FILES[:-1]:
        assert gate_config_reason(path), path
        assert gate_write_reason(path), path
        assert gw.gate_definition_write(f"{ROOT}/{path}", (ROOT,)), path
    # Neighbours are not swept in.
    assert not gate_write_reason("apps/evals/locus_evals/runner.py")
    assert not gate_write_reason("locus_runtime/rsi_notes.md")
    assert not gate_write_reason("locus_tooling/cli.py")
    assert not gate_write_reason("locus_tooling/evals_sync_notes.md")


def test_codeowners_names_the_rsi_paths() -> None:
    patterns = {rule.pattern for rule in parse_codeowners(CODEOWNERS)}
    for prefix in RSI_PATHS:
        assert f"/{prefix}" in patterns


@pytest.mark.parametrize("path", PROTECTED_FILES)
@pytest.mark.parametrize("codeowners", [CODEOWNERS, None], ids=["codeowners", "baseline-only"])
def test_a_pr_touching_the_exam_or_the_rule_is_never_auto_merged(
    path: str, codeowners: str | None
) -> None:
    decision = evaluate_auto_merge(
        [ChangedFile(path, patch="+easier\n")],
        [GateCheck("ci / test", "completed", "success")],
        codeowners_text=codeowners,
    )
    assert decision.action == "hold", path
    assert any("protected path changed" in r for r in decision.reasons), decision.reasons


def test_agent_writes_to_the_suite_are_asked_not_silently_allowed() -> None:
    from tests.gateway_support import FakeEngine

    gateway = gw.Gateway(FakeEngine(), lambda _r: None)
    caps = gw.Capabilities(
        allowed_tools=frozenset({"read_file", "write_file", "process_exec"}),
        read_roots=(ROOT,),
        write_roots=(ROOT,),
        allowed_executables=("bash",),
    )
    session = gateway.open_session(run_id="r", principal="p", engine="e", capabilities=caps)
    try:
        for path in PROTECTED_FILES[:-1]:
            write = session.authorize(kind="file_write", tool="edit", target=f"{ROOT}/{path}")
            assert write.outcome == "ask" and write.risk == gw.RiskClass.R3, path
        ordinary = session.authorize(kind="file_write", tool="edit", target=f"{ROOT}/src/app.py")
        assert ordinary.outcome == "allow"
    finally:
        session.close()
