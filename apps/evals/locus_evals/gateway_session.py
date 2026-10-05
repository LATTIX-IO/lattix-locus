"""Gateway sessions for evaluation runs (LOCUS-332).

Every eval instance acts through an explicit gateway session, like any other
run: the agent's reads, writes and process executions are authorized by the
Rego policies before they happen. Eval sessions carry the ``evals`` run
profile, which is what lets tool_jail accept a SWE-bench instance container
(``docker-exec`` with networking disabled) as a jail.

Only the gateway built here accepts ``evals`` sessions
(``Gateway(..., allow_eval_sessions=True)``); the backend's gateway refuses
them, so a normal run cannot claim the evaluation-container jail. Host
workspaces (synthetic-mini) run under the platform's confining executor
(``default_executor``), exactly as normal runs do.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from locus_runtime.gateway import (
    EVALS_PROFILE,
    Capabilities,
    Gateway,
    GatewayAuditRecord,
    GatewaySession,
    default_allowed_executables,
)
from locus_runtime.policy_engine import PolicyEngine, build_policy_engine

LOGGER = logging.getLogger(__name__)

#: Operations an eval instance performs (agent_policy ``allowed_tools``).
EVAL_OPERATIONS = frozenset({"read_file", "write_file", "process_exec"})


class JsonlAuditSink:
    """Appends gateway decisions to ``<output_dir>/gateway-audit.jsonl``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, record: GatewayAuditRecord) -> None:
        line = json.dumps(record.as_metadata(), sort_keys=True, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def build_eval_gateway(
    output_dir: Path,
    *,
    engine: PolicyEngine | None = None,
    audit_sink: Callable[[GatewayAuditRecord], None] | None = None,
) -> Gateway:
    """The evaluation harness's gateway: the configured policy engine (OPA by
    default), a JSONL audit trail, and acceptance of ``evals`` sessions."""
    policy_engine: Any = engine if engine is not None else build_policy_engine()
    start = getattr(policy_engine, "start", None)
    if engine is None and callable(start):
        start()  # an engine that cannot start raises: no eval runs unauthorized
    sink = audit_sink or JsonlAuditSink(Path(output_dir) / "gateway-audit.jsonl")
    return Gateway(policy_engine, sink, allow_eval_sessions=True)


def eval_capabilities(root: str) -> Capabilities:
    return Capabilities(
        allowed_tools=EVAL_OPERATIONS,
        read_roots=(str(root),),
        write_roots=(str(root),),
        allowed_executables=default_allowed_executables(),
        runtime_profile=EVALS_PROFILE,
    )


def open_eval_session(gateway: Gateway, *, run_id: str, root: str) -> GatewaySession:
    """An ``evals`` session for one instance, confined to its workspace ``root``."""
    return gateway.open_session(
        run_id=run_id,
        principal="locus-evals",
        engine="evals",
        capabilities=eval_capabilities(root),
    )


def close_eval_gateway(gateway: Gateway) -> None:
    try:
        gateway.engine.close()
    except Exception:  # noqa: BLE001 - closing is cleanup
        LOGGER.exception("evals.gateway_close_error")
