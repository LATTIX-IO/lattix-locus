"""LOCUS-351: scorecard settings of the loop and the shared toolchain switch."""

from __future__ import annotations

from pathlib import Path

import pytest

from locus_runtime.loop_runner.scorecard_gate import parse_scorecard_mode
from locus_runtime.loop_runner.state import LoopConfig
from locus_runtime.win_toolchain import TOOLCHAIN_HOME_ENV, toolchain_for

_ENV = (
    "LOCUS_LOOP_SCORECARD",
    "LOCUS_LOOP_SCORECARD_TRIALS",
    "LOCUS_LOOP_SCORECARD_SPLITS",
    "LOCUS_LOOP_SCORECARD_MODEL",
    "LOCUS_LOOP_SCORECARD_PYTHON",
    "LOCUS_LOOP_TAG_VARIANTS",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def test_loop_scorecard_is_off_by_default(tmp_path: Path) -> None:
    # The candidate runs agent-written code outside the jail, so running the
    # scorecard is an explicit principal opt-in (P32).
    cfg = LoopConfig.load(tmp_path, home=tmp_path / "home")
    assert cfg.scorecard_mode == "off"
    assert (cfg.scorecard_trials, cfg.scorecard_splits) == (1, ("dev", "heldout"))
    assert cfg.scorecard_model == "" and cfg.tag_variants is False
    # The bare dataclass (tests, embedders) stays off.
    assert LoopConfig(repo_path=tmp_path, home=tmp_path, project_slug="s").scorecard_mode == "off"


def test_loop_scorecard_settings_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_LOOP_SCORECARD", "REQUIRED")
    monkeypatch.setenv("LOCUS_LOOP_SCORECARD_TRIALS", "3")
    monkeypatch.setenv("LOCUS_LOOP_SCORECARD_SPLITS", "heldout")
    monkeypatch.setenv("LOCUS_LOOP_SCORECARD_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("LOCUS_LOOP_TAG_VARIANTS", "1")
    cfg = LoopConfig.load(tmp_path, home=tmp_path / "home")
    assert cfg.scorecard_mode == "required"
    assert (cfg.scorecard_trials, cfg.scorecard_splits) == (3, ("heldout",))
    assert cfg.scorecard_model == "gpt-oss:20b" and cfg.tag_variants is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", "advisory"), ("off", "off"), ("0", "off"), ("required", "required"), ("x", "advisory")],
)
def test_parse_scorecard_mode(raw: str, expected: str) -> None:
    assert parse_scorecard_mode(raw) == expected


def test_toolchain_home_switch_only_applies_without_an_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path / "candidate-home"))
    monkeypatch.delenv(TOOLCHAIN_HOME_ENV, raising=False)
    assert toolchain_for().root.parent == tmp_path / "candidate-home"
    monkeypatch.setenv(TOOLCHAIN_HOME_ENV, str(tmp_path / "installed"))
    assert toolchain_for().root.parent == tmp_path / "installed"
    assert toolchain_for(tmp_path / "explicit").root.parent == tmp_path / "explicit"


def test_candidate_python_needs_configuration_in_a_frozen_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from locus_runtime.loop_runner.scorecard_gate import ScorecardUnavailable, candidate_python

    assert candidate_python("C:/venv/python.exe") == "C:/venv/python.exe"
    assert candidate_python("") == sys.executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ScorecardUnavailable, match="LOCUS_LOOP_SCORECARD_PYTHON"):
        candidate_python("")


def test_default_runner_maps_an_unavailable_suite_to_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.loop_runner import scorecard_gate as sg
    from locus_runtime.rsi.variants import VariantArchive

    class Suite:
        class SuiteUnavailable(RuntimeError):
            pass

        class SuiteRunConfig:
            def __init__(self, **kw: object) -> None:
                self.kw = kw

        @staticmethod
        def run_suite(config: object) -> object:
            raise Suite.SuiteUnavailable("model endpoint unreachable (ConnectError)")

    monkeypatch.setattr(sg, "_import_suite_runner", lambda _repo: Suite)
    request = sg.ScorecardRequest(
        candidate_checkout=tmp_path,
        repo_path=tmp_path,
        output_dir=tmp_path / "o",
        git_sha="a" * 40,
        branch="b",
    )
    result = sg.evaluate_candidate(request, sg.default_scorecard_runner, VariantArchive(tmp_path))
    assert result.status == "skipped" and "unreachable" in result.reason

    def missing(_repo: Path) -> object:
        raise ImportError("no locus_evals")

    monkeypatch.setattr(sg, "_import_suite_runner", missing)
    result = sg.evaluate_candidate(request, sg.default_scorecard_runner, VariantArchive(tmp_path))
    assert result.status == "skipped" and "not installed" in result.reason
