from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_primary_docs_no_longer_claim_lattix_locus_package_exists() -> None:
    readme = _read("README.md")
    architecture = _read("docs/ARCHITECTURE.md")
    foss_release = _read("docs/FOSS_RELEASE.md")

    assert "transitional legacy package slated for removal" not in readme
    assert "still exists in the current tree" not in readme
    assert "lattix_locus/orchestrator/" not in architecture
    assert "lattix_locus/guardrails/" not in architecture
    assert "lattix_locus/agents/" not in architecture
    assert "- `lattix_locus/`" not in foss_release


def test_threat_model_defines_phase3_legacy_surface_retirement() -> None:
    threat_model = _read("THREAT-MODEL.md")

    assert "## Phase 3 focus" in threat_model
    assert "legacy-surface retirement and documentation convergence" in threat_model
    assert "Historical migration record for removed `lattix_locus/`" in threat_model


def test_imported_reference_docs_are_marked_historical_not_canonical() -> None:
    reference_readme = _read("docs/reference/lattix-locus-docs/README.md")
    reference_architecture = _read("docs/reference/lattix-locus-docs/docs/ARCHITECTURE.md")
    reference_security = _read("docs/reference/lattix-locus-docs/docs/SECURITY.md")

    assert "historical reference archive" in reference_readme.lower()
    assert "canonical current-state architecture" in reference_architecture.lower()
    assert "historical phase 3 direction" in reference_architecture.lower()
    assert "submodule migration plan" in reference_architecture.lower()
    assert "historical reference note" in reference_security.lower()
