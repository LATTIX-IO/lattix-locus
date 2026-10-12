"""Administrator-pinned tool catalog for the single gateway-mediated pod broker.

Only a trusted supervisor constructs this catalog. The model supplies a tool
identifier and a bounded idempotency key, never argv, mounts, executable, network
settings, or a subprocess runner. All effects delegate to PodSandboxBroker,
which owns admission, native Gateway policy, fenced receipts and cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from locus_runtime.action_receipts import ActionRecord, PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_sandbox_broker import BrokerCommand, BrokerOutcome, PodSandboxBroker
from locus_runtime.tool_result_contract import (
    StatusResult, ToolResultPolicy, ToolResultRejected, decode_tool_result,
)

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
        if (not isinstance(self.argv, tuple) or not self.argv
            or any(not isinstance(arg, str) for arg in self.argv)):
            raise ValueError("registered tool requires fixed string argv tuple")
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


@dataclass(frozen=True, slots=True)
class TypedToolStatus:
    """Status-only result bound to the real broker's completed receipt."""

    receipt: ActionRecord
    status: StatusResult


@dataclass(frozen=True, slots=True, init=False, repr=False)
class SandboxedPodToolBroker:
    """Frozen administrator catalog with a canonical, content-bound identity."""

    _tools: Mapping[str, BrokerTool]
    _executor: PodSandboxBroker
    _catalog_digest: str

    def __init__(self, receipts: PodActionReceipts,
                 tools: Mapping[str, BrokerTool]) -> None:
        if not isinstance(receipts, PodActionReceipts):
            raise ValueError("trusted receipt supervisor is required")
        if not isinstance(tools, Mapping) or not tools:
            raise ValueError("nonempty administrator tool registry required")
        if any(not isinstance(v, BrokerTool) or key != v.name
               for key, v in tools.items()):
            raise ValueError("registry keys must match trusted descriptors")
        # Snapshot first to prevent the administrator's mutable source map
        # from changing under the broker. Both the slots and frozen dataclass
        # block accidental rebinding of the admitted catalog/dispatch sink.
        snapshot = dict(tools)
        canonical = [
            {"name": key, "argv": list(tool.argv), "cwd": tool.cwd,
             "max_seconds": tool.max_seconds}
            for key, tool in sorted(snapshot.items())
        ]
        material = json.dumps(
            canonical, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hashlib.sha256(
            b"locus.admin-tool-catalog.v1\x00" + material
        ).hexdigest()
        object.__setattr__(self, "_tools", MappingProxyType(snapshot))
        object.__setattr__(self, "_executor", PodSandboxBroker(receipts))
        object.__setattr__(self, "_catalog_digest", digest)

    @property
    def catalog_digest(self) -> str:
        """Nonsecret snapshot correlation only; not an attested signature."""
        return self._catalog_digest

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

    def execute_status(self, lease: PodLease, *, tool_name: str,
                       action_idempotency_key: str) -> TypedToolStatus:
        """Project an already-fenced invocation into the strict status schema.

        No alternative invocation or output stream exists here. A launched
        action with a non-representable exit code has already consumed its
        idempotency key; validation failure never permits a second launch.
        """
        outcome = self.execute(
            lease, tool_name=tool_name,
            action_idempotency_key=action_idempotency_key,
        )
        if outcome.receipt.state != "succeeded":
            raise ToolResultRejected("tool status requires terminal execution receipt")
        raw = json.dumps({
            "schema": "status/v1",
            "ok": outcome.exit_code == 0,
            "code": outcome.exit_code,
        }, separators=(",", ":")).encode("ascii")
        result = decode_tool_result(raw, ToolResultPolicy("status/v1"))
        if not isinstance(result, StatusResult):
            raise ToolResultRejected("status policy yielded unexpected schema")
        return TypedToolStatus(receipt=outcome.receipt, status=result)
