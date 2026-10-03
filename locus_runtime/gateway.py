"""The gateway: the single policy enforcement point (PEP) for side effects (LOCUS-332).

Every side effect an agent can cause -- running a process, writing or reading a
file, calling a tool or an MCP server, opening a network connection -- is
described as a :class:`GatewayAction` and passed to :meth:`Gateway.authorize`
*before* it executes (P6, 13 §4). The gateway performs, in order:

1. **Authenticate** the caller: the action's run token must match a session the
   gateway opened for that run, principal and engine (:meth:`Gateway.open_session`).
   Capabilities come from the registered session, never from the action, so a
   caller cannot widen its own envelope (P7).
2. **Resolve** the action: kind, tool, target, redacted argument summary and a
   deterministic risk class. The gateway recomputes the risk class from the
   action facts and keeps the higher of the two (it never lowers a class).
3. *(seam)* Grant verification -- Biscuit capability tokens (13 §5) plug in via
   the ``grants`` argument (:class:`GrantVerifier`). v1 ships no grants:
   :class:`NoGrants` covers nothing.
4. **Evaluate policy** through :class:`~locus_runtime.policy_engine.PolicyEngine`
   (repository Rego; see "Policy mapping" below). Any deny, any engine error and
   an unavailable engine all deny (fail closed).
5. *(seam)* Taint / intent gate (13 §6) plugs in via ``intent_gate``
   (:class:`IntentGate`); v1 passes everything to step 6 unchanged.
6. **Decide** ``allow`` | ``ask`` | ``deny`` from the policy result, the risk
   class and the run's autonomy tier.
7. *(not here)* Execution with secrets by reference / egress proxy is the
   caller's job once the decision is ``allow``.
8. **Record** an audit event for every decision through the injected
   ``audit_sink``. If the sink fails, the decision becomes ``deny``: an action
   that cannot be attributed is not taken (P11).

Risk classes (04 §3, 11 §5) -- computed by :func:`classify_risk`
-----------------------------------------------------------------

====================  =====================================================  =====
Action kind           Condition (first match wins, highest class first)      Class
====================  =====================================================  =====
file_write            target is a credential file (ssh keys, .aws, .netrc)   R4
                      or inside the policy directory (change policy)
file_write            target inside the run's write roots (workspace)        R1
file_write            anything else (outside the workspace)                  R2
file_read             any path (policy still denies secret files)            R0
process_exec          command touches credential stores (.ssh, .aws/creds,   R4
                      keychain, gpg --export-secret-keys)
process_exec          outbound or irreversible: git push, gh pr merge/       R3
                      create, publish/upload, package install, docker push,
                      kubectl/terraform apply, curl/wget with a body or
                      non-GET method, ssh/scp/rsync to a remote, mail,
                      rm -r on an absolute, home or parent path
process_exec          plain network fetch (curl/wget GET, an http(s) URL)   R2
process_exec          anything else (runs inside the bound workspace)        R1
network_egress        any host                                               R2
tool_call /           name pairs an export verb with a credential noun, or   R4
mcp_tool_call         a mutate verb with policy/audit/grant/permission
tool_call /           name contains an outbound/irreversible verb (send,     R3
mcp_tool_call         post, pay, delete, push, merge, deploy, install, share,
                      invite, ...), HTTP DELETE, or payment-like arguments
tool_call /           name contains only a read verb (get, list, search, ...) R0
mcp_tool_call         or the HTTP method is GET/HEAD
tool_call /           anything else (external effect, unknown semantics)     R2
mcp_tool_call
====================  =====================================================  =====

Decision table (tiered autonomy is the default, D-05):

* any policy deny, engine error/unavailable, unauthenticated caller → ``deny``
* R4 → ``deny`` always (11 §5: "R4 prohibited -- Never"; only the human acts)
* R3 → ``ask`` unless a grant covers it or the human approved this exact action
  (single-use, run-scoped :class:`ApprovalLedger` entry)
* R2 under the ``supervised`` tier → ``ask`` (same approval rule)
* otherwise → ``allow``

Policy mapping (inputs are documented on each builder below):

* ``agent_policy`` -- every action; ``tool`` is the canonical operation
  (``read_file``, ``write_file``, ``process_exec``, ``network_egress``) or the
  tool name for tool/MCP calls, checked against the session's ``allowed_tools``.
* ``tool_jail`` -- ``process_exec``; the executor reports its *real* jail facts.
* ``filesystem_access`` -- ``file_read`` / ``file_write``.
* ``network_egress`` -- ``network_egress`` and tool/MCP calls with an egress host.
* ``budget_policy`` -- when the session carries numeric budget figures.

No decision is cached: each call evaluates policy afresh, so a policy change or
an engine outage takes effect on the next action.

Why our own PEP (P30): the enforcement points are in-process Python call sites
(subprocess spawns, file writes, MCP JSON-RPC calls). Off-the-shelf PEPs --
Envoy ext_authz, OPA's Envoy plugin, Gatekeeper -- sit on HTTP or Kubernetes
admission paths and cannot see a ``subprocess.run`` or a file write. This
module is thin glue around OPA (the decision engine); it holds no rules itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import IntEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol, runtime_checkable
from urllib import parse as urlparse
from uuid import uuid4

from locus_runtime.persistence import redact_sensitive_payload
from locus_runtime.policy_engine import Decision, PolicyEngine, default_policy_dir

logger = logging.getLogger(__name__)

ActionKind = Literal[
    "tool_call", "file_write", "file_read", "network_egress", "process_exec", "mcp_tool_call"
]
ACTION_KINDS: frozenset[str] = frozenset(
    {"tool_call", "file_write", "file_read", "network_egress", "process_exec", "mcp_tool_call"}
)
Outcome = Literal["allow", "ask", "deny"]
AutonomyTier = Literal["tiered", "supervised", "envelope-autonomous"]

# Reason codes the gateway itself emits (policy reasons come from the engine).
REASON_UNAUTHENTICATED = "gateway.unauthenticated_caller"
REASON_NOT_INSTALLED = "gateway.not_installed"
REASON_INVALID_ACTION = "gateway.invalid_action"
REASON_ENGINE_ERROR = "gateway.engine_error"
REASON_AUDIT_UNAVAILABLE = "gateway.audit_unavailable"
REASON_R4_PROHIBITED = "gateway.risk_r4_prohibited"
REASON_APPROVAL_REQUIRED = "gateway.approval_required"
REASON_APPROVED = "gateway.approved_by_human"
REASON_GRANT = "gateway.grant_covers_action"

#: Canonical agent_policy operation per action kind (tool/MCP calls use the tool name).
CANONICAL_OPERATION: Mapping[str, str] = {
    "file_read": "read_file",
    "file_write": "write_file",
    "process_exec": "process_exec",
    "network_egress": "network_egress",
}

_ARG_VALUE_MAX = 160
_ARG_KEYS_MAX = 24
# Argument keys whose values are content, not intent: summarised by length only.
_CONTENT_KEYS = frozenset({"file_text", "content", "text", "new_str", "old_str", "body", "data"})
_EXTRA_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
        ),
        "[redacted]",
    ),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[redacted]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[redacted]"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1[redacted]@"),
)

# Process-local key: binds approvals and decisions to this process. Never persisted.
_PROCESS_KEY = secrets.token_bytes(32)


class RiskClass(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4

    @property
    def label(self) -> str:
        return f"R{int(self)}"


# --------------------------------------------------------------------------- #
# Redaction and fingerprints
# --------------------------------------------------------------------------- #
def _redact_text(text: str) -> str:
    value = str(redact_sensitive_payload(text))
    for pattern, replacement in _EXTRA_SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact_text(text: str, *, limit: int = _ARG_VALUE_MAX) -> str:
    """Redact secret-looking substrings and truncate (never raw secrets in audit, P10)."""
    value = _redact_text(str(text or ""))
    return value if len(value) <= limit else value[: limit - 1] + "…"


def summarize_args(args: Mapping[str, Any] | None) -> dict[str, str]:
    """A redacted, bounded summary of tool arguments, safe for audit and events."""
    if not isinstance(args, Mapping):
        return {}
    redacted = redact_sensitive_payload({str(k): v for k, v in args.items()})
    summary: dict[str, str] = {}
    for key in sorted(redacted)[:_ARG_KEYS_MAX]:
        if key == "redacted":
            continue
        value = redacted[key]
        if key in _CONTENT_KEYS and isinstance(args.get(key), str):
            summary[key] = f"<{len(str(args.get(key)))} chars>"
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
        summary[key] = redact_text(text)
    return summary


def args_digest(args: Any) -> str:
    """Process-keyed HMAC of the canonical raw arguments.

    Binds an approval or a decision to the exact arguments without storing them;
    the key never leaves the process, so the digest reveals nothing offline.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        canonical = repr(args)
    return hmac.new(_PROCESS_KEY, canonical.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Risk classification (pure, deterministic)
# --------------------------------------------------------------------------- #
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_EXPORT_VERBS = frozenset(
    {"export", "dump", "reveal", "exfiltrate", "print", "show", "copy", "get", "read"}
)
_CREDENTIAL_NOUNS = frozenset(
    {
        "credential",
        "credentials",
        "password",
        "passwords",
        "secret",
        "secrets",
        "privatekey",
        "keychain",
        "apikey",
        "apikeys",
        "vault",
    }
)
_MUTATE_VERBS = frozenset(
    {
        "disable",
        "delete",
        "remove",
        "modify",
        "change",
        "update",
        "set",
        "widen",
        "grant",
        "edit",
        "write",
        "turn",
        "stop",
        "clear",
        "purge",
    }
)
_CONTROL_NOUNS = frozenset(
    {"policy", "policies", "audit", "grant", "grants", "permission", "permissions", "rego"}
)
_R3_VERBS = frozenset(
    {
        "send",
        "post",
        "publish",
        "pay",
        "payment",
        "purchase",
        "buy",
        "order",
        "transfer",
        "refund",
        "charge",
        "wire",
        "delete",
        "remove",
        "destroy",
        "drop",
        "purge",
        "truncate",
        "push",
        "merge",
        "deploy",
        "release",
        "install",
        "uninstall",
        "share",
        "invite",
        "accept",
        "approve",
        "email",
        "sms",
        "tweet",
        "reply",
        "forward",
        "book",
        "cancel",
        "submit",
        "execute",
        "exec",
        "run",
        "kill",
        "terminate",
        "revoke",
        "upload",
    }
)
_R0_VERBS = frozenset(
    {
        "get",
        "list",
        "read",
        "search",
        "fetch",
        "query",
        "describe",
        "view",
        "show",
        "find",
        "lookup",
        "count",
        "status",
        "inspect",
        "check",
        "head",
        "browse",
        "retrieve",
        "ls",
    }
)
_PAYMENT_ARG_KEYS = frozenset(
    {"amount", "price", "payment", "card_number", "iban", "account_number"}
)

_CRED_PATH = re.compile(
    r"(^|[/\\])(\.ssh|\.gnupg|\.aws|\.kube|\.docker)([/\\]|$)|(^|[/\\])(id_rsa|id_dsa|id_ecdsa|"
    r"id_ed25519|authorized_keys|\.netrc|\.pypirc|\.npmrc|\.git-credentials)$",
    re.IGNORECASE,
)
_CMD_R4 = re.compile(
    r"(\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.git-credentials|\.netrc\b|\.pypirc\b|"
    r"security\s+find-(generic|internet)-password|gpg\s+.*--export-secret|keychain|"
    r"cmdkey\s+/list|vaultcmd)",
    re.IGNORECASE,
)
_CMD_R3 = (
    re.compile(r"\bgit\s+(-c\s+\S+\s+)*push\b"),
    re.compile(r"\bgh\s+(pr\s+(merge|create)|release\s+create|repo\s+(delete|create)|api\b)"),
    re.compile(r"\b(npm|pnpm|yarn|cargo|gem)\s+publish\b|\btwine\s+upload\b|\bpoetry\s+publish\b"),
    re.compile(
        r"\b(pip3?|uv\s+pip|npm|pnpm|yarn|apt(-get)?|brew|choco|winget|cargo|gem|go|conda|dnf|yum|apk)"
        r"\s+(install|add|i)\b"
    ),
    re.compile(
        r"\bdocker\s+(push|login)\b|\bkubectl\s+(apply|delete|create|patch)\b|\bterraform\s+(apply|destroy)\b"
    ),
    re.compile(
        r"\b(curl|wget|http|httpie)\b[^|;&]*(\s-X\s*(POST|PUT|PATCH|DELETE)|\s(-d|--data\S*|-F|--form|-T|--upload-file|--post-data|--post-file)\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(ssh|scp|sftp)\s+\S|\brsync\b[^|;&]*\S+:\S*"),
    re.compile(r"\b(sendmail|mailx?|mutt)\b"),
    re.compile(r"\brm\s+(-[a-z]*r[a-z]*|--recursive)\b[^|;&]*\s(/|~|\.\.)"),
    re.compile(r"\b(shutdown|reboot|mkfs\S*|dd\s+if=)\b"),
)
_CMD_R2 = re.compile(r"\b(curl|wget|nc|ncat|telnet|ftp)\b|https?://", re.IGNORECASE)


def _name_tokens(name: str) -> set[str]:
    spaced = _CAMEL.sub("_", str(name or ""))
    return {token for token in _TOKEN_SPLIT.split(spaced.lower()) if token}


def _norm_path(path: str) -> tuple[str, ...]:
    text = str(path or "").replace("\\", "/")
    is_windows = bool(re.match(r"^[A-Za-z]:/", text))
    parts = (PureWindowsPath(text) if is_windows else PurePosixPath(text)).parts
    out: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part.lower() if is_windows else part)
    return tuple(p.rstrip("\\/") or p for p in out)


def path_within(path: str, root: str) -> bool:
    """Lexical containment of ``path`` in ``root`` (both should be absolute)."""
    candidate, base = _norm_path(path), _norm_path(root)
    return bool(base) and candidate[: len(base)] == base


def _tool_risk(tool: str, args: Mapping[str, Any] | None, method: str) -> RiskClass:
    tokens = _name_tokens(tool)
    if (tokens & _CREDENTIAL_NOUNS and tokens & _EXPORT_VERBS) or (
        tokens & _CONTROL_NOUNS and tokens & _MUTATE_VERBS
    ):
        return RiskClass.R4
    arg_keys = {str(key).lower() for key in (args or {})}
    if tokens & _R3_VERBS or method == "DELETE" or arg_keys & _PAYMENT_ARG_KEYS:
        return RiskClass.R3
    if method in {"GET", "HEAD"} or (tokens & _R0_VERBS and not tokens & _MUTATE_VERBS):
        return RiskClass.R0
    return RiskClass.R2


def classify_command(command: str) -> RiskClass:
    """Risk class of a shell command line (see the module table)."""
    text = str(command or "")
    if _CMD_R4.search(text):
        return RiskClass.R4
    if any(pattern.search(text) for pattern in _CMD_R3):
        return RiskClass.R3
    if _CMD_R2.search(text):
        return RiskClass.R2
    return RiskClass.R1


def classify_risk(
    *,
    kind: str,
    tool: str = "",
    target: str = "",
    command: str = "",
    args: Mapping[str, Any] | None = None,
    method: str = "",
    write_roots: Sequence[str] = (),
    policy_dir: str | None = None,
) -> RiskClass:
    """Deterministic risk class for an action (table in the module docstring)."""
    if kind == "file_read":
        return RiskClass.R0
    if kind == "file_write":
        policy_root = policy_dir if policy_dir is not None else str(default_policy_dir())
        if _CRED_PATH.search(str(target or "")) or (
            policy_root and path_within(target, policy_root)
        ):
            return RiskClass.R4
        if any(path_within(target, root) for root in write_roots):
            return RiskClass.R1
        return RiskClass.R2
    if kind == "process_exec":
        return classify_command(command or target)
    if kind == "network_egress":
        return RiskClass.R2
    if kind in {"tool_call", "mcp_tool_call"}:
        return _tool_risk(tool, args, str(method or "").upper())
    return RiskClass.R4  # unknown kinds are treated as prohibited


# --------------------------------------------------------------------------- #
# Data contracts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GatewayCaller:
    """Who is asking: run, principal (human or service the run acts for), engine."""

    run_id: str
    principal: str
    engine: str
    token: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class JailFacts:
    """What the executor really provides; fed to ``tool_jail`` unmodified.

    Executors derive these from the strategy they actually launch with (see
    ``locus_runtime.harness.executor``); agents and tool callers never supply them.
    ``strategy`` is the isolation tier name tool_jail matches on: ``kernel-bwrap``,
    ``kernel-seatbelt``, ``hardened-docker``, ``windows-appcontainer``,
    ``docker-exec`` (a container we exec into), ``local-direct``,
    ``restricted-process``, ``unavailable`` (no confining sandbox on this host) or
    ``none``.
    """

    strategy: str
    readonly_rootfs: bool = False
    run_as_user: str = ""
    allow_network: bool = True
    require_egress_mediation: bool = False
    # Windows AppContainer tier: the child runs in an AppContainer, inside a Job
    # Object, and the launcher fails closed rather than dropping to Job-Object-only.
    appcontainer: bool = False
    job_object: bool = False
    require_appcontainer: bool = False
    # Why no confining sandbox exists on this host (strategy "unavailable").
    unavailable_reason: str = ""


UNJAILED = JailFacts(strategy="none")

#: Session profile that may use an evaluation container as a jail (tool_jail).
EVALS_PROFILE = "evals"


@dataclass(frozen=True)
class BudgetFigures:
    tokens_used: float
    max_tokens: float
    duration_used_seconds: float | None = None
    max_duration_seconds: float | None = None
    cost_used_usd: float | None = None
    max_cost_usd: float | None = None

    def as_input(self) -> dict[str, float]:
        out: dict[str, float] = {"tokens_used": self.tokens_used, "max_tokens": self.max_tokens}
        for name in (
            "duration_used_seconds",
            "max_duration_seconds",
            "cost_used_usd",
            "max_cost_usd",
        ):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out


@dataclass(frozen=True)
class Capabilities:
    """The run envelope's capability set (P2) as the gateway sees it."""

    allowed_tools: frozenset[str] = frozenset()
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    allowed_executables: tuple[str, ...] = ()
    allowed_egress_hosts: tuple[str, ...] = ()
    autonomy_tier: AutonomyTier = "tiered"
    max_tool_calls: int = 0
    budget: BudgetFigures | None = None
    # Run profile registered with the session. ``"evals"`` is accepted only by a
    # gateway built with ``allow_eval_sessions=True`` (apps/evals); see open_session.
    runtime_profile: str = ""


@dataclass(frozen=True)
class GatewayAction:
    """One side effect, described before it happens. Holds no raw secrets."""

    caller: GatewayCaller
    kind: str
    tool: str
    target: str
    args_summary: Mapping[str, str]
    risk: RiskClass
    args_digest: str = ""
    command_summary: str = ""
    executable: str = ""
    jail: JailFacts | None = None
    egress_host: str = ""
    method: str = ""

    @classmethod
    def create(
        cls,
        *,
        caller: GatewayCaller,
        kind: str,
        tool: str,
        target: str = "",
        args: Mapping[str, Any] | None = None,
        command: str = "",
        executable: str = "",
        jail: JailFacts | None = None,
        egress_host: str = "",
        method: str = "",
        capabilities: Capabilities | None = None,
    ) -> GatewayAction:
        caps = capabilities or Capabilities()
        command_text = redact_text(command, limit=400) if command else ""
        risk = classify_risk(
            kind=kind,
            tool=tool,
            target=target,
            command=command,
            args=args,
            method=method,
            write_roots=caps.write_roots,
        )
        return cls(
            caller=caller,
            kind=kind,
            tool=str(tool or ""),
            target=redact_text(target, limit=400),
            args_summary=summarize_args(args),
            risk=risk,
            args_digest=args_digest({"args": args, "command": command, "target": target}),
            command_summary=command_text,
            executable=str(executable or ""),
            jail=jail,
            egress_host=str(egress_host or "").strip().lower(),
            method=str(method or "").upper(),
        )

    @property
    def fingerprint(self) -> str:
        """Run-scoped identity of this exact action (approval binding)."""
        material = "|".join(
            (
                self.caller.run_id,
                self.kind,
                self.tool,
                self.target,
                self.egress_host,
                self.args_digest,
            )
        )
        return hmac.new(_PROCESS_KEY, material.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


@dataclass(frozen=True)
class GatewayDecision:
    outcome: Outcome
    reasons: tuple[str, ...]
    audit_id: str
    policy_version: str
    risk: RiskClass = RiskClass.R4
    action_kind: str = ""
    tool: str = ""
    target: str = ""
    fingerprint: str = ""
    args_digest: str = ""
    seal: str = field(default="", repr=False, compare=False)

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow" and verify_decision(self)

    def describe(self) -> str:
        reasons = ", ".join(self.reasons) or "no reason given"
        return f"{self.outcome} ({self.risk.label}; {reasons}; audit {self.audit_id or 'n/a'})"


def _seal(decision: GatewayDecision) -> str:
    material = "|".join(
        (
            decision.outcome,
            decision.audit_id,
            decision.action_kind,
            decision.tool,
            decision.fingerprint,
            decision.args_digest,
        )
    )
    return hmac.new(_PROCESS_KEY, material.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_decision(decision: GatewayDecision) -> bool:
    """True only for a decision this process's gateway issued (not a hand-built one)."""
    return bool(decision.seal) and hmac.compare_digest(decision.seal, _seal(decision))


def _sealed(decision: GatewayDecision) -> GatewayDecision:
    return replace(decision, seal=_seal(decision))


class GatewayBlocked(PermissionError):
    """Raised by file-operation sinks when the gateway did not allow the action."""

    def __init__(self, decision: GatewayDecision) -> None:
        super().__init__(f"gateway {decision.describe()}")
        self.decision = decision


@dataclass(frozen=True)
class GatewayAuditRecord:
    audit_id: str
    created_at: float
    principal: str
    run_id: str
    engine: str
    action_kind: str
    tool: str
    target: str
    outcome: Outcome
    reasons: tuple[str, ...]
    policy_version: str
    risk_class: str
    args_summary: Mapping[str, str]
    command: str
    fingerprint: str

    def as_metadata(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "principal": self.principal,
            "run_id": self.run_id,
            "engine": self.engine,
            "action_kind": self.action_kind,
            "tool": self.tool,
            "target": self.target,
            "gateway_outcome": self.outcome,
            "reasons": list(self.reasons),
            "policy_version": self.policy_version,
            "risk_class": self.risk_class,
            "args_summary": dict(self.args_summary),
            "command": self.command,
            "fingerprint": self.fingerprint,
        }


AuditSink = Callable[[GatewayAuditRecord], None]
DecisionListener = Callable[[GatewayAction, GatewayDecision], None]


# --------------------------------------------------------------------------- #
# Extension seams (steps 3 and 5)
# --------------------------------------------------------------------------- #
@runtime_checkable
class GrantVerifier(Protocol):
    """Step 3 seam: Biscuit capability grants covering an action pattern (13 §5)."""

    def covers(self, action: GatewayAction, capabilities: Capabilities) -> bool: ...


class NoGrants:
    """v1: no grants exist, so nothing is covered."""

    def covers(self, action: GatewayAction, capabilities: Capabilities) -> bool:  # noqa: ARG002
        return False


@runtime_checkable
class IntentGate(Protocol):
    """Step 5 seam: taint and intent judgments (13 §6). Returns extra deny/ask reasons."""

    def review(
        self, action: GatewayAction, capabilities: Capabilities
    ) -> tuple[Outcome, tuple[str, ...]]: ...


class ApprovalLedger:
    """Single-use, run-scoped human approvals of exact actions ("ask" → approved).

    An approval matches only the same run and the same action fingerprint
    (kind, tool, target, args) and is consumed by the first matching attempt.
    Approvals never override a policy deny or an R4 prohibition.
    """

    def __init__(self, *, max_entries: int = 2048) -> None:
        self._entries: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_entries

    def approve(self, run_id: str, fingerprint: str, approver: str) -> None:
        if not run_id or not fingerprint:
            raise ValueError("run_id and fingerprint are required")
        with self._lock:
            self._entries[(run_id, fingerprint)] = str(approver or "")
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def consume(self, run_id: str, fingerprint: str) -> str | None:
        with self._lock:
            return self._entries.pop((run_id, fingerprint), None)

    def revoke_run(self, run_id: str) -> None:
        with self._lock:
            for key in [k for k in self._entries if k[0] == run_id]:
                del self._entries[key]


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@dataclass
class _SessionRecord:
    caller: GatewayCaller
    capabilities: Capabilities
    listener: DecisionListener | None
    tool_calls_used: int = 0


class GatewaySession:
    """A run's authenticated handle on the gateway (what executors and call sites hold)."""

    def __init__(self, gateway: Gateway, caller: GatewayCaller, capabilities: Capabilities) -> None:
        self._gateway = gateway
        self.caller = caller
        self.capabilities = capabilities

    @property
    def gateway(self) -> Gateway:
        return self._gateway

    def action(self, **kwargs: Any) -> GatewayAction:
        return GatewayAction.create(caller=self.caller, capabilities=self.capabilities, **kwargs)

    def authorize(self, **kwargs: Any) -> GatewayDecision:
        return self._gateway.authorize(self.action(**kwargs))

    def close(self) -> None:
        self._gateway.close_session(self)

    def report_budget(self, figures: BudgetFigures) -> BudgetFigures | None:
        """Report the run's current budget usage; see :meth:`Gateway.report_budget`."""
        merged = self._gateway.report_budget(self, figures)
        if merged is not None:
            self.capabilities = replace(self.capabilities, budget=merged)
        return merged


# --------------------------------------------------------------------------- #
# Gateway
# --------------------------------------------------------------------------- #
class Gateway:
    """The single PEP. Construct once per process with a policy engine and audit sink."""

    def __init__(
        self,
        engine: PolicyEngine,
        audit_sink: AuditSink,
        *,
        approvals: ApprovalLedger | None = None,
        grants: GrantVerifier | None = None,
        intent_gate: IntentGate | None = None,
        clock: Callable[[], float] = time.time,
        max_sessions: int = 4096,
        allow_eval_sessions: bool = False,
    ) -> None:
        self._engine = engine
        # Only the evaluation harness builds a gateway that accepts evals sessions;
        # the backend's gateway never does, so a normal run cannot claim the
        # evaluation-container jail.
        self._allow_eval_sessions = allow_eval_sessions
        self._audit_sink = audit_sink
        self.approvals = approvals or ApprovalLedger()
        self._grants: GrantVerifier = grants or NoGrants()
        self._intent_gate = intent_gate
        self._clock = clock
        # Bounded: the oldest sessions are evicted (their actions then deny).
        self._sessions: OrderedDict[str, _SessionRecord] = OrderedDict()
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    # -- posture --------------------------------------------------------------
    @property
    def engine(self) -> PolicyEngine:
        return self._engine

    @property
    def healthy(self) -> bool:
        """The engine reports itself running (an unavailable engine still denies)."""
        running = getattr(self._engine, "running", None)
        return bool(running) if running is not None else False

    # -- sessions -------------------------------------------------------------
    def open_session(
        self,
        *,
        run_id: str,
        principal: str,
        engine: str,
        capabilities: Capabilities,
        on_decision: DecisionListener | None = None,
    ) -> GatewaySession:
        if (
            not str(run_id or "").strip()
            or not str(principal or "").strip()
            or not str(engine or "").strip()
        ):
            raise ValueError("run_id, principal and engine are required to open a gateway session")
        if (
            str(capabilities.runtime_profile or "").strip().lower() == EVALS_PROFILE
            and not self._allow_eval_sessions
        ):
            raise ValueError("this gateway does not accept evals sessions")
        token = secrets.token_urlsafe(32)
        caller = GatewayCaller(run_id=run_id, principal=principal, engine=engine, token=token)
        with self._lock:
            self._sessions[_token_key(token)] = _SessionRecord(caller, capabilities, on_decision)
            while len(self._sessions) > self._max_sessions:
                self._sessions.popitem(last=False)
        return GatewaySession(self, caller, capabilities)

    def close_session(self, session: GatewaySession) -> None:
        with self._lock:
            self._sessions.pop(_token_key(session.caller.token), None)
        # Approvals outlive the session: a run continued later (same run id) may
        # retry the approved action once. The ledger is bounded.

    def report_budget(
        self, session: GatewaySession, figures: BudgetFigures
    ) -> BudgetFigures | None:
        """Update the used-budget figures ``budget_policy`` evaluates for this session.

        The run loop calls this before each action (LOCUS-337) so the policy sees
        current spend. It can only tighten: used figures never decrease, a limit
        never rises above the registered one and a registered limit is never
        dropped. Returns the merged figures, or ``None`` for an unknown session.
        """
        with self._lock:
            record = self._sessions.get(_token_key(session.caller.token))
            if record is None:
                return None
            merged = _merge_budget(record.capabilities.budget, figures)
            record.capabilities = replace(record.capabilities, budget=merged)
            return merged

    def _authenticate(self, caller: GatewayCaller) -> _SessionRecord | None:
        if not caller.token:
            return None
        with self._lock:
            record = self._sessions.get(_token_key(caller.token))
        if record is None:
            return None
        registered = record.caller
        if (registered.run_id, registered.principal, registered.engine) != (
            caller.run_id,
            caller.principal,
            caller.engine,
        ):
            return None
        return record

    # -- authorize ------------------------------------------------------------
    def authorize(self, action: GatewayAction) -> GatewayDecision:
        audit_id = f"gw-{uuid4()}"
        record = self._authenticate(action.caller)
        if record is None:
            return self._finish(action, None, "deny", (REASON_UNAUTHENTICATED,), "", audit_id)
        caps = record.capabilities
        if action.kind not in ACTION_KINDS or not action.tool:
            return self._finish(action, record, "deny", (REASON_INVALID_ACTION,), "", audit_id)

        # Step 2: never trust a lower caller-supplied class.
        recomputed = classify_risk(
            kind=action.kind,
            tool=action.tool,
            target=action.target,
            command=action.command_summary,
            method=action.method,
            write_roots=caps.write_roots,
        )
        if recomputed > action.risk:
            action = replace(action, risk=recomputed)

        tool_calls_used = record.tool_calls_used + (
            1 if action.kind in {"tool_call", "mcp_tool_call"} else 0
        )

        # Step 4: policy.
        reasons: list[str] = []
        versions: set[str] = set()
        denied = False
        for policy, payload in policy_inputs(action, caps, tool_calls_used=tool_calls_used):
            try:
                result: Decision = self._engine.decide(policy, payload)
            except Exception:  # noqa: BLE001 - any engine failure denies
                logger.exception("gateway.engine_error", extra={"policy": policy})
                denied = True
                reasons.append(f"{REASON_ENGINE_ERROR}:{policy}")
                continue
            if result.policy_version:
                versions.add(result.policy_version)
            if result.allow is not True:
                denied = True
                reasons.extend(result.reasons or [f"{policy}.not_allowed"])
                # Policies may name why they denied (``deny_reason``), e.g. tool_jail's
                # "no_confining_sandbox"; it is a label, never a decision input.
                deny_reason = (getattr(result, "outputs", None) or {}).get("deny_reason")
                if isinstance(deny_reason, str) and re.fullmatch(r"[a-z0-9_]{1,64}", deny_reason):
                    reasons.append(f"{policy}.{deny_reason}")
            else:
                reasons.extend(result.reasons or [f"{policy}.allow"])
        policy_version = ",".join(sorted(versions)) or "unknown"
        if denied:
            return self._finish(action, record, "deny", tuple(reasons), policy_version, audit_id)

        # Step 5 seam.
        if self._intent_gate is not None:
            try:
                gate_outcome, gate_reasons = self._intent_gate.review(action, caps)
            except Exception:  # noqa: BLE001 - a broken gate denies
                gate_outcome, gate_reasons = "deny", ("gateway.intent_gate_error",)
            reasons.extend(gate_reasons)
            if gate_outcome == "deny":
                return self._finish(
                    action, record, "deny", tuple(reasons), policy_version, audit_id
                )
            if gate_outcome == "ask":
                return self._ask_or_approved(action, record, reasons, policy_version, audit_id)

        # Step 6: decide by risk class and tier.
        if action.risk >= RiskClass.R4:
            reasons.append(REASON_R4_PROHIBITED)
            return self._finish(action, record, "deny", tuple(reasons), policy_version, audit_id)
        needs_ask = action.risk == RiskClass.R3 or (
            action.risk == RiskClass.R2 and caps.autonomy_tier == "supervised"
        )
        if needs_ask:
            if self._grants.covers(action, caps):
                reasons.append(REASON_GRANT)
            else:
                return self._ask_or_approved(action, record, reasons, policy_version, audit_id)
        decision = self._finish(action, record, "allow", tuple(reasons), policy_version, audit_id)
        if decision.outcome == "allow" and action.kind in {"tool_call", "mcp_tool_call"}:
            with self._lock:
                record.tool_calls_used += 1
        return decision

    def _ask_or_approved(
        self,
        action: GatewayAction,
        record: _SessionRecord,
        reasons: list[str],
        policy_version: str,
        audit_id: str,
    ) -> GatewayDecision:
        approver = self.approvals.consume(action.caller.run_id, action.fingerprint)
        if approver is not None:
            reasons.append(REASON_APPROVED)
            return self._finish(action, record, "allow", tuple(reasons), policy_version, audit_id)
        reasons.append(REASON_APPROVAL_REQUIRED)
        return self._finish(action, record, "ask", tuple(reasons), policy_version, audit_id)

    def _finish(
        self,
        action: GatewayAction,
        record: _SessionRecord | None,
        outcome: Outcome,
        reasons: tuple[str, ...],
        policy_version: str,
        audit_id: str,
    ) -> GatewayDecision:
        audit = GatewayAuditRecord(
            audit_id=audit_id,
            created_at=self._clock(),
            principal=action.caller.principal or "unauthenticated",
            run_id=action.caller.run_id,
            engine=action.caller.engine,
            action_kind=action.kind,
            tool=action.tool,
            target=action.target,
            outcome=outcome,
            reasons=reasons,
            policy_version=policy_version or "unknown",
            risk_class=action.risk.label,
            args_summary=action.args_summary,
            command=action.command_summary,
            fingerprint=action.fingerprint,
        )
        try:
            self._audit_sink(audit)
        except Exception:  # noqa: BLE001 - unattributable actions are not taken
            logger.exception("gateway.audit_unavailable", extra={"audit_id": audit_id})
            outcome = "deny"
            reasons = (*reasons, REASON_AUDIT_UNAVAILABLE)
        decision = _sealed(
            GatewayDecision(
                outcome=outcome,
                reasons=reasons,
                audit_id=audit_id,
                policy_version=policy_version or "unknown",
                risk=action.risk,
                action_kind=action.kind,
                tool=action.tool,
                target=action.target,
                fingerprint=action.fingerprint,
                args_digest=action.args_digest,
            )
        )
        if record is not None and record.listener is not None:
            try:
                record.listener(action, decision)
            except Exception:  # noqa: BLE001 - listeners report; they never change the decision
                logger.exception("gateway.listener_error", extra={"audit_id": audit_id})
        return decision


def _merge_budget(current: BudgetFigures | None, new: BudgetFigures) -> BudgetFigures:
    """Monotonic merge: used = max(current, new); limit = min of the declared limits."""
    if current is None:
        return new

    def used(a: float | None, b: float | None) -> float | None:
        values = [v for v in (a, b) if v is not None]
        return max(values) if values else None

    def limit(a: float | None, b: float | None) -> float | None:
        values = [v for v in (a, b) if v is not None]
        return min(values) if values else None

    return BudgetFigures(
        tokens_used=max(current.tokens_used, new.tokens_used),
        max_tokens=min(current.max_tokens, new.max_tokens),
        duration_used_seconds=used(current.duration_used_seconds, new.duration_used_seconds),
        max_duration_seconds=limit(current.max_duration_seconds, new.max_duration_seconds),
        cost_used_usd=used(current.cost_used_usd, new.cost_used_usd),
        max_cost_usd=limit(current.max_cost_usd, new.max_cost_usd),
    )


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Policy inputs (one builder per family)
# --------------------------------------------------------------------------- #
def policy_inputs(
    action: GatewayAction, caps: Capabilities, *, tool_calls_used: int = 0
) -> list[tuple[str, dict[str, Any]]]:
    """The (policy, input) pairs evaluated for ``action``. All must allow."""
    plan: list[tuple[str, dict[str, Any]]] = [
        ("agent_policy", agent_policy_input(action, caps, tool_calls_used))
    ]
    if action.kind == "process_exec":
        plan.append(("tool_jail", tool_jail_input(action, caps)))
    if action.kind in {"file_read", "file_write"}:
        plan.append(("filesystem_access", filesystem_access_input(action, caps)))
    egress_host = action.egress_host or (action.target if action.kind == "network_egress" else "")
    if action.kind == "network_egress" or egress_host:
        plan.append(("network_egress", network_egress_input(egress_host, caps)))
    if caps.budget is not None:
        plan.append(("budget_policy", caps.budget.as_input()))
    return plan


def agent_policy_input(
    action: GatewayAction, caps: Capabilities, tool_calls_used: int = 0
) -> dict[str, Any]:
    operation = CANONICAL_OPERATION.get(action.kind, action.tool)
    payload: dict[str, Any] = {
        "agent_id": action.caller.engine,
        "tool": operation,
        "action": operation,
        "allowed_tools": sorted(caps.allowed_tools),
        "resource": action.target,
        "allowed_targets": sorted(caps.allowed_egress_hosts),
    }
    if action.kind == "network_egress":
        payload["target"] = action.target
    if caps.max_tool_calls > 0:
        payload["max_tool_calls"] = caps.max_tool_calls
        payload["tool_calls_used"] = tool_calls_used
    if caps.budget is not None:
        payload["budget"] = {
            "tokens_used": caps.budget.tokens_used,
            "max_tokens": caps.budget.max_tokens,
        }
    return payload


def tool_jail_input(action: GatewayAction, caps: Capabilities) -> dict[str, Any]:
    """Jail facts come from the executor (``action.jail``); the run profile comes from
    the registered session, never from the action."""
    jail = action.jail or UNJAILED
    executable = action.executable or action.tool
    return {
        "command": [executable] if executable else [],
        "allowed_executables": list(caps.allowed_executables),
        "isolation_tier": jail.strategy,
        "readonly_rootfs": jail.readonly_rootfs,
        "run_as_user": jail.run_as_user,
        "allow_network": jail.allow_network,
        "require_egress_mediation": jail.require_egress_mediation,
        "appcontainer": jail.appcontainer,
        "job_object": jail.job_object,
        "require_appcontainer": jail.require_appcontainer,
        "runtime_profile": str(caps.runtime_profile or "").strip().lower(),
        "allowed_hosts": [],
        "requested_hosts": [],
    }


def filesystem_access_input(action: GatewayAction, caps: Capabilities) -> dict[str, Any]:
    return {
        "action": "write" if action.kind == "file_write" else "read",
        "path": action.target,
        "allowed_paths": list(caps.read_roots),
        "allowed_write_paths": list(caps.write_roots),
    }


def network_egress_input(host: str, caps: Capabilities) -> dict[str, Any]:
    return {
        "action": "network_egress",
        "target": host,
        "allowed_targets": sorted(caps.allowed_egress_hosts),
    }


def host_of(url: str) -> str:
    """Lower-cased host of a URL (empty when there is none)."""
    try:
        return (urlparse.urlparse(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


# --------------------------------------------------------------------------- #
# Process-wide installation and unbound callers
# --------------------------------------------------------------------------- #
@runtime_checkable
class Authorizer(Protocol):
    def authorize(self, action: GatewayAction) -> GatewayDecision: ...


_PROCESS_GATEWAY: Authorizer | None = None
_PROCESS_LOCK = threading.Lock()
UNBOUND_CALLER = GatewayCaller(run_id="", principal="", engine="unbound", token="")
_CURRENT_TOOL: ContextVar[str] = ContextVar("locus_gateway_current_tool", default="")


def install_gateway(gateway: Authorizer | None) -> None:
    """Install (or with ``None`` remove) the process gateway used by unbound callers."""
    global _PROCESS_GATEWAY
    with _PROCESS_LOCK:
        _PROCESS_GATEWAY = gateway


def installed_gateway() -> Authorizer | None:
    return _PROCESS_GATEWAY


def gateway_enforcing() -> bool:
    """Posture fact: a real :class:`Gateway` with a healthy engine is installed here."""
    gateway = _PROCESS_GATEWAY
    return isinstance(gateway, Gateway) and gateway.healthy


@contextmanager
def tool_context(name: str) -> Iterator[None]:
    """Label executor-level actions with the agent tool that caused them."""
    token = _CURRENT_TOOL.set(str(name or ""))
    try:
        yield
    finally:
        _CURRENT_TOOL.reset(token)


def current_tool(default: str = "harness.internal") -> str:
    return _CURRENT_TOOL.get() or default


def not_installed_decision(action: GatewayAction) -> GatewayDecision:
    logger.warning(
        "gateway.not_installed",
        extra={"action_kind": action.kind, "tool": action.tool, "engine": action.caller.engine},
    )
    return _sealed(
        GatewayDecision(
            outcome="deny",
            reasons=(REASON_NOT_INSTALLED,),
            audit_id="",
            policy_version="unknown",
            risk=action.risk,
            action_kind=action.kind,
            tool=action.tool,
            target=action.target,
            fingerprint=action.fingerprint,
            args_digest=action.args_digest,
        )
    )


def authorize_action(session: GatewaySession | None, **kwargs: Any) -> GatewayDecision:
    """Authorize through a run session, else the process gateway as an unbound caller.

    An unbound caller is never authenticated by a real :class:`Gateway` (deny);
    with no gateway installed at all, the result is also a deny (fail closed).
    """
    if session is not None:
        return session.authorize(**kwargs)
    action = GatewayAction.create(caller=UNBOUND_CALLER, **kwargs)
    gateway = installed_gateway()
    if gateway is None:
        return not_installed_decision(action)
    try:
        return gateway.authorize(action)
    except Exception:  # noqa: BLE001 - a crashing gateway denies
        logger.exception("gateway.authorize_error")
        return not_installed_decision(action)


def gateway_message(decision: GatewayDecision, tool: str) -> str:
    """Typed, agent-facing result for an action the gateway did not allow.

    The run continues: a deny or an ask is returned to the agent as a tool
    result, never raised. ``ask`` actions were not executed and wait for a human
    approval recorded against ``decision.audit_id``.
    """
    reasons = ", ".join(decision.reasons) or "no reason given"
    where = f" on '{decision.target}'" if decision.target else ""
    if decision.outcome == "ask":
        return (
            f"[permission required] {tool}: {decision.action_kind}{where} is risk "
            f"{decision.risk.label} and needs human approval (request {decision.audit_id}). "
            "It was NOT executed. Continue with other work; retry this exact action only "
            "after it has been approved."
        )
    return (
        f"[denied by policy] {tool}: {decision.action_kind}{where} was denied by the gateway "
        f"({reasons}; audit {decision.audit_id or 'n/a'}). It was NOT executed. Do not "
        "retry the same action; choose another approach or report the blocker."
    )


def default_allowed_executables() -> tuple[str, ...]:
    configured = str(os.getenv("LOCUS_GATEWAY_ALLOWED_EXECUTABLES") or "").strip()
    if configured:
        return tuple(item.strip() for item in configured.split(",") if item.strip())
    return ("bash", "sh", "git", "python", "python3", "pytest", "rg", "grep", "codex")


def current_uid_user() -> str:
    """``uid:gid`` of this process on POSIX; empty where there are no numeric uids."""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return ""
    return f"{getuid()}:{getgid()}"
