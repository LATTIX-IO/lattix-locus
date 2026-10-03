"""LOCUS-332: no execution entry point bypasses the gateway PEP (P6).

Two layers:

* **dynamic** -- every executor class's side-effecting methods, the coding
  toolset, the Codex launcher and the MCP client are driven with a spy
  authorizer; each must consult the gateway first and must not spawn a process
  or touch a file when the gateway does not allow it.
* **static** -- an AST scan of the harness modules: process spawns, network
  clients and file writes may appear only inside the known private sinks, and
  each sink may only be called from a function that calls ``_gate`` first.

Adding a new executor, sink or spawn site without gating fails this test.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.harness import codex_backend, executor as executor_module
from locus_runtime.harness.executor import (
    GATEWAY_BLOCKED_EXIT_CODE,
    DockerContainerExecutor,
    LocalDirectExecutor,
    LocalSandboxExecutor,
)
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from locus_runtime.sandbox import IsolationStrategy, SandboxManager
from tests.gateway_support import FixedAuthorizer, installed

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "locus_runtime" / "harness"


class _Spawns:
    """Records subprocess spawns (and the order relative to gateway calls)."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def run(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        self.log.append("spawn")
        return subprocess.CompletedProcess(
            args=args[0] if args else [], returncode=0, stdout="", stderr=""
        )

    def popen(self, *args: Any, **kwargs: Any) -> Any:
        self.log.append("spawn")
        raise FileNotFoundError("spawn blocked in test")


class _OrderedAuthorizer(FixedAuthorizer):
    def __init__(self, outcome: gw.Outcome, log: list[str]) -> None:
        super().__init__(outcome)
        self.log = log

    def authorize(self, action: gw.GatewayAction) -> gw.GatewayDecision:
        self.log.append(f"gate:{action.kind}")
        return super().authorize(action)


@pytest.fixture()
def spawn_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    log: list[str] = []
    spawns = _Spawns(log)
    monkeypatch.setattr(executor_module.subprocess, "run", spawns.run)
    monkeypatch.setattr(codex_backend.subprocess, "Popen", spawns.popen)
    return log


def _executors(tmp_path: Path) -> list[Any]:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    manager = SandboxManager(force_strategy=IsolationStrategy.HARDENED_DOCKER)
    return [
        LocalDirectExecutor(tmp_path),
        LocalSandboxExecutor(tmp_path, manager=manager),
        DockerContainerExecutor("container-1", workdir_path="/testbed"),
    ]


def test_every_executor_class_is_gated() -> None:
    """Enumerate executor classes: anything with ``run`` must be a gated executor."""
    classes = [
        obj
        for _, obj in inspect.getmembers(executor_module, inspect.isclass)
        if obj.__module__ == executor_module.__name__
        and hasattr(obj, "run")
        and hasattr(obj, "write_file")
        and not getattr(obj, "_is_protocol", False)
    ]
    assert {cls.__name__ for cls in classes} == {
        "LocalDirectExecutor",
        "LocalSandboxExecutor",
        "DockerContainerExecutor",
    }, "a new executor class must be added to this test and gated"
    for cls in classes:
        assert issubclass(cls, executor_module._GatedExecutor), cls.__name__


@pytest.mark.parametrize("outcome", ["deny", "ask"])
def test_blocked_actions_never_execute(
    tmp_path: Path, spawn_log: list[str], outcome: gw.Outcome
) -> None:
    for ex in _executors(tmp_path):
        authorizer = _OrderedAuthorizer(outcome, spawn_log)
        with installed(authorizer):
            res = ex.run(["git", "status"])
            assert res.exit_code == GATEWAY_BLOCKED_EXIT_CODE and res.gateway is not None
            assert res.gateway.outcome == outcome
            res = ex.run_shell("echo hi")
            assert res.exit_code == GATEWAY_BLOCKED_EXIT_CODE
            with pytest.raises(gw.GatewayBlocked):
                ex.write_file("b.txt", "data")
            with pytest.raises(gw.GatewayBlocked):
                ex.read_file("a.txt")
        kinds = [action.kind for action in authorizer.actions]
        assert kinds[:4] == ["process_exec", "process_exec", "file_write", "file_read"], ex.backend
        assert "spawn" not in spawn_log, ex.backend
    assert not (tmp_path / "b.txt").exists()


def test_allowed_actions_gate_before_executing(tmp_path: Path, spawn_log: list[str]) -> None:
    for ex in _executors(tmp_path):
        spawn_log.clear()
        authorizer = _OrderedAuthorizer("allow", spawn_log)
        with installed(authorizer):
            ex.run(["git", "status"])
            ex.run_shell("echo hi")
        assert spawn_log == ["gate:process_exec", "spawn", "gate:process_exec", "spawn"], ex.backend


def test_local_file_writes_are_gated_with_real_io(tmp_path: Path) -> None:
    ex = LocalDirectExecutor(tmp_path)
    authorizer = FixedAuthorizer("allow")
    with installed(authorizer):
        ex.write_file("w.txt", "x")
        assert ex.read_file("w.txt") == "x"
    assert [(a.kind, Path(a.target).name) for a in authorizer.actions] == [
        ("file_write", "w.txt"),
        ("file_read", "w.txt"),
    ]


def test_toolset_returns_typed_results_and_tags_the_tool(
    tmp_path: Path, spawn_log: list[str]
) -> None:
    (tmp_path / "f.py").write_text("x = 1\n", encoding="utf-8")
    toolset = CodingToolset(workspace=Workspace(run_id="t", executor=LocalDirectExecutor(tmp_path)))
    authorizer = FixedAuthorizer("deny")
    with installed(authorizer):
        bash = toolset.dispatch("execute_bash", {"command": "git push"})
        edit = toolset.dispatch(
            "str_replace_editor", {"command": "create", "path": "n.py", "file_text": "y"}
        )
        view = toolset.dispatch("str_replace_editor", {"command": "view", "path": "f.py"})
    for out in (bash, edit, view):
        assert out.startswith("[denied by policy]"), out
    assert [a.tool for a in authorizer.actions] == [
        "execute_bash",
        "str_replace_editor",
        "str_replace_editor",
    ]
    assert toolset.telemetry.gateway_denied == 3
    assert not (tmp_path / "n.py").exists()
    assert "spawn" not in spawn_log
    with installed(FixedAuthorizer("ask")):
        asked = toolset.dispatch("execute_bash", {"command": "git push"})
    assert asked.startswith("[permission required]")
    assert "NOT executed" in asked


def test_codex_launch_is_gated(spawn_log: list[str]) -> None:
    authorizer = _OrderedAuthorizer("deny", spawn_log)
    with installed(authorizer):
        result = codex_backend.run_codex(prompt="do it", cwd=str(REPO))
    assert result.outcome == "denied"
    assert spawn_log == ["gate:process_exec"]


def test_mcp_call_tool_requires_a_matching_allow_decision() -> None:
    from app import mcp_client

    client = mcp_client.McpHttpClient("http://127.0.0.1:9/mcp")
    with pytest.raises(mcp_client.McpGatewayRequired):
        client.call_tool("list_issues", {})
    forged = gw.GatewayDecision(
        outcome="allow",
        reasons=(),
        audit_id="x",
        policy_version="v",
        action_kind="mcp_tool_call",
        tool="list_issues",
    )
    with pytest.raises(mcp_client.McpGatewayRequired):
        client.call_tool("list_issues", {}, decision=forged)
    with installed(FixedAuthorizer("allow")):
        decision = gw.authorize_action(
            None, kind="mcp_tool_call", tool="srv__list_issues", target="127.0.0.1", args={"q": "a"}
        )
    with pytest.raises(mcp_client.McpGatewayRequired):  # different arguments
        client.call_tool("list_issues", {"q": "b"}, decision=decision)
    with pytest.raises(mcp_client.McpGatewayRequired):  # different tool
        client.call_tool("delete_repo", {"q": "a"}, decision=decision)


# --- static scan ----------------------------------------------------------------------

#: Side-effect calls that must live in a gated sink.
_SPAWN_ATTRS = {
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_output"),
    ("subprocess", "check_call"),
    ("os", "system"),
    ("os", "popen"),
    ("os", "execv"),
    ("os", "execvp"),
    ("os", "spawnv"),
    ("shutil", "rmtree"),
}
_NETWORK_MODULES = {"httpx", "requests", "urllib", "socket"}
_WRITE_ATTRS = {"write_text", "write_bytes", "unlink", "rmdir", "rename"}

#: (module, function) -> why it may contain a raw side effect.
ALLOWED_SINKS: dict[tuple[str, str], str] = {
    ("executor.py", "LocalDirectExecutor._spawn"): "gated sink: called only after _gate",
    ("executor.py", "LocalDirectExecutor._write_bytes"): "gated sink: called only after _gate",
    ("executor.py", "LocalSandboxExecutor._spawn"): "gated sink: called only after _gate",
    ("executor.py", "DockerContainerExecutor._spawn"): "gated sink: called only after _gate",
    ("codex_backend.py", "run_codex"): "gated in-function: authorize_action precedes Popen",
    ("workspace_binding.py", "_git"): "platform provisioning (git worktree) before the agent runs",
    ("workspace_binding.py", "WorkspaceManager._remove_worktree"): "platform cleanup after the run",
    (
        "trajectory.py",
        "TrajectoryRecorder.__post_init__",
    ): "platform telemetry file, not an agent action",
    ("trajectory.py", "TrajectoryRecorder._emit"): "platform telemetry file, not an agent action",
}
_GATED_SINK_NAMES = {"_spawn", "_write_bytes", "_read_text"}


def _qualified_functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef]]:
    out: list[tuple[str, ast.FunctionDef]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    out.append((f"{node.name}.{item.name}", item))
    return out


def _side_effects(func: ast.FunctionDef) -> list[str]:
    found: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute):
            base = target.value
            base_name = base.id if isinstance(base, ast.Name) else ""
            if (base_name, target.attr) in _SPAWN_ATTRS or base_name in _NETWORK_MODULES:
                found.append(f"{base_name}.{target.attr}")
            elif target.attr in _WRITE_ATTRS:
                found.append(f".{target.attr}")
            elif target.attr == "open" and _opens_for_write(node):
                found.append(".open(w)")
        elif isinstance(target, ast.Name) and target.id == "open" and _opens_for_write(node):
            found.append("open(w)")
    return found


def _opens_for_write(call: ast.Call) -> bool:
    modes = [
        arg for arg in call.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    modes += [
        kw.value for kw in call.keywords if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
    ]
    return any(any(flag in str(m.value) for flag in "wax+") for m in modes)


def test_harness_side_effects_live_only_in_known_sinks() -> None:
    violations: list[str] = []
    for path in sorted(HARNESS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qualname, func in _qualified_functions(tree):
            effects = _side_effects(func)
            if effects and (path.name, qualname) not in ALLOWED_SINKS:
                violations.append(f"{path.name}:{qualname} -> {sorted(set(effects))}")
    assert not violations, "ungated side effects (route them through the gateway):\n" + "\n".join(
        violations
    )


def test_gated_sinks_are_only_called_after_gate() -> None:
    violations: list[str] = []
    for path in sorted(HARNESS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qualname, func in _qualified_functions(tree):
            if func.name in _GATED_SINK_NAMES:
                continue
            gate_lines = [
                node.lineno
                for node in ast.walk(func)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_gate"
            ]
            for node in ast.walk(func):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _GATED_SINK_NAMES
                    and not any(line < node.lineno for line in gate_lines)
                ):
                    violations.append(
                        f"{path.name}:{qualname} calls {node.func.attr} at line {node.lineno} without _gate first"
                    )
    assert not violations, "\n".join(violations)


def test_codex_launch_gates_before_popen() -> None:
    source = inspect.getsource(codex_backend.run_codex)
    assert source.index("authorize_action(") < source.index("subprocess.Popen(")


def test_allowed_sinks_exist() -> None:
    """Keep the allowlist honest: every entry names a real function."""
    present = {
        (path.name, qualname)
        for path in HARNESS.glob("*.py")
        for qualname, _ in _qualified_functions(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert set(ALLOWED_SINKS) <= present, set(ALLOWED_SINKS) - present
