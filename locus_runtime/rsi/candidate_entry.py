"""Entry point executed *inside* a candidate instance (LOCUS-351).

The evaluator runs this file **by path from its own (trusted) install** with the
candidate checkout first on ``PYTHONPATH``, a scrubbed environment and a separate
app home (see :mod:`locus_runtime.rsi.candidate`)::

    <python> -s <trusted>/locus_runtime/rsi/candidate_entry.py request.json result.json

Everything it imports from ``locus_runtime`` is the **candidate's** code: the
runtime (``create_runtime``), prompts, tools, executor wiring, gateway and model
client. One run, one sample, through the gateway with the real OPA engine, the
sandboxed executor and the metering proxy as the only model endpoint.

What it reports is the candidate's account of the run. The evaluator does not
grade from it: grading, tokens and canaries are measured outside this process.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|_PAT\b|AUTH)", re.I)
MODEL_CALL_TOOL = "llm_call"


def isolation_facts() -> dict[str, Any]:
    """What this process can see: proves the candidate's isolation from inside."""
    facts: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "pid": os.getpid(),
        "app_home": os.environ.get("LOCUS_APP_HOME", ""),
        "loop_home": os.environ.get("LOCUS_LOOP_HOME", ""),
        "telemetry_db": os.environ.get("LOCUS_TELEMETRY_DB", ""),
        "home": os.path.expanduser("~"),
        "secret_like_env": sorted(
            name
            for name in os.environ
            if _SECRET_NAME.search(name) and name not in {"PYTHON_KEYRING_BACKEND"}
        ),
    }
    try:
        import keyring

        facts["keyring_backend"] = type(keyring.get_keyring()).__module__
    except Exception as exc:  # noqa: BLE001 - reported, not fatal
        facts["keyring_backend"] = f"unavailable:{type(exc).__name__}"
    try:
        from locus_runtime.model_client import PROVIDERS, ProviderKeyStore
        from locus_tooling.common import default_app_home

        # Where the secret store's DPAPI fallback would look, and whether any keyed
        # provider resolves a key here (it must not: no secrets in a candidate).
        facts["secret_store_home"] = str(default_app_home())
        store = ProviderKeyStore()
        facts["provider_keys"] = sorted(
            name for name, spec in PROVIDERS.items() if spec.key_env and store.configured(name)
        )
    except Exception as exc:  # noqa: BLE001
        facts["provider_keys"] = f"unavailable:{type(exc).__name__}"
    try:
        import locus_runtime

        facts["locus_runtime"] = str(Path(locus_runtime.__file__).resolve().parent)
    except Exception as exc:  # noqa: BLE001
        facts["locus_runtime"] = f"unavailable:{type(exc).__name__}"
    return facts


def _tool_calls(messages: list[dict[str, Any]]) -> list[list[str]]:
    calls: list[list[str]] = []
    for message in messages:
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            calls.append([str(fn.get("name") or "")[:80], str(fn.get("arguments") or "")[:2000]])
    return calls[:400]


def run(request: dict[str, Any]) -> dict[str, Any]:
    """One sample with the candidate's runtime. Never raises (errors are reported)."""
    from locus_runtime import gateway as gw
    from locus_runtime import telemetry
    from locus_runtime.harness.executor import default_executor
    from locus_runtime.harness.llm import GatedChatClient
    from locus_runtime.harness.mediation import MediationMonitor
    from locus_runtime.harness.model_profiles import resolve_profile
    from locus_runtime.harness.prompts import SWE_SYSTEM_PROMPT, build_task_prompt
    from locus_runtime.harness.run_envelope import RunEnvelope
    from locus_runtime.harness.runtime_contract import RuntimeRequest
    from locus_runtime.harness.runtimes import create_runtime, default_runtime_name
    from locus_runtime.harness.tools import CodingToolset
    from locus_runtime.harness.trajectory import TrajectoryRecorder
    from locus_runtime.harness.workspace import Workspace
    from locus_runtime.loop_runner.delivery import GitOps, HostWorkspaceGit
    from locus_runtime.model_client import GatewayModelGate, ModelRouter, ModelTier, build_client
    from locus_runtime.policy_engine import OpaSidecarEngine, find_opa_binary

    run_id = str(request["run_id"])
    root = Path(str(request["workspace"])).resolve()
    out_dir = Path(str(request["output_dir"])).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime_name = str(request.get("runtime") or default_runtime_name())
    provider = str(request["provider"])
    model = str(request["model"])
    canaries = [str(c) for c in request.get("canaries") or [] if c]
    record: dict[str, Any] = {
        "run_id": run_id,
        "runtime": runtime_name,
        "model": f"{provider}/{model}",
    }
    started = time.time()
    telemetry.ensure_configured()
    binary = find_opa_binary()
    if binary is None:
        record.update(end_state="error", error="no OPA binary (LOCUS_OPA_BIN)")
        return record
    engine = OpaSidecarEngine(opa_binary=binary, timeout_seconds=10.0)
    try:
        engine.start()
        audit_path = out_dir / f"{run_id}.audit.jsonl"

        def jsonl(rec: gw.GatewayAuditRecord) -> None:
            with audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.as_metadata(), sort_keys=True, default=str) + "\n")

        monitor = MediationMonitor(jsonl)
        gateway = gw.Gateway(engine, monitor.audit_sink)
        if not gateway.healthy:
            record.update(end_state="error", error="the policy engine is not running")
            return record
        envelope = RunEnvelope.from_dict(dict(request["envelope"]))
        caps = envelope.gateway_capabilities()
        caps = dataclasses.replace(caps, allowed_tools=caps.allowed_tools | {MODEL_CALL_TOOL})
        session = gateway.open_session(
            run_id=run_id, principal="locus-rsi-eval", engine=runtime_name, capabilities=caps
        )
        try:
            executor = default_executor(root, gateway_session=session)
            monitor.attach(executor)
            git = GitOps()
            git.seal(root)
            workspace = Workspace(
                run_id=run_id,
                executor=executor,
                test_command=str(request.get("test_command") or ""),
                host_git=HostWorkspaceGit(git, root),
            )
            gate = GatewayModelGate(session=session)
            router = ModelRouter(
                [ModelTier(provider, model)],
                client_factory=lambda tier: build_client(tier, gate=gate, run_id=run_id),
            )
            client = monitor.wrap_client(GatedChatClient(router))
            profile = resolve_profile(provider, model)
            toolset = CodingToolset(workspace=workspace, edit_format=profile.edit_format)
            recorder = TrajectoryRecorder(
                run_id=run_id, file_path=out_dir / f"{run_id}.trajectory.jsonl"
            )
            runtime = create_runtime(runtime_name)
            result = runtime.run(
                RuntimeRequest(
                    envelope=envelope,
                    toolset=toolset,
                    client=client,
                    profile=profile,
                    system_prompt=SWE_SYSTEM_PROMPT,
                    user_prompt=build_task_prompt(
                        str(request["problem"]), test_hint=str(request.get("test_command") or "")
                    ),
                    run_id=run_id,
                    recorder=recorder,
                    agent_id="locus-rsi-eval",
                    task_meta={
                        "task": str(request.get("task_id") or ""),
                        "trial": request.get("trial", 0),
                    },
                )
            )
        finally:
            session.close()
        report = monitor.report()
        messages = recorder.messages()
        tool_outputs = [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]
        record.update(
            {
                "end_state": result.end_state,
                "verified": result.verified,
                "stop_kind": (result.stop or {}).get("kind"),
                "stop_dimension": (result.stop or {}).get("dimension"),
                "blocker_kind": (result.blocker or {}).get("kind"),
                "usage": {
                    k: result.usage.get(k)
                    for k in (
                        "steps",
                        "model_calls",
                        "actions",
                        "prompt_tokens",
                        "completion_tokens",
                        "cost_usd",
                    )
                },
                "gateway": {
                    "allow": report.outcomes.get("allow", 0),
                    "deny": report.outcomes.get("deny", 0),
                    "ask": report.outcomes.get("ask", 0),
                    "model_call_decisions": int(report.model_call_decisions),
                },
                "mediation": {
                    "model_coverage": round(report.model_coverage, 4),
                    "side_effect_coverage": round(report.side_effect_coverage, 4),
                    "model_calls_observed": report.model_calls_observed,
                    "side_effects_observed": report.side_effects_observed,
                    "unmediated": len(report.unmediated),
                },
                "tool_calls": _tool_calls(messages),
                "canary_in_tool_output": any(c in t for c in canaries for t in tool_outputs),
                "injection_text_seen": any(
                    str(request.get("injection_text") or "\0") in t for t in tool_outputs
                ),
            }
        )
    except Exception as exc:  # noqa: BLE001 - a crashed run is a recorded error
        record.update(
            end_state="error",
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
            traceback_tail=traceback.format_exc()[-1500:],
        )
    finally:
        engine.close()
        telemetry.force_flush()
        record["wall_seconds_inside"] = round(time.time() - started, 2)
    return record


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: candidate_entry.py <request.json> <result.json>\n")
        return 2
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    facts = isolation_facts()
    if request.get("mode") == "probe":
        result: dict[str, Any] = {"isolation": facts}
    else:
        result = run(request)
        result["isolation"] = facts
    Path(argv[1]).write_text(json.dumps(result, sort_keys=True, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
