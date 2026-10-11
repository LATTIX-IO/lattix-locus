"""SDD contract traceability for the accepted general-purpose meta-harness direction."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_meta_harness_has_accepted_decision_and_traceability() -> None:
    adr = _read("docs/adr/ADR-0002-general-purpose-meta-harness.md")
    roadmap = _read("docs/product/20-roadmap-and-decisions.md")
    assert "Status: **Accepted product direction**" in adr
    assert "Task Orchestrator" in adr
    assert "LOCUS-411" in adr and "LOCUS-435" in adr
    for decision in ("D-32", "D-33", "D-34", "D-35"):
        assert f"| {decision} |" in roadmap


def test_meta_harness_security_and_consent_are_explicit() -> None:
    security = _read("docs/product/13-security-architecture.md")
    deployment = _read("docs/product/19-platform-and-deployment.md")
    skills = _read("docs/product/15-skills-mcp-and-extensions.md")
    assert "R4" in security and "not a new policy decision point" in security
    assert "off by default" in deployment.casefold()
    assert "hosted shared-runtime multi-tenancy" in deployment.casefold()
    assert "Savant access is optional" in skills and "native skill" in skills.lower()


def test_contract_and_remaining_capability_work_are_not_misrepresented() -> None:
    modules = _read("docs/ARCHITECTURE-MODULES.md")
    model = _read("docs/product/04-concept-model.md")
    agents = _read("docs/product/11-agentic-model.md")
    assert "TaskIntent" in model and "DependencyGate" in model
    assert "does not implement a scheduler" in modules
    assert "not" in agents.lower() and "running multi-pod dispatcher" in agents
