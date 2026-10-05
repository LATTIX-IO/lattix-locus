"""Narrow stdio MCP surface for Codex runs owned by the Locus loop.

Codex receives only these Locus tools. File and process actions are dispatched
through the normal ``CodingToolset`` and a run-scoped Locus gateway session.
The MCP server writes protocol messages to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from locus_runtime.gateway import (
    BudgetFigures,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    install_gateway,
)
from locus_runtime.harness.executor import LocalSandboxExecutor
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from locus_runtime.sandbox import IsolationStrategy, SandboxManager
from locus_runtime.win_toolchain import WindowsToolchain

_MAX_MESSAGE_BYTES = 2 * 1024 * 1024
_SUPPORTED_TOOLS = frozenset({"execute_bash", "search", "str_replace_editor", "run_tests"})
_PROTOCOL_VERSION = "2025-03-26"


class _JsonlAudit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, record: GatewayAuditRecord) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record.as_metadata(), sort_keys=True, default=str) + "\n")


def _capabilities(raw: Mapping[str, Any]) -> Capabilities:
    budget_raw = raw.get("budget")
    budget = BudgetFigures(**budget_raw) if isinstance(budget_raw, dict) else None
    return Capabilities(
        allowed_tools=frozenset(str(item) for item in raw.get("allowed_tools", [])),
        read_roots=tuple(str(item) for item in raw.get("read_roots", [])),
        write_roots=tuple(str(item) for item in raw.get("write_roots", [])),
        allowed_executables=tuple(str(item) for item in raw.get("allowed_executables", [])),
        allowed_egress_hosts=tuple(str(item) for item in raw.get("allowed_egress_hosts", [])),
        autonomy_tier=str(raw.get("autonomy_tier") or "tiered"),  # type: ignore[arg-type]
        max_tool_calls=int(raw.get("max_tool_calls") or 0),
        budget=budget,
        runtime_profile=str(raw.get("runtime_profile") or ""),
        data_classification=str(raw.get("data_classification") or ""),
        allowed_apps=tuple(str(item) for item in raw.get("allowed_apps", [])),
        denied_apps=tuple(str(item) for item in raw.get("denied_apps", [])),
    )


@dataclass
class McpToolServer:
    toolset: CodingToolset
    kill_switch_path: Path | None = None

    def __post_init__(self) -> None:
        self._schemas: dict[str, dict[str, Any]] = {}
        for wrapped in self.toolset.schemas():
            function = wrapped.get("function") if isinstance(wrapped, dict) else None
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "")
            if name not in _SUPPORTED_TOOLS:
                continue
            parameters = function.get("parameters")
            schema = parameters if isinstance(parameters, dict) else {"type": "object"}
            self._schemas[name] = {
                "name": name,
                "description": str(function.get("description") or "Locus coding tool"),
                "inputSchema": schema,
            }

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return None
        request_id = request.get("id")
        method = str(request.get("method") or "")
        raw_params = request.get("params")
        params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
        if method == "notifications/initialized" or method.startswith("notifications/"):
            return None
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = (
                requested if requested in {"2024-11-05", _PROTOCOL_VERSION} else _PROTOCOL_VERSION
            )
            return self._result(
                request_id,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "locus-loop-coding", "version": "1.0.0"},
                },
            )
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": list(self._schemas.values())})
        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments")
            if name not in self._schemas or not isinstance(arguments, dict):
                return self._error(request_id, -32602, "Unknown tool or invalid arguments")
            if self.kill_switch_path is not None and self.kill_switch_path.exists():
                return self._result(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": "[stopped] Locus loop kill switch is set"}
                        ],
                        "isError": True,
                    },
                )
            try:
                output = self.toolset.dispatch(name, arguments)
            except Exception as exc:  # noqa: BLE001 - never turn a tool failure into success
                return self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": f"[error] {type(exc).__name__}"}],
                        "isError": True,
                    },
                )
            return self._result(
                request_id,
                {
                    "content": [{"type": "text", "text": str(output)}],
                    "isError": str(output).startswith("[error]"),
                },
            )
        if request_id is None:
            return None
        return self._error(request_id, -32601, "Method not found")

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any] | None:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any] | None:
        if request_id is None:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def run_stdio(server: McpToolServer, *, stdin: Any = None, stdout: Any = None) -> int:
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    while True:
        line = source.readline(_MAX_MESSAGE_BYTES + 1)
        if not line:
            return 0
        if len(line) > _MAX_MESSAGE_BYTES:
            return 2
        try:
            request = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response: dict[str, Any] | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
        else:
            response = server.handle(request)
        if response is not None:
            sink.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
            sink.flush()


def _start(config_path: Path) -> tuple[Gateway, Any, CodingToolset, Path | None]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("invalid Locus MCP run configuration")
    workspace = Path(str(config.get("workspace") or "")).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("the Locus MCP workspace is unavailable")
    audit_path = Path(str(config.get("audit_path") or "")).resolve()
    strategy_value = str(config.get("isolation_strategy") or "")
    try:
        strategy = IsolationStrategy(strategy_value)
    except ValueError as exc:
        raise ValueError("unsupported Locus MCP sandbox strategy") from exc
    if strategy not in {
        IsolationStrategy.KERNEL_BWRAP,
        IsolationStrategy.KERNEL_SEATBELT,
        IsolationStrategy.WINDOWS_APPCONTAINER,
        IsolationStrategy.HARDENED_DOCKER,
    }:
        raise ValueError("Locus MCP requires an OS confinement tier")
    policy_config = config.get("policy_engine")
    if not isinstance(policy_config, dict) or policy_config.get("backend") != "opa-sidecar":
        raise ValueError("Codex MCP requires the configured OPA sidecar policy engine")
    from locus_runtime.policy_engine import OpaSidecarEngine, validate_loopback_url

    raw_opa_url = str(policy_config.get("opa_url") or "").strip()
    opa_url = validate_loopback_url(raw_opa_url) if raw_opa_url else ""
    engine = OpaSidecarEngine(
        base_url=opa_url or None,
        opa_binary=str(policy_config.get("opa_binary") or "") or None,
        policy_dir=Path(str(policy_config.get("policy_dir") or "")).resolve(strict=True),
    )
    start = getattr(engine, "start", None)
    if callable(start):
        start()
    gateway = Gateway(engine, _JsonlAudit(audit_path))
    if not gateway.healthy:
        raise RuntimeError("the Locus MCP policy gateway is unhealthy")
    install_gateway(gateway)
    caps_raw = config.get("capabilities")
    if not isinstance(caps_raw, dict):
        raise ValueError("missing Locus MCP run capabilities")
    session = gateway.open_session(
        run_id=str(config.get("run_id") or ""),
        principal=str(config.get("principal") or "locus-self-improvement-loop"),
        engine="codex-mcp-tools",
        capabilities=_capabilities(caps_raw),
    )
    executor = LocalSandboxExecutor(
        workspace,
        manager=SandboxManager(force_strategy=strategy),
        allow_network=False,
        gateway_session=session,
        toolchain=(
            WindowsToolchain(root=Path(str(config["toolchain_root"])))
            if str(config.get("toolchain_root") or "").strip()
            else None
        ),
    )
    if strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
        toolchain = executor.toolchain
        if toolchain is None or not toolchain.is_installed():
            raise RuntimeError("the Windows Locus toolchain is unavailable")
    toolset = CodingToolset(
        workspace=Workspace(run_id=session.caller.run_id, executor=executor),
        out_of_bounds="deny",
        bash_timeout=60,
        test_timeout=600,
    )
    raw_kill_path = str(config.get("kill_switch_path") or "").strip()
    kill_path = Path(raw_kill_path).resolve() if raw_kill_path else None
    return gateway, session, toolset, kill_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Locus coding tools for Codex via MCP stdio")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    gateway = None
    session = None
    try:
        gateway, session, toolset, kill_path = _start(args.config)
        return run_stdio(McpToolServer(toolset, kill_switch_path=kill_path))
    except Exception as exc:  # noqa: BLE001 - initialization failure is fail-closed
        print(f"Locus MCP server failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        if session is not None:
            session.close()
        if gateway is not None:
            install_gateway(None)
            close = getattr(gateway.engine, "close", None)
            if callable(close):
                close()


if __name__ == "__main__":
    raise SystemExit(main())
