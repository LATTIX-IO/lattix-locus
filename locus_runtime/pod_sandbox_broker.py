"""Trusted, gateway-mediated command broker for an admitted ephemeral pod.

No new PEP, no shell, no network and no direct execution fallback. This module
is *only* called by trusted control-plane code; do not expose its objects or
GatewaySession tokens to untrusted agents. ActionReceipts owns the write-ahead
dispatch, kernel fencing and at-most-once broker invocation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
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
    _validated_seccomp_profile_path,
    _verify_broker_seccomp_profile,
    _docker_protected_workspace_mounts,
    detect_host_platform,
    minimal_agent_env,
    sandbox_runner_image,
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
_BROKER_TOOL_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}\Z", re.ASCII)
_ACTION_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z", re.ASCII)
_IMAGE_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,239}\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class BrokerCommand:
    tool: str
    argv: tuple[str, ...]
    working_dir: str
    idempotency_key: str
    timeout_seconds: int = 30
    catalog_digest: str | None = None

    def __post_init__(self) -> None:
        # Use the administrator catalog's exact ASCII tool-name grammar.
        # No whitespace, control sequences, slash aliases, or Unicode
        # lookalikes can reach gateway/audit action labels.
        if (not isinstance(self.tool, str)
            or _BROKER_TOOL_ID.fullmatch(self.tool) is None):
            raise ValueError("bounded canonical tool identifier required")
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
        if (not isinstance(self.working_dir, str)
            or not 1 <= len(self.working_dir) <= 4096
            or not self.working_dir.strip()
            or any(char in self.working_dir for char in ("\x00", "\r", "\n"))):
            raise ValueError("bounded working directory required")
        # Mirror the durable receipt key grammar at construction, before
        # strategy planning or gateway receipt work begins. Do not admit
        # characters that the ledger would reject only at prepare().
        if (not isinstance(self.idempotency_key, str)
            or _ACTION_KEY.fullmatch(self.idempotency_key) is None):
            raise ValueError("bounded canonical idempotency key required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= _MAX_TIMEOUT:
            raise ValueError("execution timeout must be in 1..60 seconds")
        if (self.catalog_digest is not None
            and (type(self.catalog_digest) is not str
                 or len(self.catalog_digest) != 64
                 or any(char not in "0123456789abcdef" for char in self.catalog_digest))):
            raise ValueError("catalog digest must be 64 canonical SHA-256 hex characters")


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


def _require_content_addressed_image(image: str | None) -> None:
    """Deny mutable Docker image tags when strict admin policy is enabled.

    This validates a *reference*, not registry attestations or local daemon
    image identity. A digest must remain in the image argument actually
    delivered to Docker, validated again by the exact launch contract.
    """
    if not isinstance(image, str) or image.count("@sha256:") != 1:
        raise AdmissionDenied("Docker sandbox image digest pin required")
    name, digest = image.split("@sha256:")
    # The repository name must have at least one valid component, and no
    # path traversal, repeated separators, tags, or whitespace. Port notation
    # may occur only in a registry prefix such as host:5000/team/runner.
    last_segment = name.rsplit("/", 1)[-1]
    if (not _IMAGE_NAME.fullmatch(name)
        or not _IMAGE_SHA256.fullmatch(digest)
        or ".." in name or "//" in name or "/./" in name
        or ":" in last_segment or name.endswith((".", "/", "-", "_"))):
        raise AdmissionDenied("Docker sandbox image digest pin required")


def _expected_docker_launch_contract(
    spec: ExecutionSpec, policy: SandboxPolicy, *, image: str,
) -> list[str]:
    """Snapshot the one permitted Docker command BEFORE invoking the planner.

    SandboxPolicy and ExecutionSpec carry mutable lists, so reconstructing
    the contract *after* SandboxManager.plan() would trust mutations made by
    the planner. Freeze this immutable string vector before crossing that
    boundary instead.
    """
    if policy.allow_network or spec.env or spec.requested_hosts:
        raise AdmissionDenied("Docker sandbox isolation contract denied")
    seccomp_path = _validated_seccomp_profile_path()
    try:
        _verify_broker_seccomp_profile(seccomp_path)
    except ValueError:
        raise AdmissionDenied("Docker seccomp baseline denied") from None
    writable = {
        str(Path(path).expanduser().resolve())
        for path in policy.allowed_write_paths
    }
    readable = {
        str(Path(path).expanduser().resolve())
        for path in policy.allowed_read_paths
    }
    # Source path strings become comma-delimited Docker --mount options.
    # Reject ambiguity before any policy decision, receipt or process launch.
    for path in writable | readable:
        if any(ord(char) in (0, 10, 13, 44) for char in path):
            raise AdmissionDenied("Docker mount source path denied")
    try:
        sensitive_readonly, missing_protected = _docker_protected_workspace_mounts(
            set(policy.allowed_write_paths)
        )
    except (OSError, RuntimeError, ValueError):
        raise AdmissionDenied("Docker protected workspace path denied") from None
    expected = [
        "docker", "run", "--rm",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--read-only", "--user=1000:1000", "--ipc=private",
        f"--security-opt=seccomp={seccomp_path}",
        "--network=none",
        f"--memory={policy.memory_limit}",
        f"--cpus={policy.cpu_limit}",
        f"--pids-limit={policy.pid_limit}",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=100m",
    ]
    for path in sorted(writable):
        expected.extend((
            "--mount", f"type=bind,source={path},target={path}",
        ))
    readonly = readable - writable
    for path in sorted(readonly):
        # Match the planner's no-auto-create contract before Gateway/receipts.
        if not Path(path).exists():
            raise AdmissionDenied("Docker read mount source denied")
        expected.extend((
            "--mount", f"type=bind,source={path},target={path},readonly,bind-recursive=readonly",
        ))
    for protected in sensitive_readonly:
        if protected not in readonly:
            expected.extend((
                "--mount", f"type=bind,source={protected},target={protected},readonly,bind-recursive=readonly",
            ))
    for protected in missing_protected:
        expected.extend((
            "--tmpfs", f"{protected}:ro,nosuid,nodev,noexec,size=4k,mode=0555",
        ))
    if spec.cwd:
        expected.extend(("-w", spec.cwd))
    expected.extend((image, *spec.command))
    return expected


def _validate_docker_launch_contract(
    argv: list[str], expected: list[str],
) -> None:
    """Deny conflicting, additional or planner-forged Docker CLI arguments."""
    if argv != expected:
        raise AdmissionDenied("Docker sandbox isolation contract denied")


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


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PodSandboxBroker:
    """Immutable trusted configuration; executes only via fenced receipt sink."""

    _receipts: PodActionReceipts
    _require_immutable_docker_image: bool
    _pinned_docker_image: str | None

    def __init__(self, receipts: PodActionReceipts, *,
                 require_immutable_docker_image: bool = False,
                 pinned_docker_image: str | None = None) -> None:
        if type(require_immutable_docker_image) is not bool:
            raise ValueError("Docker image pin policy must be a boolean")
        if require_immutable_docker_image:
            try:
                _require_content_addressed_image(pinned_docker_image)
            except AdmissionDenied:
                raise ValueError("strict Docker image policy requires an approved digest") from None
        elif pinned_docker_image is not None:
            raise ValueError("approved Docker image must have strict mode enabled")
        # Freeze the validated supervisor settings and the single receipt
        # delegate at construction; application code must create a new broker
        # for a configuration revision rather than mutate a live one.
        object.__setattr__(self, "_receipts", receipts)
        object.__setattr__(self, "_require_immutable_docker_image",
                           require_immutable_docker_image)
        object.__setattr__(self, "_pinned_docker_image", pinned_docker_image)

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
        # Strict image admission is meaningful only if Docker actually runs
        # the selected digest. Never silently substitute Seatbelt,
        # AppContainer, or another confinement backend when the supervisor
        # explicitly requires the approved Docker image.
        if (self._require_immutable_docker_image
            and strategy != IsolationStrategy.HARDENED_DOCKER):
            raise AdmissionDenied("strict Docker image policy requires hardened Docker")
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
        # Capture the image from trusted supervisor configuration exactly
        # once. A mutable environment change during planning cannot authorize
        # a different image argument; the exact argv contract will reject it.
        docker_image = (
            sandbox_runner_image()
            if strategy == IsolationStrategy.HARDENED_DOCKER else None
        )
        if (self._require_immutable_docker_image
            and strategy == IsolationStrategy.HARDENED_DOCKER):
            if docker_image is None:
                raise AdmissionDenied("Docker sandbox image digest pin required")
            _require_content_addressed_image(docker_image)
            if docker_image != self._pinned_docker_image:
                raise AdmissionDenied("Docker sandbox image does not match approved digest")
        # Snapshot the immutable authorized Docker argv before the planner
        # can mutate the spec or policy's mutable fields.
        expected_docker = (
            _expected_docker_launch_contract(spec, policy, image=docker_image)
            if docker_image is not None else None
        )
        try:
            plan = SandboxManager(force_strategy=strategy).plan(spec, policy)
        except PermissionError:
            # A mutated planner may reject its own widened request (for
            # example network access without host allowlisting). Normalize
            # this early planner denial into the broker's fail-closed API;
            # never expose or persist planner-supplied diagnostic strings.
            if strategy == IsolationStrategy.HARDENED_DOCKER:
                raise AdmissionDenied("Docker sandbox isolation contract denied") from None
            raise
        if (plan.strategy != strategy or plan.backend != strategy.value
            or not isinstance(plan.command, list) or not plan.command
            or any(not isinstance(arg, str) or not arg or "\x00" in arg
                   for arg in plan.command)):
            raise AdmissionDenied("sandbox planning failed closed")
        # Never allow a planner to silently degrade to the allowlisted host
        # executable. A policy decision is not itself a process boundary.
        if plan.command[0] == command.argv[0] or plan.command == list(command.argv):
            raise AdmissionDenied("direct host execution is prohibited")
        if (strategy == IsolationStrategy.HARDENED_DOCKER
            and plan.command[:3] != ["docker", "run", "--rm"]):
            raise AdmissionDenied("unrecognized Docker sandbox launcher")
        # Docker's vetted planner emits NO host-side launcher directory,
        # metadata, or network route. Never allow a mutable planner to widen
        # the host working directory or smuggle network parameters that the
        # verified Docker command itself does not reflect.
        if strategy == IsolationStrategy.HARDENED_DOCKER:
            if plan.metadata != {} or plan.network_name is not None:
                raise AdmissionDenied("Docker sandbox planner metadata denied")
            launcher_cwd = None
            if expected_docker is None:
                raise AdmissionDenied("Docker sandbox isolation contract denied")
            _validate_docker_launch_contract(plan.command, expected_docker)
        else:
            launcher_cwd = plan.metadata.get("launcher_cwd")
            if launcher_cwd is not None and (
                not isinstance(launcher_cwd, str) or not launcher_cwd
                or not (Path(launcher_cwd).is_absolute()
                        or PureWindowsPath(launcher_cwd).is_absolute())
            ):
                raise AdmissionDenied("sandbox launcher directory is invalid")
        # The planner may retain its mutable plan object. The receipt engine
        # performs durable preparation/fencing before invoke(), so never read
        # the planner-owned argv or backend again after this validation point.
        # A post-validation mutation must not change the dispatched command.
        authorized_launch_argv = tuple(plan.command)
        authorized_backend = strategy.value
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
            # Docker's client can time out while the daemon keeps the
            # container running. Use a broker-generated name, never one taken
            # from a model, to attempt bounded cleanup on client timeout.
            argv = list(authorized_launch_argv)
            container_name: str | None = None
            if strategy == IsolationStrategy.HARDENED_DOCKER:
                if argv[:3] != ["docker", "run", "--rm"]:
                    raise AdmissionDenied("unrecognized Docker sandbox launcher")
                container_name = "locus-pod-" + secrets.token_hex(16)
                argv[3:3] = ["--name", container_name]
            # No shell and no output payload enters logs, telemetry or
            # the durable action ledger. Rich bounded output requires a
            # separate, typed and redacted tool-response adapter.
            try:
                result = subprocess.run(
                    argv, shell=False, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=command.timeout_seconds,
                    env=env, cwd=launcher_cwd,
                )
            except subprocess.TimeoutExpired:
                if container_name is not None:
                    try:
                        subprocess.run(
                            ["docker", "kill", container_name],
                            shell=False, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=5, env=env, cwd=launcher_cwd,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        # Unknown external state remains 'uncertain'; the
                        # timeout is never converted to permission to replay.
                        pass
                raise
            return {
                "exit_code": result.returncode,
                "backend": authorized_backend,
                "elapsed_ms": max(0, int((time.monotonic() - start) * 1000)),
            }

        # Bind the supervisor-approved Docker image *reference* to both the
        # native gateway action fingerprint and receipt's canonical HMAC.
        # A hash of the full ref commits to its registry/repository as well
        # as its digest; never emit a raw registry URL into the audit args.
        authorization_args: dict[str, str] = {}
        if command.catalog_digest is not None:
            authorization_args["catalog_digest"] = command.catalog_digest
        if (self._require_immutable_docker_image
            and strategy == IsolationStrategy.HARDENED_DOCKER):
            if docker_image is None:
                raise AdmissionDenied("Docker sandbox image digest pin required")
            authorization_args["sandbox_image_ref_digest"] = hashlib.sha256(
                b"locus.sandbox-image-ref.v1\x00" + docker_image.encode("utf-8"),
            ).hexdigest()
            # Bind the *entire* pre-receipt verified Docker launch contract,
            # excluding only the randomly generated container name. This
            # captures limits, mounts, seccomp, image, cwd, timeout and the
            # admitted argv. Changing any of these under an old idempotency key must
            # not be treated as the same durable action.
            canonical_launch = json.dumps(
                {"backend": authorized_backend,
                 "argv": authorized_launch_argv,
                 "launcher_cwd": launcher_cwd,
                 "timeout_seconds": command.timeout_seconds},
                sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("ascii")
            authorization_args["sandbox_launch_contract_digest"] = (
                hashlib.sha256(
                    b"locus.sandbox-launch-contract.v1\x00" + canonical_launch,
                ).hexdigest()
            )
        receipt, data = self._receipts.execute_once(
            lease, idempotency_key=command.idempotency_key,
            authorization={
                "kind": "process_exec", "tool": command.tool, "target": cwd,
                "command": audit_command, "executable": command.argv[0],
                "jail": facts,
                # Structured, nonsecret identity material is canonically
                # included in the Gateway fingerprint and durable receipt.
                # Legacy direct callers without strict image policy or
                # catalog identity retain the original authorization shape.
                **({"args": authorization_args} if authorization_args else {}),
            },
            invoke=invoke,
        )
        return BrokerOutcome(receipt, int(data["exit_code"]), str(data["backend"]),
                             int(data["elapsed_ms"]))
