"""LOCUS-472: real Locus Deep Agents draft producer, confined stage writer.

DeepAgentsRuntime is the actual native harness. Its model client is built from
the admitted child PodLease, not supplied by a caller. No filesystem/process
tool is offered to the model; its only deliverable is a verified 'submit'
answer. A separately authenticated stage-only Docker broker writes those bytes
through its sole writable disposable stage, and the independent escrow seals
the result. This is NOT a generic host file tool or a Codex substitute.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.llm import ChatResponse, GatedChatClient
from locus_runtime.harness.model_profiles import resolve_profile
from locus_runtime.harness.run_envelope import (
    EnvelopeCapabilities, FileCheck, RunBudget, RunEnvelope,
)
from locus_runtime.harness.runtime_contract import RuntimeRequest
from locus_runtime.harness.runtimes import DEEP_AGENTS, create_runtime
from locus_runtime.harness.tools import CodingToolset
from locus_runtime.harness.workspace import Workspace
from locus_runtime.model_client import (
    GatewayModelGate, ModelClient, ModelEndpoint, ModelRouter, ModelTier,
    provenance_denial,
)
from locus_runtime.pod_artifact_escrow import DetachedArtifactEscrow, EscrowRecord
from locus_runtime.pod_reservations import AdmissionDenied, PodLease, ResourceBudget
from locus_runtime.pod_sandbox_broker import BrokerCommand
from locus_runtime.pod_stage_docker import StageDockerBroker

NATIVE_ENGINE_ID = "locus-native/deep-agents"
STAGE_WRITER = "/usr/local/bin/locus-stage-writer"


class _LeaseBoundModel:
    """Conservatively reserve model budget before every native model call."""

    provider = "ollama"

    def __init__(self, lease: PodLease, model: str, client: GatedChatClient) -> None:
        self.lease = lease
        self.model = model
        self.client = client
        self.calls = 0

    def complete(self, messages: list[dict], **kwargs: object) -> ChatResponse:
        if self.calls >= 8:
            raise AdmissionDenied("native specialist model-call budget exhausted")
        try:
            serialized = json.dumps(messages, ensure_ascii=True, allow_nan=False).encode()
        except (ValueError, TypeError) as exc:
            raise AdmissionDenied("unbounded native model context") from exc
        if not 0 < len(serialized) <= 16384:
            raise AdmissionDenied("native specialist context exceeds bound")
        cap = 512
        requested = kwargs.get("max_tokens")
        if requested is not None and (type(requested) is not int or requested < 1):
            raise AdmissionDenied("invalid native output ceiling")
        self.calls += 1
        active = self.lease._ledger.require_active(self.lease.reservation_id)
        used = active.used
        self.lease.settle(ResourceBudget(
            used.actions + 1, used.tool_calls + 1,
            used.tokens + len(serialized) + cap,
            used.wall_seconds, used.spend_microunits,
        ))
        kwargs["max_tokens"] = cap
        return self.client.complete(messages, **kwargs)


@dataclass(frozen=True, slots=True)
class NativeDraftResult:
    escrow: EscrowRecord
    runtime: str
    model_calls: int
    draft_sha256: str
    action_id: str


class NativeLocusDraftWorker:
    """Supervisor-managed real native Locus Deep Agents brief producer."""

    def __init__(self, broker: StageDockerBroker, *, model: str = "llama3.2:1b") -> None:
        if not isinstance(broker, StageDockerBroker):
            raise AdmissionDenied("trusted stage-only Docker broker required")
        self.broker = broker
        self.model = model

    def run(self, lease: PodLease, stage_id: str, *, task: str) -> NativeDraftResult:
        if not isinstance(lease, PodLease):
            raise AdmissionDenied("authenticated native child lease required")
        if not isinstance(task, str) or not 1 <= len(task.encode("utf-8")) <= 2048:
            raise AdmissionDenied("bounded supervisor-authored brief required")
        stage = self.broker.stages.inspect(lease, stage_id)
        if stage.engine_id != NATIVE_ENGINE_ID or stage.state not in ("bound", "receipt-linked"):
            raise AdmissionDenied("native engine stage pin mismatch")
        if lease._session.capabilities.write_roots:
            raise AdmissionDenied("native model must not have host write capabilities")
        if "model:ollama" not in lease._session.capabilities.allowed_tools:
            raise AdmissionDenied("native model Gateway grant missing")
        if provenance_denial("ollama", self.model, "http://127.0.0.1:11434/v1"):
            raise AdmissionDenied("native local model provenance is not authorized")
        if STAGE_WRITER not in lease._session.capabilities.allowed_executables:
            raise AdmissionDenied("stage writer executable not granted")
        target = ModelEndpoint(
            provider="ollama", model=self.model, base_url="http://127.0.0.1:11434/v1",
        )
        gate = GatewayModelGate(session=lease._session)
        client = ModelClient(target, gate=gate, max_retries=0, timeout=40, run_id=lease.run_id)
        router = ModelRouter(
            [ModelTier("ollama", self.model)], client_factory=lambda _: client,
        )
        native_model = _LeaseBoundModel(lease, self.model, GatedChatClient(router))
        # No shell, editor, browser, filesystem or sub-agent tool is offered.
        # The native runtime's intrinsic submit is the only deliverable.
        envelope = RunEnvelope(
            goal="Draft a concise, factual technical brief",
            done_criteria=(FileCheck(
                id="no-artifact-before-supervisor-commit",
                path="draft.txt", must_exist=False,
            ),),
            capabilities=EnvelopeCapabilities(
                tools=(), read_roots=(stage.staged_path,),
                write_roots=(), egress_hosts=("127.0.0.1",),
            ),
            budget=RunBudget(
                max_steps=8, max_seconds=40, max_tokens=28000,
                max_cost_usd=0.01, max_actions=8,
            ),
        )
        workspace = Workspace(
            run_id=lease.run_id,
            executor=LocalDirectExecutor(
                stage.staged_path, gateway_session=lease._session,
            ),
        )
        toolset = CodingToolset(workspace=workspace, allow_edits=False)
        request = RuntimeRequest(
            envelope=envelope, toolset=toolset, client=native_model,
            profile=resolve_profile("ollama", self.model),
            user_prompt=(
                "Produce a short evidence-grounded noncoding technical brief. "
                "No file changes or tools. When complete, call submit with the "
                "full brief in the answer field. Topic: " + task
            ),
            system_prompt=(
                "You are a Locus native specialist under zero-trust scope. "
                "Do not call tools other than submit; the supervisor writes "
                "verified outputs into an isolated stage."
            ),
            run_id=lease.run_id,
            options={"subagents": False, "compaction": False},
            agent_id="locus-native-brief",
        )
        result = create_runtime(DEEP_AGENTS).run(request)
        submission = result.run.submission if result.run is not None else None
        answer = submission.get("answer") if isinstance(submission, dict) else None
        if (
            not result.done or not result.verified
            or not isinstance(answer, str)
            or not 1 <= len(answer.encode("utf-8")) <= 32768
            or native_model.calls == 0
        ):
            raise AdmissionDenied("native runtime did not independently verify a bounded draft")
        raw = answer.encode("utf-8")
        outcome = self.broker.execute(
            lease, stage_id,
            BrokerCommand(
                tool="run_command",
                argv=(STAGE_WRITER,),
                working_dir=stage.staged_path,
                idempotency_key="native-stage-" + hashlib.sha256(raw).hexdigest(),
                timeout_seconds=8,
            ),
            stdin=raw,
        )
        if outcome.exit_code != 0:
            raise AdmissionDenied("native artifact was not committed in isolated stage")
        escrow = DetachedArtifactEscrow(self.broker.stages).seal(
            lease, stage_id, allowed_output_paths=frozenset({"draft.txt"}),
        )
        if escrow.payload_sha256 != hashlib.sha256(raw).hexdigest():
            raise AdmissionDenied("native artifact integrity mismatch")
        return NativeDraftResult(
            escrow=escrow, runtime=DEEP_AGENTS, model_calls=native_model.calls,
            draft_sha256=escrow.payload_sha256, action_id=outcome.receipt.action_id,
        )
