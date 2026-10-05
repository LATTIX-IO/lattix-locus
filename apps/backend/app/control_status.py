"""Single source of truth for security-control posture (LOCUS-313, P9).

Every posture surface (``/platform/security-policy``, ``/healthz/details``,
``/audit/atf-alignment-report`` and the UI views built on them) reports
controls through :func:`build_control_status_report`. A control's state is
derived from runtime facts -- whether the module is imported, whether the code
path is wired, whether the service is configured -- never from a config flag
alone.

States:

* ``enforced``   -- the control is on the execution path and active.
* ``degraded``   -- active, but weaker than its specification.
* ``off``        -- not on the execution path (disabled, or declared only).
* ``unverified`` -- something is present, but nothing proves it is enforced.

Declared-only components (OPA server, Biscuit, Vault broker, Envoy authz,
NATS) can never report ``enforced`` here; that requires wiring them into the
execution path first and updating this module with the check that proves it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ControlState = Literal["enforced", "degraded", "off", "unverified"]
CONTROL_STATES: tuple[ControlState, ...] = ("enforced", "degraded", "off", "unverified")

PresidioState = Literal["loaded", "unavailable", "not_loaded"]

# Sandbox strategies that confine the process with a real OS/container boundary.
_CONFINING_SANDBOX_STRATEGIES = frozenset(
    {"kernel-bwrap", "kernel-seatbelt", "windows-appcontainer", "hardened-docker"}
)
# What the harness reports when no confining tier exists on this host.
_NO_SANDBOX_STRATEGY = "unavailable"
# Strategies the planner can name but that Locus does not implement itself.
_DELEGATED_SANDBOX_STRATEGIES = frozenset({"k8s-gvisor", "k8s-kata"})

_ENVOY_AUTHZ_MARKERS = ("ext_authz", "jwt_authn")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ENVOY_CONFIG = _REPO_ROOT / "envoy" / "envoy.yaml"


@dataclass(frozen=True)
class ControlStatus:
    id: str
    label: str
    state: ControlState
    evidence: str

    def to_payload(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "state": self.state, "evidence": self.evidence}


@dataclass(frozen=True)
class PostureFacts:
    """Runtime facts the control states are derived from.

    Collected by :func:`collect_posture_facts`; tests construct it directly.
    """

    auth_required: bool
    a2a_signed_messages: bool
    a2a_trusted_subject_count: int
    a2a_replay_protection: bool
    egress_allowlist: bool
    guardrail_signals_enabled: bool
    guardrail_signal_enforcement: str
    presidio_flag: bool
    presidio_state: PresidioState
    audit_durable: bool
    sandbox_requested: bool | None
    sandbox_strategy: str | None
    # locus_runtime.policy_engine.policy_engine_available(): an engine can run here.
    policy_engine_available: bool
    biscuit_loaded: bool
    vault_addr_configured: bool
    envoy_authz_filters: bool | None
    nats_loaded: bool
    # locus_tooling.native_secrets.secret_storage_mode(); None if undeterminable.
    secret_storage_mode: str | None
    # locus_runtime.gateway.gateway_enforcing(): a Gateway with a running engine is
    # installed in this process (LOCUS-332). CI evidence that every execution entry
    # point calls it: tests/harness/test_gateway_bypass.py.
    gateway_enforcing: bool = False
    # locus_runtime.gateway.grants_enforcing(): that gateway verifies Biscuit grants
    # (BiscuitGrantVerifier installed, grant authority keys loaded) -- LOCUS-334.
    grants_enforcing: bool = False
    # locus_runtime.model_client.model_gate_installed(): the backend installed its
    # gateway-bound model gate, so every ModelClient authorizes a ``model_call``
    # (engine, egress host, data ceiling, budget) before each request -- LOCUS-336.
    model_gate_installed: bool = False
    # locus_runtime.loop_runner.loop_status(): the self-improvement loop's kill-switch
    # state and last run (LOCUS-338). Operational status, not a security control.
    self_improvement_loop: Mapping[str, Any] | None = None
    # locus_runtime.computer_use.controller_installed(): a computer-use controller
    # is wired in this process, so browser / desktop tools run under its panic
    # latch and authorize every UI action at the gateway -- LOCUS-341.
    computer_use_installed: bool = False
    # The principal's own browser (LOCUS-350, D-25): pairing, connection and the
    # browser tier with its consent record, from
    # locus_runtime.computer_use.user_browser (None if undeterminable).
    user_browser: Mapping[str, Any] | None = None
    # locus_runtime.telemetry.posture() (LOCUS-375): the local trace store and each
    # external exporter with its destination class (None if undeterminable).
    telemetry: Mapping[str, Any] | None = None


def _policy_engine(facts: PostureFacts) -> ControlStatus:
    # "enforced" only when the engine can run here AND the gateway (the single PEP
    # on every execution entry point) is installed with that engine running (P9).
    if facts.policy_engine_available and facts.gateway_enforcing:
        return ControlStatus(
            "policy_engine_rego",
            "Policy engine (Rego)",
            "enforced",
            "locus_runtime.gateway.Gateway evaluates policies/*.rego through a running OPA "
            "sidecar before every harness exec/file write, tool-call node and MCP tool call "
            "(fail closed); bypass test: tests/harness/test_gateway_bypass.py.",
        )
    if facts.policy_engine_available:
        return ControlStatus(
            "policy_engine_rego",
            "Policy engine (Rego)",
            "unverified",
            "locus_runtime.policy_engine can evaluate policies/*.rego with OPA "
            "(loopback sidecar, fail closed), but no gateway with a running engine is "
            "installed in this process; side effects are denied until it is.",
        )
    return ControlStatus(
        "policy_engine_rego",
        "Policy engine (Rego)",
        "off",
        "No Rego engine is available (no OPA binary found and LOCUS_OPA_URL is unset); "
        "no request path evaluates policies/*.rego.",
    )


def _capability_tokens(facts: PostureFacts) -> ControlStatus:
    # "enforced" only when the running gateway verifies Biscuit grants with loaded
    # keys (P9); a loaded library alone proves nothing.
    if facts.grants_enforcing and facts.gateway_enforcing:
        return ControlStatus(
            "capability_tokens_biscuit",
            "Capability grants (Biscuit)",
            "enforced",
            "locus_runtime.grants.BiscuitGrantVerifier is installed in the enforcing gateway "
            "with Ed25519 grant keys loaded from the secure secret store; R3 actions run "
            "without asking only under a valid, unrevoked, unexpired covering grant "
            "(tests/unit/test_gateway_grants.py, tests/policy/test_gateway_opa.py).",
        )
    if facts.biscuit_loaded:
        return ControlStatus(
            "capability_tokens_biscuit",
            "Capability grants (Biscuit)",
            "unverified",
            "biscuit_auth is loaded, but no gateway in this process verifies grants with a "
            "loaded grant authority key (LOCUS_GRANT_AUTHORITY_KEY); R3 actions always ask.",
        )
    return ControlStatus(
        "capability_tokens_biscuit",
        "Capability grants (Biscuit)",
        "off",
        "No Biscuit library is loaded; no capability grants are verified and R3 actions "
        "always ask.",
    )


def _model_calls(facts: PostureFacts) -> ControlStatus:
    label = "Gated model calls"
    # "enforced" needs both the wiring (the backend gate is the default for every
    # model client) and an enforcing gateway behind it (P9).
    if facts.model_gate_installed and facts.gateway_enforcing:
        return ControlStatus(
            "model_calls_gated",
            label,
            "enforced",
            "Every model request (backend chat, streaming, harness nodes, skill eval) is a "
            "gateway model_call: engine allowed, egress host allowlisted, data ceiling and "
            "budget checked, usage audited without prompt text "
            "(tests/unit/test_model_client.py, tests/policy/test_model_call_opa.py, "
            "apps/backend/tests/test_model_calls.py).",
        )
    if facts.model_gate_installed:
        return ControlStatus(
            "model_calls_gated",
            label,
            "unverified",
            "Model clients authorize every request through the gateway, but no gateway with "
            "a running policy engine is installed in this process; model calls are denied "
            "until it is.",
        )
    return ControlStatus(
        "model_calls_gated",
        label,
        "off",
        "No model gate is installed in this process; model calls are not authorized by "
        "the gateway.",
    )


def _computer_use(facts: PostureFacts) -> ControlStatus:
    label = "Computer use containment"
    if facts.computer_use_installed and facts.gateway_enforcing:
        return ControlStatus(
            "computer_use",
            label,
            "enforced",
            "Every browser / desktop action is a gateway ui_* / browser_* action classified "
            "from the perceived element (secret-field typing R4, send/pay/delete R3, taint "
            "gate: no grant turns an ask into allow), app allow/deny lists and http(s)-only "
            "navigation in the computer_use policy, egress allowlist at navigation, request "
            "interception and a loopback egress proxy, panic latch (tests/unit/"
            "test_computer_use_*.py, tests/policy/test_computer_use_opa.py). Verified on "
            "Windows (UIA) and Chromium; macOS (AX) is unverified.",
        )
    if facts.computer_use_installed:
        return ControlStatus(
            "computer_use",
            label,
            "unverified",
            "A computer-use controller is installed, but no gateway with a running policy "
            "engine is; every UI action is denied until one is.",
        )
    return ControlStatus(
        "computer_use",
        label,
        "off",
        "Computer use is not wired in this process; no browser or desktop actions run here.",
    )


def _user_browser(facts: PostureFacts) -> ControlStatus:
    """P32: a tier above strict is shown with who accepted it and when."""
    label = "User browser (own profile)"
    info = facts.user_browser or {}
    if not info.get("paired"):
        return ControlStatus(
            "user_browser",
            label,
            "off",
            "No browser is paired; the agent cannot use the principal's own browser profiles.",
        )
    if not facts.gateway_enforcing:
        return ControlStatus(
            "user_browser",
            label,
            "unverified",
            "A browser is paired, but no gateway with a running policy engine is installed; "
            "every user-browser action is denied until one is.",
        )
    floor = (
        "Floor in every tier: gateway mediation and audit of every action (user_browser "
        "policy), panic stops the extension, page content is tainted, secret fields are never "
        "read or typed (R4)."
    )
    tier = str(info.get("effective_tier") or "strict")
    connected = "connected" if info.get("connected") else "not connected"
    if tier == "strict":
        return ControlStatus(
            "user_browser",
            label,
            "enforced",
            f"Paired ({connected}); tier strict: the agent reads only tabs the principal "
            f"shares and every other action asks. {floor}",
        )
    consent = info.get("consent") if isinstance(info.get("consent"), Mapping) else {}
    who = str(consent.get("actor") or "unknown")
    when = str(consent.get("recorded_at_iso") or "unknown time")
    return ControlStatus(
        "user_browser",
        label,
        "degraded",
        f"Paired ({connected}); tier {tier} accepted by {who} at {when} (informed consent, "
        f"P32): fewer actions ask than at strict. {floor}",
    )


_DESTINATION_TEXT = {
    "loopback": "a collector on this machine",
    "remote": "another host; data leaves the machine",
    "hosted_proprietary": "hosted, proprietary; data leaves the machine",
    "local": "this machine",
}


def _telemetry(facts: PostureFacts) -> ControlStatus:
    """Where traces go (P14). Not a security control in itself: it is reported so
    that any egress of run data is visible and labelled."""
    label = "Telemetry stays local"
    info = facts.telemetry or {}
    if not info.get("configured"):
        return ControlStatus(
            "telemetry_local",
            label,
            "off",
            "Telemetry is not configured in this process; no spans are recorded.",
        )
    local = info.get("local") if isinstance(info.get("local"), Mapping) else {}
    external = [e for e in info.get("external") or [] if isinstance(e, Mapping)]
    parts: list[str] = []
    if local.get("state") == "active":
        parts.append("Local SQLite trace store on (app home)")
    else:
        parts.append(
            f"Local trace store {local.get('state') or 'off'} ({local.get('reason') or 'n/a'})"
        )
    active: list[Mapping[str, Any]] = []
    for exporter in external:
        state = str(exporter.get("state") or "off")
        if state == "off":
            continue
        where = _DESTINATION_TEXT.get(str(exporter.get("destination") or ""), "unknown")
        text = f"{exporter.get('kind')} exporter {state} to {exporter.get('host') or '?'} ({where})"
        if state == "blocked":
            text += f", reason {exporter.get('reason') or 'unknown'}"
        else:
            active.append(exporter)
        parts.append(text)
    if not any(e.get("state") != "off" for e in external):
        parts.append("no external exporter enabled")
    capture = bool(info.get("capture_content"))
    parts.append(
        "message and tool content captured (redacted, truncated; payloads pruned after "
        f"{info.get('payload_retention_days')} days)"
        if capture
        else "content capture off"
    )
    evidence = (
        "; ".join(parts)
        + ". Every exported string is redacted on the export path; secrets never reach an "
        "exporter (tests/unit/test_telemetry.py)."
    )
    if local.get("state") != "active" and not active:
        return ControlStatus("telemetry_local", label, "off", evidence)
    if active or capture:
        return ControlStatus("telemetry_local", label, "degraded", evidence)
    return ControlStatus("telemetry_local", label, "enforced", evidence)


def _vault(facts: PostureFacts) -> ControlStatus:
    if facts.vault_addr_configured:
        return ControlStatus(
            "secret_broker_vault",
            "Secret broker (Vault)",
            "unverified",
            "VAULT_ADDR is set; VaultClient is only used to resolve 'secret/...' integration "
            "references and is not a runtime secret broker.",
        )
    return ControlStatus(
        "secret_broker_vault",
        "Secret broker (Vault)",
        "off",
        "VAULT_ADDR is not set; VaultClient exists but is not integrated into the runtime.",
    )


def _secret_storage(facts: PostureFacts) -> ControlStatus:
    label = "Secret storage (OS keychain)"
    mode = facts.secret_storage_mode
    if mode == "keychain":
        return ControlStatus(
            "secret_storage",
            label,
            "enforced",
            "Native secrets are held in the OS keychain (Credential Manager / macOS Keychain "
            "/ Secret Service) via keyring.",
        )
    if mode == "dpapi_file":
        return ControlStatus(
            "secret_storage",
            label,
            "degraded",
            "Credential Manager was unusable; native secrets are a DPAPI-encrypted file "
            "(Windows user scope) under the app home.",
        )
    if mode == "plaintext_file_opt_in":
        return ControlStatus(
            "secret_storage",
            label,
            "degraded",
            "Native secrets are a 0600 plaintext file (opt-in, LOCUS-317): "
            "LOCUS_SECRETS_ALLOW_FILE=1 and no Secret Service is available.",
        )
    if mode == "unavailable":
        return ControlStatus(
            "secret_storage",
            label,
            "off",
            "No secure secret store is usable on this host; native secret storage is "
            "refused (fail closed).",
        )
    if mode == "env_only":
        evidence = (
            "Secrets were supplied through the environment; where they are stored is "
            "outside Locus and cannot be verified."
        )
    else:
        evidence = "The secret storage mode could not be determined."
    return ControlStatus("secret_storage", label, "unverified", evidence)


def _envoy(facts: PostureFacts) -> ControlStatus:
    if facts.envoy_authz_filters:
        return ControlStatus(
            "gateway_envoy_authz",
            "Envoy authz filters",
            "unverified",
            "envoy/envoy.yaml declares ext_authz/jwt_authn filters, but the backend cannot "
            "verify that Envoy fronts its API.",
        )
    if facts.envoy_authz_filters is None:
        evidence = "No Envoy configuration found; the backend is the only enforcement point."
    else:
        evidence = (
            "envoy/envoy.yaml has no ext_authz or jwt_authn filter; the backend is the only "
            "enforcement point."
        )
    return ControlStatus("gateway_envoy_authz", "Envoy authz filters", "off", evidence)


def _nats(facts: PostureFacts) -> ControlStatus:
    if facts.nats_loaded:
        return ControlStatus(
            "messaging_nats",
            "NATS messaging",
            "unverified",
            "A NATS client module is loaded but no Locus code path publishes through it.",
        )
    return ControlStatus(
        "messaging_nats",
        "NATS messaging",
        "off",
        "No NATS client module is imported by the runtime.",
    )


def _authentication(facts: PostureFacts) -> ControlStatus:
    if facts.auth_required:
        return ControlStatus(
            "api_authentication",
            "API authentication",
            "enforced",
            "main._enforce_request_authn rejects unauthenticated requests "
            "(require_authenticated_requests resolved from the runtime profile).",
        )
    return ControlStatus(
        "api_authentication",
        "API authentication",
        "off",
        "require_authenticated_requests resolves to false; main._enforce_request_authn "
        "admits anonymous actors.",
    )


def _a2a_signing(facts: PostureFacts) -> ControlStatus:
    if not facts.a2a_signed_messages:
        return ControlStatus(
            "a2a_signed_messages",
            "Signed agent-to-agent messages",
            "off",
            "a2a_require_signed_messages is false; the signature check is skipped.",
        )
    if facts.a2a_trusted_subject_count <= 0:
        return ControlStatus(
            "a2a_signed_messages",
            "Signed agent-to-agent messages",
            "degraded",
            "Signature check is on but no trusted subjects are configured.",
        )
    return ControlStatus(
        "a2a_signed_messages",
        "Signed agent-to-agent messages",
        "enforced",
        "main._enforce_request_authn verifies x-locus-signature against "
        f"{facts.a2a_trusted_subject_count} trusted subject(s) on internal/A2A routes.",
    )


def _a2a_replay(facts: PostureFacts) -> ControlStatus:
    if facts.a2a_replay_protection and facts.a2a_signed_messages:
        return ControlStatus(
            "a2a_replay_protection",
            "A2A replay protection",
            "enforced",
            "Nonce reuse is rejected inside the signed-message check (store.a2a_seen_nonces).",
        )
    return ControlStatus(
        "a2a_replay_protection",
        "A2A replay protection",
        "off",
        "Replay protection only runs inside the signed-message check, which is off."
        if facts.a2a_replay_protection
        else "a2a_replay_protection is false.",
    )


def _egress(facts: PostureFacts) -> ControlStatus:
    if not facts.egress_allowlist:
        return ControlStatus(
            "egress_allowlist",
            "Egress allowlist",
            "off",
            "enforce_egress_allowlist is false; tool and retrieval hosts are not checked.",
        )
    return ControlStatus(
        "egress_allowlist",
        "Egress allowlist",
        "degraded",
        "Host checks run for workflow tool and retrieval nodes only; there is no per-run "
        "egress proxy for agent or sandboxed processes.",
    )


def _guardrail_signals(facts: PostureFacts) -> ControlStatus:
    mode = str(facts.guardrail_signal_enforcement or "").strip().lower()
    if not facts.guardrail_signals_enabled or mode in {"", "off"}:
        return ControlStatus(
            "guardrail_signals",
            "Prompt-injection / exfiltration signals",
            "off",
            "Platform guardrail signals are disabled.",
        )
    if mode in {"block_high", "raise_high"}:
        return ControlStatus(
            "guardrail_signals",
            "Prompt-injection / exfiltration signals",
            "enforced",
            "main._evaluate_guardrail runs heuristic signal checks on chat, tool and graph "
            f"input/output (mode '{mode}').",
        )
    return ControlStatus(
        "guardrail_signals",
        "Prompt-injection / exfiltration signals",
        "degraded",
        f"Heuristic signals run in '{mode}' mode and do not block.",
    )


def _presidio(facts: PostureFacts) -> ControlStatus:
    label = "PII analyzer (Presidio)"
    if not facts.presidio_flag:
        return ControlStatus(
            "pii_analyzer_presidio",
            label,
            "off",
            "LOCUS_ENABLE_PRESIDIO_PII_ANALYZER is off; only the regex PII tier runs.",
        )
    if facts.presidio_state == "unavailable":
        return ControlStatus(
            "pii_analyzer_presidio",
            label,
            "degraded",
            "Flag is on but presidio_analyzer failed to load; only the regex PII tier runs.",
        )
    if facts.presidio_state == "not_loaded":
        return ControlStatus(
            "pii_analyzer_presidio",
            label,
            "unverified",
            "Flag is on; the analyzer loads lazily and has not been initialised yet.",
        )
    if not facts.guardrail_signals_enabled:
        return ControlStatus(
            "pii_analyzer_presidio",
            label,
            "off",
            "Analyzer is loaded but platform signals are disabled, so it is never called.",
        )
    return ControlStatus(
        "pii_analyzer_presidio",
        label,
        "enforced",
        "AnalyzerEngine is loaded and called from the platform PII signal check.",
    )


def _audit(facts: PostureFacts) -> ControlStatus:
    if facts.audit_durable:
        evidence = (
            "Events are appended to the Postgres locus_audit_events table; "
            "the log is not hash-chained or signed."
        )
    else:
        evidence = (
            "Events are kept in memory (capped at 2000) and persisted with store snapshots; "
            "the log is not hash-chained or signed."
        )
    return ControlStatus("audit_log", "Audit log", "degraded", evidence)


def _sandbox(facts: PostureFacts) -> ControlStatus:
    label = "Agent execution sandbox"
    strategy = facts.sandbox_strategy
    if strategy is None:
        return ControlStatus(
            "execution_sandbox",
            label,
            "unverified",
            "locus_runtime.sandbox.SandboxManager could not be loaded to detect a tier.",
        )
    if facts.sandbox_requested is None:
        return ControlStatus(
            "execution_sandbox",
            label,
            "unverified",
            f"Planner tier '{strategy}' detected, but the harness executor selection "
            "could not be inspected.",
        )
    if not facts.sandbox_requested:
        return ControlStatus(
            "execution_sandbox",
            label,
            "off",
            "Harness agents use LocalDirectExecutor (explicit LOCUS_SANDBOX_AGENTS=0 opt-out "
            "or a K8s pod); tool_jail denies its process execution "
            f"(tier available here: '{strategy}').",
        )
    if strategy == _NO_SANDBOX_STRATEGY:
        return ControlStatus(
            "execution_sandbox",
            label,
            "off",
            "No confining sandbox is available on this host (bubblewrap / seatbelt / "
            "AppContainer / Docker); tool_jail denies agent process execution (fail closed).",
        )
    if strategy in _CONFINING_SANDBOX_STRATEGIES:
        return ControlStatus(
            "execution_sandbox",
            label,
            "enforced",
            f"Harness executes through LocalSandboxExecutor on the '{strategy}' tier selected "
            "for this host (the default); tool_jail accepts only confining tiers.",
        )
    if strategy in _DELEGATED_SANDBOX_STRATEGIES:
        return ControlStatus(
            "execution_sandbox",
            label,
            "unverified",
            f"Tier '{strategy}' is delegated to the cluster RuntimeClass; gVisor/Kata are not "
            "implemented or verified by Locus.",
        )
    return ControlStatus(
        "execution_sandbox",
        label,
        "degraded",
        f"Only the '{strategy}' tier is available (no kernel or container confinement).",
    )


def _self_improvement_loop(facts: PostureFacts) -> ControlStatus:
    # Operational status only: an enabled loop is never reported as "enforced". Its
    # safety rests on the gateway, the sandbox and the D-22 merge guard reported above.
    status = facts.self_improvement_loop
    label = "Self-improvement loop"
    if not status:
        return ControlStatus(
            "self_improvement_loop", label, "off", "Loop status is unavailable in this process."
        )
    last = status.get("last_run") or {}
    last_text = (
        f"last run {last.get('run_id')} on {last.get('issue')}: {last.get('outcome')} "
        f"at {last.get('finished_at')}"
        if isinstance(last, Mapping) and last.get("run_id")
        else "no run recorded yet"
    )
    if not status.get("enabled"):
        return ControlStatus(
            "self_improvement_loop",
            label,
            "off",
            f"Disabled ({status.get('disabled_reason') or 'kill switch'}); {last_text}.",
        )
    return ControlStatus(
        "self_improvement_loop",
        label,
        "unverified",
        "Enabled (operational status, not a security control): it runs only while the "
        "gateway is enforcing and auto-merges only per D-22; "
        f"{status.get('runs_today', 0)}/{status.get('max_runs_per_day', '?')} runs today; "
        f"{last_text}.",
    )


_CONTROL_BUILDERS = (
    _authentication,
    _a2a_signing,
    _a2a_replay,
    _egress,
    _guardrail_signals,
    _presidio,
    _sandbox,
    _audit,
    _secret_storage,
    _policy_engine,
    _capability_tokens,
    _model_calls,
    _computer_use,
    _user_browser,
    _telemetry,
    _vault,
    _envoy,
    _nats,
    _self_improvement_loop,
)


def evaluate_controls(facts: PostureFacts) -> list[ControlStatus]:
    """Pure mapping from runtime facts to per-control states."""
    return [builder(facts) for builder in _CONTROL_BUILDERS]


def build_control_status_report(facts: PostureFacts) -> dict[str, Any]:
    controls = evaluate_controls(facts)
    summary = {state: 0 for state in CONTROL_STATES}
    for control in controls:
        summary[control.state] += 1
    return {
        "controls": [control.to_payload() for control in controls],
        "summary": summary,
    }


def enforced_control_ids(report: Mapping[str, Any]) -> list[str]:
    controls = report.get("controls")
    if not isinstance(controls, list):
        return []
    return [
        str(item.get("id"))
        for item in controls
        if isinstance(item, Mapping) and item.get("state") == "enforced"
    ]


# --- runtime fact collection (IO boundary) ---------------------------------


def _module_loaded(*names: str) -> bool:
    return any(name in sys.modules for name in names)


def _envoy_authz_filters(config_path: Path | None) -> bool | None:
    path = config_path or _DEFAULT_ENVOY_CONFIG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return any(marker in text for marker in _ENVOY_AUTHZ_MARKERS)


def _detect_sandbox_strategy() -> str | None:
    """The tier the harness default executor actually selects on this host."""
    try:
        from locus_runtime.sandbox import select_confining_strategy

        selection = select_confining_strategy()
    except Exception:  # noqa: BLE001 - reported as "unverified", never as enforced
        return None
    if selection.strategy is None:
        return _NO_SANDBOX_STRATEGY
    return str(selection.strategy.value)


def _sandbox_executor_requested() -> bool | None:
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        # K8s runs harness work on the direct executor (see workspace_binding).
        return False
    try:
        from locus_runtime.harness.workspace_binding import (
            _sandbox_executor_requested as requested,
        )

        return bool(requested())
    except Exception:  # noqa: BLE001 - reported as "unverified", never as enforced
        return None


def _policy_engine_available() -> bool:
    try:
        from locus_runtime.policy_engine import policy_engine_available

        return bool(policy_engine_available())
    except Exception:  # noqa: BLE001 - reported as "off", never as enforced
        return False


def _gateway_enforcing() -> bool:
    try:
        from locus_runtime.gateway import gateway_enforcing

        return bool(gateway_enforcing())
    except Exception:  # noqa: BLE001 - reported as not enforced
        return False


def _grants_enforcing() -> bool:
    try:
        from locus_runtime.gateway import grants_enforcing

        return bool(grants_enforcing())
    except Exception:  # noqa: BLE001 - reported as not enforced
        return False


def _model_gate_installed() -> bool:
    try:
        from locus_runtime.model_client import model_gate_installed

        return bool(model_gate_installed())
    except Exception:  # noqa: BLE001 - reported as not enforced
        return False


def _self_improvement_loop_status() -> Mapping[str, Any] | None:
    try:
        from locus_runtime.loop_runner import loop_status

        return loop_status()
    except Exception:  # noqa: BLE001 - reported as "off"
        return None


def _computer_use_installed() -> bool:
    try:
        from locus_runtime.computer_use.controller import controller_installed

        return bool(controller_installed())
    except Exception:  # noqa: BLE001 - reported as not enforced
        return False


def _user_browser_posture() -> Mapping[str, Any] | None:
    try:
        from locus_runtime.computer_use.user_browser.relay import get_hub
        from locus_runtime.computer_use.user_browser.tiers import current_tier_settings

        hub = get_hub()
        settings = current_tier_settings()
        return {
            "paired": hub.paired,
            "connected": hub.connected(),
            "tier": settings.tier,
            "effective_tier": settings.effective_tier,
            "consent": settings.consent.as_dict() if settings.consent is not None else None,
        }
    except Exception:  # noqa: BLE001 - reported as "off"
        return None


def _telemetry_posture() -> Mapping[str, Any] | None:
    try:
        from locus_runtime.telemetry import posture

        return posture().model_dump(mode="json")
    except Exception:  # noqa: BLE001 - reported as "off"
        return None


def _detect_secret_storage_mode() -> str | None:
    try:
        from locus_tooling.native_secrets import secret_storage_mode

        return str(secret_storage_mode())
    except Exception:  # noqa: BLE001 - reported as "unverified", never as enforced
        return None


def collect_posture_facts(
    *,
    auth_required: bool,
    a2a_signed_messages: bool,
    a2a_trusted_subject_count: int,
    a2a_replay_protection: bool,
    egress_allowlist: bool,
    guardrail_signals_enabled: bool,
    guardrail_signal_enforcement: str,
    presidio_flag: bool,
    presidio_state: PresidioState,
    audit_durable: bool,
    envoy_config_path: Path | None = None,
) -> PostureFacts:
    """Gather the process-level facts and combine them with backend state."""
    return PostureFacts(
        auth_required=auth_required,
        a2a_signed_messages=a2a_signed_messages,
        a2a_trusted_subject_count=a2a_trusted_subject_count,
        a2a_replay_protection=a2a_replay_protection,
        egress_allowlist=egress_allowlist,
        guardrail_signals_enabled=guardrail_signals_enabled,
        guardrail_signal_enforcement=guardrail_signal_enforcement,
        presidio_flag=presidio_flag,
        presidio_state=presidio_state,
        audit_durable=audit_durable,
        sandbox_requested=_sandbox_executor_requested(),
        sandbox_strategy=_detect_sandbox_strategy(),
        policy_engine_available=_policy_engine_available(),
        biscuit_loaded=_module_loaded("biscuit_auth", "biscuit"),
        vault_addr_configured=bool(str(os.getenv("VAULT_ADDR") or "").strip()),
        envoy_authz_filters=_envoy_authz_filters(envoy_config_path),
        nats_loaded=_module_loaded("nats"),
        secret_storage_mode=_detect_secret_storage_mode(),
        gateway_enforcing=_gateway_enforcing(),
        grants_enforcing=_grants_enforcing(),
        model_gate_installed=_model_gate_installed(),
        self_improvement_loop=_self_improvement_loop_status(),
        computer_use_installed=_computer_use_installed(),
        user_browser=_user_browser_posture(),
        telemetry=_telemetry_posture(),
    )
