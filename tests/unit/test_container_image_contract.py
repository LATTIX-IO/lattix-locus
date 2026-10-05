from __future__ import annotations

from pathlib import Path

from locus_runtime.sandbox import DEFAULT_SANDBOX_RUNNER_IMAGE, sandbox_runner_image


REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_PYTHON_IMAGE = "python:3.12.10-slim-bookworm"
PINNED_AGENT_IMAGE = "lattix-locus/agent-base:3.12.10-slim-bookworm"


def test_agent_dockerfile_uses_pinned_lightweight_python_base() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile.agent").read_text(encoding="utf-8")
    assert f"ARG PYTHON_BASE_IMAGE={PINNED_PYTHON_IMAGE}" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE} AS builder" in dockerfile
    assert "FROM ${PYTHON_BASE_IMAGE} AS runtime" in dockerfile
    assert "apt-get install --yes --no-install-recommends build-essential curl" not in dockerfile
    assert "apt-get install --yes --no-install-recommends curl" not in dockerfile


def test_backend_and_agent_dockerfiles_copy_license_for_package_metadata() -> None:
    backend_dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    agent_dockerfile = (REPO_ROOT / "Dockerfile.agent").read_text(encoding="utf-8")

    for dockerfile in (backend_dockerfile, agent_dockerfile):
        assert "COPY pyproject.toml README.md LICENSE ./" in dockerfile
        assert "COPY locus_tooling ./locus_tooling" in dockerfile
        assert "COPY locus_runtime ./locus_runtime" in dockerfile


def test_full_compose_uses_pinned_default_agent_image() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"LOCUS_AGENT_IMAGE:-{PINNED_AGENT_IMAGE}" in compose
    assert f"LOCUS_AGENT_PYTHON_BASE_IMAGE:-{PINNED_PYTHON_IMAGE}" in compose


def test_local_compose_uses_pinned_python_images_for_backend_and_workers() -> None:
    compose = (REPO_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")
    assert "image: python:3.12.10-slim-bookworm" in compose
    assert "image: python:3.12-slim" not in compose


def test_compose_files_set_bounded_json_file_logging() -> None:
    full_compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    local_compose = (REPO_ROOT / "docker-compose.local.yml").read_text(encoding="utf-8")

    for compose in (full_compose, local_compose):
        assert "driver: json-file" in compose
        assert 'max-size: "10m"' in compose
        assert 'max-file: "3"' in compose


def test_agent_templates_do_not_use_latest_tags() -> None:
    deployment_template = (
        REPO_ROOT
        / "apps"
        / "workers"
        / "services"
        / "AGENT_SERVICE_TEMPLATE"
        / "k8s"
        / "deployment.yaml"
    ).read_text(encoding="utf-8")
    compose_template = (
        REPO_ROOT / "apps" / "workers" / "services" / "docker-compose.example.yml"
    ).read_text(encoding="utf-8")
    assert ":latest" not in deployment_template
    assert ":latest" not in compose_template
    assert "ghcr.io/your-org/agent-service:v0.1.0" in deployment_template
    assert "ghcr.io/your-org/agent-service:v0.1.0" in compose_template
    assert "USER appuser" in (
        REPO_ROOT / "apps" / "workers" / "services" / "AGENT_SERVICE_TEMPLATE" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "runAsNonRoot: true" in deployment_template
    assert "allowPrivilegeEscalation: false" in deployment_template
    assert "no-new-privileges:true" in compose_template
    assert "read_only: true" in compose_template


def test_sandbox_runner_default_is_pinned_lightweight_python_image(monkeypatch) -> None:
    monkeypatch.delenv("SANDBOX_RUNNER_IMAGE", raising=False)
    assert DEFAULT_SANDBOX_RUNNER_IMAGE == PINNED_PYTHON_IMAGE
    assert sandbox_runner_image() == PINNED_PYTHON_IMAGE
