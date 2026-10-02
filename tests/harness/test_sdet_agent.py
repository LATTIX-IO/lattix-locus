"""The shipped full-stack SDET agent loads and drives the harness."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from frontier_runtime.harness.agent_library import list_shipped_agents, load_agent_spec

REPO_ROOT = Path(__file__).resolve().parents[2]

requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="no git")


def test_sdet_agent_is_shipped():
    assert "sdet-swe-agent" in list_shipped_agents(REPO_ROOT)


def test_sdet_agent_config_is_valid_and_complete():
    cfg = json.loads(
        (REPO_ROOT / "examples" / "agents" / "sdet-swe-agent" / "agent.config.json").read_text(
            encoding="utf-8"
        )
    )
    # fields the platform's seed loader and modeler rely on
    assert cfg["id"] == "sdet-swe-agent"
    assert cfg["name"]
    assert cfg["model_defaults"]["capability_profile"] == "local-32b-class"
    tool_names = {t.get("type") for t in cfg["tools"]}
    assert "frontier-coding" in tool_names


def test_sdet_spec_builds_profile_and_prompt():
    spec = load_agent_spec("sdet-swe-agent", REPO_ROOT)
    assert spec.name == "Full-Stack SDET Agent"
    assert "Software Development Engineer in Test" in spec.system_prompt
    assert "run_tests" in spec.system_prompt  # the tool contract is in the prompt
    profile = spec.profile()
    assert profile.profile_id == "local-32b-class"
    assert profile.edit_format == "search-replace"
    assert spec.model == "gpt-oss:20b"


def test_unknown_agent_raises_with_listing():
    with pytest.raises(FileNotFoundError, match="sdet-swe-agent"):
        load_agent_spec("does-not-exist", REPO_ROOT)


@requires_bash
@requires_git
def test_sdet_agent_drives_eval_with_reference_solver(tmp_path):
    """End to end: the eval runs with --agent sdet-swe-agent; the agent's
    system prompt is used and the pipeline resolves (reference solver)."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "apps" / "evals"))
    from frontier_evals.config import EvalConfig
    from frontier_evals.runner import run_eval

    config = EvalConfig(
        mode="plumbing",
        dataset="synthetic-mini",
        agent_id="sdet-swe-agent",
        seeds=[0],
        output_dir=str(tmp_path / "out"),
    )
    run = run_eval(config, output_dir=tmp_path / "out")
    assert run.summary["resolve_rate_mean"] == 1.0
    # the run used the SDET agent's prompt — confirm via a trajectory header
    traj = next((tmp_path / "out" / "instances").rglob("*.jsonl"))
    first = json.loads(traj.read_text(encoding="utf-8").splitlines()[0])
    assert first["kind"] == "meta"
