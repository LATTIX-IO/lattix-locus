from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_symphony_is_bound_to_xfrontier_and_omniroute() -> None:
    workflow = _read("WORKFLOW.md")
    wrapper = _read("scripts/symphony-codex.sh")
    makefile = _read("Makefile")

    assert 'project_slug: "3b160e533200"' in workflow
    assert 'remote: "https://github.com/LATTIX-IO/lattix-xfrontier.git"' in workflow
    assert "agent:eligible" in workflow
    assert "agent:human-review-required" in workflow
    assert 'model_providers.omniroute.base_url="http://127.0.0.1:20128/v1"' in wrapper
    assert 'model_providers.omniroute.env_key="OMNIROUTE_API_KEY"' in wrapper
    assert 'model_providers.omniroute.wire_api="responses"' in wrapper
    assert "symphony-preflight:" in makefile
    preflight = _read("scripts/Test-SymphonyOrchestration.ps1")
    assert 'slug = "3b160e533200"' in preflight
    assert '$linearProject.name -ne "xFrontier"' in preflight


def test_ci_runs_required_gates_for_every_push() -> None:
    ci = _read(".github/workflows/ci.yml")

    assert "on:\n  push:\n  pull_request:" in ci
    for job in (
        "frontend-unit",
        "backend-unit",
        "integration",
        "sast-codeql",
        "lattix-cicd-pentest",
        "sbom-sca",
        "dast",
        "performance",
        "windows-sandbox",
        "required-gates",
    ):
        assert f"\n  {job}:" in ci

    assert "e5634fa96e81bbdc129d762a45a2acf7f814932b" in ci
    assert "OWASP ZAP baseline scan" in ci
    assert "--phase shift-left" in ci
    assert "--phase sbom" in ci
