"""Trusted supervisor's narrow sandboxed tool-broker for admitted Locus pods.

This boundary *never* runs a raw agent-supplied command. A pinned local tool
descriptor provides argv and permitted paths; the gateway authorizes the
specific action and the receipts ledger writes its dispatch first. Only a
genuinely confining SandboxManager plan can be executed, never a fallback.
"""
from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from locus_runtime.action_receipts import ActionRecord, PodActionReceipts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.sandbox import (
    CONFINING_STRATEGIES, ExecutionSpec, HostPlatform, SandboxManager, SandboxPolicy,
)


def _run_bounded(
    command: list[str], *, cwd: str, env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    """Drain both subprocess pipes while retaining <=64KiB of each.

    Draining continues even after the output cap so a noisy child cannot
    deadlock on a full pipe. A timeout kills the sandbox launcher and still
    collects the bounded tails; the caller records an uncertain action.
    """
    process = subprocess.Popen(
        command, shell=False, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    collected: dict[str, bytes] = {"stdout": b"", "stderr": b""}
    failures: list[BaseException] = []

    def drain(name: str) -> None:
        stream = process.stdout if name == "stdout" else process.stderr
        assert stream is not None
        parts = bytearray()
        try:
            while True:
                chunk = stream.read(32768)
                if not chunk:
                    break
                remaining = 65536 - len(parts)
                if remaining > 0:
                    parts.extend(chunk[:remaining])
        except OSError as exc:
            failures.append(exc)
        finally:
            collected[name] = bytes(parts)
            stream.close()

    readers = [
        threading.Thread(target=drain, args=(name,), daemon=True)
        for name in ("stdout", "stderr")
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join()
        raise
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    for reader in readers:
        reader.join()
    if failures:
        raise OSError("sandbox output drain failed") from failures[0]
    return subprocess.CompletedProcess(
        args=command, returncode=process.returncode,
        stdout=collected["stdout"], stderr=collected["stderr"],
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
                 run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
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
        # An isolation plan must invoke a distinct sandbox launcher, never
        # hand back the tool command for unconfined direct host execution.
        if list(plan.command) == list(descriptor.argv):
            raise AdmissionDenied("sandbox plan cannot directly invoke tool")
        if any(not isinstance(arg, str) or not arg or "\x00" in arg
               for arg in plan.command):
            raise AdmissionDenied("malformed sandbox launcher command")
        launcher_cwd = plan.metadata.get("launcher_cwd", descriptor.cwd)
        if not isinstance(launcher_cwd, str) or not Path(launcher_cwd).is_absolute():
            raise AdmissionDenied("invalid sandbox launcher working directory")
        def invoke() -> BrokerResult:
            try:
                if self._run is None:
                    result = _run_bounded(list(plan.command), cwd=launcher_cwd,
                        env={"PATH": os.defpath}, timeout=descriptor.max_seconds)
                else:
                    # Explicitly injected runners are for deterministic unit
                    # tests; production always uses bounded pipe drainage.
                    result = self._run(list(plan.command), shell=False,
                        cwd=launcher_cwd, env={"PATH": os.defpath},
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
