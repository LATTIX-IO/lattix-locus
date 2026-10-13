"""Trusted, non-executing OpenCode/POD launch preflight (LOCUS-416 B).

This binds one pre-reviewed OpenCode profile to an *active* Gateway-issued
PodLease, its durable plan, an independently observed CLI version, and a
bounded measured executable. It deliberately does not invoke the CLI, grant
provider egress, construct a host subprocess, or accept the model's claims as
attestation. Process isolation and model/tool network transport are separate
requirements before this preflight may be used to launch a real worker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from locus_runtime.harness.opencode_contract import (
    OpenCodeContractDenied,
    OpenCodeLaunchProfile,
    OpenCodeVersion,
    compile_opencode_launch,
    validate_effective_opencode_config,
)
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_tool_broker import _verify_pinned_executable

# OpenCode's server name "locus" prefixes the exact tools published by
# codex_mcp_server.py (tool "search" is presented as "locus_search").
# Do not infer grant names from a model-supplied MCP catalog.
_AUTHORIZED_MCP_EXPORTS = frozenset({
    "locus_execute_bash": "execute_bash",
    "locus_search": "search",
    "locus_str_replace_editor": "str_replace_editor",
    "locus_run_tests": "run_tests",
}.items())
_MAX_VERSION_OUTPUT = 128


@dataclass(frozen=True, slots=True)
class OpenCodePodPreflight:
    """Auditable correlation record; explicitly NOT a launch authorization."""

    parent_run_id: str
    child_run_id: str
    node_id: str
    reservation_id: str
    accepted_plan_digest: str
    executable_sha256: str
    config_sha256: str
    model: str
    allowed_mcp_tools: tuple[str, ...]
    version: str
    binding_sha256: str
    result: str = "verified_preflight_unexecuted"


def _validate_observed_version(raw: str, expected: OpenCodeVersion) -> str:
    """Only an independently contained runner may supply the observed banner."""
    if not isinstance(raw, str) or not 1 <= len(raw) <= _MAX_VERSION_OUTPUT:
        raise OpenCodeContractDenied("CLI version observation is invalid")
    line = raw.rstrip("\n")
    if "\n" in line or "\r" in line or "\0" in line:
        raise OpenCodeContractDenied("CLI version observation is ambiguous")
    if line.startswith("opencode "):
        line = line[len("opencode "):]
    actual = OpenCodeVersion.parse(line)
    if actual != expected:
        raise OpenCodeContractDenied("observed CLI version mismatches accepted contract")
    return f"{actual.major}.{actual.minor}.{actual.patch}"


def _verify_binary(profile: OpenCodeLaunchProfile) -> None:
    if not isinstance(profile, OpenCodeLaunchProfile):
        raise OpenCodeContractDenied("trusted launch profile required")
    expected_platform = "windows" if os.name == "nt" else "posix"
    if profile.platform != expected_platform:
        raise OpenCodeContractDenied("cross-platform executable measurement denied")
    original = Path(profile.executable)
    try:
        if original != original.resolve(strict=True):
            raise AdmissionDenied("CLI executable symlink alias denied")
        # Deny symlinks in every path component, not just the final binary.
        if any(candidate.is_symlink() for candidate in (original, *original.parents)):
            raise AdmissionDenied("CLI executable ancestor symlink denied")
        st = original.stat(follow_symlinks=False)
    except AdmissionDenied:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise AdmissionDenied("pinned CLI binary cannot be inspected") from exc
    if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
        raise AdmissionDenied("pinned CLI must be a nonempty regular file")
    if os.name != "nt" and (st.st_mode & 0o022 or not st.st_mode & 0o111):
        raise AdmissionDenied("pinned CLI cannot be writable by group/other or nonexecuting")
    # Reuse the existing bounded, O_NOFOLLOW, fd-based digest check. It detects
    # mutations while reading, but cannot prevent post-check TOCTOU replacement:
    # actual launch still requires an image/fd pinned by the sandbox broker.
    _verify_pinned_executable(original, profile.executable_sha256)


def approved_profile_digest(profile: OpenCodeLaunchProfile) -> str:
    """Canonical digest to store in trusted operator-owned engine registry."""
    if not isinstance(profile, OpenCodeLaunchProfile):
        raise OpenCodeContractDenied("trusted launch profile required")
    material = json.dumps(
        asdict(profile), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(material).hexdigest()


def _validate_profile_integrity(profile: OpenCodeLaunchProfile) -> None:
    """Recompile the frozen operator contract; fail closed on dataclass substitution."""
    version = profile.version
    if not isinstance(version, OpenCodeVersion):
        raise OpenCodeContractDenied("invalid approved CLI version")
    expected = compile_opencode_launch(
        version=f"{version.major}.{version.minor}.{version.patch}",
        executable=profile.executable,
        executable_sha256=profile.executable_sha256,
        platform=profile.platform,
        model=profile.model,
        workspace=profile.workspace,
        private_root=profile.private_root,
        request_file=profile.request_file,
        mcp_python=profile.mcp_python,
        mcp_config_file=profile.mcp_config_file,
        allowed_mcp_tools=profile.allowed_mcp_tools,
    )
    if profile != expected:
        raise OpenCodeContractDenied("external launch profile was substituted")


def _validate_pod_mcp_grants(profile: OpenCodeLaunchProfile, lease: PodLease) -> None:
    grants = dict(_AUTHORIZED_MCP_EXPORTS)
    allowed = lease._session.capabilities.allowed_tools
    for name in profile.allowed_mcp_tools:
        actual = grants.get(name)
        if actual is None or actual not in allowed:
            raise AdmissionDenied("OpenCode tool grant exceeds admitted pod authority")
    # No additional MCP or skill names may be inserted through a vendor config.
    expected = profile.config()
    if profile.allowed_mcp_tools:
        servers = (
            expected["mcp"]["servers"]
            if profile.version.major == 2 else expected["mcp"]
        )
        if set(servers) != {"locus"}:
            raise AdmissionDenied("OpenCode MCP server identity not bound to Locus")


def preflight_opencode_pod(
    profile: OpenCodeLaunchProfile,
    lease: PodLease,
    *,
    expected_profile_sha256: str,
    observed_version: str,
    observed_effective_config: Mapping[str, Any],
) -> OpenCodePodPreflight:
    """Check trusted parent-derived admission and executable without launching.

    The observed version and effective config must be obtained by trusted
    preflight code running under an independent, already established jail.
    They must not originate in model output or untrusted event JSONL.
    expected_profile_sha256 must come from a separately trusted, immutable
    operator registry, never recomputed from a model-supplied mutated profile.
    The returned fingerprint is correlation metadata, not an authorization
    token, signature, or evidence that a CLI process was safely executed.
    """
    if not isinstance(lease, PodLease):
        raise AdmissionDenied("active Gateway pod lease required")
    reservation = lease._ledger.require_active(lease.reservation_id)
    if reservation.child_run_id != lease.run_id:
        raise AdmissionDenied("child lease identity is not bound to reservation")
    if not isinstance(profile, OpenCodeLaunchProfile):
        raise OpenCodeContractDenied("trusted launch profile required")
    if (
        not isinstance(expected_profile_sha256, str)
        or len(expected_profile_sha256) != 64
        or any(c not in "0123456789abcdef" for c in expected_profile_sha256)
        or not hmac.compare_digest(
            approved_profile_digest(profile), expected_profile_sha256,
        )
    ):
        raise OpenCodeContractDenied("OpenCode profile is not approved by registry")
    _validate_profile_integrity(profile)
    _validate_pod_mcp_grants(profile, lease)
    verified_version = _validate_observed_version(observed_version, profile.version)
    validate_effective_opencode_config(profile, observed_effective_config)
    # Executable I/O occurs last, after identity/capability checks, and before
    # any worker call. Never read or execute a binary for an unauthorized pod.
    _verify_binary(profile)
    # The pod could expire/revoke while measuring its binary.
    renewed = lease._ledger.require_active(lease.reservation_id)
    if (
        renewed.child_run_id != reservation.child_run_id
        or renewed.plan_digest != reservation.plan_digest
        or renewed.parent_run_id != reservation.parent_run_id
    ):
        raise AdmissionDenied("pod changed during external engine preflight")
    material = {
        "schema": "locus.external-preflight.v1",
        "parent": reservation.parent_run_id,
        "node": reservation.node_id,
        "child": reservation.child_run_id,
        "reservation": reservation.reservation_id,
        "plan": reservation.plan_digest,
        "cli": profile.executable_sha256,
        "config": profile.config_sha256,
        "profile": expected_profile_sha256,
        "version": verified_version,
        "model": profile.model,
        "mcp_tools": list(profile.allowed_mcp_tools),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")
    fingerprint = hashlib.sha256(canonical).hexdigest()
    # This digest is correlation-only, not an authorization, signature or
    # substitute for durable action receipts/lease checks.
    return OpenCodePodPreflight(
        parent_run_id=reservation.parent_run_id,
        child_run_id=lease.run_id,
        node_id=reservation.node_id,
        reservation_id=lease.reservation_id,
        accepted_plan_digest=reservation.plan_digest,
        executable_sha256=profile.executable_sha256,
        config_sha256=profile.config_sha256,
        model=profile.model,
        allowed_mcp_tools=profile.allowed_mcp_tools,
        version=verified_version,
        binding_sha256=fingerprint,
    )
