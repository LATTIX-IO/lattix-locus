from pathlib import Path
import subprocess

from locus_runtime.install import (
    docker_compose_available,
    docker_daemon_available,
    hostname_prefix_valid,
    port_available,
    writable_directory,
)


def test_hostname_prefix_valid_accepts_dns_safe_name() -> None:
    result = hostname_prefix_valid("locus-demo")
    assert result.ok is True


def test_hostname_prefix_valid_rejects_unsafe_name() -> None:
    result = hostname_prefix_valid("Locus Demo")
    assert result.ok is False


def test_writable_directory_reports_success(tmp_path: Path) -> None:
    result = writable_directory(tmp_path / "installer")
    assert result.ok is True


def test_port_available_returns_result() -> None:
    result = port_available(0)
    assert result.ok is True


def test_docker_daemon_available_reports_actionable_message(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "docker" if name == "docker" else None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="daemon not running"
        ),
    )

    result = docker_daemon_available()

    assert result.ok is False
    assert "Start Docker Desktop or the docker service" in result.message


def test_docker_compose_available_reports_missing_plugin(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "docker" if name == "docker" else None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="compose not installed"
        ),
    )

    result = docker_compose_available()

    assert result.ok is False
    assert "Docker Compose v2 plugin is not available" in result.message
