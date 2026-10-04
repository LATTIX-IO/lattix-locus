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
3. Grant verification -- Biscuit capability grants (13 §5, LOCUS-334) plug in via
   the ``grants`` argument (:class:`GrantVerifier`; the real one is
   :class:`locus_runtime.grants.BiscuitGrantVerifier`). Grants are looked up
   server-side for the authenticated principal -- never taken from the action.
   Without a grant authority key the gateway runs with :class:`NoGrants`, which
   covers nothing (fail closed: R3 still asks). A grant only ever turns an
   ``ask`` into ``allow`` at step 6; it never overrides a policy deny or R4.
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
model_call            engine on a loopback host (local Ollama etc.)          R1
model_call            any other host (data leaves the machine)               R2
tool_call /           name pairs an export verb with a credential noun, or   R4
mcp_tool_call         a mutate verb with policy/audit/grant/permission
tool_call /           name contains an outbound/irreversible verb (send,     R3
mcp_tool_call         post, pay, delete, push, merge, deploy, install, share,
                      invite, ...), HTTP DELETE, or payment-like arguments
tool_call /           name contains only a read verb (get, list, search, ...) R0
mcp_tool_call         or the HTTP method is GET/HEAD
tool_call /           anything else (external effect, unknown semantics)     R2
mcp_tool_call
ui_* / browser_*      typing / keys / select into a password, card, CVV,    R4
                      SSN or one-time-code field (never grantable)
ui_* / browser_*      activating a control labelled send / submit / pay /   R3
                      buy / delete / confirm / transfer / ..., submitting a
                      form holding a secret or payment field, Win/Cmd chord
ui_click / ui_type /  anything else                                          R2
ui_key / browser_act
browser_navigate      any URL (host must also pass network_egress)           R2
browser_read          screenshot stored under the run dir                    R1
ui_observe /          accessibility tree / page text                         R0
browser_read
user_browser_act      the R4 / R3 / R2 rows above, plus (user profile only)   R3
                      a control naming account / security / password /
                      billing / 2FA settings; scroll is R1
user_browser_navigate any URL in the principal's own browser                 R2
user_browser_read     tab list / page text R0; visible-tab screenshot R1     R0/R1
====================  =====================================================  =====

User-browser kinds (LOCUS-350, D-25) drive the principal's own signed-in
browser through the Locus extension. On top of the table above, the
``user_browser`` policy applies the principal's browser autonomy tier
(Strict / Assisted / Trusted / Open): it may *add* an ask
(``require_approval``) and, in the Open tier only, report that the
principal's recorded consent covers an R3 action
(``tier_allows_irreversible``). Neither output can lift a policy deny or R4.

Computer-use kinds (LOCUS-341, :func:`classify_ui`) are classified from the
:class:`UiFacts` the tool perceived, not from model claims. They are always
tainted (13 §6): the taint gate skips standing grants for them, so only a
single-use human approval of the exact action turns their ``ask`` into ``allow``.

Decision table (tiered autonomy is the default, D-05):

* any policy deny, engine error/unavailable, unauthenticated caller → ``deny``
* R4 → ``deny`` always (11 §5: "R4 prohibited -- Never"; only the human acts)
* R3 → ``ask`` unless a grant covers it (reasons then carry
  ``gateway.grant_covers_action`` and ``gateway.grant:<id>``) or the human
  approved this exact action (single-use, run-scoped :class:`ApprovalLedger` entry)
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
* ``user_browser`` (LOCUS-350) -- every ``user_browser_*`` action instead of
  ``computer_use``: the principal's browser tier, the site lists and the floor
  (pairing, panic, secret fields, http(s) navigation, shared tabs).
* ``computer_use`` (LOCUS-341) -- every ``ui_*`` / ``browser_*`` action: app
  allow/deny lists for desktop actions, no data entry into secret fields, and
  ``http(s)`` only for ``browser_navigate`` (whose host also goes to
  ``network_egress``).
* ``model_call`` (LOCUS-336) -- ``agent_policy`` with operation ``llm_call``
  (``provider`` is ``local`` for a loopback engine; ``classification`` is the
  session's data ceiling, so ``restricted`` data never reaches a hosted engine),
  plus ``network_egress`` on the engine's host and ``budget_policy`` when the
  session carries token/cost figures. No prompt text enters the action.

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
    "tool_call",
    "file_write",
    "file_read",
    "network_egress",
    "process_exec",
    "mcp_tool_call",
    "model_call",
    "ui_observe",
    "ui_click",
    "ui_type",
    "ui_key",
    "browser_navigate",
    "browser_read",
    "browser_act",
    "user_browser_read",
    "user_browser_navigate",
    "user_browser_act",
]
#: Actions on the principal's own signed-in browser profile (LOCUS-350, D-25).
#: Evaluated by the ``user_browser`` policy (tiers + floor), not ``computer_use``.
USER_BROWSER_KINDS: frozenset[str] = frozenset(
    {"user_browser_read", "user_browser_navigate", "user_browser_act"}
)
#: Computer-use action kinds (LOCUS-341, doc 12). Each is its own agent_policy
#: operation, so a run's envelope must list the kinds it may use.
COMPUTER_USE_KINDS: frozenset[str] = frozenset(
    {
        "ui_observe",
        "ui_click",
        "ui_type",
        "ui_key",
        "browser_navigate",
        "browser_read",
        "browser_act",
    }
    | USER_BROWSER_KINDS
)
ACTION_KINDS: frozenset[str] = frozenset(
    {
        "tool_call",
        "file_write",
        "file_read",
        "network_egress",
        "process_exec",
        "mcp_tool_call",
        "model_call",
    }
    | COMPUTER_USE_KINDS
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
REASON_GRANT_ID_PREFIX = "gateway.grant:"
REASON_GRANT_ERROR = "gateway.grant_verifier_error"
REASON_TAINT_NO_GRANT = "gateway.taint_gate_grant_not_applicable"
REASON_TIER_ASK = "gateway.user_browser_tier_requires_approval"
REASON_OPEN_TIER = "gateway.user_browser_open_tier_consent"

#: Canonical agent_policy operation per action kind (tool/MCP calls use the tool name).
CANONICAL_OPERATION: Mapping[str, str] = {
    "file_read": "read_file",
    "file_write": "write_file",
    "process_exec": "process_exec",
    "network_egress": "network_egress",
    # agent_policy already carries the data-ceiling rule for ``llm_call``.
    "model_call": "llm_call",
    **{kind: kind for kind in COMPUTER_USE_KINDS},
}

#: Hosts that keep a model call on this machine (risk R1 instead of R2).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0:0:0:0:0:0:0:1"})

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


# Argument keys a grant pattern does not pin (free text the human approved the
# *kind* of, not the wording). Recipient and amount keys get their own facets.
_FREE_TEXT_KEYS = _CONTENT_KEYS | frozenset(
    {
        "subject",
        "title",
        "message",
        "msg",
        "comment",
        "description",
        "summary",
        "note",
        "notes",
        "caption",
    }
)
RECIPIENT_KEYS = frozenset(
    {
        "to",
        "cc",
        "bcc",
        "recipient",
        "recipients",
        "email",
        "emails",
        "to_address",
        "address",
        "addresses",
        "attendees",
        "invitees",
    }
)
AMOUNT_KEYS = frozenset({"amount", "price", "total", "cost", "amount_usd"})
#: Amount facet for an amount argument that is not a finite number: above any ceiling.
AMOUNT_UNPARSEABLE = 2**62
_EMAIL = re.compile(r"^[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")
_FACET_VALUE_MAX = 200


def _recipient_items(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;]", value) if item.strip()]
    if isinstance(value, Mapping):
        found = value.get("email") or value.get("address")
        return _recipient_items(found) if found else [json.dumps(value, sort_keys=True)]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_recipient_items(item))
        return out
    return [str(value)] if value is not None else []


def _amount_cents(value: Any) -> int:
    if isinstance(value, bool):
        return AMOUNT_UNPARSEABLE
    try:
        number = float(str(value).replace(",", "").strip().lstrip("$\u20ac\u00a3"))
    except (TypeError, ValueError):
        return AMOUNT_UNPARSEABLE
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return AMOUNT_UNPARSEABLE
    return min(int(round(number * 100)), AMOUNT_UNPARSEABLE)


def grant_facets(
    kind: str, args: Mapping[str, Any] | None, command_summary: str = ""
) -> dict[str, str]:
    """Facts a grant pattern is matched against, derived from the real arguments.

    * ``args`` (tool/MCP calls): canonical JSON of the redacted argument summary
      without free text, recipients and amounts -- a grant pins these exactly.
    * ``recipients``: sorted ``@domain`` for e-mail addresses, the lower-cased
      value otherwise -- a grant pins the domain set, not each address.
    * ``amount_cents``: the largest amount argument -- a grant sets a ceiling.
    * ``command`` (process_exec): the redacted command line, pinned exactly.

    Computed by :meth:`GatewayAction.create` from the arguments that will run;
    never supplied by the agent as a separate claim.
    """
    facets: dict[str, str] = {}
    if kind == "process_exec":
        facets["command"] = command_summary
        return facets
    if kind not in {"tool_call", "mcp_tool_call"}:
        return facets
    raw = {str(key): value for key, value in args.items()} if isinstance(args, Mapping) else {}
    lowered = {key.lower(): value for key, value in raw.items()}
    recipient_values = [lowered[key] for key in sorted(lowered) if key in RECIPIENT_KEYS]
    if recipient_values:
        tokens: set[str] = set()
        for value in recipient_values:
            for item in _recipient_items(value):
                match = _EMAIL.match(item)
                token = f"@{match.group(1).lower()}" if match else item.lower()
                tokens.add(token[:_FACET_VALUE_MAX])
        facets["recipients"] = ",".join(sorted(tokens))
    amounts = [_amount_cents(lowered[key]) for key in lowered if key in AMOUNT_KEYS]
    if amounts:
        facets["amount_cents"] = str(max(amounts))
    summary = summarize_args(
        {
            key: value
            for key, value in raw.items()
            if key.lower() not in _FREE_TEXT_KEYS | RECIPIENT_KEYS | AMOUNT_KEYS
        }
    )
    facets["args"] = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return facets


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


def is_loopback_host(host: str) -> bool:
    """True for a loopback host name or address (a model call that stays local)."""
    value = str(host or "").strip().lower()
    return value in _LOOPBACK_HOSTS or value.startswith("127.")


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


# --------------------------------------------------------------------------- #
# Computer use (LOCUS-341): perceived UI facts and their risk class
# --------------------------------------------------------------------------- #
#: Controls that put data into the UI (typing, key presses, choosing an option).
UI_ENTRY_CONTROLS = frozenset({"fill", "type", "press", "key", "select"})
UI_CONTROLS = frozenset(
    {
        "observe",
        "read",
        "screenshot",
        "navigate",
        "click",
        "fill",
        "type",
        "press",
        "select",
        "key",
        "tabs",
        "scroll",
    }
)
#: Read-shaped controls (perceive only).
UI_READ_CONTROLS = frozenset({"observe", "read", "screenshot", "navigate", "tabs"})
#: autocomplete tokens of secret-bearing inputs (WHATWG autofill field names).
_SECRET_AUTOCOMPLETE = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
    }
)
# Matched against the normalised (lower-case, word-split) name / label / field id.
_SECRET_FIELD_TEXT = re.compile(
    r"\b(password|passwd|passcode|passphrase|pwd|pin|cvv2?|cvc2?|csc|security code|"
    r"card number|credit card|debit card|ccnum|cc num(ber)?|cc csc|cc cvv|ssn|"
    r"social security|otp|one time (password|passcode|code)|verification code|2fa|mfa|totp|"
    r"auth(entication)? code)\b"
)
# Words on a control that make activating it outbound or irreversible (R3).
_UI_R3_WORDS = _R3_VERBS | frozenset(
    {
        "confirm",
        "checkout",
        "withdraw",
        "donate",
        "subscribe",
        "unsubscribe",
        "erase",
        "trash",
        "empty",
        "reset",
        "format",
        "download",
        "authorize",
        "authorise",
        "allow",
        "grant",
    }
)
# Chord modifiers that reach the OS shell rather than the focused app.
# Words on a control in the principal's own browser that reach account or
# security settings (D-25: those always ask below the Open tier).
_USER_BROWSER_R3_WORDS = frozenset(
    {
        "account",
        "password",
        "passwords",
        "passkey",
        "passkeys",
        "security",
        "2fa",
        "mfa",
        "authenticator",
        "recovery",
        "billing",
        "privacy",
        "permission",
        "permissions",
        "deactivate",
        "disable",
        "subscription",
    }
)
# Words on a control in the principal's own browser that make an R3 action a
# payment / purchase or an account-security change. These ask in every tier,
# Open included (D-25, principal decision 2026-10-04).
_PAYMENT_UI_WORDS = frozenset(
    {
        "pay",
        "payment",
        "purchase",
        "buy",
        "order",
        "checkout",
        "transfer",
        "refund",
        "charge",
        "wire",
        "donate",
        "subscribe",
        "withdraw",
        "billing",
        "subscription",
        "book",
    }
)
_ACCOUNT_SECURITY_UI_WORDS = _USER_BROWSER_R3_WORDS - {"billing", "subscription"}
_OS_LEVEL_KEYS = frozenset({"win", "windows", "meta", "cmd", "command", "super", "os"})
_ENTER_KEYS = frozenset({"enter", "return", "numpadenter"})
_UI_TEXT_MAX = 300


def _norm_ui_text(*parts: str) -> str:
    spaced = _CAMEL.sub(" ", " ".join(str(part or "") for part in parts))
    return " ".join(token for token in _TOKEN_SPLIT.split(spaced.lower()) if token)


@dataclass(frozen=True)
class UiFacts:
    """What a computer-use tool perceived about an action's UI target.

    Collected by the tool from the live DOM or accessibility tree immediately
    before acting (``locus_runtime.computer_use``), never taken from the model's
    claims. Every text field here comes from the screen and is **untrusted**
    (13 §6): it can only raise the risk class, and an action carrying it never
    has an ``ask`` turned into ``allow`` by a standing grant (taint gate, P8).
    """

    surface: str  # "browser" | "desktop"
    control: str  # one of UI_CONTROLS
    app: str = ""  # desktop: executable / bundle id; browser: the agent browser
    role: str = ""
    name: str = ""
    label: str = ""  # other descriptive text: placeholder, title, label element
    input_type: str = ""
    autocomplete: str = ""
    field_id: str = ""  # name / id / automation id
    is_password: bool = False
    submits_form: bool = False  # a submit control, or Enter in a form field
    form_sensitive: bool = False  # the enclosing form holds a secret / payment field
    form_text: str = ""  # text of the enclosing form's submit controls
    key: str = ""  # key chord for press / key
    url_scheme: str = ""  # browser_navigate: the URL scheme
    # User browser (LOCUS-350): registrable site (eTLD+1) of the tab or target
    # URL, from the browser's own tab URL; and whether the principal shared the
    # tab with Locus from the extension UI (or the agent opened it).
    site: str = ""
    tab_shared: bool = False

    @classmethod
    def create(cls, **kwargs: Any) -> UiFacts:
        """Normalised, bounded facts (text truncated, flags coerced to bool)."""
        clean: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in {"is_password", "submits_form", "form_sensitive", "tab_shared"}:
                clean[key] = bool(value)
            else:
                clean[key] = str(value or "").strip()[:_UI_TEXT_MAX]
        return cls(**clean)

    @property
    def sensitive_field(self) -> bool:
        """The target holds a password, payment card, CVV, SSN or one-time code."""
        if self.is_password or self.input_type.lower() == "password":
            return True
        if set(self.autocomplete.lower().split()) & _SECRET_AUTOCOMPLETE:
            return True
        return bool(_SECRET_FIELD_TEXT.search(_norm_ui_text(self.name, self.label, self.field_id)))

    def as_summary(self) -> dict[str, str]:
        """Redacted, bounded description for audit (typed values are never held here)."""
        pairs = (
            ("ui_surface", self.surface),
            ("ui_control", self.control),
            ("ui_app", self.app),
            ("ui_role", self.role),
            ("ui_name", self.name),
            ("ui_key", self.key),
            ("ui_site", self.site),
        )
        return {key: redact_text(value, limit=120) for key, value in pairs if value}


def classify_ui(kind: str, ui: UiFacts | None) -> RiskClass:
    """Risk class of a computer-use action from its perceived UI facts.

    * observe / read → R0; a stored screenshot → R1
    * navigate → R2 (the egress host must also pass ``network_egress``)
    * entering data (fill / type / press / key / select) into a password,
      payment-card, CVV, SSN or one-time-code field → R4, never grantable
    * a chord with an OS-level modifier (Win / Cmd / Meta) → R3
    * activating a control (click, select, Enter) whose text says send /
      submit / pay / buy / delete / confirm / transfer / ... → R3; submitting a
      form that holds a secret or payment field, or whose submit control says
      so → R3
    * any other click / fill / type / press / select / key → R2
    * missing facts or an unknown control → R4 (fail closed)

    Screen text can only *raise* the class: a page that labels its Delete
    button "OK" gets the default R2, never less.
    """
    if kind in {"ui_observe", "browser_read", "user_browser_read"}:
        return RiskClass.R1 if ui is not None and ui.control == "screenshot" else RiskClass.R0
    if kind in {"browser_navigate", "user_browser_navigate"}:
        return RiskClass.R2
    if ui is None or ui.control not in UI_CONTROLS:
        return RiskClass.R4
    control = ui.control
    if control in UI_READ_CONTROLS:
        return RiskClass.R4  # a read-shaped control on an acting kind is malformed
    if control == "scroll":
        # Scrolling only moves the viewport; only the user browser offers it.
        return RiskClass.R1 if kind == "user_browser_act" else RiskClass.R4
    if control in UI_ENTRY_CONTROLS and ui.sensitive_field:
        return RiskClass.R4
    chord = _name_tokens(ui.key.replace("+", " "))
    if chord & _OS_LEVEL_KEYS:
        return RiskClass.R3
    r3_words = _UI_R3_WORDS | (_USER_BROWSER_R3_WORDS if kind == "user_browser_act" else set())
    if control in {"click", "select"} or chord & _ENTER_KEYS:
        if _name_tokens(_norm_ui_text(ui.name, ui.label)) & r3_words:
            return RiskClass.R3
        if ui.submits_form and (
            ui.form_sensitive or _name_tokens(_norm_ui_text(ui.form_text)) & r3_words
        ):
            return RiskClass.R3
    return RiskClass.R2


def protected_ui_kind(kind: str, ui: UiFacts | None) -> str:
    """``"payment"``, ``"account_security"`` or ``""`` for a user-browser action.

    Payments / purchases and account-security changes ask in every browser
    tier, Open included. Like :func:`classify_ui` this reads only perceived
    screen facts and can only add protection: missing facts on an acting
    control count as protected, and submitting a form that holds a secret or
    payment-card field counts as a payment (the form can't be told apart from
    a checkout by its fields alone).
    """
    if kind != "user_browser_act":
        return ""
    if ui is None:
        return "payment"
    if ui.control not in {"click", "select", "press", "type", "fill"}:
        return ""
    chord = _name_tokens(ui.key.replace("+", " "))
    activates = ui.control in {"click", "select"} or bool(chord & _ENTER_KEYS)
    if not activates:
        return ""
    words = _name_tokens(_norm_ui_text(ui.name, ui.label))
    if ui.submits_form:
        words = words | _name_tokens(_norm_ui_text(ui.form_text))
        if ui.form_sensitive:
            return "payment"
    if words & _PAYMENT_UI_WORDS:
        return "payment"
    if words & _ACCOUNT_SECURITY_UI_WORDS:
        return "account_security"
    return ""


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
    egress_host: str = "",
    ui: UiFacts | None = None,
) -> RiskClass:
    """Deterministic risk class for an action (table in the module docstring)."""
    if kind in COMPUTER_USE_KINDS:
        return classify_ui(kind, ui)
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
    if kind == "model_call":
        # Data leaves the machine unless the engine is on loopback (D-21, 13 §4).
        return RiskClass.R1 if is_loopback_host(egress_host) else RiskClass.R2
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
    # Highest data class in this run's context (area data ceiling seam, 10 §6 /
    # 13 §4). Fed to agent_policy's ``llm_call`` rule: ``restricted`` data may
    # only go to a local engine. Empty = not classified (the rule does not fire).
    data_classification: str = ""
    # Computer use (LOCUS-341): desktop apps this run may drive (executable or
    # bundle id, case-insensitive). Empty = no desktop app. The computer_use
    # policy's built-in deny list (password managers, banking, OS security and
    # credential prompts, shells, the user's own browsers, Locus) always wins.
    allowed_apps: tuple[str, ...] = ()
    # Extra apps denied for this run, on top of the built-in list and
    # ``LOCUS_COMPUTER_USE_DENIED_APPS``. Deny lists only ever widen.
    denied_apps: tuple[str, ...] = ()


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
    # Grant-matching facts derived from the real arguments (see grant_facets).
    facets: Mapping[str, str] = field(default_factory=dict)
    # Computer use: what the tool perceived about the UI target (LOCUS-341).
    ui: UiFacts | None = None
    # Arguments derive from untrusted content (screen / page text, 13 §6). A
    # tainted action's ask is never turned into allow by a standing grant; only
    # a single-use human approval of this exact action can. Computer-use kinds
    # are always treated as tainted by the gateway.
    tainted: bool = False

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
        ui: UiFacts | None = None,
        tainted: bool = False,
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
            egress_host=egress_host,
            ui=ui,
        )
        summary = summarize_args(args)
        digest_material: dict[str, Any] = {"args": args, "command": command, "target": target}
        if ui is not None:
            summary = {**summary, **ui.as_summary()}
            digest_material["ui"] = ui
        return cls(
            caller=caller,
            kind=kind,
            tool=str(tool or ""),
            target=redact_text(target, limit=400),
            args_summary=summary,
            risk=risk,
            args_digest=args_digest(digest_material),
            command_summary=command_text,
            executable=str(executable or ""),
            jail=jail,
            egress_host=str(egress_host or "").strip().lower(),
            method=str(method or "").upper(),
            facets=grant_facets(kind, args, command_text),
            ui=ui,
            tainted=bool(tainted) or kind in COMPUTER_USE_KINDS,
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
    """Step 3: Biscuit capability grants covering an action pattern (13 §5).

    Implementations may also offer ``match(action, capabilities)`` returning an
    object with a ``grant_id`` (or ``None``); the gateway then records which
    grant authorized the action (P11).
    """

    def covers(self, action: GatewayAction, capabilities: Capabilities) -> bool: ...


class NoGrants:
    """No grant authority is configured, so nothing is covered (fail closed)."""

    ready = False

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
    def grants(self) -> GrantVerifier:
        """The installed grant verifier (:class:`NoGrants` when none is configured)."""
        return self._grants

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
            egress_host=action.egress_host,
            ui=action.ui,
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
        # User browser tier outputs (LOCUS-350). Both start closed: a missing or
        # malformed output means "ask" for the tier and "no consent" for Open.
        tier_ask = action.kind in USER_BROWSER_KINDS
        open_tier_consent = False
        for policy, payload in policy_inputs(action, caps, tool_calls_used=tool_calls_used):
            try:
                result: Decision = self._engine.decide(policy, payload)
            except Exception:  # noqa: BLE001 - any engine failure denies
                logger.exception("gateway.engine_error", extra={"policy": policy})
                denied = True
                reasons.append(f"{REASON_ENGINE_ERROR}:{policy}")
                continue
            if policy == "user_browser":
                tier_ask, open_tier_consent = _user_browser_tier_outputs(result, payload)
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
        if action.kind in USER_BROWSER_KINDS:
            # The principal's browser tier can add an ask (Strict / Assisted /
            # Trusted) and, in the Open tier only, cover an R3 action with the
            # principal's recorded consent. R2-under-supervised still asks.
            if tier_ask:
                reasons.append(REASON_TIER_ASK)
                return self._ask_or_approved(action, record, reasons, policy_version, audit_id)
            if open_tier_consent and action.risk == RiskClass.R3:
                reasons.append(REASON_OPEN_TIER)
                needs_ask = caps.autonomy_tier == "supervised"
        if needs_ask:
            # Taint gate (13 §6, P8): screen / page text never justifies turning an
            # ask into allow, so standing grants do not apply to tainted actions.
            # Only a human approval of this exact action can (_ask_or_approved).
            tainted = action.tainted or action.kind in COMPUTER_USE_KINDS
            grant_id = None
            if tainted:
                reasons.append(REASON_TAINT_NO_GRANT)
            else:
                grant_id = self._covering_grant(action, caps, reasons)
            if grant_id is not None:
                reasons.append(REASON_GRANT)
                if grant_id:
                    reasons.append(f"{REASON_GRANT_ID_PREFIX}{grant_id}")
            else:
                return self._ask_or_approved(action, record, reasons, policy_version, audit_id)
        decision = self._finish(action, record, "allow", tuple(reasons), policy_version, audit_id)
        if decision.outcome == "allow" and action.kind in {"tool_call", "mcp_tool_call"}:
            with self._lock:
                record.tool_calls_used += 1
        return decision

    def _covering_grant(
        self, action: GatewayAction, caps: Capabilities, reasons: list[str]
    ) -> str | None:
        """The id of a grant covering ``action`` ("" if unnamed), else None.

        Only reached for an action policy already allowed and that is below R4.
        A verifier error means no grant (fail closed to ``ask``).
        """
        try:
            match = getattr(self._grants, "match", None)
            if callable(match):
                found = match(action, caps)
                return None if found is None else str(getattr(found, "grant_id", "") or "")
            return "" if self._grants.covers(action, caps) else None
        except Exception:  # noqa: BLE001 - a broken verifier grants nothing
            logger.exception("gateway.grant_verifier_error")
            reasons.append(REASON_GRANT_ERROR)
            return None

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
    if action.kind in USER_BROWSER_KINDS:
        plan.append(("user_browser", user_browser_input(action, caps)))
    elif action.kind in COMPUTER_USE_KINDS:
        plan.append(("computer_use", computer_use_input(action, caps)))
    if caps.budget is not None:
        plan.append(("budget_policy", caps.budget.as_input()))
    return plan


def configured_denied_apps() -> tuple[str, ...]:
    """Extra denied apps from ``LOCUS_COMPUTER_USE_DENIED_APPS`` (comma-separated)."""
    raw = str(os.getenv("LOCUS_COMPUTER_USE_DENIED_APPS") or "")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


def computer_use_input(action: GatewayAction, caps: Capabilities) -> dict[str, Any]:
    """``computer_use`` policy input: surface, control, app and the app lists.

    The surface comes from the action kind (``browser_*`` → browser, ``ui_*`` →
    desktop), never from the tool's facts, so a desktop action cannot present
    itself as a browser one to skip the app allowlist.
    """
    ui = action.ui
    surface = "browser" if action.kind.startswith("browser_") else "desktop"
    default_control = {
        "ui_observe": "observe",
        "browser_read": "read",
        "browser_navigate": "navigate",
    }.get(action.kind, "")
    return {
        "action": action.kind,
        "surface": surface,
        "control": (ui.control if ui is not None else "") or default_control,
        "app": (ui.app if ui is not None else "").strip().lower(),
        "allowed_apps": sorted({str(app).strip().lower() for app in caps.allowed_apps if app}),
        "denied_apps": sorted(
            {str(app).strip().lower() for app in caps.denied_apps if app}
            | set(configured_denied_apps())
        ),
        "sensitive_field": bool(ui is not None and ui.sensitive_field),
        "url_scheme": (ui.url_scheme if ui is not None else "").lower(),
        "egress_host": action.egress_host,
    }


def _user_browser_tier_outputs(result: Decision, payload: Mapping[str, Any]) -> tuple[bool, bool]:
    """``(require_approval, open_tier_consent)`` from a ``user_browser`` decision.

    Fails closed: unless the policy says ``require_approval: false`` the action
    asks; Open-tier consent counts only when the policy reports it *and* the
    input the gateway itself built says the tier is ``open`` with consent.
    """
    outputs = getattr(result, "outputs", None) or {}
    require = outputs.get("require_approval")
    tier_ask = require is not False
    consent = (
        outputs.get("tier_allows_irreversible") is True
        and payload.get("tier") == "open"
        and payload.get("tier_consent") is True
    )
    return tier_ask, consent and not tier_ask


def user_browser_input(action: GatewayAction, caps: Capabilities) -> dict[str, Any]:  # noqa: ARG001
    """``user_browser`` policy input (LOCUS-350, D-25).

    The tier, its consent record, the site lists, the pairing state and the
    panic latch come from process state the principal controls (the backend's
    tier store, the extension relay, the computer-use controller) -- never
    from the action or the run envelope, so neither the agent nor page content
    can change the tier. ``site`` and ``tab_shared`` come from the tool's
    perceived facts (the browser's own tab URL and the extension's share list).
    """
    from locus_runtime.computer_use.user_browser.state import user_browser_snapshot

    ui = action.ui
    default_control = {
        "user_browser_read": "observe",
        "user_browser_navigate": "navigate",
    }.get(action.kind, "")
    return {
        "action": action.kind,
        "profile": "user",
        "control": (ui.control if ui is not None else "") or default_control,
        "site": (ui.site if ui is not None else "").strip().lower(),
        "url_scheme": (ui.url_scheme if ui is not None else "").lower(),
        "tab_shared": bool(ui is not None and ui.tab_shared),
        "sensitive_field": bool(ui is not None and ui.sensitive_field),
        "risk": action.risk.label,
        # Payments and account-security changes ask in every tier (D-25).
        "protected_action": bool(protected_ui_kind(action.kind, ui)),
        **user_browser_snapshot(),
    }


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
    if action.kind == "model_call":
        # The gateway derives "local" from the egress host itself; the caller's
        # provider label never makes a hosted call look local.
        provider = action.tool.split(":", 1)[1] if ":" in action.tool else action.tool
        payload["provider"] = "local" if is_loopback_host(action.egress_host) else provider
        if caps.data_classification:
            payload["classification"] = caps.data_classification
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


def grants_enforcing() -> bool:
    """Posture fact: the enforcing gateway verifies Biscuit grants with loaded keys."""
    gateway = _PROCESS_GATEWAY
    if not (isinstance(gateway, Gateway) and gateway.healthy):
        return False
    return getattr(gateway.grants, "ready", False) is True


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
