"""LOCUS-470/B: first real, stage-confined Codex CLI engine adapter.

This adapter is called only by the trusted parent after TaskPlan acceptance,
durable engine pinning, a child Gateway PodLease and StageLease admission. It
never executes a host Codex binary or constructs its own Gateway. The pinned
OCI image contains the real official Codex CLI and verified-version runner.
All model requests are mediated by CodexOllamaBridge via the existing child
Gateway, with Docker --network=none. MCP, arbitrary tools, hosted inference,
secret forwarding, and host profile mounts are deliberately unavailable.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

from locus_runtime.pod_codex_bridge import CodexOllamaBridge
from locus_runtime.pod_reservations import AdmissionDenied, PodLease
from locus_runtime.pod_sandbox_broker import BrokerCommand
from locus_runtime.pod_stage_docker import StageDockerBroker, StageDockerOutcome

CODEX_VERSION = "0.163.0"
ENGINE_ID = "codex-cli/" + CODEX_VERSION
ENTRYPOINT = "/usr/local/bin/locus-codex-stdio"
_MODEL = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,100}\Z", re.ASCII)
_OUTPUT = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,80}\.txt\Z", re.ASCII)


@dataclass(frozen=True, slots=True)
class CodexCliResult:
    stage_id: str
    exit_code: int
    output_path: str
    output_sha256: str
    model_calls: int
    reserved_tokens: int
    elapsed_ms: int
    action_id: str


class CodexCliWorker:
    """An authenticated supervisor adapter, not a model-owned execution port."""

    def __init__(self, broker: StageDockerBroker, *, model: str) -> None:
        if not isinstance(broker, StageDockerBroker):
            raise AdmissionDenied("stage Docker supervisor required")
        if not isinstance(model, str) or _MODEL.fullmatch(model) is None:
            raise AdmissionDenied("pinned local model required")
        self.broker = broker
        self.model = model

    def run(
        self,
        lease: PodLease,
        stage_id: str,
        *,
        prompt: str,
        output_name: str,
        idempotency_key: str,
        timeout_seconds: int = 55,
        cancel: threading.Event | None = None,
    ) -> CodexCliResult:
        if not isinstance(lease, PodLease):
            raise AdmissionDenied("authenticated child lease required")
        if not isinstance(prompt, str) or not 0 < len(prompt.encode("utf-8")) <= 48 * 1024:
            raise AdmissionDenied("bounded Codex task required")
        if not isinstance(output_name, str) or _OUTPUT.fullmatch(output_name) is None:
            raise AdmissionDenied("one plain-text terminal artifact required")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise AdmissionDenied("bounded Codex deadline required")
        stage = self.broker.stages.inspect(lease, stage_id)
        if stage.engine_id != ENGINE_ID or stage.state != "bound":
            raise AdmissionDenied("Codex stage/engine pin mismatch")
        # The output must be newly created in the stage; never overwrite a
        # source file or permit an operator/model-selected path traversal.
        from pathlib import Path

        if (Path(stage.staged_path) / output_name).exists():
            raise AdmissionDenied("terminal artifact would replace a source file")
        data = {
            "schema": "locus.codex.stdio.v1", "kind": "init",
            "version": CODEX_VERSION, "model": self.model,
            "prompt": prompt, "output": output_name,
        }
        initial = json.dumps(
            data, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        if len(initial) > 65536:
            raise AdmissionDenied("Codex init exceeds transport size limit")
        bridge = CodexOllamaBridge(lease, model=self.model)
        outcome: StageDockerOutcome = self.broker.execute(
            lease, stage_id,
            BrokerCommand(
                tool="run_command", argv=(ENTRYPOINT,),
                working_dir=stage.staged_path, idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            ),
            stdin=initial, codex_bridge=bridge, cancel=cancel,
        )
        if outcome.exit_code != 0 or not outcome.codex_terminal_sha256:
            raise AdmissionDenied("Codex execution lacked terminal evidence")
        # The digest comes from a pinned runner, not the independent verifier.
        # Consumers MUST call DetachedArtifactEscrow.seal before accepting.
        return CodexCliResult(
            stage_id=stage_id, exit_code=outcome.exit_code,
            output_path=output_name,
            output_sha256=outcome.codex_terminal_sha256,
            model_calls=len(bridge.receipts),
            reserved_tokens=sum(r.reserved_tokens for r in bridge.receipts),
            elapsed_ms=outcome.elapsed_ms,
            action_id=outcome.receipt.action_id,
        )
