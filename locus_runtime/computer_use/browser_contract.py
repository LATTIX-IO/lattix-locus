"""The ``BrowserDriver`` port (D-28 modular ports; LOCUS-350).

Two drivers implement it:

* ``profile="agent"`` -- :class:`~locus_runtime.computer_use.browser.AgentBrowser`,
  the isolated Playwright Chromium in a Locus-owned profile (LOCUS-341).
* ``profile="user"`` --
  :class:`~locus_runtime.computer_use.user_browser.driver.UserBrowserDriver`,
  the principal's own signed-in Chrome / Edge / Firefox through the Locus
  extension (LOCUS-350, D-25).

The contract every driver keeps (``tests/policy/test_browser_driver_contract.py``):

1. **Gateway first.** Every :class:`BrowserAction` that perceives or acts is
   authorized by the gateway (one ``browser_*`` / ``user_browser_*`` action)
   before anything runs in the browser; a non-``allow`` decision returns an
   ``ask`` / ``denied`` observation and the browser is not touched.
2. **Panic.** Every action runs inside a
   :class:`~locus_runtime.computer_use.controller.ComputerUseController` action;
   a panic cancels it and every later action returns ``cancelled`` until reset.
3. **Secrets.** Password, card, CVV, SSN and OTP field values never appear in
   an observation; entering data into such a field is refused (R4).
4. **Untrusted content.** Page-derived text in ``text`` is wrapped as untrusted.

The trust kernel stays outside drivers: tiers, the floor and risk classes live
in Rego and the gateway. A driver only perceives facts, asks the gateway and
executes what it allowed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from locus_runtime.computer_use.common import UiResult

PORT_VERSION = "1.0"

BrowserProfile = Literal["agent", "user"]
BrowserOp = Literal["tabs", "navigate", "read", "act", "screenshot"]
ActControl = Literal["click", "fill", "press", "select", "scroll"]
ObservationOutcome = Literal[
    "done", "denied", "ask", "proposed", "blocked_by_mode", "cancelled", "error"
]

_MAX_VALUE = 10_000


class BrowserAction(BaseModel):
    """One request to a browser driver (what the model asked for)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    op: BrowserOp
    url: str = Field(default="", max_length=4096)
    tab_id: str = Field(default="", max_length=32)
    control: ActControl | None = None
    ref: str = Field(default="", max_length=64)
    selector: str = Field(default="", max_length=1024)
    role: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=300)
    value: str = Field(default="", max_length=_MAX_VALUE)


class ElementRef(BaseModel):
    """One element an observation exposes (secret values are always redacted)."""

    model_config = ConfigDict(frozen=True)

    ref: str
    role: str = ""
    name: str = ""
    value: str = ""
    disabled: bool = False


class BrowserObservation(BaseModel):
    """What a driver reports back. ``text`` is what the model sees."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    port_version: str = PORT_VERSION
    profile: BrowserProfile
    outcome: ObservationOutcome
    text: str
    url: str = ""
    site: str = ""
    audit_id: str = ""
    risk: str = ""
    elements: list[ElementRef] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    # The sealed GatewayDecision (never serialized; the toolset uses it to
    # report asks and denials in the loop's standard way).
    decision: Any = Field(default=None, exclude=True)

    @property
    def ok(self) -> bool:
        return self.outcome == "done"

    @classmethod
    def from_ui_result(cls, profile: BrowserProfile, result: UiResult) -> BrowserObservation:
        data = dict(result.data or {})
        raw_elements = data.pop("elements", None)
        elements: list[ElementRef] = []
        if isinstance(raw_elements, list):
            for item in raw_elements:
                if isinstance(item, dict) and item.get("ref"):
                    elements.append(
                        ElementRef(
                            ref=str(item.get("ref")),
                            role=str(item.get("role") or ""),
                            name=str(item.get("name") or ""),
                            value=str(item.get("value") or ""),
                            disabled=bool(item.get("disabled")),
                        )
                    )
        from locus_runtime.computer_use.user_browser.sites import display_url

        decision = result.decision
        return cls(
            profile=profile,
            outcome=result.outcome,
            text=result.text,
            url=display_url(str(data.get("url") or "")),
            site=str(data.get("site") or ""),
            audit_id=str(getattr(decision, "audit_id", "") or ""),
            risk=str(
                getattr(getattr(decision, "risk", None), "label", "") or data.get("risk") or ""
            ),
            elements=elements,
            data=data,
            decision=decision,
        )


@runtime_checkable
class BrowserDriver(Protocol):
    """A browser the agent drives through the gateway (see the module docstring)."""

    profile: BrowserProfile
    port_version: str

    def perform(self, action: BrowserAction) -> BrowserObservation:
        """Authorize ``action`` at the gateway, then (only if allowed) run it."""
        ...

    def detach(self) -> None:
        """End of run: release the browser and stop listening for panics."""
        ...


def unsupported(profile: BrowserProfile, action: BrowserAction, why: str) -> BrowserObservation:
    return BrowserObservation(
        profile=profile,
        outcome="error",
        text=f"[error] {action.op}: {why}",
    )


__all__ = [
    "PORT_VERSION",
    "ActControl",
    "BrowserAction",
    "BrowserDriver",
    "BrowserObservation",
    "BrowserOp",
    "BrowserProfile",
    "ElementRef",
    "unsupported",
]
