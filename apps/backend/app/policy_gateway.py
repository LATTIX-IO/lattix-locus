"""Backend wiring for the gateway PEP (LOCUS-332).

The backend process owns one :class:`~locus_runtime.gateway.Gateway`, built on
the configured policy engine (OPA sidecar by default) and the backend audit
log. Each run opens a :class:`~locus_runtime.gateway.GatewaySession` whose
capabilities come from operator configuration and the run's own definition
(workspace roots, declared tools, egress allowlist) -- never from the agent.

If the engine cannot start, the gateway is still installed: every decision is
then ``deny`` (fail closed) and posture reports the control as not enforced.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from locus_runtime.gateway import (
    AuditSink,
    Authorizer,
    BudgetFigures,
    Capabilities,
    DecisionListener,
    Gateway,
    GatewaySession,
    default_allowed_executables,
    host_of,
    install_gateway,
    installed_gateway,
)
from locus_runtime.policy_engine import (
    REASON_UNAVAILABLE,
    Decision,
    build_policy_engine,
)

LOGGER = logging.getLogger(__name__)
_LOCK = threading.Lock()

#: Operations a coding run's executors perform (agent_policy ``allowed_tools``).
HARNESS_OPERATIONS = frozenset({"read_file", "write_file", "process_exec"})


class UnavailableEngine:
    """Stand-in when no engine could be constructed: every decision denies."""

    name = "unavailable"
    running = False

    def decide(self, policy: str, input: dict[str, Any]) -> Decision:  # noqa: A002, ARG002
        return Decision(
            allow=False, reasons=[REASON_UNAVAILABLE], policy_version="unknown", backend=self.name
        )

    def close(self) -> None:
        return None


def ensure_backend_gateway(audit_sink: AuditSink) -> Authorizer:
    """Install the process gateway once (idempotent); return what is installed."""
    with _LOCK:
        existing = installed_gateway()
        if existing is not None:
            return existing
        try:
            engine: Any = build_policy_engine()
        except Exception:  # noqa: BLE001 - misconfiguration denies, never allows
            LOGGER.exception("gateway.engine_config_error")
            engine = UnavailableEngine()
        start = getattr(engine, "start", None)
        if callable(start):
            try:
                start()
            except Exception as exc:  # noqa: BLE001 - an engine that is down denies
                LOGGER.warning("gateway.engine_unavailable: %s", type(exc).__name__)
        gateway = Gateway(engine, audit_sink)
        install_gateway(gateway)
        LOGGER.info("gateway.installed", extra={"healthy": gateway.healthy})
        return gateway


def egress_hosts(
    allowed_egress_hosts: Iterable[str], mcp_server_urls: Iterable[str]
) -> tuple[str, ...]:
    """Operator-configured egress allowlist plus the hosts of approved MCP servers."""
    hosts = {str(host).strip().lower() for host in allowed_egress_hosts if str(host).strip()}
    hosts |= {host_of(url) for url in mcp_server_urls if host_of(url)}
    return tuple(sorted(hosts))


def run_capabilities(
    *,
    allowed_tools: Iterable[str],
    roots: Iterable[str] = (),
    egress: Iterable[str] = (),
    max_tool_calls: int = 0,
    budget: BudgetFigures | None = None,
) -> Capabilities:
    root_list = tuple(str(Path(root)) for root in roots if str(root or "").strip())
    return Capabilities(
        allowed_tools=frozenset(str(tool) for tool in allowed_tools if str(tool or "").strip()),
        read_roots=root_list,
        write_roots=root_list,
        allowed_executables=default_allowed_executables(),
        allowed_egress_hosts=tuple(egress),
        max_tool_calls=max(0, int(max_tool_calls or 0)),
        budget=budget,
    )


def open_run_session(
    *,
    run_id: str,
    principal: str,
    engine: str,
    capabilities: Capabilities,
    on_decision: DecisionListener | None = None,
) -> GatewaySession | None:
    """Open a session on the installed :class:`Gateway`.

    Returns ``None`` when the installed authorizer is not a :class:`Gateway`
    (only test doubles are); callers then act as unbound callers of that
    authorizer, which a real gateway would deny.
    """
    gateway = installed_gateway()
    if not isinstance(gateway, Gateway):
        return None
    return gateway.open_session(
        run_id=run_id,
        principal=principal or "anonymous",
        engine=engine,
        capabilities=capabilities,
        on_decision=on_decision,
    )


def harness_session_factory(
    *,
    run_id: str,
    principal: str,
    egress: Iterable[str],
    on_decision: DecisionListener | None,
    opened: list[GatewaySession],
) -> Callable[[Any, list[str]], GatewaySession | None]:
    """``(workspace_root, extra_paths) -> session`` for harness executors."""
    egress_tuple = tuple(egress)

    def factory(root: Any, extra_paths: list[str]) -> GatewaySession | None:
        session = open_run_session(
            run_id=run_id,
            principal=principal,
            engine="harness",
            capabilities=run_capabilities(
                allowed_tools=HARNESS_OPERATIONS,
                roots=[str(root), *[str(p) for p in extra_paths]],
                egress=egress_tuple,
            ),
            on_decision=on_decision,
        )
        if session is not None:
            opened.append(session)
        return session

    return factory


def close_sessions(sessions: Iterable[GatewaySession | None]) -> None:
    for session in sessions:
        if session is None:
            continue
        try:
            session.close()
        except Exception:  # noqa: BLE001 - closing is cleanup
            LOGGER.exception("gateway.session_close_error")
