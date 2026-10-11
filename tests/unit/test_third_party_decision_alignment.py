"""Release guards for the reconciled third-party decision and security precedence."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _decisions() -> dict[str, str]:
    table = _read("docs/product/20-roadmap-and-decisions.md")
    matches = re.findall(r"^\|\s*(D-\d{2})\s*\|(.+)$", table, re.MULTILINE)
    ids = [key for key, _ in matches]
    assert len(ids) == len(set(ids)), "duplicate canonical decision identifier"
    return dict(matches)


def test_accepted_meta_harness_decisions_remain_separate_from_older_judge_record() -> None:
    rows = _decisions()
    assert "meta-harness" in rows["D-32"]
    assert "Task Orchestrator" in rows["D-33"]
    assert "fleet product RSI" in rows["D-34"]
    assert "Savant is optional" in rows["D-35"]
    assert "Clef" in rows["D-36"]
    assert "in-process" in rows["D-37"]
    assert "assistant-ui" in rows["D-38"]


def test_hosted_clef_preference_does_not_weaken_provenance_prohibition() -> None:
    rows = _decisions()
    assessment = _read("docs/third-party-assessment.md")
    routing = _read("docs/product/10-inference-and-model-routing.md")
    security = _read("SECURITY.md")
    provider = _read("locus_runtime/model_client.py")
    assert "does NOT activate" in rows["D-36"]
    assert "security exception" in rows["D-36"]
    assert "blocked" in assessment.lower()
    assert "Current routing (enforce now)" in assessment
    assert "Laya remains active" in routing or "local Laya default remains active" in routing
    assert "hosted, API or web inference of those lineages is always refused" in security
    assert "def provenance_denial(" in provider


def test_provisional_agent_isolation_and_optional_savant_are_explicit() -> None:
    architecture = _read("docs/ARCHITECTURE-MODULES.md")
    assessment = _read("docs/third-party-assessment.md")
    assert "Narrow documented legacy exception (D-37)" in architecture
    assert "normally run out of process" in architecture
    assert "strictly **optional**" in assessment


def test_ui_bakeoff_evidence_is_not_misrepresented_as_shipped() -> None:
    report = _read("docs/development/chat-ui-bakeoff-2026-10.md")
    assert "86/100 against 62/100" in report
    assert "isolated benchmark" in report
    assert "spike/chat-ui-bakeoff" in report
    assert "not proof that a production Locus UI" in report
