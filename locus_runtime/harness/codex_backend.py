"""Drive OpenAI **Codex** as a subprocess coding backend.

Locus keeps orchestration / memory / security / diff capture; Codex does the
file work in its own OS sandbox. We invoke ``codex exec --json`` headlessly in a
bound git worktree, pointed at local gpt-oss via Codex's built-in ``--oss``
(Ollama) provider, and translate its JSONL ``ThreadEvent`` stream into
Locus run-events. The produced diff is captured by ``workspace.changed_files()``.

Wire schema (grounded in codex-rs/exec/src/exec_events.rs): each stdout line is a
``ThreadEvent`` ``{"type": "...", ...}`` where type is one of ``thread.started``,
``turn.started``, ``turn.completed`` (usage), ``turn.failed`` (error), ``error``,
``item.started`` / ``item.updated`` / ``item.completed`` (carry a ``ThreadItem``
``{id, type, ...}`` whose type is ``agent_message{text}``, ``reasoning{text}``,
``command_execution{command,aggregated_output,exit_code,status}``,
``file_change{changes:[{path,kind}],status}``, ``mcp_tool_call{...}``, ``error{message}``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from locus_runtime.gateway import GatewaySession, JailFacts, authorize_action, redact_text
from locus_runtime.model_client import (
    GatewayModelGate,
    ModelCall,
    ModelCallDenied,
    ModelEndpoint,
    ModelUsage,
    estimate_cost,
    resolve_endpoint,
)
from locus_runtime.sandbox import AGENT_ENV_ALLOWLIST, minimal_agent_env


@dataclass
class CodexResult:
    answer: str = ""
    reasoning: str = ""
    files: list[str] = field(default_factory=list)
    # completed | failed | unavailable | timeout | denied | approval_required
    outcome: str = "completed"
    gateway_reasons: list[str] = field(default_factory=list)
    gateway_audit_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int | None = None
    duration_seconds: float = 0.0
    usage_reported: bool = False


def map_thread_event(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Codex ``ThreadEvent`` JSON object to a normalized step
    ``{kind, ...}``, or None if it isn't worth surfacing. Pure + unit-testable.

    Only terminal ``item.completed`` items carry full payloads; ``item.started``/
    ``item.updated`` are interim and ignored (avoids duplicate/empty surfaces).
    """
    if not isinstance(ev, dict):
        return None
    etype = str(ev.get("type") or "")
    if etype == "item.completed":
        item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
        itype = str(item.get("type") or "")
        if itype == "agent_message":
            return {"kind": "agent_message", "text": str(item.get("text") or "")}
        if itype == "reasoning":
            # Hidden reasoning is neither telemetry nor a user-facing artifact.
            return None
        if itype == "command_execution":
            return {
                "kind": "command",
                "command": str(item.get("command") or ""),
                "exit_code": item.get("exit_code"),
                "status": str(item.get("status") or ""),
                "output": str(item.get("aggregated_output") or "")[:2000],
            }
        if itype == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            return {
                "kind": "file_change",
                "status": str(item.get("status") or ""),
                "files": [
                    str(c.get("path")) for c in changes if isinstance(c, dict) and c.get("path")
                ],
                "kinds": [str(c.get("kind")) for c in changes if isinstance(c, dict)],
            }
        if itype == "mcp_tool_call":
            return {
                "kind": "mcp_tool",
                "server": item.get("server"),
                "tool": item.get("tool"),
                "status": item.get("status"),
            }
        if itype == "error":
            message = str(item.get("message") or "")
            if (
                message.startswith("Model metadata for ")
                and "Defaulting to fallback metadata" in message
            ):
                return {"kind": "warning", "message": message[:500]}
            return {"kind": "error", "message": message}
        return None
    if etype == "turn.completed":
        return {
            "kind": "usage",
            "usage": ev.get("usage") if isinstance(ev.get("usage"), dict) else {},
        }
    if etype == "turn.failed":
        err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
        return {"kind": "error", "message": str(err.get("message") or "turn failed")}
    if etype == "error":
        return {"kind": "error", "message": str(ev.get("message") or "stream error")}
    return None  # thread.started / turn.started / item.started / item.updated


def _build_command(
    *,
    codex_bin: str | list[str],
    cwd: str,
    model: str,
    sandbox: str,
    last_message_file: str,
    config_overrides: dict[str, str],
) -> list[str]:
    args = [
        *(codex_bin if isinstance(codex_bin, list) else [codex_bin]),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--cd",
        str(cwd),
        "--sandbox",
        sandbox,
        "--oss",
        "-m",
        model,
        "-o",
        last_message_file,
    ]
    for key, value in (config_overrides or {}).items():
        args += ["-c", f"{key}={value}"]
    args += ["-"]  # read the prompt from stdin
    return args


def _toml_string(value: str) -> str:
    """JSON string escaping is a valid subset of TOML basic-string escaping."""
    return json.dumps(str(value), ensure_ascii=True)


def _build_gateway_command(
    *,
    codex_bin: str | list[str],
    cwd: str,
    model: str,
    last_message_file: str,
    python_bin: str,
    mcp_config_file: str,
    ollama_base_url: str,
) -> list[str]:
    mcp_table = (
        "{ command = "
        + _toml_string(python_bin)
        + ', args = ["-m", "locus_runtime.harness.codex_mcp_server", "--config", '
        + _toml_string(mcp_config_file)
        + "] }"
    )
    args = [
        *(codex_bin if isinstance(codex_bin, list) else [codex_bin]),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--cd",
        str(cwd),
        "--sandbox",
        "read-only",
        "--oss",
        "--local-provider",
        "ollama",
        "-m",
        model,
        "-o",
        last_message_file,
    ]
    # This mode is a meta-harness: Codex may only act through the Locus MCP
    # tools. Read-only sandboxing remains a second line of defense.
    for feature in (
        "shell_tool",
        "code_mode_host",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "apps",
        "plugins",
        "remote_plugin",
    ):
        args.extend(["--disable", feature])
    args.extend(
        [
            "-c",
            f"model_providers.oss.name={_toml_string('Local Ollama')}",
            "-c",
            f"model_providers.oss.base_url={_toml_string(ollama_base_url)}",
            "-c",
            "analytics.enabled=false",
            "-c",
            f"mcp_servers.locus={mcp_table}",
            "-",
        ]
    )
    return args


def _codex_command(value: str | None) -> list[str]:
    """Resolve npm's Windows shim to a directly launchable Node command."""
    requested = str(value or "codex").strip() or "codex"
    located = requested if Path(requested).is_file() else shutil.which(requested)
    if located and str(located).lower().endswith((".cmd", ".bat", ".ps1")):
        shim_dir = Path(located).resolve().parent
        node = shim_dir / ("node.exe" if os.name == "nt" else "node")
        if not node.is_file():
            node_path = shutil.which("node.exe") or shutil.which("node")
            node = Path(node_path) if node_path else node
        entry = shim_dir / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if node.is_file() and entry.is_file():
            return [str(node), str(entry)]
        powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
        if powershell and str(located).lower().endswith(".ps1"):
            return [powershell, "-NoProfile", "-NonInteractive", "-File", str(located)]
        raise FileNotFoundError("Codex npm shim has no directly launchable Node runtime")
    if located:
        return [str(Path(located).resolve())]
    if os.name == "nt":
        native = shutil.which("codex.exe")
        if native:
            return [native]
    return [requested]


def _validate_local_endpoint(base_url: str, model: str) -> ModelEndpoint:
    endpoint = resolve_endpoint("ollama", model, base_url=base_url)
    parsed = urlsplit(endpoint.base_url)
    if parsed.username or parsed.password or not endpoint.local:
        raise ValueError("Codex loop mode requires a credential-free loopback Ollama endpoint")
    return endpoint


def _policy_engine_config() -> dict[str, str]:
    """Pass only nonsecret, explicit OPA paths/loopback origin to the MCP child."""
    from locus_runtime.policy_engine import (
        default_policy_dir,
        find_opa_binary,
        validate_loopback_url,
    )

    raw_url = str(os.getenv("LOCUS_OPA_URL") or "").strip()
    return {
        "backend": str(os.getenv("LOCUS_POLICY_ENGINE") or "opa-sidecar").strip(),
        "opa_url": validate_loopback_url(raw_url) if raw_url else "",
        "opa_binary": str(find_opa_binary() or ""),
        "policy_dir": str(default_policy_dir()),
    }


def _capabilities_payload(
    session: GatewaySession,
    *,
    max_tool_calls: int | None = None,
    max_actions: int | None = None,
) -> dict[str, Any]:
    caps = session.capabilities
    budget = caps.budget
    return {
        "allowed_tools": sorted(caps.allowed_tools),
        "read_roots": list(caps.read_roots),
        "write_roots": list(caps.write_roots),
        "allowed_executables": list(caps.allowed_executables),
        "allowed_egress_hosts": list(caps.allowed_egress_hosts),
        "autonomy_tier": caps.autonomy_tier,
        "max_tool_calls": (
            max(0, int(max_tool_calls)) if max_tool_calls is not None else caps.max_tool_calls
        ),
        "max_actions": (max(0, int(max_actions)) if max_actions is not None else caps.max_actions),
        "budget": (
            {
                "tokens_used": budget.tokens_used,
                "max_tokens": budget.max_tokens,
                "duration_used_seconds": budget.duration_used_seconds,
                "max_duration_seconds": budget.max_duration_seconds,
                "cost_used_usd": budget.cost_used_usd,
                "max_cost_usd": budget.max_cost_usd,
            }
            if budget is not None
            else None
        ),
        "runtime_profile": caps.runtime_profile,
        "data_classification": caps.data_classification,
        "allowed_apps": list(caps.allowed_apps),
        "denied_apps": list(caps.denied_apps),
    }


def run_codex_with_locus_tools(
    *,
    prompt: str,
    cwd: str,
    runtime_dir: str,
    audit_path: str,
    kill_switch_path: str,
    run_id: str,
    isolation_strategy: str,
    gateway_session: GatewaySession,
    model: str = "gpt-oss:20b",
    ollama_base_url: str = "",
    toolchain_root: str = "",
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    timeout: int = 3600,
    max_steps: int | None = None,
    max_tokens: int | None = None,
    codex_bin: str | None = None,
    max_tool_calls: int | None = None,
    max_actions: int | None = None,
) -> CodexResult:
    """Run Codex as a local-Ollama agent whose only tools are Locus MCP tools.

    The Codex CLI is configured with its shell/code/browser surfaces disabled and
    read-only sandboxing enabled. Its stdio MCP server reconstructs the same run
    capabilities and dispatches each file/process action through the Locus
    gateway and OS sandbox. Codex itself receives a sanitized environment and an
    isolated, ephemeral CODEX_HOME.
    """
    result = CodexResult()
    model = model.split("/", 1)[-1] if "/" in model else model
    base_url = (
        str(
            ollama_base_url
            or os.getenv("CODEX_OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL")
            or ""
        )
        .strip()
        .rstrip("/")
    )
    if not base_url:
        base_url = "http://127.0.0.1:11434"
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    try:
        endpoint = _validate_local_endpoint(base_url, model)
    except Exception as exc:  # noqa: BLE001 - local model setup is fail-closed
        result.outcome = "unavailable"
        result.gateway_reasons = [redact_text(str(exc), limit=300)]
        return result
    try:
        root = Path(cwd).resolve(strict=True)
        isolated_root = Path(runtime_dir).resolve()
        if isolated_root == root or root in isolated_root.parents:
            raise ValueError("Codex runtime data must be outside the agent workspace")
        binary = _codex_command(codex_bin or os.getenv("CODEX_BIN"))
        isolated_root.mkdir(parents=True, exist_ok=True)
    except FileNotFoundError:
        result.outcome = "unavailable"
        result.gateway_reasons = ["Codex workspace, runtime, or executable is unavailable"]
        return result
    except (OSError, RuntimeError, ValueError) as exc:
        result.outcome = "failed"
        result.gateway_reasons = [redact_text(str(exc), limit=300)]
        return result
    call = ModelCall(
        provider="ollama",
        model=model,
        egress_host=endpoint.egress_host,
        tools=len(_SUPPORTED_CODING_TOOLS),
        run_id=run_id,
    )
    gate = GatewayModelGate(session=gateway_session)
    started = time.monotonic()
    try:
        audit_id = gate.authorize(call)
    except ModelCallDenied as exc:
        result.outcome = "denied"
        result.gateway_audit_id = exc.audit_id
        result.gateway_reasons = [redact_text(exc.reason, limit=300)]
        return result
    result.gateway_audit_id = audit_id

    strategy = str(isolation_strategy or "").strip()
    elapsed = 0.0

    with tempfile.TemporaryDirectory(prefix="codex-local-", dir=isolated_root) as temp_name:
        temp_root = Path(temp_name)
        home = temp_root / "home"
        temp = temp_root / "tmp"
        home.mkdir()
        temp.mkdir()
        mcp_config_path = temp_root / "mcp-run.json"
        mcp_config_path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "principal": gateway_session.caller.principal,
                    "workspace": str(root),
                    "audit_path": str(Path(audit_path).resolve()),
                    "isolation_strategy": strategy,
                    "toolchain_root": str(Path(toolchain_root).resolve()) if toolchain_root else "",
                    "kill_switch_path": str(Path(kill_switch_path).resolve()),
                    "policy_engine": _policy_engine_config(),
                    "capabilities": _capabilities_payload(
                        gateway_session,
                        max_tool_calls=max_tool_calls,
                        max_actions=max_actions,
                    ),
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        last_message = temp_root / "last-message.txt"
        stdout_path = temp_root / "codex-events.jsonl"
        stderr_path = temp_root / "codex-stderr.txt"
        args = _build_gateway_command(
            codex_bin=binary,
            cwd=str(root),
            model=model,
            last_message_file=str(last_message),
            python_bin=sys.executable,
            mcp_config_file=str(mcp_config_path),
            ollama_base_url=endpoint.base_url,
        )
        safe_base = {
            key: value for key, value in os.environ.items() if key.upper() in AGENT_ENV_ALLOWLIST
        }
        env = minimal_agent_env(
            {
                "CODEX_HOME": str(home),
                "HOME": str(home),
                "USERPROFILE": str(home),
                "APPDATA": str(home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "TEMP": str(temp),
                "TMP": str(temp),
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            },
            base=safe_base,
        )
        process = None
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = subprocess.Popen(
                    args,
                    cwd=str(root),
                    stdin=subprocess.PIPE,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                )
                pending_input: bytes | None = prompt.encode("utf-8")
                deadline = time.monotonic() + max(1, int(timeout))
                event_offset = 0
                event_remainder = b""
                observed_tokens = 0
                observed_turns = 0
                unauthorized_activity = False
                while process.poll() is None:
                    try:
                        if should_stop is not None and should_stop():
                            process.kill()
                            process.communicate()
                            result.outcome = "stopped"
                            break
                        if on_heartbeat is not None:
                            on_heartbeat()
                    except Exception:  # noqa: BLE001 - coordination failure stops the child
                        process.kill()
                        process.communicate()
                        result.outcome = "failed"
                        result.gateway_reasons = ["Locus run coordination failed"]
                        break
                    if (
                        stdout_path.stat().st_size > _MAX_CODEX_STDOUT_BYTES
                        or stderr_path.stat().st_size > _MAX_CODEX_STDERR_BYTES
                    ):
                        process.kill()
                        process.communicate()
                        result.outcome = "failed"
                        result.gateway_reasons = ["Codex output exceeded the run limit"]
                        break
                    try:
                        with stdout_path.open("rb") as event_stream:
                            event_stream.seek(event_offset)
                            event_bytes = event_stream.read()
                        event_offset += len(event_bytes)
                        buffered = event_remainder + event_bytes
                        lines = buffered.split(b"\n")
                        event_remainder = lines.pop()
                        for raw_line in lines:
                            try:
                                raw_event = json.loads(raw_line)
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                continue
                            if (
                                not isinstance(raw_event, dict)
                                or raw_event.get("type") != "turn.completed"
                            ):
                                mapped = (
                                    map_thread_event(raw_event)
                                    if isinstance(raw_event, dict)
                                    else None
                                )
                                if mapped and mapped.get("kind") in {"command", "file_change"}:
                                    unauthorized_activity = True
                                continue
                            observed_turns += 1
                            usage = raw_event.get("usage")
                            if isinstance(usage, dict):
                                observed_tokens += (
                                    _usage_int(usage, "input_tokens", "prompt_tokens") or 0
                                )
                                observed_tokens += (
                                    _usage_int(usage, "output_tokens", "completion_tokens") or 0
                                )
                        if max_steps is not None and observed_turns > max(0, int(max_steps)):
                            process.kill()
                            process.communicate()
                            result.outcome = "budget_exceeded"
                            result.gateway_reasons = ["Codex turn budget exceeded"]
                            break
                        if max_tokens is not None and observed_tokens > max(0, int(max_tokens)):
                            process.kill()
                            process.communicate()
                            result.outcome = "budget_exceeded"
                            result.gateway_reasons = ["Codex token budget exceeded"]
                            break
                        if unauthorized_activity:
                            process.kill()
                            process.communicate()
                            result.outcome = "denied"
                            result.gateway_reasons = [
                                "Codex attempted file or process work outside the Locus MCP tools"
                            ]
                            break
                    except OSError:
                        process.kill()
                        process.communicate()
                        result.outcome = "failed"
                        result.gateway_reasons = ["Codex event stream could not be read"]
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        process.kill()
                        process.communicate()
                        result.outcome = "timeout"
                        break
                    try:
                        process.communicate(input=pending_input, timeout=min(0.25, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pending_input = None
                result.exit_code = process.returncode
                elapsed = max(0.0, time.monotonic() - started)
                result.duration_seconds = elapsed
                if (
                    stdout_path.stat().st_size > _MAX_CODEX_STDOUT_BYTES
                    or stderr_path.stat().st_size > _MAX_CODEX_STDERR_BYTES
                ):
                    result.outcome = "failed"
                    result.gateway_reasons = ["Codex output exceeded the run limit"]
                else:
                    for line in stdout_path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines():
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        mapped = map_thread_event(event)
                        if not mapped:
                            continue
                        if mapped.get("kind") == "agent_message":
                            mapped["text"] = redact_text(
                                str(mapped.get("text") or ""), limit=80_000
                            )
                        elif mapped.get("kind") in {"command", "file_change"}:
                            result.outcome = "denied"
                            result.gateway_reasons = [
                                "Codex attempted file or process work outside the Locus MCP tools"
                            ]
                        result.events.append(mapped)
                        if on_event:
                            try:
                                on_event(str(mapped.get("kind") or ""), mapped)
                            except Exception:  # noqa: BLE001 - telemetry cannot change run outcome
                                pass
                        if mapped.get("kind") == "agent_message" and mapped.get("text"):
                            result.answer = str(mapped["text"])
                        elif mapped.get("kind") == "mcp_tool":
                            continue
                        elif mapped.get("kind") == "usage":
                            current = mapped.get("usage") or {}
                            result.usage = _sum_usage(result.usage, current)
                        elif mapped.get("kind") == "error" and result.outcome == "completed":
                            result.outcome = "failed"
                if result.outcome == "completed" and result.exit_code not in (0, None):
                    result.outcome = "failed"
                try:
                    last = last_message.read_text(encoding="utf-8").strip()
                    if last:
                        result.answer = redact_text(last, limit=80_000)
                except OSError:
                    # The JSONL agent-message stream remains available if this optional file was not written.
                    pass
                stderr_file.flush()
                result.duration_seconds = max(0.0, time.monotonic() - started)
        except FileNotFoundError:
            result.outcome = "unavailable"
        except subprocess.SubprocessError as exc:
            result.outcome = "failed"
            result.gateway_reasons = [redact_text(type(exc).__name__, limit=100)]
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    # The process may have exited between poll() and kill().
                    pass
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        # Preserve the original timeout result if the child already exited.
                        pass
                    try:
                        process.communicate()
                    except (OSError, subprocess.SubprocessError):
                        # Cleanup is best-effort after the bounded timeout has already fired.
                        pass
                except (OSError, subprocess.SubprocessError):
                    # Do not replace the backend outcome with a secondary drain failure.
                    pass

    result.duration_seconds = max(result.duration_seconds, elapsed)
    if result.outcome == "completed" and not result.answer:
        result.outcome = "failed"
    input_tokens = _usage_int(result.usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_int(result.usage, "output_tokens", "completion_tokens")
    result.usage_reported = input_tokens is not None and output_tokens is not None
    input_count = max(0, input_tokens or 0)
    output_count = max(0, output_tokens or 0)
    cost, cost_known = estimate_cost("ollama", model, input_count, output_count)
    gate.record(
        call,
        ModelUsage(
            provider="ollama",
            model=model,
            tokens_in=input_count,
            tokens_out=output_count,
            est_cost_usd=cost,
            cost_known=cost_known,
            usage_reported=result.usage_reported,
            audit_id=audit_id,
            duration_ms=int(result.duration_seconds * 1000),
            ok=result.outcome == "completed",
            run_id=run_id,
        ),
    )
    return result


def _usage_int(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        try:
            value = usage.get(key)
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def _sum_usage(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(current)
    for key, value in incoming.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = int(result.get(key) or 0) + int(value)
        else:
            result[key] = value
    return result


_SUPPORTED_CODING_TOOLS = ("execute_bash", "search", "str_replace_editor", "run_tests")
_MAX_CODEX_STDOUT_BYTES = 16 * 1024 * 1024
_MAX_CODEX_STDERR_BYTES = 2 * 1024 * 1024


def run_codex(
    *,
    prompt: str,
    cwd: str,
    model: str = "gpt-oss:20b",
    sandbox: str = "workspace-write",
    config_overrides: dict[str, str] | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    timeout: int = 900,
    codex_bin: str | None = None,
    gateway_session: GatewaySession | None = None,
) -> CodexResult:
    """Run ``codex exec --json`` in ``cwd`` and stream its events.

    Degrades to ``outcome="unavailable"`` if the codex binary is missing, so the
    caller can fall back to the native backend rather than crash.

    Launching the Codex engine is a gateway ``process_exec`` action (P6). Codex's
    own sandbox is not a jail Locus can verify, so the launch reports no jail
    facts; tool calls *inside* Codex do not pass the gateway (see LOCUS-332 notes).
    """
    binary = codex_bin or os.getenv("CODEX_BIN", "codex")
    model = model.split("/", 1)[-1] if "/" in model else model  # strip provider prefix
    overrides = dict(config_overrides or {})
    # Point Codex's built-in OSS provider at our Ollama endpoint when it isn't the
    # default localhost (e.g. a sidecar). Finalize the exact key at live smoke.
    base = os.getenv("CODEX_OLLAMA_BASE_URL") or os.getenv("OLLAMA_BASE_URL")
    if base and "localhost" not in base and "127.0.0.1" not in base:
        overrides.setdefault("model_providers.oss.base_url", f"{base.rstrip('/')}/v1")

    result = CodexResult()
    decision = authorize_action(
        gateway_session,
        kind="process_exec",
        tool="codex",
        target=str(cwd),
        command=f"codex exec --sandbox {sandbox} -m {model}",
        executable="codex",
        jail=JailFacts(strategy=f"codex-{sandbox}"),
    )
    if not decision.allowed:
        result.outcome = "approval_required" if decision.outcome == "ask" else "denied"
        result.gateway_reasons = list(decision.reasons)
        result.gateway_audit_id = decision.audit_id
        return result
    last_msg_path = ""
    try:
        fd, last_msg_path = tempfile.mkstemp(prefix="codex-last-", suffix=".txt")
        os.close(fd)
        cmd = _build_command(
            codex_bin=binary,
            cwd=cwd,
            model=model,
            sandbox=sandbox,
            last_message_file=last_msg_path,
            config_overrides=overrides,
        )
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            result.outcome = "unavailable"
            return result

        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass

        deadline = time.time() + timeout
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.time() > deadline:
                proc.kill()
                result.outcome = "timeout"
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            mapped = map_thread_event(ev)
            if not mapped:
                continue
            result.events.append(mapped)
            if on_event:
                try:
                    on_event(mapped["kind"], mapped)
                except Exception:  # noqa: BLE001
                    pass
            kind = mapped["kind"]
            if kind == "agent_message" and mapped.get("text"):
                result.answer = mapped["text"]
            elif kind == "reasoning" and mapped.get("text"):
                result.reasoning = (result.reasoning + "\n" + mapped["text"]).strip()
            elif kind == "file_change":
                result.files.extend(mapped.get("files") or [])
            elif kind == "usage":
                result.usage = mapped.get("usage") or {}
            elif kind == "error" and result.outcome == "completed":
                result.outcome = "failed"

        try:
            result.exit_code = proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        # Prefer the explicit last-message file for the final answer.
        try:
            text = Path(last_msg_path).read_text(encoding="utf-8").strip()
            if text:
                result.answer = text
        except Exception:  # noqa: BLE001
            pass
        if result.exit_code not in (0, None) and result.outcome == "completed":
            result.outcome = "failed"
        return result
    finally:
        if last_msg_path:
            try:
                os.unlink(last_msg_path)
            except OSError:
                pass
