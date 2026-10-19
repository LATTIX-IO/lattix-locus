"""LOCUS-466: receipt-fenced Linux Docker launch with stage as the sole bind.

Trusted supervisor code only. No agent can call this object or choose mounts,
image, daemon, Docker flags, or launch identity. Output remains in stage and
MUST be independently verified before any later source promotion.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shlex
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from locus_runtime.action_receipts import ActionRecord, PodActionReceipts
from locus_runtime.gateway import JailFacts
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, PodReservationLedger
from locus_runtime.pod_sandbox_broker import BrokerCommand, _require_content_addressed_image
from locus_runtime.pod_stage_bindings import PodStageBindings
from locus_runtime.pod_stage_guardian import require_guardian_ready
from locus_runtime.sandbox import IsolationStrategy, _validated_seccomp_profile_path
from locus_runtime.pod_workspace_staging import staged_workspace


logger = logging.getLogger(__name__)


class StagePodCancelled(RuntimeError):
    """The trusted parent cancelled an attempted container invocation."""


@dataclass(frozen=True, slots=True)
class StageDockerOutcome:
    receipt: ActionRecord
    exit_code: int
    elapsed_ms: int
    stage_id: str
    generation: str


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def _trusted_local_binary(candidate: Path) -> tuple[str, str]:
    """Require a root-owned, non-writable regular Docker CLI on Linux."""
    if not candidate.is_absolute():
        raise AdmissionDenied("Docker CLI must be an absolute trusted host executable")
    try:
        resolved = candidate.resolve(strict=True)
        data = resolved.stat()
        if (
            not stat.S_ISREG(data.st_mode) or data.st_uid != 0
            or stat.S_IMODE(data.st_mode) & 0o022
            or not os.access(resolved, os.X_OK)
        ):
            raise AdmissionDenied("Docker CLI provenance cannot be trusted")
        sha = hashlib.sha256()
        with resolved.open("rb") as stream:
            while block := stream.read(262144):
                sha.update(block)
        # Root ownership and mode make modification by this unprivileged
        # supervisor impossible; the host/root are in the trusted base.
        after = resolved.stat()
        if (
            (data.st_dev, data.st_ino, data.st_size, data.st_mtime_ns, data.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        ):
            raise AdmissionDenied("Docker CLI changed during attestation")
        return str(resolved), sha.hexdigest()
    except OSError as exc:
        raise AdmissionDenied("trusted Docker CLI unavailable") from exc


def _local_socket(candidate: Path) -> str:
    """Pin the daemon to a local Unix socket; ignore user Docker contexts."""
    if not candidate.is_absolute():
        raise AdmissionDenied("Docker daemon socket must be absolute")
    try:
        resolved = candidate.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise AdmissionDenied("trusted local Docker daemon unavailable") from exc
    if not stat.S_ISSOCK(info.st_mode):
        raise AdmissionDenied("local Docker endpoint is not a Unix socket")
    if (
        info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(info.st_mode) & 0o002
    ):
        raise AdmissionDenied("Docker socket is not under trusted host control")
    return "unix://" + str(resolved)


def _command_vector(
    *, docker: str, socket_uri: str, image: str, name: str,
    stage: str, entrypoint: str, arguments: tuple[str, ...],
    seccomp: str, uid: int, gid: int, proof: str,
) -> tuple[str, ...]:
    """One fixed, admin-constructed launch vector. No planner or model flags."""
    return (
        docker, "--host", socket_uri, "run", "--rm", "--pull=never",
        "--name", name,
        "--label=io.lattix.locus.stage-only=v1",
        "--label=io.lattix.locus.stage-proof=" + proof,
        "--network=none", "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--security-opt=seccomp=" + seccomp,
        "--read-only", "--user=" + str(uid) + ":" + str(gid),
        "--ipc=private", "--cgroupns=private",
        "--memory=256m", "--cpus=1", "--pids-limit=64",
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
        "--mount",
        "type=bind,source=" + stage
        + ",target=/workspace,bind-propagation=rprivate,bind-recursive=disabled",
        "--workdir=/workspace", "--entrypoint=" + entrypoint,
        image, *arguments,
    )


def _require_pristine_snapshot(
    *, source: str, stage: str, expected_sha256: str,
) -> None:
    """Rehash the source and staged initial tree with the canonical copier.

    The supervisor copies into a *different* disposable sibling, never into
    the admitted stage or original source. This deliberately repeats work
    under the same anchored no-follow/hardlink/size rules as the source
    snapshot. A newly injected secret path on the stage fails, rather than
    being silently excluded by the copier.
    """
    stage_root = Path(stage)
    with staged_workspace(source, staging_parent=stage_root.parent) as original:
        if original.sha256 != expected_sha256:
            raise AdmissionDenied("original workspace changed since approved snapshot")
    with staged_workspace(stage, staging_parent=stage_root.parent) as current:
        if current.sha256 != expected_sha256 or current.excluded:
            raise AdmissionDenied("stage differs from sealed source snapshot")


def _stage_proof(*, stage_id: str, binding: str, generation: str,
                 reservation: str, name: str, image: str) -> str:
    """Daemon-visible provenance label sealed before Gateway authorization."""
    return _digest({
        "schema": "locus.stage-docker-recovery.v1",
        "stage_id": stage_id, "binding": binding,
        "generation": generation, "reservation": reservation,
        "container_name": name, "image": image,
    })


def _record_launch(
    receipts: PodActionReceipts, *, stage_id: str, action_id: str,
    name: str, launch_sha256: str, state: str, exit_code: int | None = None,
    proof: str | None = None, image_ref: str | None = None,
    socket_uri: str | None = None,
) -> None:
    """Durable bounded launch audit, in the same SQLite ledger as receipts."""
    with receipts.reservations._connect() as db:
        receipts.reservations._tx(db)
        row = db.execute(
            "SELECT state,launch_sha256,container_name,action_id,stage_proof,image_ref,socket_uri FROM pod_stage_docker_launch "
            "WHERE stage_id=?", (stage_id,),
        ).fetchone()
        if row is None:
            if (state != "prepared" or not proof or not image_ref or not socket_uri):
                raise AdmissionDenied("verified launch descriptor must be prepared first")
            db.execute(
                "INSERT INTO pod_stage_docker_launch "
                "(stage_id,action_id,container_name,launch_sha256,stage_proof,"
                "image_ref,socket_uri,state,stamp) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (stage_id, action_id, name, launch_sha256, proof, image_ref,
                 socket_uri, state, receipts.reservations._now()),
            )
        else:
            if (row["launch_sha256"] != launch_sha256
                or row["container_name"] != name or row["action_id"] != action_id
                or (proof is not None and row["stage_proof"] != proof)
                or (image_ref is not None and row["image_ref"] != image_ref)
                or (socket_uri is not None and row["socket_uri"] != socket_uri)):
                raise AdmissionDenied("launch identity was substituted")
            if row["state"] == state and state == "prepared":
                raise AdmissionDenied("stage launch cannot be prepared twice")
            if (row["state"], state) not in {
                ("prepared", "dispatching"),
                ("dispatching", "finished"),
                ("dispatching", "uncertain"),
            }:
                raise AdmissionDenied("invalid or replayed stage launch transition")
            db.execute(
                "UPDATE pod_stage_docker_launch SET state=?,exit_code=?,stamp=? "
                "WHERE stage_id=? AND state=?",
                (state, exit_code, receipts.reservations._now(), stage_id,
                 row["state"]),
            )


def initialize_stage_docker_schema(ledger: PodReservationLedger) -> None:
    """Bootstrap the durable launch table before starting OS guardian service.

    No container, receipt, capability or launch authority is issued here.
    The reservation, action and StageLease tables must exist first.
    """
    if not isinstance(ledger, PodReservationLedger):
        raise AdmissionDenied("trusted supervisor ledger required")
    with ledger._connect() as db:
        ledger._tx(db)
        db.execute("""CREATE TABLE IF NOT EXISTS pod_stage_docker_launch (
            stage_id TEXT PRIMARY KEY REFERENCES pod_stage_binding(id),
            action_id TEXT NOT NULL UNIQUE REFERENCES pod_action_receipt(id),
            container_name TEXT NOT NULL UNIQUE,
            launch_sha256 TEXT NOT NULL,
            stage_proof TEXT NOT NULL,
            image_ref TEXT NOT NULL,
            socket_uri TEXT NOT NULL,
            state TEXT NOT NULL CHECK(
                state IN ('prepared','dispatching','finished','uncertain')
            ),
            exit_code INTEGER,
            stamp INTEGER NOT NULL
        )""")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class StageDockerBroker:
    """Only path for stage-bound writable Docker until independent acceptance."""

    stages: PodStageBindings
    receipts: PodActionReceipts
    image: str
    docker_path: Path
    socket_path: Path

    def __init__(
        self, stages: PodStageBindings, receipts: PodActionReceipts, *,
        image: str,
        docker_path: str | Path = "/usr/bin/docker",
        socket_path: str | Path = "/var/run/docker.sock",
    ) -> None:
        if stages.receipts is not receipts or stages.ledger.path != receipts.reservations.path:
            raise AdmissionDenied("Docker stage requires one authenticated receipt authority")
        _require_content_addressed_image(image)
        object.__setattr__(self, "stages", stages)
        object.__setattr__(self, "receipts", receipts)
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "docker_path", Path(docker_path))
        object.__setattr__(self, "socket_path", Path(socket_path))
        initialize_stage_docker_schema(receipts.reservations)
    def execute(
        self, lease: PodLease, stage_id: str, command: BrokerCommand, *,
        cancel: threading.Event | None = None,
    ) -> StageDockerOutcome:
        if not isinstance(lease, PodLease) or not isinstance(command, BrokerCommand):
            raise AdmissionDenied("trusted lease and bounded command required")
        if cancel is not None and not isinstance(cancel, threading.Event):
            raise AdmissionDenied("cancellation must be a trusted local event")
        if cancel is not None and cancel.is_set():
            raise StagePodCancelled("cancelled before admission")
        if os.name != "posix" or os.uname().sysname != "Linux" or os.geteuid() == 0:
            raise AdmissionDenied("stage Docker launch requires nonroot Linux supervisor")
        active = self.receipts.reservations.require_active(lease.reservation_id)
        # Never authorize a launch without an OS-notified independent guardian.
        # The epoch is pinned across receipt dispatch; a guardian replacement
        # during execution cannot silently inherit the prior authorization.
        guardian_epoch = require_guardian_ready(
            self.receipts.reservations, self.socket_path,
        )
        if (
            command.timeout_seconds > active.requested.wall_seconds
            or self.receipts.reservations._now() + command.timeout_seconds
            >= active.expires_at
        ):
            raise AdmissionDenied("stage command exceeds child duration or lease deadline")
        caps = lease._session.capabilities
        if (
            caps.write_roots or command.tool not in caps.allowed_tools
            or command.argv[0] not in caps.allowed_executables
        ):
            raise AdmissionDenied("unapproved or host-writable child command")
        stage = self.stages.inspect(lease, stage_id)
        if (
            stage.state != "bound" or stage.engine_id != lease._session.caller.engine
            or command.working_dir != stage.staged_path
        ):
            raise AdmissionDenied("command does not belong to bound stage attempt")
        # Fail before receipt preparation unless the host daemon, binary,
        # seccomp and stage witness can be independently re-attested.
        docker, cli_sha = _trusted_local_binary(self.docker_path)
        socket_uri = _local_socket(self.socket_path)
        seccomp = str(_validated_seccomp_profile_path())
        _require_pristine_snapshot(
            source=stage.source_path, stage=stage.staged_path,
            expected_sha256=stage.source_sha256,
        )
        generation = self.stages.make_executable(lease, stage_id)
        if generation != self.stages_generation(lease, stage_id):
            raise AdmissionDenied("stage generation was substituted")
        name = "locus-pod-" + secrets.token_hex(16)
        uid, gid = os.geteuid(), os.getegid()
        proof = _stage_proof(
            stage_id=stage_id, binding=stage.binding_sha256,
            generation=generation, reservation=stage.reservation_id,
            name=name, image=self.image,
        )
        argv = _command_vector(
            docker=docker, socket_uri=socket_uri, image=self.image,
            name=name, stage=stage.staged_path, entrypoint=command.argv[0],
            arguments=command.argv[1:], seccomp=seccomp, uid=uid, gid=gid,
            proof=proof,
        )
        launch_sha256 = _digest({
            "argv": argv, "stage_id": stage_id,
            "stage_binding": stage.binding_sha256, "stage_generation": generation,
            "source_snapshot": stage.source_sha256,
            "child": stage.child_run_id, "reservation": stage.reservation_id,
            "dag_revision": stage.graph_fingerprint, "engine": stage.engine_id,
            "engine_pins": stage.engine_pins_sha256, "pod_spec": stage.pod_revision,
            "capabilities": stage.capability_sha256,
            "docker_cli_sha256": cli_sha, "timeout_seconds": command.timeout_seconds,
            "guardian_epoch": guardian_epoch,
        })
        # The exact frozen argv including digest-pinned image, container name,
        # stage source and resource limits is sealed into Gateway/receipt auth.
        authorization = {
            "kind": "process_exec", "tool": command.tool,
            "target": stage.staged_path, "executable": command.argv[0],
            "command": shlex.join(command.argv),
            "jail": JailFacts(
                strategy=IsolationStrategy.HARDENED_DOCKER.value,
                readonly_rootfs=True, run_as_user=str(uid) + ":" + str(gid),
                allow_network=False,
            ),
            "args": {
                "stage_binding_sha256": stage.binding_sha256,
                "stage_generation_sha256": hashlib.sha256(
                    generation.encode("ascii")
                ).hexdigest(),
                "docker_launch_sha256": launch_sha256,
                "docker_cli_sha256": cli_sha,
                "guardian_epoch_sha256": hashlib.sha256(
                    guardian_epoch.encode("ascii")
                ).hexdigest(),
            },
        }
        receipt = self.receipts.prepare(
            lease, idempotency_key=command.idempotency_key,
            authorization=authorization,
        )
        linked = self.stages.link_prepared_receipt(lease, stage_id, receipt.action_id)
        if linked.action_id != receipt.action_id:
            raise AdmissionDenied("stage receipt link changed")
        _record_launch(
            self.receipts, stage_id=stage_id, action_id=receipt.action_id,
            name=name, launch_sha256=launch_sha256, state="prepared",
            proof=proof, image_ref=self.image, socket_uri=socket_uri,
        )

        def invoke() -> dict[str, int]:
            # Inside the kernel-held action fence; no other broker can replay.
            if require_guardian_ready(
                self.receipts.reservations, self.socket_path,
            ) != guardian_epoch:
                raise AdmissionDenied("guardian epoch changed before dispatch")
            pristine = self.stages.inspect(lease, stage_id)
            _require_pristine_snapshot(
                source=stage.source_path, stage=stage.staged_path,
                expected_sha256=stage.source_sha256,
            )
            attested = self.stages.inspect_executable(lease, stage_id)
            if (
                pristine.action_id != receipt.action_id
                or attested.binding_sha256 != stage.binding_sha256
                or self.stages_generation(lease, stage_id) != generation
                or cancel is not None and cancel.is_set()
            ):
                raise AdmissionDenied("stage changed or was cancelled before process dispatch")
            _record_launch(
                self.receipts, stage_id=stage_id, action_id=receipt.action_id,
                name=name, launch_sha256=launch_sha256, state="dispatching",
            )
            start = time.monotonic()
            # No user Docker profile, context, environment or remote daemon.
            with tempfile.TemporaryDirectory(prefix="locus-docker-empty-") as config:
                env = {
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "DOCKER_CONFIG": config,
                    "HOME": config,
                }
                process: subprocess.Popen[bytes] | None = None
                try:
                    if require_guardian_ready(
                        self.receipts.reservations, self.socket_path,
                    ) != guardian_epoch:
                        raise AdmissionDenied("guardian epoch changed at OS boundary")
                    process = subprocess.Popen(
                        argv, shell=False, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=True, env=env,
                    )
                    deadline = start + command.timeout_seconds
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise StagePodCancelled("container cancelled")
                        if require_guardian_ready(
                            self.receipts.reservations, self.socket_path,
                        ) != guardian_epoch:
                            raise AdmissionDenied("guardian restarted during child execution")
                        # Revoking a parent or child must not leave an
                        # already-running Docker worker unchecked until its
                        # original timeout. Authentication is rechecked
                        # without creating another Gateway action receipt.
                        self.receipts.reservations.require_active(
                            lease.reservation_id,
                        )
                        if lease._session.gateway._authenticate(
                            lease._session.caller
                        ) is None:
                            raise AdmissionDenied("child Gateway identity revoked")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, command.timeout_seconds)
                        try:
                            code = process.wait(timeout=min(0.2, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    if code != 0:
                        # Docker CLI exit 125 can mean the container NEVER
                        # launched. No positive receipt may be written for a
                        # rejected daemon invocation or failed tool action.
                        raise AdmissionDenied("stage container exited unsuccessfully")
                    self.stages.inspect_executable(lease, stage_id)
                    _record_launch(
                        self.receipts, stage_id=stage_id, action_id=receipt.action_id,
                        name=name, launch_sha256=launch_sha256, state="finished",
                        exit_code=code,
                    )
                    return {
                        "exit_code": int(code),
                        "elapsed_ms": max(0, int((time.monotonic() - start) * 1000)),
                    }
                except BaseException:
                    # A killed Docker CLI may leave an orphaned daemon process.
                    # A failed cleanup is an *uncertain* action, never replayed.
                    try:
                        subprocess.run(
                            [docker, "--host", socket_uri, "kill", name],
                            shell=False, check=False, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=5,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        # Uncertain receipt fencing blocks replays even if
                        # Docker daemon cleanup could not be confirmed.
                        logger.warning("stage Docker container kill was not confirmed")
                    try:
                        _record_launch(
                            self.receipts, stage_id=stage_id,
                            action_id=receipt.action_id, name=name,
                            launch_sha256=launch_sha256, state="uncertain",
                        )
                    except Exception:
                        # Preserve the original error; a dispatching receipt
                        # remains nonreplayable and must be reconciled.
                        logger.error("stage Docker uncertainty audit failed; manual reconciliation required")
                    raise
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)

        final, data = self.receipts.execute_prepared_once(
            lease, receipt.action_id, invoke=invoke,
        )
        return StageDockerOutcome(
            final, data["exit_code"], data["elapsed_ms"], stage_id, generation,
        )

    def stages_generation(self, lease: PodLease, stage_id: str) -> str:
        """Extract only the registered nonce after independent V2 attestation."""
        self.stages.inspect_executable(lease, stage_id)
        with self.receipts.reservations._connect() as db:
            row = db.execute(
                "SELECT generation FROM pod_stage_exec_generation WHERE stage_id=?",
                (stage_id,),
            ).fetchone()
            if row is None:
                raise AdmissionDenied("missing executable generation")
            return str(row["generation"])
