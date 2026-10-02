"""Phase-1 composer capability tests: working-folder resolution, option parsing,
per-chat MCP/skill filtering, reasoning_effort threading, and plan-mode gating.
No network required.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

main = pytest.importorskip("app.main", reason="backend not importable")
gc = pytest.importorskip("app.graph_compiler")
from frontier_runtime.harness.llm import ChatResponse, OpenAIChatClient  # noqa: E402


# --- user settings + composer options normalization --------------------------
def test_normalize_user_settings_coerces_unknowns():
    out = main._normalize_user_settings(
        {
            "default_mode": "bogus",
            "preferred_reasoning_effort": "ULTRA",
            "default_working_folder": " /projects/x ",
        }
    )
    assert out["default_mode"] == "execute"
    assert out["preferred_reasoning_effort"] == ""
    assert out["default_working_folder"] == "/projects/x"


def test_composer_options_parsing():
    opts = main._composer_options(
        {
            "model": "ollama/gpt-oss:20b",
            "reasoning_effort": "HIGH",
            "mode": "plan",
            "mcp_server_ids": ["A", "b"],
            "skill_ids": [],
            "workspace": {"repo_path": "/projects/x"},
        }
    )
    assert opts["model"] == "ollama/gpt-oss:20b"
    assert opts["reasoning_effort"] == "high"
    assert opts["mode"] == "plan"
    assert opts["mcp_server_ids"] == {"a", "b"}
    assert opts["skill_ids"] == set()
    assert opts["workspace"] == {"repo_path": "/projects/x"}


# --- working-folder resolution (escape-safe) ---------------------------------
def test_resolve_working_folder_confines_to_root(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_PROJECTS_ROOT", str(tmp_path))
    (tmp_path / "repo").mkdir()
    resolved = main._resolve_working_folder("repo")
    assert resolved == str((tmp_path / "repo").resolve())
    # escapes are rejected
    assert main._resolve_working_folder("../etc") is None
    assert main._resolve_working_folder("/etc/passwd") is None
    assert main._resolve_working_folder("") is None
    # a leading "projects/" prefix is tolerated
    assert main._resolve_working_folder("projects/repo") == str((tmp_path / "repo").resolve())


# --- per-chat skill filtering ------------------------------------------------
def test_skill_selection_filters(monkeypatch):
    from app.main import SkillDefinition

    skill = SkillDefinition(
        id="sk-test",
        name="Test Skill",
        description="",
        content="DO THE X PROCEDURE",
        status="enabled",
        auto_inject=False,
    )
    monkeypatch.setitem(main.store.skills, "sk-test", skill)

    # explicit selection includes a non-auto-inject skill
    out = main._augment_system_prompt_with_skills("BASE", selected_skill_ids={"sk-test"})
    assert "DO THE X PROCEDURE" in out
    # empty selection injects nothing
    assert "DO THE X PROCEDURE" not in main._augment_system_prompt_with_skills(
        "BASE", selected_skill_ids=set()
    )
    # default (None) respects auto_inject=False -> not injected
    assert "DO THE X PROCEDURE" not in main._augment_system_prompt_with_skills("BASE")


# --- reasoning_effort reaches the request body -------------------------------
def test_reasoning_effort_in_request_body(monkeypatch):
    captured: dict = {}

    class _M:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _M()

    class _Resp:
        choices = [_Choice()]
        usage = None

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        chat = _Chat()

    client = OpenAIChatClient(model="gpt-oss:20b", base_url="http://x/v1")
    monkeypatch.setattr(client, "_ensure_client", lambda: _FakeClient())
    client.complete([{"role": "user", "content": "hi"}], reasoning_effort="high")
    assert captured.get("extra_body", {}).get("reasoning_effort") == "high"


# --- plan mode withholds tools / code execution ------------------------------
def test_plan_mode_forces_chat_not_code():
    class _Node:
        id = "build"
        type = "frontier/agent"
        title = "Build"
        config = {"agent_id": "sdet", "phase": "build"}

    class _FakeClient:
        provider = "x"
        model = "m"

        def complete(self, messages, **kw):
            return ChatResponse(text="here is the execution plan")

    resolution = gc.AgentResolution(
        agent_id="sdet",
        system_prompt="sp",
        model="m",
        provider="ollama",
        base_url="http://x/v1",
        execution_mode="code",
    )
    deps = gc.CompilerDeps(
        resolve_agent=lambda cfg: resolution,
        make_chat_client=lambda r: _FakeClient(),
        execute_native=lambda *a: {},
        mode="plan",
    )
    out = gc._run_agent_node(
        _Node(), incoming=[], out_ports=[], state={"run_input": {"message": "do it"}}, deps=deps
    )
    assert out["mode"] == "live"  # chat path, not the SweAgent code path
    assert "patch" not in out  # no code delegation happened
