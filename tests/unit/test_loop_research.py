from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.loop_runner.linear import LinearIssue
from locus_runtime.loop_runner.research import parse_research_proposals
from locus_runtime.loop_runner.runner import LoopRunner
from locus_runtime.loop_runner.state import LoopConfig


def test_proposal_parser_bounds_deduplicates_and_prioritizes() -> None:
    parsed = parse_research_proposals(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Improve harness retry handling",
                        "hypothesis": "h",
                        "change": "c",
                        "test_case": "Given a timeout, When retrying, Then it stops",
                        "falsifier": "No improvement",
                        "priority": 4,
                    },
                    {
                        "title": "Improve harness retry handling",
                        "hypothesis": "duplicate",
                        "change": "c",
                        "test_case": "test",
                        "falsifier": "false",
                        "priority": 1,
                    },
                    {
                        "title": "Add deterministic eval comparison",
                        "hypothesis": "h",
                        "change": "c",
                        "test_case": "Given a case, When scoring, Then records score",
                        "falsifier": "score does not improve",
                        "priority": 2,
                    },
                    {
                        "title": "Incomplete proposal",
                        "hypothesis": "h",
                        "change": "c",
                        "priority": 1,
                    },
                ]
            }
        ),
        limit=1,
    )

    assert len(parsed) == 1
    assert parsed[0].title == "Add deterministic eval comparison"
    assert parsed[0].priority == 2


class ResearchTracker:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    def list_project_issues(self, _project_slug: str) -> list[LinearIssue]:
        return []

    def project_team_id(self, _project_slug: str) -> str:
        return "team-1"

    def create_issue(self, **values: Any) -> str:
        self.created.append(values)
        return f"LOCUS-{10 + len(self.created)}"


def _research_runner(tmp_path: Path, tracker: ResearchTracker) -> LoopRunner:
    runner = object.__new__(LoopRunner)
    runner.config = LoopConfig(
        repo_path=tmp_path,
        home=tmp_path / "loop-home",
        project_slug="locus",
        research_mode=True,
        research_issues_per_day=2,
    )
    runner.tracker = tracker
    runner.clock = lambda: datetime(2026, 10, 4, tzinfo=UTC)
    runner.research_planner = lambda _context: json.dumps(
        {
            "items": [
                {
                    "title": "Add regression coverage for model retries",
                    "hypothesis": "A deterministic retry test will catch regressions.",
                    "change": "Add an isolated unit test.",
                    "test_case": "Given a timeout, When the retry runs, Then the retry limit is respected.",
                    "falsifier": "The test does not distinguish correct from incorrect retry limits.",
                    "priority": 2,
                },
                {
                    "title": "Document cross-harness execution contract",
                    "hypothesis": "An explicit contract improves routing consistency.",
                    "change": "Document and validate the harness adapter contract.",
                    "test_case": "Given each configured harness, When the adapter runs, Then the same completion and test gates apply.",
                    "falsifier": "The contract cannot be verified with current adapters.",
                    "priority": 3,
                },
            ]
        }
    )
    return runner


def test_empty_queue_research_resolves_team_and_creates_daily_bounded_issues(
    tmp_path: Path,
) -> None:
    tracker = ResearchTracker()
    runner = _research_runner(tmp_path, tracker)

    first = runner._research_backlog(gateway=None)  # type: ignore[arg-type]
    second = runner._research_backlog(gateway=None)  # type: ignore[arg-type]

    assert first.status == "research_created"
    assert "2 prioritized hypothesis issue(s)" in first.detail
    assert second.status == "research_idle"
    assert [item["priority"] for item in tracker.created] == [2, 3]
    assert all(item["team_id"] == "team-1" for item in tracker.created)
    assert all(item["state_name"] == "Todo" for item in tracker.created)
    assert all(item["label_name"] == "agent:eligible" for item in tracker.created)
    assert all("Falsification criterion" in item["description"] for item in tracker.created)


def test_research_configuration_is_enabled_by_default_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "WORKFLOW.md").write_text("---\n---\n", encoding="utf-8")
    monkeypatch.delenv("LOCUS_LOOP_RESEARCH_MODE", raising=False)
    monkeypatch.setenv("LOCUS_LOOP_RESEARCH_ISSUES_PER_DAY", "9")

    enabled = LoopConfig.load(tmp_path, home=tmp_path / "home")

    assert enabled.research_mode is True
    assert enabled.research_issues_per_day == 5

    monkeypatch.setenv("LOCUS_LOOP_RESEARCH_MODE", "false")
    disabled = LoopConfig.load(tmp_path, home=tmp_path / "home")
    assert disabled.research_mode is False
