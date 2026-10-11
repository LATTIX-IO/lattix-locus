"""Trusted supervisor's narrow sandboxed tool-broker for admitted Locus pods.

This boundary *never* runs a raw agent-supplied command. A pinned local tool
descriptor provides argv and permitted paths; the gateway authorizes the
specific action and the receipts ledger writes its dispatch first. Only a
genuinely confining SandboxManager plan can be executed, never a fallback.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from locus_runtime.action_receipts import ActionRecord, PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.sandbox import (
    CONFINING_STRATEGIES, ExecutionSpec, HostPlatform, SandboxManager, SandboxPolicy,
)


@dataclass(frozen=True, slots=True)
class BrokerTool:
    """Admin-authored immutable allowlist entry, not a model-written tool."""
    name: str
    argv: tuple[str, ...]
    cwd: str
    max_seconds: int = 30

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii() or len(self.name) > 128:
            raise ValueError("invalid registered tool")
        if not self.argv or any(not isinstance(x, str) or not x for x in self.argv):
            raise ValueError("registered command must be nonempty argv")
        if not 1 <= self.max_seconds <= 300:
            raise ValueError("registered tool requires bounded timeout")
        if not Path(self.argv[0]).is_absolute() or not Path(self.cwd).is_absolute():
            raise ValueError("registered executable and cwd must be absolute")
        if any("\x00" in a for a in self.argv) or "\x00" in self.cwd:
            raise ValueError("NUL in trusted tool descriptor")


@dataclass(frozen=True, slots=True)
class BrokerResult:
    exit_code: int
    stdout: str
    stderr: str


class SandboxedPodToolBroker:
    """No dynamic tool installation, remote MCP execution or shell fallback."""

    def __init__(self, receipts: PodActionReceipts,
                 tools: Mapping[str, BrokerTool], *,
                 manager: SandboxManager | None = None,
                 run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
                 platform: HostPlatform | None = None) -> None:
        self.receipts = receipts
        self._tools = dict(tools)
        self._manager = manager or SandboxManager()
        self._run = run
        self._platform = platform
        if not self._tools or any(k != v.name for k, v in self._tools.items()):
            raise ValueError("tool registry must contain correctly named entries")

    def execute(self, lease: PodLease, *, tool_name: str,
                action_idempotency_key: str) -> tuple[ActionRecord, BrokerResult]:
        descriptor = self._tools.get(tool_name)
        if descriptor is None:
            raise AdmissionDenied("tool is not registered with the trusted broker")
        # Exactly this named tool must also be in the authenticated child
        # capabilities. An arbitrary agent-provided command is never accepted.
        if tool_name not in lease._session.capabilities.allowed_tools:
            raise AdmissionDenied("tool exceeds child gateway capabilities")
        if not Path(descriptor.argv[0]).is_file() or not Path(descriptor.cwd).is_dir():
            raise AdmissionDenied("trusted executable or workspace is unavailable")
        # The plan describes an entirely network-disabled jailed command.
        # No inherited environment, secret mounts, arbitrary writable roots
        # or agent-supplied args are accepted.
        from locus_runtime.sandbox import detect_host_platform
        platform = self._platform or detect_host_platform()
        policy = SandboxPolicy(platform=platform, allow_network=False,
            allowed_hosts=[], allowed_read_paths=[descriptor.cwd],
            allowed_write_paths=[], allowed_executables=[descriptor.argv[0]],
            timeout_seconds=descriptor.max_seconds)
        spec = ExecutionSpec(tool_id=descriptor.name, command=list(descriptor.argv),
            cwd=descriptor.cwd, env={}, input_paths=[descriptor.cwd],
            output_paths=[], requested_hosts=[])
        plan = self._manager.plan(spec, policy)
        if plan.strategy not in CONFINING_STRATEGIES or not plan.command:
            raise AdmissionDenied("sandbox is not sufficiently confining")
        if plan.strategy.value == "restricted_process":
            raise AdmissionDenied("restricted process is not a security sandbox")
        def invoke() -> BrokerResult:
            try:
                result = self._run(list(plan.command), shell=False,
                    cwd=descriptor.cwd, env={"PATH": os.defpath},
                    capture_output=True, timeout=descriptor.max_seconds, check=False)
            except (subprocess.TimeoutExpired, OSError):
                # Receipt layer quarantines ambiguous callbacks and forbids replay.
                raise
            stdout = result.stdout[:65536].decode("utf-8", errors="replace")
            stderr = result.stderr[:65536].decode("utf-8", errors="replace")
            return BrokerResult(int(result.returncode), stdout, stderr)
        return self.receipts.execute_once(
            lease, idempotency_key=action_idempotency_key,
            authorization={"kind": "tool_call", "tool":tool_name,
                           "args": {"tool_name":tool_name, "argv":list(descriptor.argv),
                                    "sandbox_strategy":plan.strategy.value}},
            invoke=invoke,
        )
