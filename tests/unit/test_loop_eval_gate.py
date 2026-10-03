"""LOCUS-339: the loop's eval gate -- modes, the merge hold, honest skips, the history
file, and one real synthetic-mini plumbing run through apps/evals."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.harness.llm import ChatResponse, ScriptedChatClient
from locus_runtime.loop_runner import eval_gate as eg
from locus_runtime.loop_runner.eval_gate import (
    EvalGateResult,
    EvalHistory,
    EvalRequest,
    default_eval_runner,
    eval_merge_hold_reason,
    parse_eval_mode,
)
from tests.gateway_support import eval_run_doubles

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", "advisory"),
        (None, "advisory"),
        ("off", "off"),
        ("0", "off"),
        ("REQUIRED", "required"),
        ("on", "advisory"),
        ("bogus", "advisory"),
    ],
)
def test_parse_eval_mode(raw: str | None, expected: str) -> None:
    assert parse_eval_mode(raw) == expected


def test_a_pass_needs_a_measured_rate_at_or_above_threshold() -> None:
    assert EvalGateResult("pass", resolve_rate=None).status == "fail"
    assert EvalGateResult("pass", resolve_rate=0.2, threshold=0.3).status == "fail"
    assert EvalGateResult("pass", resolve_rate=0.3, threshold=0.3).status == "pass"
    skipped = EvalGateResult.skipped("no model")
    assert skipped.status == "skipped" and skipped.resolve_rate is None
    assert "skipped: no model" in skipped.markdown()[0]


@pytest.mark.parametrize(
    ("mode", "status", "holds"),
    [
        ("off", None, False),
        ("advisory", "fail", False),
        ("advisory", "skipped", False),
        ("required", "pass", False),
        ("required", "fail", True),
        ("required", "skipped", True),
        ("required", None, True),
    ],
)
def test_eval_only_holds_the_merge_when_required(
    mode: str, status: str | None, holds: bool
) -> None:
    assert bool(eval_merge_hold_reason(mode, status)) is holds


def test_history_appends_and_loads(tmp_path: Path) -> None:
    history = EvalHistory(tmp_path)
    history.append(EvalGateResult("fail", resolve_rate=0.0), run_id="r1", issue="LOC-1")
    history.append(EvalGateResult.skipped("down"), run_id="r2", issue="LOC-2")
    rows = history.load()
    assert [r["status"] for r in rows] == ["fail", "skipped"]
    assert rows[1]["resolve_rate"] is None and rows[0]["issue"] == "LOC-1"


def _request(tmp_path: Path, factory: Any, **kw: Any) -> EvalRequest:
    return EvalRequest(client_factory=factory, output_dir=tmp_path / "eval", repo_path=REPO, **kw)


def test_unreachable_model_chain_is_skipped_never_passed(tmp_path: Path) -> None:
    def factory() -> Any:
        raise ConnectionError("NIM and Ollama are both down")

    result = default_eval_runner(_request(tmp_path, factory))
    assert result.status == "skipped" and "ConnectionError" in result.reason


def test_failing_preflight_call_is_skipped(tmp_path: Path) -> None:
    class Down(ScriptedChatClient):
        def complete(self, messages: Any, **kw: Any) -> ChatResponse:
            raise TimeoutError("no tier answered")

    result = default_eval_runner(_request(tmp_path, lambda: Down()))
    assert result.status == "skipped"


def test_missing_eval_harness_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_repo: Path) -> Any:
        raise ImportError("no locus_evals")

    monkeypatch.setattr(eg, "_import_run_eval", missing)
    result = default_eval_runner(_request(tmp_path, lambda: ScriptedChatClient()))
    assert result.status == "skipped" and "not installed" in result.reason


@pytest.mark.parametrize(
    ("summary", "status"),
    [
        ({"resolve_rate_mean": 0.67, "n_instances": 3}, "pass"),
        ({"resolve_rate_mean": 0.1, "n_instances": 3}, "fail"),
        ({"n_instances": 3}, "error"),
        ({"resolve_rate_mean": 1.0, "n_instances": 0}, "error"),
    ],
)
def test_resolve_rate_maps_to_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, summary: dict[str, Any], status: str
) -> None:
    class Config:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

    seen: dict[str, Any] = {}

    def run_eval(config: Config, **kw: Any) -> Any:
        seen.update(config=config.kw, **kw)
        return type("Run", (), {"summary": summary})()

    monkeypatch.setattr(eg, "_import_run_eval", lambda _repo: (Config, run_eval))
    result = default_eval_runner(_request(tmp_path, lambda: ScriptedChatClient(), threshold=0.3))
    assert result.status == status
    assert seen["config"]["mode"] == "plumbing" and seen["config"]["dataset"] == "synthetic-mini"


def test_crashing_eval_is_an_error_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run_eval(config: Any, **kw: Any) -> Any:
        raise RuntimeError("docker exploded")

    monkeypatch.setattr(eg, "_import_run_eval", lambda _repo: (dict, run_eval))
    result = default_eval_runner(_request(tmp_path, lambda: ScriptedChatClient()))
    assert result.status == "error" and result.resolve_rate is None


@pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None, reason="needs bash and git"
)
def test_real_synthetic_plumbing_run_with_a_model_that_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apps/evals end to end: a 'model' that only submits resolves nothing (rate 0, fail)."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # the import may append apps/evals

    def factory() -> ScriptedChatClient:
        from locus_runtime.harness.llm import ToolCall

        submit = ChatResponse(
            tool_calls=[ToolCall(id="s", name="submit", arguments='{"answer": "x"}')]
        )
        return ScriptedChatClient(responses=[ChatResponse(text="OK")] + [submit] * 10)

    result = default_eval_runner(
        _request(
            tmp_path,
            factory,
            instance_ids=["syn-add-sign"],
            max_steps=3,
            run_kwargs=eval_run_doubles(),
        )
    )
    assert result.status == "fail", result
    assert result.resolve_rate == 0.0 and result.n_instances == 1
    assert (tmp_path / "eval" / "summary.json").exists()
