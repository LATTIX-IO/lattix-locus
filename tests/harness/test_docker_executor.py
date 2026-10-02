"""Validate DockerContainerExecutor against a real container.

This is the execution backend used for SWE-bench / DeepSWE live runs (the agent
execs into a per-instance container). Gated on Docker being available so CI
without Docker skips it; when Docker is present it proves the run_shell /
read_file / write_file / exists contract against a real container.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from frontier_runtime.harness.executor import DockerContainerExecutor

_IMAGE = "python:3.12-slim"


def _docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


requires_docker = pytest.mark.skipif(not _docker_ok(), reason="docker not available")


@pytest.fixture
def container():
    subprocess.run(["docker", "pull", "-q", _IMAGE], check=True, capture_output=True, timeout=300)
    cid = subprocess.run(
        ["docker", "run", "-d", "--rm", _IMAGE, "sleep", "180"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout.strip()
    try:
        yield cid
    finally:
        subprocess.run(["docker", "kill", cid], capture_output=True, timeout=30)


@requires_docker
def test_docker_executor_full_contract(container):
    ex = DockerContainerExecutor(container, workdir_path="/tmp")

    r = ex.run_shell("echo hello && python3 --version", timeout=30)
    assert r.exit_code == 0
    assert "hello" in r.stdout and "Python 3.12" in r.stdout

    ex.write_file("mathlib.py", "def add(a, b):\n    return a + b\n")
    assert ex.read_file("mathlib.py") == "def add(a, b):\n    return a + b\n"
    assert ex.exists("mathlib.py") is True
    assert ex.exists("missing.py") is False

    # run code inside the container that imports the file we wrote
    r2 = ex.run_shell(
        "cd /tmp && python3 -c 'import mathlib; print(mathlib.add(2, 3))'", timeout=30
    )
    assert r2.exit_code == 0
    assert r2.stdout.strip() == "5"

    # a failing command surfaces a non-zero exit (for execution grading)
    r3 = ex.run_shell("python3 -c 'assert False'", timeout=30)
    assert r3.exit_code != 0
