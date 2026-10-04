"""LOCUS-338: `lattix secrets set`, `lattix loop status|disable|enable`, loop config, and
the self_improvement_loop posture control (never a security "enforced" claim)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

import locus_tooling.native_secrets as native_secrets
from locus_runtime.loop_runner.state import KILL_FILE, Ledger, LoopConfig, loop_status
from locus_runtime.rsi import secret_scan
from locus_tooling.cli import cli

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LOCUS_LOOP_HOME", str(tmp_path / "loop"))
    monkeypatch.delenv("LOCUS_LOOP_DISABLED", raising=False)
    # Hermetic: never read the host's HKCU\Environment (LOCUS-380 status warning).
    monkeypatch.setattr(secret_scan, "read_user_environment", lambda: {})
    return tmp_path / "loop"


def test_loop_status_warns_about_secret_names_in_the_persistent_user_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = "fake-persistent-value-0123456789"
    monkeypatch.setattr(
        secret_scan,
        "read_user_environment",
        lambda: {"NVIDIA_API_KEY": value, "GH_TOKEN": value, "PATH": "C:\\bin"},
    )
    status = loop_status()
    [warning] = status["warnings"]
    assert "GH_TOKEN, NVIDIA_API_KEY" in warning and "lattix secrets set" in warning
    assert "PATH" not in warning and value not in warning
    result = CliRunner().invoke(cli, ["loop", "status"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["warnings"] == [warning]
    assert "warning: " in result.stderr and value not in result.output


def test_secrets_set_prompts_hidden_and_never_echoes(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, str] = {}

    def fake_set(name: str, value: str, *, app_home: object = None) -> str:
        stored[name] = value
        return "keychain"

    monkeypatch.setattr(native_secrets, "set_secret", fake_set)
    result = CliRunner().invoke(
        cli, ["secrets", "set", "LINEAR_API_KEY"], input="s3cr3t-val\ns3cr3t-val\n"
    )
    assert result.exit_code == 0, result.output
    assert stored == {"LINEAR_API_KEY": "s3cr3t-val"}
    assert "s3cr3t-val" not in result.output
    assert json.loads(result.output[result.output.index("{") :])["storage"] == "keychain"


def test_secrets_set_rejects_bad_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_secrets, "set_secret", lambda *a, **k: pytest.fail("must not store"))
    result = CliRunner().invoke(cli, ["secrets", "set", "../../etc"], input="x\nx\n")
    assert result.exit_code != 0


def test_loop_status_disable_enable(_home: Path) -> None:
    runner = CliRunner()
    status = json.loads(runner.invoke(cli, ["loop", "status"]).output)
    assert status["enabled"] is True and status["last_run"] is None
    runner.invoke(cli, ["loop", "disable"])
    assert (_home / KILL_FILE).exists()
    assert loop_status()["enabled"] is False
    enabled = json.loads(runner.invoke(cli, ["loop", "enable"]).output)
    assert enabled["enabled"] is True


def test_env_kill_switch_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_LOOP_DISABLED", "true")
    assert loop_status()["enabled"] is False
    assert "LOCUS_LOOP_DISABLED" in loop_status()["disabled_reason"]


def test_config_reads_workflow_front_matter(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [k for k in os.environ if k.startswith("LOCUS_LOOP_")]:
        if name != "LOCUS_LOOP_HOME":
            monkeypatch.delenv(name)
    monkeypatch.setenv("LOCUS_LOOP_TYPECHECK_COMMAND", "python -m mypy locus_runtime/loop_runner")
    config = LoopConfig.load(REPO)
    assert config.project_slug == "3b160e533200"
    assert "In Progress" in config.active_states
    assert "agent:human-review-required" in config.exclude_labels
    assert config.poll_interval_seconds == 30.0
    assert config.auto_merge is False  # opt-in only
    assert dict(config.check_commands) == {"typecheck": "python -m mypy locus_runtime/loop_runner"}


# --------------------------------------------------------------------------- #
# Posture control
# --------------------------------------------------------------------------- #
def _posture_states(status: dict | None) -> dict[str, tuple[str, str]]:
    import sys

    sys.path.insert(0, str(REPO / "apps" / "backend"))
    from app.control_status import PostureFacts, evaluate_controls

    facts = PostureFacts(
        auth_required=True,
        a2a_signed_messages=True,
        a2a_trusted_subject_count=1,
        a2a_replay_protection=True,
        egress_allowlist=True,
        guardrail_signals_enabled=True,
        guardrail_signal_enforcement="block_high",
        presidio_flag=False,
        presidio_state="not_loaded",
        audit_durable=False,
        sandbox_requested=True,
        sandbox_strategy="kernel-bwrap",
        policy_engine_available=False,
        biscuit_loaded=False,
        vault_addr_configured=False,
        envoy_authz_filters=False,
        nats_loaded=False,
        secret_storage_mode="keychain",
        self_improvement_loop=status,
    )
    return {c.id: (c.state, c.evidence) for c in evaluate_controls(facts)}


def test_posture_reports_loop_enabled_or_disabled_but_never_enforced(_home: Path) -> None:
    ledger = Ledger.load(_home)
    ledger.set_last({"run_id": "r1", "issue": "LOC-1", "outcome": "done", "finished_at": "t"})
    ledger.save()
    state, evidence = _posture_states(loop_status())["self_improvement_loop"]
    assert (
        state == "unverified" and "LOC-1: done" in evidence and "not a security control" in evidence
    )
    (_home / KILL_FILE).write_text("x", encoding="utf-8")
    state, evidence = _posture_states(loop_status())["self_improvement_loop"]
    assert state == "off" and "Disabled" in evidence
    assert _posture_states(None)["self_improvement_loop"][0] == "off"
