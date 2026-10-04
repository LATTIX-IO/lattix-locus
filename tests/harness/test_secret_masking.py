"""LOCUS-362 (P10): approved secret-bearing content is masked before the model sees it.

A real Gateway (FakeEngine allows every policy, so the risk class decides) and
the real CodingToolset over a LocalDirectExecutor.
"""

from __future__ import annotations

from pathlib import Path

from locus_runtime import gateway as gw
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from tests.gateway_support import FakeEngine
from tests.harness.conftest import requires_bash, requires_git

SECRET_TOML = "[db]\nuser = 'admin'\npassword = 'hunter2'\napi_token = \"tok-123456789\"\n"


def _toolset(root: Path) -> tuple[CodingToolset, gw.Gateway, gw.GatewaySession]:
    gateway = gw.Gateway(FakeEngine(), lambda _r: None)
    caps = gw.Capabilities(
        allowed_tools=frozenset({"read_file", "write_file", "process_exec"}),
        read_roots=(str(root),),
        write_roots=(str(root),),
        allowed_executables=("bash",),
    )
    session = gateway.open_session(
        run_id="run-mask", principal="alice", engine="harness", capabilities=caps
    )
    executor = LocalDirectExecutor(root, gateway_session=session)
    return (
        CodingToolset(workspace=Workspace(run_id="run-mask", executor=executor)),
        gateway,
        session,
    )


def _approve_last(gateway: gw.Gateway, toolset: CodingToolset) -> None:
    block = toolset.gateway_blocks[-1]
    assert block["outcome"] == "ask"
    gateway.approvals.approve("run-mask", block["fingerprint"], "alice")


def test_secret_like_view_asks_then_is_masked_after_approval(tmp_path: Path) -> None:
    (tmp_path / "secrets.toml").write_text(SECRET_TOML, encoding="utf-8")
    toolset, gateway, session = _toolset(tmp_path)

    first = toolset.dispatch("str_replace_editor", {"command": "view", "path": "secrets.toml"})
    assert first.startswith("[permission required]")
    assert "hunter2" not in first

    _approve_last(gateway, toolset)
    shown = toolset.dispatch("str_replace_editor", {"command": "view", "path": "secrets.toml"})

    assert shown.startswith(gw.SECRET_CONTENT_NOTICE)
    assert "hunter2" not in shown and "tok-123456789" not in shown and "admin" not in shown
    assert "password" in shown and "[db]" in shown
    assert toolset.telemetry.secret_reads_masked == 1
    assert session.tainted


def test_dotenv_view_is_denied_outright(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("API_TOKEN=LOCUS_CANARY_7f3a9c\n", encoding="utf-8")
    toolset, _gateway, _session = _toolset(tmp_path)
    out = toolset.dispatch("str_replace_editor", {"command": "view", "path": ".env"})
    assert out.startswith("[denied by policy]")
    assert "LOCUS_CANARY" not in out


@requires_bash
def test_shell_read_of_a_secret_file_is_masked_after_approval(tmp_path: Path) -> None:
    (tmp_path / "secrets.toml").write_text(SECRET_TOML, encoding="utf-8")
    toolset, gateway, _session = _toolset(tmp_path)
    first = toolset.dispatch("execute_bash", {"command": "cat secrets.toml"})
    assert "[permission required]" in first
    _approve_last(gateway, toolset)
    shown = toolset.dispatch("execute_bash", {"command": "cat secrets.toml"})
    assert gw.SECRET_CONTENT_NOTICE in shown
    assert "hunter2" not in shown and "tok-123456789" not in shown
    # cat .env is refused, never run.
    (tmp_path / ".env").write_text("API_TOKEN=LOCUS_CANARY_7f3a9c\n", encoding="utf-8")
    refused = toolset.dispatch("execute_bash", {"command": "cat .env"})
    assert "[denied by policy]" in refused and "LOCUS_CANARY" not in refused


@requires_bash
@requires_git
def test_submit_diff_masks_secret_file_hunks_but_keeps_the_patch(tmp_path: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "secrets.toml").write_text("password = 'old'\n", encoding="utf-8")
    git("init", "-q")
    git("-c", "user.email=t@e.x", "-c", "user.name=T", "add", "-A")
    git("-c", "user.email=t@e.x", "-c", "user.name=T", "commit", "-qm", "init")
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "secrets.toml").write_text("password = 'hunter2'\n", encoding="utf-8")
    toolset, _gateway, _session = _toolset(tmp_path)

    reply = toolset.dispatch("submit", {"answer": "done"})

    assert "+x = 2" in reply
    assert "hunter2" not in reply and "'old'" not in reply
    assert toolset.submission is not None
    assert "hunter2" in toolset.submission["patch"]  # delivery keeps the exact patch
