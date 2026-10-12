"""Administrator-pinned tool catalog for the single gateway-mediated pod broker.

Only a trusted supervisor constructs this catalog. The model supplies a tool
identifier and a bounded idempotency key, never argv, mounts, executable, network
settings, or a subprocess runner. All effects delegate to PodSandboxBroker,
which owns admission, native Gateway policy, fenced receipts and cleanup.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
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
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_PINNED_EXECUTABLE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BrokerTool:
    """Trusted administrator's fixed, immutable command descriptor."""

    name: str
    argv: tuple[str, ...]
    cwd: str
    max_seconds: int = 30
    executable_sha256: str | None = None

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
        if (self.executable_sha256 is not None
            and (not isinstance(self.executable_sha256, str)
                 or _SHA256_HEX.fullmatch(self.executable_sha256) is None)):
            raise ValueError("executable digest must be canonical SHA-256 hex")
        # Share the executor's strict grammar, interpreter denial and time
        # ceiling. The artificial key is only for registration validation.
        try:
            BrokerCommand(
                self.name, self.argv, self.cwd,
                "descriptor-preflight", self.max_seconds,
            )
        except (AdmissionDenied, ValueError) as exc:
            raise ValueError("registered tool violates broker command policy") from exc


def _verify_pinned_executable(path: Path, expected: str) -> None:
    """Bounded best-effort digest check; does not hold an execution file handle.

    Deny unexpected file types, huge files, changing metadata, short reads,
    permission errors and substitutions, before the receipt can be created.
    This remains vulnerable to a replacement *after* the check; it is not an
    OS-backed race-free executable binding.
    """
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as fh:
            before = os.fstat(fh.fileno())
            if (not stat.S_ISREG(before.st_mode)
                or before.st_size > _MAX_PINNED_EXECUTABLE_BYTES):
                raise AdmissionDenied("registered executable digest scope denied")
            digest = hashlib.sha256()
            size = 0
            while data := fh.read(1024 * 1024):
                size += len(data)
                if size > _MAX_PINNED_EXECUTABLE_BYTES:
                    raise AdmissionDenied("registered executable digest scope denied")
                digest.update(data)
            after = os.fstat(fh.fileno())
            facts_before = (before.st_dev, before.st_ino, before.st_size,
                            before.st_mtime_ns, before.st_ctime_ns)
            facts_after = (after.st_dev, after.st_ino, after.st_size,
                           after.st_mtime_ns, after.st_ctime_ns)
            if size != before.st_size or facts_before != facts_after:
                raise AdmissionDenied("registered executable changed during measurement")
    except AdmissionDenied:
        # AdmissionDenied subclasses PermissionError/OSError. Preserve explicit
        # fail-closed scope and mutation denials instead of mislabeling them
        # as filesystem availability errors.
        raise
    except (OSError, RuntimeError):
        raise AdmissionDenied("registered executable measurement unavailable") from None
    if not hmac.compare_digest(digest.hexdigest(), expected):
        raise AdmissionDenied("registered executable digest mismatch")


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
    _require_pinned_executables: bool
    _require_immutable_docker_image: bool
    _pinned_docker_image: str | None

    def __init__(self, receipts: PodActionReceipts,
                 tools: Mapping[str, BrokerTool], *,
                 require_pinned_executables: bool = False,
                 require_immutable_docker_image: bool = False,
                 pinned_docker_image: str | None = None) -> None:
        if not isinstance(receipts, PodActionReceipts):
            raise ValueError("trusted receipt supervisor is required")
        if not isinstance(tools, Mapping) or not tools:
            raise ValueError("nonempty administrator tool registry required")
        if type(require_pinned_executables) is not bool:
            raise ValueError("executable pin requirement must be a boolean")
        if type(require_immutable_docker_image) is not bool:
            raise ValueError("Docker image pin policy must be a boolean")
        # Validate administrator configuration at registration before any
        # catalog state can be created or any process can be dispatched.
        validated_executor = PodSandboxBroker(
            receipts, require_immutable_docker_image=require_immutable_docker_image,
            pinned_docker_image=pinned_docker_image,
        )
        # Snapshot first; all validation and digest material derive from the
        # same immutable descriptor values, not the mutable caller mapping.
        snapshot = dict(tools)
        if any(not isinstance(v, BrokerTool) or key != v.name
               for key, v in snapshot.items()):
            raise ValueError("registry keys must match trusted descriptors")
        if (require_pinned_executables
            and any(tool.executable_sha256 is None for tool in snapshot.values())):
            raise ValueError("strict tool catalog requires all executable digests")
        # Snapshot first to prevent the administrator's mutable source map
        # from changing under the broker. Both the slots and frozen dataclass
        # block accidental rebinding of the admitted catalog/dispatch sink.
        canonical = [
            {"name": key, "argv": list(tool.argv), "cwd": tool.cwd,
             "max_seconds": tool.max_seconds,
             "executable_sha256": tool.executable_sha256}
            for key, tool in sorted(snapshot.items())
        ]
        material = json.dumps(
            {"require_pinned_executables": require_pinned_executables,
             "require_immutable_docker_image": require_immutable_docker_image,
             "pinned_docker_image": pinned_docker_image,
             "tools": canonical}, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hashlib.sha256(
            b"locus.admin-tool-catalog.v1\x00" + material
        ).hexdigest()
        object.__setattr__(self, "_tools", MappingProxyType(snapshot))
        object.__setattr__(self, "_executor", validated_executor)
        object.__setattr__(self, "_catalog_digest", digest)
        object.__setattr__(self, "_require_pinned_executables",
                           require_pinned_executables)
        object.__setattr__(self, "_require_immutable_docker_image",
                           require_immutable_docker_image)
        object.__setattr__(self, "_pinned_docker_image", pinned_docker_image)

    @property
    def catalog_digest(self) -> str:
        """Nonsecret snapshot correlation only; not an attested signature."""
        return self._catalog_digest

    @property
    def requires_pinned_executables(self) -> bool:
        """Supervisor-pinned admission setting (not a binary attestation)."""
        return self._require_pinned_executables

    @property
    def requires_immutable_docker_image(self) -> bool:
        """Trusted supervisor's Docker image reference requirement."""
        return self._require_immutable_docker_image

    def execute(self, lease: PodLease, *, tool_name: str,
                action_idempotency_key: str) -> BrokerOutcome:
        if not isinstance(lease, PodLease):
            raise AdmissionDenied("trusted admitted pod lease required")
        if not isinstance(tool_name, str):
            raise AdmissionDenied("tool identifier must be a string")
        descriptor = self._tools.get(tool_name)
        if descriptor is None:
            raise AdmissionDenied("tool is not registered")
        if self._require_pinned_executables and descriptor.executable_sha256 is None:
            raise AdmissionDenied("tool digest required by catalog policy")
        caps = lease._session.capabilities
        if descriptor.name not in caps.allowed_tools:
            raise AdmissionDenied("tool outside admitted capabilities")
        if descriptor.argv[0] not in caps.allowed_executables:
            raise AdmissionDenied("executable outside admitted capabilities")
        # A symlink in *any ancestor* is an alias, even if the final path
        # component is a regular file/directory. Bind the exact administrator
        # and child capability paths to canonical filesystem objects at
        # dispatch. This is a deny-first check, not a race-free file handle.
        executable = Path(descriptor.argv[0])
        cwd = Path(descriptor.cwd)
        try:
            actual_executable = executable.resolve(strict=True)
            actual_cwd = cwd.resolve(strict=True)
        except (OSError, RuntimeError):
            raise AdmissionDenied("registered tool path is unavailable") from None
        if (actual_executable != executable or not actual_executable.is_file()
            or actual_cwd != cwd or not actual_cwd.is_dir()):
            raise AdmissionDenied("registered tool path is missing or linked")
        if descriptor.executable_sha256 is not None:
            _verify_pinned_executable(actual_executable, descriptor.executable_sha256)
        return self._executor.execute(
            lease, BrokerCommand(
                tool=descriptor.name, argv=descriptor.argv,
                working_dir=descriptor.cwd,
                idempotency_key=action_idempotency_key,
                timeout_seconds=descriptor.max_seconds,
                catalog_digest=self._catalog_digest,
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
