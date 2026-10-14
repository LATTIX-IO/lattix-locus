"""Versioned OpenCode CLI admission and event contracts (LOCUS-416).

This module compiles an operator-approved, fail-closed execution profile. It
does NOT launch a process or treat OpenCode's own permissions as an OS sandbox.
An isolated, attested runner and Locus Gateway MCP broker must execute the
plan; external CLI commands and model-supplied environment are never accepted.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Mapping, cast

_VERSION = re.compile(r"v?([12])\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,4})\Z", re.ASCII)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MODEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:+/-]{0,159}\Z")
_TOOL = re.compile(r"locus_[a-z][a-z0-9_]{0,63}\Z", re.ASCII)
_MAX_OUTPUT = 8 * 1024 * 1024
_MAX_EVENT_LINE = 64 * 1024
_MAX_EVENTS = 4096
_RUN_MESSAGE = "Follow the attached task using only the approved Locus MCP tools."
_IGNORED_EVENTS = frozenset({"step_start", "step-start", "reasoning"})
_FINISH_EVENTS = frozenset({"step_finish", "step-finish"})


class OpenCodeContractDenied(ValueError):
    """Untrusted engine, configuration, result or execution boundary denied."""


@dataclass(frozen=True, slots=True)
class OpenCodeVersion:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw: str) -> OpenCodeVersion:
        if not isinstance(raw, str):
            raise OpenCodeContractDenied("OpenCode version must be canonical")
        match = _VERSION.fullmatch(raw)
        if match is None:
            raise OpenCodeContractDenied("unknown OpenCode CLI version")
        version = cls(*(int(v) for v in match.groups()))
        # Earlier V1 releases had incompatible tool permission semantics.
        if version.major == 1 and (version.minor, version.patch) < (1, 1):
            raise OpenCodeContractDenied("OpenCode version predates audited permission API")
        return version

    @property
    def dialect(self) -> str:
        return "v2" if self.major == 2 else "v1"


@dataclass(frozen=True, slots=True)
class OpenCodeLaunchProfile:
    """Immutable plan bound to a pinned executable and accepted Gateway toolset.

    Paths refer to pre-staged, read-only files inside the future isolated
    runner. This does not attest that the files or OS sandbox exist.
    """

    version: OpenCodeVersion
    platform: Literal["posix", "windows"]
    executable: str
    executable_sha256: str
    model: str
    workspace: str
    private_root: str
    request_file: str
    mcp_python: str
    mcp_config_file: str
    allowed_mcp_tools: tuple[str, ...]
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...]
    config_json: str
    config_sha256: str

    def config(self) -> dict[str, Any]:
        parsed: object = json.loads(self.config_json)
        if not isinstance(parsed, dict) or any(
            not isinstance(key, str) for key in parsed
        ):
            raise OpenCodeContractDenied("invalid OpenCode config object")
        return cast(dict[str, Any], parsed)

    def environment(self) -> dict[str, str]:
        return dict(self.env)


def _absolute_path(
    value: str, *, name: str, platform: Literal["posix", "windows"],
) -> PurePath:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4096
        or any(ord(ch) < 32 for ch in value)
    ):
        raise OpenCodeContractDenied(f"invalid {name}")
    if platform == "windows":
        # UNC shares and device paths cannot become trusted mount sources.
        if value.startswith("\\") or "/" in value:
            raise OpenCodeContractDenied(f"untrusted Windows {name} path")
        path: PurePath = PureWindowsPath(value)
    else:
        if "\\" in value:
            raise OpenCodeContractDenied(f"untrusted POSIX {name} path")
        path = PurePosixPath(value)
    if (not path.is_absolute() or str(path) != value
        or ".." in path.parts or "." in path.parts):
        raise OpenCodeContractDenied(f"{name} must be a canonical absolute path")
    return path


def compile_opencode_launch(
    *,
    version: str,
    executable: str,
    platform: Literal["posix", "windows"] = "posix",
    executable_sha256: str,
    model: str,
    workspace: str,
    private_root: str,
    request_file: str,
    mcp_python: str,
    mcp_config_file: str,
    allowed_mcp_tools: tuple[str, ...] = (),
) -> OpenCodeLaunchProfile:
    """Build version-specific config/argv, with no process/side effect.

    The operator independently pins the CLI digest, approves the provider
    through the model Gateway and stages files under a private runtime root.
    No user prompt is put in an argv string, shell command or environment.
    """
    parsed = OpenCodeVersion.parse(version)
    if platform not in ("posix", "windows"):
        raise OpenCodeContractDenied("unsupported engine platform")
    if not isinstance(executable_sha256, str) or not _DIGEST.fullmatch(executable_sha256):
        raise OpenCodeContractDenied("pinned OpenCode executable SHA-256 required")
    if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
        raise OpenCodeContractDenied("model must be a bounded provider/model identifier")
    binary = _absolute_path(executable, name="executable", platform=platform)
    expected_name = "opencode.exe" if platform == "windows" else "opencode"
    if binary.name.lower() != expected_name:
        raise OpenCodeContractDenied("CLI executable must be a pinned OpenCode binary")
    root = _absolute_path(private_root, name="private_root", platform=platform)
    work = _absolute_path(workspace, name="workspace", platform=platform)
    request = _absolute_path(request_file, name="request_file", platform=platform)
    python = _absolute_path(mcp_python, name="mcp_python", platform=platform)
    mcp_cfg = _absolute_path(mcp_config_file, name="mcp_config_file", platform=platform)
    if work == root or work in root.parents or root in work.parents:
        raise OpenCodeContractDenied("private runtime root must be isolated from workspace")
    if root not in request.parents or root not in mcp_cfg.parents:
        raise OpenCodeContractDenied("request and MCP manifests must remain in private root")
    if request == mcp_cfg:
        raise OpenCodeContractDenied("request and MCP manifest must be separate files")
    if (
        not isinstance(allowed_mcp_tools, tuple)
        or len(allowed_mcp_tools) > 32
        or any(not isinstance(t, str) or _TOOL.fullmatch(t) is None
               for t in allowed_mcp_tools)
        or len(set(allowed_mcp_tools)) != len(allowed_mcp_tools)
    ):
        raise OpenCodeContractDenied("noncanonical or duplicate Locus MCP grant")
    tools = tuple(sorted(allowed_mcp_tools))
    provider_id = model.split("/", 1)[0]
    mcp_cmd = [
        str(python), "-m", "locus_runtime.harness.pod_mcp_stdio_proxy",
        "--config", str(mcp_cfg),
    ]
    if parsed.major == 1:
        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "model": model,
            "formatter": False,
            "lsp": False,
            "snapshot": False,
            "enabled_providers": [provider_id],
            "permission": {
                "*": "deny",
                **{name: "allow" for name in tools},
            },
            "default_agent": "locus",
            "agent": {"locus": {
                "description": "Locus approved external worker",
                "mode": "primary",
                "permission": {"*": "deny", **{name: "allow" for name in tools}},
            }},
            "mcp": (
                {"locus": {"type": "local", "command": mcp_cmd, "enabled": True}}
                if tools else {}
            ),
        }
    else:
        # V2 uses ordered rules, "permissions", and "mcp.servers". Code Mode
        # would expose a code execution tool, so explicitly disable it.
        rules = [
            {"action": "*", "resource": "*", "effect": "deny"},
            *[
                {"action": name, "resource": "*", "effect": "allow"}
                for name in tools
            ],
        ]
        hard_rules = [
            # Providers are a separate trust boundary from tool actions.
            {"action": "provider.use", "resource": "*", "effect": "deny"},
            {"action": "provider.use", "resource": provider_id, "effect": "allow"},
            {"action": "permission", "resource": "*", "effect": "deny"},
            *[
                {"action": "permission", "resource": f"{name}:*", "effect": "allow"}
                for name in tools
            ],
        ]
        config = {
            "$schema": "https://opencode.ai/config.json",
            "update": "disable",
            "share": "disabled",
            "model": model,
            "formatter": False,
            "lsp": False,
            "snapshots": False,
            "permissions": rules,
            "default_agent": "locus",
            "agents": {"locus": {
                "description": "Locus approved external worker",
                "mode": "primary",
                "permissions": list(rules),
            }},
            "experimental": {"policies": hard_rules},
            "mcp": (
                {"servers": {"locus": {
                    "type": "local", "command": mcp_cmd,
                    "codemode": False,
                }}}
                if tools else {"servers": {}}
            ),
        }
    serialized = json.dumps(config, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    # The future runner must use an isolated container/process and audit its
    # EFFECTIVE config; setting these flags alone does not prove isolation.
    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "OPENCODE_CONFIG_CONTENT": serialized,
        "OPENCODE_CONFIG_DIR": str(root / "opencode-config"),
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
    }
    if platform == "windows":
        env.update({
            "USERPROFILE": str(root / "home"),
            "APPDATA": str(root / "appdata"),
            "LOCALAPPDATA": str(root / "localappdata"),
            "TEMP": str(root / "tmp"),
            "TMP": str(root / "tmp"),
        })
    return OpenCodeLaunchProfile(
        version=parsed,
        platform=platform,
        executable=str(binary),
        executable_sha256=executable_sha256,
        model=model,
        workspace=str(work),
        private_root=str(root),
        request_file=str(request),
        mcp_python=str(python),
        mcp_config_file=str(mcp_cfg),
        allowed_mcp_tools=tools,
        argv=(
            str(binary), "run",
            *(("--standalone",) if parsed.major == 2 else ()),
            "--format", "json", "--model", model,
            "--agent", "locus", "--file", str(request), _RUN_MESSAGE,
        ),
        env=tuple(sorted(env.items())),
        config_json=serialized,
        config_sha256=hashlib.sha256(serialized.encode("ascii")).hexdigest(),
    )


def validate_effective_opencode_config(
    profile: OpenCodeLaunchProfile, effective: Mapping[str, Any],
) -> None:
    """Deny external/project/plugin/agent overlays that can restore authority.

    Feed the resolved configuration from an independently sandboxed CLI
    preflight. Do NOT accept a config echoed by the model or the pod itself.
    The check is deliberately conservative; unknown settings require review.
    """
    if not isinstance(profile, OpenCodeLaunchProfile) or not isinstance(effective, Mapping):
        raise OpenCodeContractDenied("trusted profile and effective config required")
    expected = profile.config()
    for section in (
        "permission", "permissions", "mcp", "experimental",
        "agent", "agents", "default_agent", "formatter", "lsp", "snapshots",
        "snapshot", "enabled_providers",
    ):
        if effective.get(section) != expected.get(section):
            raise OpenCodeContractDenied("effective OpenCode policy was overridden")
    # The effective provider must be explicit: absent model identity could
    # silently fall back to a globally configured unapproved provider.
    if effective.get("model") != profile.model:
        raise OpenCodeContractDenied("resolved model differs from approved provider")
    # The installed CLI must not load extra plugins, agents, tools, skills,
    # formatters or project instructions capable of overriding this profile.
    for key in ("plugin", "plugins", "tools", "instructions",
                "formatter", "lsp", "command", "commands", "skills"):
        if effective.get(key):
            raise OpenCodeContractDenied("untrusted OpenCode extension in effective config")
    if profile.version.major == 1:
        if effective.get("autoupdate") is not False:
            raise OpenCodeContractDenied("OpenCode automatic update must be disabled")
    elif effective.get("update") != "disable":
        raise OpenCodeContractDenied("OpenCode V2 update policy must be disabled")
    if effective.get("share") != "disabled":
        raise OpenCodeContractDenied("OpenCode public sharing must be disabled")


@dataclass(frozen=True, slots=True)
class OpenCodeEventEvidence:
    """Untrusted engine output summary; not an independently verified artifact."""

    session_id: str
    answer_sha256: str
    tokens_in: int
    tokens_out: int
    completed_tools: tuple[str, ...]
    event_count: int
    state: str = "complete_unverified"


def audit_opencode_jsonl(
    raw: bytes, *,
    allowed_mcp_tools: tuple[str, ...],
    max_tokens: int,
) -> OpenCodeEventEvidence:
    """Conservatively parse bounded JSONL without exposing content or reasoning.

    Native shell/file/web/skill/plugin events are denied even if the CLI
    returns process exit 0. The returned digest is only a *claim*; it must
    never satisfy the independent verification gate by itself.
    """
    if type(max_tokens) is not int or not 1 <= max_tokens <= 10_000_000:
        raise OpenCodeContractDenied("token budget must be a positive bounded integer")
    if (
        not isinstance(allowed_mcp_tools, tuple)
        or any(not isinstance(t, str) or _TOOL.fullmatch(t) is None
               for t in allowed_mcp_tools)
        or len(set(allowed_mcp_tools)) != len(allowed_mcp_tools)
    ):
        raise OpenCodeContractDenied("untrusted event tool allowance")
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_OUTPUT:
        raise OpenCodeContractDenied("OpenCode event output unavailable or unbounded")
    lines = raw.splitlines()
    if len(lines) > _MAX_EVENTS:
        raise OpenCodeContractDenied("OpenCode event count exceeded")
    seen_session = ""
    answer = hashlib.sha256()
    answer_parts = 0
    final = False
    tokens_in = 0
    tokens_out = 0
    tools: list[str] = []
    calls: set[str] = set()
    allowed = frozenset(allowed_mcp_tools)
    for line in lines:
        if not line or len(line) > _MAX_EVENT_LINE or final:
            raise OpenCodeContractDenied("invalid or trailing OpenCode event")
        try:
            event = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OpenCodeContractDenied("malformed OpenCode JSONL event") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise OpenCodeContractDenied("malformed OpenCode event envelope")
        session = event.get("sessionID")
        if not isinstance(session, str) or not 1 <= len(session) <= 128:
            raise OpenCodeContractDenied("missing or invalid OpenCode session identity")
        if seen_session and seen_session != session:
            raise OpenCodeContractDenied("cross-session OpenCode event mixture")
        seen_session = session
        typ = event["type"]
        part = event.get("part")
        if typ in _IGNORED_EVENTS:
            continue
        if typ == "text":
            if not isinstance(part, dict) or part.get("type") != "text":
                raise OpenCodeContractDenied("malformed OpenCode text event")
            text = part.get("text")
            if not isinstance(text, str) or not text or len(text) > _MAX_EVENT_LINE:
                raise OpenCodeContractDenied("invalid OpenCode text payload")
            answer.update(text.encode("utf-8"))
            answer_parts += 1
            continue
        if typ in {"tool", "tool_use"}:
            if not isinstance(part, dict) or part.get("type") != "tool":
                raise OpenCodeContractDenied("malformed OpenCode tool event")
            name = part.get("tool")
            call_id = part.get("callID")
            state = part.get("state")
            if (
                not isinstance(name, str) or name not in allowed
                or not isinstance(call_id, str) or not 1 <= len(call_id) <= 128
                or call_id in calls
                or not isinstance(state, dict) or state.get("status") != "completed"
            ):
                raise OpenCodeContractDenied("unauthorized or ambiguous OpenCode tool event")
            calls.add(call_id)
            tools.append(name)
            continue
        if typ in _FINISH_EVENTS:
            if not isinstance(part, dict) or part.get("type") not in _FINISH_EVENTS:
                raise OpenCodeContractDenied("malformed OpenCode terminal step")
            usage = part.get("tokens")
            if not isinstance(usage, dict):
                raise OpenCodeContractDenied("missing OpenCode usage counters")
            a, b = usage.get("input"), usage.get("output")
            if (
                type(a) is not int or type(b) is not int
                or a < 0 or b < 0
            ):
                raise OpenCodeContractDenied("invalid OpenCode token usage")
            tokens_in += a
            tokens_out += b
            if tokens_in + tokens_out > max_tokens:
                raise OpenCodeContractDenied("OpenCode token budget exceeded")
            if part.get("reason") == "stop":
                final = True
            elif part.get("reason") not in {"tool-calls", "length", "max-tokens"}:
                raise OpenCodeContractDenied("unknown OpenCode completion reason")
            continue
        raise OpenCodeContractDenied("unrecognized or denied OpenCode event")
    if not final or answer_parts == 0:
        raise OpenCodeContractDenied("OpenCode response incomplete or missing artifact")
    return OpenCodeEventEvidence(
        session_id=seen_session,
        answer_sha256=answer.hexdigest(),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        completed_tools=tuple(tools),
        event_count=len(lines),
    )
