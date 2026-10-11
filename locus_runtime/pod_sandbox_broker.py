"""Trusted, gateway-mediated command broker for an admitted ephemeral pod.

No new PEP, no shell, no network and no direct execution fallback. This module
is *only* called by trusted control-plane code; do not expose its objects or
GatewaySession tokens to untrusted agents. ActionReceipts owns the write-ahead
dispatch, kernel fencing and at-most-once broker invocation.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Sequence

from locus_runtime.action_receipts import ActionRecord, PodActionReceipts
from locus_runtime.gateway import JailFacts, current_uid_user
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.sandbox import (
    CONFINING_STRATEGIES,
    ExecutionSpec,
    IsolationStrategy,
    SandboxManager,
    SandboxPolicy,
    detect_host_platform,
    minimal_agent_env,
    select_confining_strategy,
)

# This narrow first cut denies arbitrary shells and inline interpreters.
# A later audited command grammar may explicitly enable such operations.
_SHELLS = frozenset({
    "sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell",
    "powershell.exe", "pwsh", "python", "python3", "node", "ruby",
})
_MAX_ARGV = 32
_MAX_ARG_LEN = 2048
_MAX_TIMEOUT = 60


@dataclass(frozen=True, slots=True)
class BrokerCommand:
    tool: str
    argv: tuple[str, ...]
    working_dir: str
    idempotency_key: str
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if (not isinstance(self.tool, str) or not self.tool.strip()
            or len(self.tool) > 128):
            raise ValueError("bounded tool identifier required")
        if not isinstance(self.argv, tuple) or not 1 <= len(self.argv) <= _MAX_ARGV:
            raise ValueError("command must be a bounded tuple of arguments")
        if any(
            not isinstance(v, str) or not v or "\x00" in v
            or "\r" in v or "\n" in v or len(v) > _MAX_ARG_LEN
            for v in self.argv
        ):
            raise ValueError("malformed command argument")
        # Match POSIX and Windows basename syntax regardless of the broker's
        # test/build host, so a Windows absolute shell path cannot bypass a
        # Linux-side authorization or vice versa.
        if (Path(self.argv[0]).name.lower() in _SHELLS
            or PureWindowsPath(self.argv[0]).name.lower() in _SHELLS):
            raise AdmissionDenied("shell and inline-interpreter commands are not admitted")
        if not isinstance(self.working_dir, str) or not self.working_dir.strip():
            raise ValueError("explicit working directory required")
        if not isinstance(self.idempotency_key, str) or not self.idempotency_key:
            raise ValueError("action idempotency key required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= _MAX_TIMEOUT:
            raise ValueError("execution timeout must be in 1..60 seconds")


@dataclass(frozen=True, slots=True)
class BrokerOutcome:
    receipt: ActionRecord
    exit_code: int
    backend: str
    elapsed_ms: int


def _jail_facts(strategy: IsolationStrategy) -> JailFacts:
    if strategy == IsolationStrategy.HARDENED_DOCKER:
        return JailFacts(strategy=strategy.value, readonly_rootfs=True,
                         run_as_user="1000:1000", allow_network=False)
    if strategy == IsolationStrategy.KERNEL_SEATBELT:
        return JailFacts(strategy=strategy.value, readonly_rootfs=True,
                         run_as_user=current_uid_user(), allow_network=False)
    if strategy == IsolationStrategy.WINDOWS_APPCONTAINER:
        return JailFacts(strategy=strategy.value, allow_network=False,
                         appcontainer=True, job_object=True, require_appcontainer=True)
    raise AdmissionDenied("no approved confined process execution strategy")


def _choose_strategy() -> IsolationStrategy:
    choice = select_confining_strategy()
    strategy = choice.strategy
    if strategy not in CONFINING_STRATEGIES:
        raise AdmissionDenied("no proven confining sandbox on this endpoint")
    if strategy == IsolationStrategy.KERNEL_BWRAP:
        # The current shared bwrap planner read-binds '/' as a whole. A
        # trustworthy data-centric broker cannot make that a confined file
        # reader merely by narrowing policy.read_roots. Prefer Docker's
        # explicit mounts or fail closed until bwrap's mount contract changes.
        profile = os.getenv("LOCUS_RUNTIME_PROFILE", "").lower()
        if profile in {"native", "local-native", "local_native"} or not shutil.which("docker"):
            raise AdmissionDenied("bwrap default read-all mount is not acceptable")
        return IsolationStrategy.HARDENED_DOCKER
    return strategy


def _bounded_dir(path: str, permitted: Sequence[str]) -> str:
    try:
        actual = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise AdmissionDenied("working directory is unavailable") from exc
    if not actual.is_dir() or not permitted:
        raise AdmissionDenied("working directory has no authorized scope")
    roots = [Path(p).expanduser().resolve(strict=True) for p in permitted]
    if not any(actual == root or root in actual.parents for root in roots):
        raise AdmissionDenied("working directory exceeds pod read scope")
    return str(actual)


class PodSandboxBroker:
    """Admitted CLI tool, sandboxed off-host effects and durable action receipt."""

    def __init__(self, receipts: PodActionReceipts) -> None:
        self._receipts = receipts

    def execute(self, lease: PodLease, command: BrokerCommand) -> BrokerOutcome:
        # Fail before any side effect or action receipt when capabilities are
        # missing or the platform would silently degrade to host execution.
        if not isinstance(lease, PodLease):
            raise AdmissionDenied("trusted pod lease is required")
        self._receipts.reservations.require_active(lease.reservation_id)
        caps = lease._session.capabilities
        if command.tool not in caps.allowed_tools:
            raise AdmissionDenied("tool is outside the admitted pod")
        if command.argv[0] not in caps.allowed_executables:
            raise AdmissionDenied("executable is not explicitly allowlisted")
        cwd = _bounded_dir(command.working_dir, caps.read_roots)
        strategy = _choose_strategy()
        if strategy not in CONFINING_STRATEGIES:
            raise AdmissionDenied("unsupported unconfined strategy")
        policy = SandboxPolicy(
            platform=detect_host_platform(),
            allow_network=False,
            allowed_hosts=[],
            allowed_read_paths=list(caps.read_roots),
            allowed_write_paths=list(caps.write_roots),
            allowed_executables=[command.argv[0]],
            timeout_seconds=command.timeout_seconds,
            require_appcontainer=True,
        )
        spec = ExecutionSpec(
            tool_id=command.tool, command=list(command.argv), cwd=cwd, env={},
            requested_hosts=[],
        )
        plan = SandboxManager(force_strategy=strategy).plan(spec, policy)
        if (plan.strategy != strategy or plan.backend != strategy.value
            or not isinstance(plan.command, list) or not plan.command
            or any(not isinstance(arg, str) or not arg or "\x00" in arg
                   for arg in plan.command)):
            raise AdmissionDenied("sandbox planning failed closed")
        # Never allow a planner to silently degrade to the allowlisted host
        # executable. A policy decision is not itself a process boundary.
        if plan.command[0] == command.argv[0] or plan.command == list(command.argv):
            raise AdmissionDenied("direct host execution is prohibited")
        launcher_cwd = plan.metadata.get("launcher_cwd")
        if launcher_cwd is not None and (
            not isinstance(launcher_cwd, str) or not launcher_cwd
            or not (Path(launcher_cwd).is_absolute()
                    or PureWindowsPath(launcher_cwd).is_absolute())
        ):
            raise AdmissionDenied("sandbox launcher directory is invalid")
        audit_command = shlex.join(command.argv)
        facts = _jail_facts(strategy)
        # Do not let a second runner reclassify the jail based on metadata
        # provided by the model. The supervisor derives these facts from the
        # chosen concrete sandbox strategy.
        def invoke() -> dict[str, int | str]:
            start = time.monotonic()
            if strategy == IsolationStrategy.HARDENED_DOCKER:
                # Never inherit DOCKER_HOST/DOCKER_CONTEXT, which might route
                # an apparently local no-network command to a remote daemon.
                env = minimal_agent_env(base={})
            else:
                env = minimal_agent_env()
            # No shell and no output payload enters logs, telemetry or
            # the durable action ledger. Rich bounded output requires a
            # separate, typed and redacted tool-response adapter.
            result = subprocess.run(
                plan.command, shell=False, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=command.timeout_seconds,
                env=env, cwd=launcher_cwd,
            )
            return {
                "exit_code": result.returncode,
                "backend": plan.backend,
                "elapsed_ms": max(0, int((time.monotonic() - start) * 1000)),
            }

        receipt, data = self._receipts.execute_once(
            lease, idempotency_key=command.idempotency_key,
            authorization={
                "kind": "process_exec", "tool": command.tool, "target": cwd,
                "command": audit_command, "executable": command.argv[0],
                "jail": facts,
            },
            invoke=invoke,
        )
        return BrokerOutcome(receipt, int(data["exit_code"]), str(data["backend"]),
                             int(data["elapsed_ms"]))
