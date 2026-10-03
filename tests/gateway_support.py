"""Test doubles for the gateway PEP (LOCUS-332). Test-only: never import from runtime code.

``AllowAllAuthorizer`` lets the existing harness/backend suites exercise their
own behaviour without an OPA sidecar. Gateway behaviour itself is tested
against the real ``Gateway`` in ``tests/unit/test_gateway.py`` and
``tests/policy/test_gateway_opa.py``; the bypass test installs its own spy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from locus_runtime import gateway as gw
from locus_runtime.policy_engine import Decision


def _decision(action: gw.GatewayAction, outcome: gw.Outcome, reason: str) -> gw.GatewayDecision:
    return gw._sealed(  # noqa: SLF001 - test double issues sealed decisions like the gateway
        gw.GatewayDecision(
            outcome=outcome,
            reasons=(reason,),
            audit_id=f"test-{len(reason)}",
            policy_version="test",
            risk=action.risk,
            action_kind=action.kind,
            tool=action.tool,
            target=action.target,
            fingerprint=action.fingerprint,
            args_digest=action.args_digest,
        )
    )


class AllowAllAuthorizer:
    """Allows every action and records it."""

    def __init__(self) -> None:
        self.actions: list[gw.GatewayAction] = []

    def authorize(self, action: gw.GatewayAction) -> gw.GatewayDecision:
        self.actions.append(action)
        return _decision(action, "allow", "test.allow_all")


class FixedAuthorizer:
    """Returns one fixed outcome for every action and records it."""

    def __init__(self, outcome: gw.Outcome) -> None:
        self.outcome = outcome
        self.actions: list[gw.GatewayAction] = []

    def authorize(self, action: gw.GatewayAction) -> gw.GatewayDecision:
        self.actions.append(action)
        return _decision(action, self.outcome, f"test.{self.outcome}")


class FakeEngine:
    """PolicyEngine double: per-policy allow map, optional failure, call log."""

    name = "fake"

    def __init__(
        self,
        allow: dict[str, bool] | None = None,
        *,
        default: bool = True,
        running: bool = True,
        raises: bool = False,
    ) -> None:
        self.allow = dict(allow or {})
        self.default = default
        self.running = running
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def decide(self, policy: str, input: dict[str, Any]) -> Decision:  # noqa: A002
        self.calls.append((policy, input))
        if self.raises:
            raise RuntimeError("engine exploded")
        if not self.running:
            return Decision(
                allow=False,
                reasons=["policy_engine_unavailable"],
                policy_version="sha256:fake",
                backend=self.name,
            )
        allowed = self.allow.get(policy, self.default)
        return Decision(
            allow=allowed,
            reasons=[f"{policy}.{'allow' if allowed else 'not_allowed'}"],
            policy_version="sha256:fake",
            backend=self.name,
        )

    def close(self) -> None:
        return None


@contextmanager
def installed(authorizer: gw.Authorizer | None) -> Iterator[gw.Authorizer | None]:
    previous = gw.installed_gateway()
    gw.install_gateway(authorizer)
    try:
        yield authorizer
    finally:
        gw.install_gateway(previous)
