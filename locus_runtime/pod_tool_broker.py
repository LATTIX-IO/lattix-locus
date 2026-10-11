"""Administrator-pinned tool catalog for the single gateway-mediated pod broker.

Only a trusted supervisor constructs this catalog. The model supplies a tool
identifier and a bounded idempotency key, never argv, mounts, executable, network
settings, or a subprocess runner. All effects delegate to PodSandboxBroker,
which owns admission, native Gateway policy, fenced receipts and cleanup.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from locus_runtime.action_receipts import PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_sandbox_broker import BrokerCommand, BrokerOutcome, PodSandboxBroker

_TOOL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class BrokerTool:
    """Trusted administrator's fixed, immutable command descriptor."""

    name: str
    argv: tuple[str, ...]
    cwd: str
    max_seconds: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME.fullmatch(self.name):
            raise ValueError("invalid registered tool name")
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("registered tool requires fixed argv tuple")
        if not isinstance(self.cwd, str) or not self.cwd or len(self.cwd) > 4096:
            raise ValueError("registered tool requires bounded working directory")
        if not Path(self.argv[0]).is_absolute() or not Path(self.cwd).is_absolute():
            raise ValueError("registered executable and cwd must be absolute paths")
        if "\x00" in self.cwd or "\r" in self.cwd or "\n" in self.cwd:
            raise ValueError("invalid registered working directory")
        # Share the executor's strict grammar, interpreter denial and time
        # ceiling. The artificial key is only for registration validation.
        try:
            BrokerCommand(
                self.name, self.argv, self.cwd,
                "descriptor-preflight", self.max_seconds,
            )
        except (AdmissionDenied, ValueError) as exc:
            raise ValueError("registered tool violates broker command policy") from exc


class SandboxedPodToolBroker:
    """Fixed tool registry; exactly one security/dispatch sink."""

    def __init__(self, receipts: PodActionReceipts,
                 tools: Mapping[str, BrokerTool]) -> None:
        if not isinstance(receipts, PodActionReceipts):
            raise ValueError("trusted receipt supervisor is required")
        if not isinstance(tools, Mapping) or not tools:
            raise ValueError("nonempty administrator tool registry required")
        if any(not isinstance(v, BrokerTool) or key != v.name
               for key, v in tools.items()):
            raise ValueError("registry keys must match trusted descriptors")
        # Copy to cut off subsequent mutation by the caller; frozen values and
        # read-only mapping prevent registration from being rewritten in place.
        self._tools: Mapping[str, BrokerTool] = MappingProxyType(dict(tools))
        self._executor = PodSandboxBroker(receipts)

    def execute(self, lease: PodLease, *, tool_name: str,
                action_idempotency_key: str) -> BrokerOutcome:
        if not isinstance(lease, PodLease):
            raise AdmissionDenied("trusted admitted pod lease required")
        if not isinstance(tool_name, str):
            raise AdmissionDenied("tool identifier must be a string")
        descriptor = self._tools.get(tool_name)
        if descriptor is None:
            raise AdmissionDenied("tool is not registered")
        caps = lease._session.capabilities
        if descriptor.name not in caps.allowed_tools:
            raise AdmissionDenied("tool outside admitted capabilities")
        if descriptor.argv[0] not in caps.allowed_executables:
            raise AdmissionDenied("executable outside admitted capabilities")
        # Pin a regular executable and workspace at dispatch. A symlink may
        # redirect execution outside administrator-pinned filesystem identity.
        executable = Path(descriptor.argv[0])
        if executable.is_symlink() or not executable.is_file():
            raise AdmissionDenied("registered executable is missing or linked")
        cwd = Path(descriptor.cwd)
        if cwd.is_symlink() or not cwd.is_dir():
            raise AdmissionDenied("registered workspace is missing or linked")
        return self._executor.execute(
            lease, BrokerCommand(
                tool=descriptor.name, argv=descriptor.argv,
                working_dir=descriptor.cwd,
                idempotency_key=action_idempotency_key,
                timeout_seconds=descriptor.max_seconds,
            ),
        )
