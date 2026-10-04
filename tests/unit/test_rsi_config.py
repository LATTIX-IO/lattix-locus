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
    "LOCUS_RSI_CANDIDATE_UNJAILED",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def _jail(monkeypatch: pytest.MonkeyPatch, tier: str | None) -> None:
    from locus_runtime.rsi import jail

    reason = "Linux bubblewrap" if tier else "bubblewrap is not installed"
    monkeypatch.setattr(
        jail, "jail_availability", lambda: jail.JailAvailability(tier, "linux", reason)
    )


def test_loop_scorecard_is_advisory_by_default_where_the_candidate_can_be_jailed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # LOCUS-379: the candidate runs in an OS jail, so the scorecard runs (advisory).
    _jail(monkeypatch, "bwrap")
    cfg = LoopConfig.load(tmp_path, home=tmp_path / "home")
    assert cfg.scorecard_mode == "advisory"
    assert (cfg.scorecard_trials, cfg.scorecard_splits) == (1, ("dev", "heldout"))
    assert cfg.scorecard_model == "" and cfg.tag_variants is False
    # The bare dataclass (tests, embedders) stays off.
    assert LoopConfig(repo_path=tmp_path, home=tmp_path, project_slug="s").scorecard_mode == "off"


def test_loop_scorecard_stays_off_without_a_jail_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.loop_runner.report import build_report, render_text
    from locus_runtime.loop_runner.scorecard_gate import scorecard_posture
    from locus_runtime.loop_runner.state import loop_status

    _jail(monkeypatch, None)
    assert LoopConfig.load(tmp_path, home=tmp_path / "home").scorecard_mode == "off"
    posture = scorecard_posture({})
    assert posture["mode"] == "off" and posture["candidate_jail"] is None
    assert "no OS jail for the candidate" in posture["reason"]
    assert "bubblewrap is not installed" in posture["reason"]
    status = loop_status(tmp_path / "home")
    assert status["scorecard"]["mode"] == "off"
    assert "no OS jail" in status["scorecard"]["reason"]
    text = render_text(build_report([], [], [], scorecard_posture=posture))
    assert "RSI scorecard mode: off (no OS jail for the candidate" in text
    # An explicit setting still wins (and the candidate then refuses or is skipped).
    monkeypatch.setenv("LOCUS_LOOP_SCORECARD", "advisory")
    assert LoopConfig.load(tmp_path, home=tmp_path / "home").scorecard_mode == "advisory"
    assert scorecard_posture()["configured"] is True


def test_scorecard_posture_with_a_jail(monkeypatch: pytest.MonkeyPatch) -> None:
    from locus_runtime.loop_runner.scorecard_gate import scorecard_posture

    _jail(monkeypatch, "bwrap")
    posture = scorecard_posture({"LOCUS_LOOP_SCORECARD": "off"})
    assert posture["mode"] == "off" and posture["default"] == "advisory"
    assert posture["candidate_jail"] == "bwrap" and posture["unjailed_opt_out"] is False
    assert scorecard_posture({"LOCUS_RSI_CANDIDATE_UNJAILED": "1"})["unjailed_opt_out"] is True


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
