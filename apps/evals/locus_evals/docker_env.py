"""Per-instance SWE-bench Docker environments (live mode, remote runner).

Boots the official SWE-bench instance image (repo checked out at the buggy
commit under /testbed) and returns its container id, which the harness's
``DockerContainerExecutor`` execs into. All docker calls honour ``docker_host``
so the fleet runs on a remote runner box, never the local dev machine.

This module is only exercised in live runs (it needs Docker); it has no
import-time dependency on docker so the package imports cleanly in CI.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from typing import Iterator


def _docker_env(docker_host: str) -> dict[str, str]:
    env = dict(os.environ)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    return env


def instance_image(instance_id: str, *, namespace: str = "swebench") -> str:
    """Official SWE-bench image name for an instance.

    SWE-bench publishes per-instance images as
    ``swebench/sweb.eval.x86_64.<instance_id>``. Override ``namespace`` for a
    private registry mirror.
    """
    safe = instance_id.replace("__", "_1776_")  # swebench tag-encoding
    return f"{namespace}/sweb.eval.x86_64.{safe}:latest"


@contextmanager
def instance_container(
    instance_id: str, *, docker_host: str = "", namespace: str = "swebench"
) -> Iterator[str]:
    """Start the instance image, yield the container id, always clean up."""
    image = instance_image(instance_id, namespace=namespace)
    env = _docker_env(docker_host)
    create = subprocess.run(
        ["docker", "run", "-d", "--rm", image, "sleep", "infinity"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    container_id = create.stdout.strip()
    try:
        yield container_id
    finally:
        subprocess.run(["docker", "kill", container_id], capture_output=True, text=True, env=env)
