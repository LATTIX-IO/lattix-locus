"""Shared fixtures/helpers for harness tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from frontier_runtime.harness.llm import ChatResponse, ToolCall


def tc(call_id: str, name: str, **arguments) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def tool_response(call_id: str, name: str, **arguments) -> ChatResponse:
    return ChatResponse(text="", tool_calls=[tc(call_id, name, **arguments)])


def which_bash() -> bool:
    return shutil.which("bash") is not None


def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def git_init(root: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")


requires_bash = pytest.mark.skipif(not which_bash(), reason="bash not available on this host")
requires_git = pytest.mark.skipif(not git_available(), reason="git not available on this host")
