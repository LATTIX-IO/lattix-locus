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
    rego_engine_loaded: bool
    biscuit_loaded: bool
    vault_addr_configured: bool
    envoy_authz_filters: bool | None
    nats_loaded: bool


def _policy_engine(facts: PostureFacts) -> ControlStatus:
    if facts.rego_engine_loaded:
        return ControlStatus(
            "policy_engine_rego",
            "Policy engine (Rego)",
            "unverified",
            "A Rego evaluator module is loaded but no request path is proven to call it.",
        )
    return ControlStatus(
        "policy_engine_rego",
        "Policy engine (Rego)",
        "off",
        "No Rego evaluator is loaded and the OPA server is never called; "
        "locus_runtime.security.OPAClient is a hand-written Python copy of the rules "
        "and is not on the backend request path.",
    )


def _capability_tokens(facts: PostureFacts) -> ControlStatus:
    if facts.biscuit_loaded:
        return ControlStatus(
            "capability_tokens_biscuit",
            "Capability grants (Biscuit)",
            "unverified",
            "A Biscuit module is loaded but grant minting still uses HMAC JSON tokens.",
        )
    return ControlStatus(
        "capability_tokens_biscuit",
        "Capability grants (Biscuit)",
        "off",
        "No Biscuit library is loaded; locus_runtime.security.CapabilityMinter issues "
        "custom HMAC-signed JSON tokens (shared secret, no attenuation).",
    )


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
            "Harness agents use the unconfined LocalDirectExecutor; set LOCUS_SANDBOX_AGENTS "
            f"or the local-native profile to use the planner (detected tier '{strategy}').",
        )
    if strategy in _CONFINING_SANDBOX_STRATEGIES:
        return ControlStatus(
            "execution_sandbox",
            label,
            "enforced",
            f"LocalSandboxExecutor plans through SandboxManager with tier '{strategy}'.",
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


_CONTROL_BUILDERS = (
    _authentication,
    _a2a_signing,
    _a2a_replay,
    _egress,
    _guardrail_signals,
    _presidio,
    _sandbox,
    _audit,
    _policy_engine,
    _capability_tokens,
    _vault,
    _envoy,
    _nats,
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
    try:
        from locus_runtime.sandbox import SandboxManager

        return str(SandboxManager().active_strategy.value)
    except Exception:  # noqa: BLE001 - reported as "unverified", never as enforced
        return None


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
        rego_engine_loaded=_module_loaded("regorus"),
        biscuit_loaded=_module_loaded("biscuit_auth", "biscuit"),
        vault_addr_configured=bool(str(os.getenv("VAULT_ADDR") or "").strip()),
        envoy_authz_filters=_envoy_authz_filters(envoy_config_path),
        nats_loaded=_module_loaded("nats"),
    )
