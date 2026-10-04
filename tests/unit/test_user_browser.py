"""User-browser building blocks without a browser or OPA (LOCUS-350, D-25).

Sites, the principal-only tier store, the relay hub (pairing, sessions, panic),
the native-messaging host and its registration (temp dirs and a fake registry
only), the gateway's fail-closed handling of the tier outputs, the driver's
gate-first behaviour and secret redaction, and the D-28 driver factory.
Rego and real-browser coverage: tests/policy/test_user_browser_opa.py and
tests/policy/test_browser_driver_contract.py.
"""

from __future__ import annotations

import io
import itertools
import json
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import gateway as gw
from locus_runtime.computer_use import controller as cu
from locus_runtime.computer_use.browser_contract import BrowserAction, BrowserObservation
from locus_runtime.computer_use.drivers import build_browser_drivers
from locus_runtime.computer_use.toolset import ComputerUseToolset
from locus_runtime.computer_use.user_browser import native_host as nh
from locus_runtime.computer_use.user_browser import pairing
from locus_runtime.computer_use.user_browser import relay as relay_mod
from locus_runtime.computer_use.user_browser import tiers as tiers_mod
from locus_runtime.computer_use.user_browser.driver import UserBrowserDriver, _redact_elements
from locus_runtime.computer_use.user_browser.sites import normalize_site, site_of
from locus_runtime.computer_use.wiring import build_run_toolset, release_run_toolset
from locus_runtime.gateway import Capabilities, Gateway, RiskClass, UiFacts
from locus_runtime.harness.executor import LocalDirectExecutor
from locus_runtime.harness.workspace import Workspace
from locus_runtime.policy_engine import Decision
from locus_tooling import native_messaging as nm
from tests.gateway_support import FixedAuthorizer, installed

KEY = "unit-pairing-key"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def controller() -> Iterator[cu.ComputerUseController]:
    previous = cu._DEFAULT, cu._INSTALLED  # noqa: SLF001
    ctl = cu.ComputerUseController("takeover")
    cu.install_controller(ctl)
    try:
        yield ctl
    finally:
        cu._DEFAULT, cu._INSTALLED = previous  # noqa: SLF001


@pytest.fixture()
def hub(controller: cu.ComputerUseController) -> Iterator[relay_mod.RelayHub]:
    previous = relay_mod._HUB  # noqa: SLF001
    state = {"key": KEY}
    the_hub = relay_mod.RelayHub(key_loader=lambda: state["key"])
    the_hub.attach(controller)
    the_hub.state = state  # type: ignore[attr-defined]
    relay_mod.install_hub(the_hub)
    try:
        yield the_hub
    finally:
        relay_mod.install_hub(previous)


@pytest.fixture()
def store() -> Iterator[tiers_mod.TierStore]:
    previous = tiers_mod._STORE  # noqa: SLF001
    the_store = tiers_mod.TierStore()
    tiers_mod.install_tier_store(the_store)
    try:
        yield the_store
    finally:
        tiers_mod.install_tier_store(previous)


def _hello(hub: relay_mod.RelayHub) -> tuple[str, str]:
    return hub.hello(origin=pairing.CHROMIUM_ORIGIN, presented_key=KEY, browser="chrome")


class FakeExtension:
    """Answers relay commands like the extension would (on a thread)."""

    def __init__(self, hub: relay_mod.RelayHub, answers: dict[str, Any]) -> None:
        self.hub = hub
        self.answers = answers
        self.seen: list[dict[str, Any]] = []
        self.client_id, self.token = _hello(hub)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                commands = self.hub.next_commands(self.client_id, self.token, wait_s=0.05)
            except relay_mod.RelayAuthError:
                return
            for command in commands:
                self.seen.append(command)
                if command.get("type") != "command":
                    continue
                answer = self.answers.get(command["op"], {})
                if callable(answer):
                    answer = answer(command)
                self.hub.post_result(
                    self.client_id,
                    self.token,
                    {"type": "result", "id": command["id"], "ok": True, "result": answer},
                )

    def ops(self) -> list[str]:
        return [c.get("op", c.get("type")) for c in self.seen]

    def stop(self) -> None:
        self._stop.set()
        self.thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Sites
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("url", "site"),
    [
        ("https://mail.google.com/x?token=1", "google.com"),
        ("https://www.bbc.co.uk/news", "bbc.co.uk"),
        ("https://alice.github.io/", "alice.github.io"),  # private suffix: its own site
        ("http://127.0.0.1:8000/", "127.0.0.1"),
        ("http://localhost/", "localhost"),
        ("javascript:alert(1)", ""),
        ("", ""),
    ],
)
def test_site_of(url: str, site: str) -> None:
    assert site_of(url) == site


def test_normalize_site_accepts_urls_and_hosts() -> None:
    assert normalize_site("https://Mail.Example.COM/inbox") == "example.com"
    assert normalize_site("shop.example.co.uk:443") == "example.co.uk"
    assert normalize_site("  ") == ""


# --------------------------------------------------------------------------- #
# Tier store
# --------------------------------------------------------------------------- #
def test_default_tier_is_strict(store: tiers_mod.TierStore) -> None:
    assert store.settings.tier == "strict" and store.settings.effective_tier == "strict"


@pytest.mark.parametrize("principal_type", ["agent", "service", "npe", ""])
def test_only_a_human_principal_changes_the_tier(
    store: tiers_mod.TierStore, principal_type: str
) -> None:
    with pytest.raises(tiers_mod.TierChangeRefused):
        store.update(
            tier="open", actor="agent:x", principal_type=principal_type, acknowledge_risk=True
        )
    assert store.settings.tier == "strict"


def test_widening_needs_acknowledged_risk_and_records_consent(
    store: tiers_mod.TierStore,
) -> None:
    clock = itertools.count(1000.0, 1000.0)
    store._clock = lambda: next(clock)  # noqa: SLF001
    with pytest.raises(tiers_mod.TierChangeRefused, match="acknowledge"):
        store.update(tier="trusted", granted_sites=["a.com"], actor="alice", principal_type="user")
    settings = store.update(
        tier="trusted",
        granted_sites=["https://www.a.com/x"],
        actor="alice",
        principal_type="user",
        acknowledge_risk=True,
    )
    assert settings.granted_sites == ("a.com",)
    assert settings.consent is not None and settings.consent.actor == "alice"
    assert settings.consent.tier == "trusted" and settings.effective_tier == "trusted"
    assert settings.consent.risk_acknowledged == tiers_mod.TIER_RISKS["trusted"]
    # Adding a site widens again: a fresh acknowledgement is needed.
    with pytest.raises(tiers_mod.TierChangeRefused):
        store.update(
            tier="trusted", granted_sites=["a.com", "b.com"], actor="alice", principal_type="user"
        )
    # Removing one narrows: no acknowledgement, consent kept.
    narrowed = store.update(tier="trusted", granted_sites=[], actor="alice", principal_type="user")
    assert narrowed.consent == settings.consent
    # Back to strict clears consent.
    strict = store.update(tier="strict", actor="alice", principal_type="user")
    assert strict.consent is None and strict.tier_consent is False
    assert [h["to_tier"] for h in store.history] == ["trusted", "trusted", "strict"]


def test_unknown_tier_refused(store: tiers_mod.TierStore) -> None:
    with pytest.raises(tiers_mod.TierChangeRefused):
        store.update(tier="yolo", actor="alice", principal_type="user", acknowledge_risk=True)


def test_tier_store_persists_and_fails_to_strict(tmp_path: Path) -> None:
    path = tmp_path / "tier.json"
    first = tiers_mod.TierStore(path)
    first.update(
        tier="assisted",
        allowlisted_sites=["example.com"],
        actor="alice",
        principal_type="user",
        acknowledge_risk=True,
    )
    again = tiers_mod.TierStore(path)
    assert again.settings.tier == "assisted" and again.settings.tier_consent
    assert again.settings.allowlisted_sites == ("example.com",)
    path.write_text("{not json", encoding="utf-8")
    assert tiers_mod.TierStore(path).settings.effective_tier == "strict"
    path.write_text(json.dumps({"tier": "open", "consent": {"tier": "open"}}), encoding="utf-8")
    assert tiers_mod.TierStore(path).settings.effective_tier == "strict"


def test_visible_tabs_per_tier() -> None:
    tabs = [
        {"tab_id": 1, "site": "bank.com", "shared": False},
        {"tab_id": 2, "site": "example.com", "shared": False},
        {"tab_id": 3, "site": "news.org", "shared": True},
        {"tab_id": 4, "site": "mail.example.com", "shared": False},
    ]
    consent = tiers_mod.ConsentRecord

    def ids(settings: tiers_mod.TierSettings) -> list[int]:
        return [t["tab_id"] for t in tiers_mod.visible_tabs(settings, tabs)]

    assert ids(tiers_mod.TierSettings()) == [3]
    assisted = tiers_mod.TierSettings(
        tier="assisted",
        allowlisted_sites=("example.com",),
        consent=consent("assisted", "alice", "user", 0.0, ""),
    )
    assert ids(assisted) == [2, 3, 4]
    no_consent = tiers_mod.TierSettings(tier="open")
    assert ids(no_consent) == [3]
    opened = tiers_mod.TierSettings(tier="open", consent=consent("open", "alice", "user", 0, ""))
    assert ids(opened) == [1, 2, 3, 4]


# --------------------------------------------------------------------------- #
# Pairing and the relay hub
# --------------------------------------------------------------------------- #
def test_pinned_ids_match_the_extension_manifest() -> None:
    manifest = json.loads((pairing.EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert pairing.chromium_extension_id(manifest["key"]) == pairing.CHROMIUM_EXTENSION_ID
    assert manifest["browser_specific_settings"]["gecko"]["id"] == pairing.FIREFOX_EXTENSION_ID
    assert manifest["manifest_version"] == 3
    assert "nativeMessaging" in manifest["permissions"]
    # The desktop entry point checks the Firefox ID literally (cheap argv check).
    source = (Path(__file__).resolve().parents[2] / "locus_tooling" / "desktop_main.py").read_text(
        encoding="utf-8"
    )
    assert f'"{pairing.FIREFOX_EXTENSION_ID}"' in source


def test_hello_refuses_unpaired_mismatched_and_unpinned(hub: relay_mod.RelayHub) -> None:
    with pytest.raises(relay_mod.RelayAuthError) as unpinned:
        hub.hello(origin="chrome-extension://abcdefghijklmnopabcdefghijklmnop/", presented_key=KEY)
    assert unpinned.value.code == "origin_not_pinned"
    with pytest.raises(relay_mod.RelayAuthError) as mismatch:
        hub.hello(origin=pairing.CHROMIUM_ORIGIN, presented_key="wrong")
    assert mismatch.value.code == "pairing_mismatch"
    hub.state["key"] = None  # type: ignore[attr-defined]
    with pytest.raises(relay_mod.RelayAuthError) as unpaired:
        hub.hello(origin=pairing.CHROMIUM_ORIGIN, presented_key=KEY)
    assert unpaired.value.code == "not_paired"
    with pytest.raises(relay_mod.RelayAuthError):
        hub.hello(origin=pairing.CHROMIUM_ORIGIN, presented_key="")


def test_sessions_are_bound_and_unpairing_revokes_them(hub: relay_mod.RelayHub) -> None:
    client_id, token = _hello(hub)
    assert hub.connected()
    with pytest.raises(relay_mod.RelayAuthError):
        hub.next_commands(client_id, token + "x", wait_s=0)
    with pytest.raises(relay_mod.RelayAuthError):
        hub.next_commands("ub-unknown", token, wait_s=0)
    assert hub.next_commands(client_id, token, wait_s=0) == []
    hub.state["key"] = None  # type: ignore[attr-defined]
    assert not hub.connected()
    with pytest.raises(relay_mod.RelayAuthError) as revoked:
        hub.next_commands(client_id, token, wait_s=0)
    assert revoked.value.code == "not_paired"


def test_call_round_trip_and_extension_errors(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController
) -> None:
    ext = FakeExtension(hub, {"list_tabs": {"tabs": []}})
    try:
        assert hub.call("list_tabs", controller=controller, timeout_s=5) == {"tabs": []}
        ext.answers["boom"] = lambda c: None
    finally:
        ext.stop()
    client_id, token = _hello(hub)

    def answer_with_error() -> None:
        for _ in range(100):
            commands = hub.next_commands(client_id, token, wait_s=0.05)
            for command in commands:
                hub.post_result(
                    client_id,
                    token,
                    {"id": command["id"], "ok": False, "error": {"code": "secret_field"}},
                )
                return

    worker = threading.Thread(target=answer_with_error)
    worker.start()
    with pytest.raises(relay_mod.RelayError) as failed:
        hub.call("act", {}, client_id=client_id, controller=controller, timeout_s=5)
    worker.join()
    assert failed.value.code == "secret_field"


def test_no_browser_connected_and_timeouts(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController
) -> None:
    with pytest.raises(relay_mod.RelayError) as none:
        hub.call("list_tabs", controller=controller)
    assert none.value.code == "no_browser"
    _hello(hub)  # connected, but never answers
    with pytest.raises(relay_mod.RelayError) as slow:
        hub.call("list_tabs", controller=controller, timeout_s=0.2)
    assert slow.value.code == "timeout"


def test_panic_fails_in_flight_calls_reaches_the_extension_and_latches(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController
) -> None:
    client_id, token = _hello(hub)
    epoch = hub.epoch
    outcome: dict[str, Any] = {}

    def in_flight() -> None:
        token_ = controller.begin("user_browser_read")
        try:
            hub.call("observe", {}, cancel=token_, controller=controller, timeout_s=10)
        except (relay_mod.RelayError, cu.ComputerUseCancelled) as exc:
            outcome["error"] = exc
            outcome["at"] = time.perf_counter()
        finally:
            controller.end(token_)

    worker = threading.Thread(target=in_flight)
    worker.start()
    time.sleep(0.2)
    controller.panic("test")
    panicked_at = time.perf_counter()
    worker.join(timeout=5)
    assert (outcome["at"] - panicked_at) * 1000 < 100
    queued = hub.next_commands(client_id, token, wait_s=0)
    assert queued == [{"type": "panic", "epoch": epoch}]  # pending command dropped
    assert hub.epoch == epoch + 1
    with pytest.raises(relay_mod.RelayError) as latched:
        hub.call("list_tabs", controller=controller)
    assert latched.value.code == "panic"


# --------------------------------------------------------------------------- #
# Gateway: the tier outputs fail closed
# --------------------------------------------------------------------------- #
class TierEngine:
    """Allows every policy; ``user_browser`` returns the given outputs."""

    name = "tier-fake"
    running = True

    def __init__(self, outputs: dict[str, Any] | None) -> None:
        self.outputs = outputs
        self.inputs: list[dict[str, Any]] = []

    def decide(self, policy: str, input: dict[str, Any]) -> Decision:  # noqa: A002
        if policy == "user_browser":
            self.inputs.append(input)
            return Decision(True, ["ub"], "v", self.name, outputs=dict(self.outputs or {}))
        return Decision(True, [f"{policy}.allow"], "v", self.name)

    def close(self) -> None:
        return None


def _ub_session(engine: Any) -> gw.GatewaySession:
    gateway = Gateway(engine, lambda record: None)
    caps = Capabilities(
        allowed_tools=frozenset({"user_browser_read", "user_browser_navigate", "user_browser_act"})
    )
    return gateway.open_session(run_id="r", principal="alice", engine="t", capabilities=caps)


def _click(name: str = "Next", **kw: Any) -> dict[str, Any]:
    ui = UiFacts.create(surface="browser", control="click", role="button", name=name, **kw)
    return {"kind": "user_browser_act", "tool": "t", "target": "x", "ui": ui}


@pytest.mark.parametrize(
    ("outputs", "name", "expected"),
    [
        (None, "Next", "ask"),  # no outputs: the tier asks
        ({"require_approval": "false"}, "Next", "ask"),  # not a real boolean
        ({"require_approval": False}, "Next", "allow"),
        ({"require_approval": False}, "Delete", "ask"),  # R3 without Open consent
        ({"require_approval": False, "tier_allows_irreversible": True}, "Delete", "ask"),
        ({"require_approval": True, "tier_allows_irreversible": True}, "Delete", "ask"),
    ],
)
def test_tier_outputs_fail_closed(
    store: tiers_mod.TierStore,
    hub: relay_mod.RelayHub,
    outputs: dict[str, Any] | None,
    name: str,
    expected: str,
) -> None:
    decision = _ub_session(TierEngine(outputs)).authorize(**_click(name))
    assert decision.outcome == expected, decision.describe()


def test_open_consent_needs_the_gateway_built_input_too(
    store: tiers_mod.TierStore, hub: relay_mod.RelayHub
) -> None:
    engine = TierEngine({"require_approval": False, "tier_allows_irreversible": True})
    store.update(tier="open", actor="alice", principal_type="user", acknowledge_risk=True)
    allowed = _ub_session(engine).authorize(**_click("Delete"))
    assert allowed.outcome == "allow" and gw.REASON_OPEN_TIER in allowed.reasons
    payload = engine.inputs[-1]
    assert payload["tier"] == "open" and payload["tier_consent"] is True
    assert payload["profile"] == "user" and payload["risk"] == "R3"
    # R4 is never lifted, whatever the policy says.
    secret = _ub_session(engine).authorize(
        kind="user_browser_act",
        tool="t",
        target="x",
        ui=UiFacts.create(surface="browser", control="fill", input_type="password"),
    )
    assert secret.outcome == "deny" and secret.risk == RiskClass.R4


def test_user_browser_input_comes_from_process_state_not_the_action(
    store: tiers_mod.TierStore, hub: relay_mod.RelayHub, controller: cu.ComputerUseController
) -> None:
    _hello(hub)
    engine = TierEngine({"require_approval": False})
    _ub_session(engine).authorize(**_click(site="example.com", tab_shared=True))
    payload = engine.inputs[-1]
    assert payload["tier"] == "strict" and payload["extension_paired"] is True
    assert payload["panicked"] is False and payload["site"] == "example.com"
    assert payload["tab_shared"] is True
    plans = dict(
        gw.policy_inputs(
            gw.GatewayAction.create(caller=gw.UNBOUND_CALLER, **_click()), Capabilities()
        )
    )
    assert "user_browser" in plans and "computer_use" not in plans


def test_user_browser_classification() -> None:
    ui = UiFacts.create
    assert gw.classify_ui("user_browser_read", ui(surface="browser", control="tabs")) == 0
    assert gw.classify_ui("user_browser_read", ui(surface="browser", control="screenshot")) == 1
    assert gw.classify_ui("user_browser_navigate", ui(surface="browser", control="navigate")) == 2
    assert gw.classify_ui("user_browser_act", ui(surface="browser", control="scroll")) == 1
    assert gw.classify_ui("browser_act", ui(surface="browser", control="scroll")) == 4
    settings_click = ui(surface="browser", control="click", name="Security settings")
    assert gw.classify_ui("user_browser_act", settings_click) == 3
    assert gw.classify_ui("browser_act", settings_click) == 2  # agent browser unchanged


# --------------------------------------------------------------------------- #
# The user driver: gate first, redact always
# --------------------------------------------------------------------------- #
def test_driver_does_not_send_an_act_the_gateway_did_not_allow(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController, tmp_path: Path
) -> None:
    ext = FakeExtension(
        hub,
        {
            "tab_info": {"tab_id": 7, "url": "https://example.com/a", "shared": True},
            "inspect": {
                "facts": {"tag": "button", "role": "button", "name": "Next"},
                "digest": "d",
            },
            "act": {"done": True},
        },
    )
    try:
        with installed(FixedAuthorizer("deny")):
            driver = UserBrowserDriver(None, controller=controller, hub=hub, run_dir=tmp_path)
            result = driver.perform(BrowserAction(op="act", tab_id="7", control="click", ref="e1"))
        assert result.outcome == "denied"
        assert "act" not in ext.ops()  # perception only; the act never left Locus
        with installed(FixedAuthorizer("ask")):
            asked = driver.perform(BrowserAction(op="read", tab_id="7"))
        assert asked.outcome == "ask" and "observe" not in ext.ops()
    finally:
        ext.stop()


def test_driver_relays_allowed_acts_with_the_element_digest(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController, tmp_path: Path
) -> None:
    ext = FakeExtension(
        hub,
        {
            "tab_info": {"tab_id": 7, "url": "https://example.com/a", "shared": True},
            "inspect": {
                "facts": {"tag": "button", "role": "button", "name": "Next"},
                "digest": "d1",
            },
            "act": {"done": True, "url": "https://example.com/b"},
        },
    )
    try:
        driver = UserBrowserDriver(None, controller=controller, hub=hub, run_dir=tmp_path)
        result = driver.perform(BrowserAction(op="act", tab_id="7", control="click", ref="e1"))
        assert result.ok, result.text
        act = next(c for c in ext.seen if c.get("op") == "act")
        assert act["args"]["expect_digest"] == "d1" and act["args"]["ref"] == "e1"
    finally:
        ext.stop()


def test_driver_rejects_bad_tab_ids_and_maps_panic_to_cancelled(
    hub: relay_mod.RelayHub, controller: cu.ComputerUseController, tmp_path: Path
) -> None:
    driver = UserBrowserDriver(None, controller=controller, hub=hub, run_dir=tmp_path)
    bad = driver.perform(BrowserAction(op="read", tab_id="1; drop"))
    assert bad.outcome == "error"
    failure = UserBrowserDriver._relay_failure("t", relay_mod.RelayError("panic"))  # noqa: SLF001
    assert failure.outcome == "cancelled"


def test_redaction_never_lets_a_secret_value_through() -> None:
    elements = _redact_elements(
        [
            {"ref": "e1", "tag": "input", "type": "password", "value": "hunter2"},
            {"ref": "e2", "tag": "input", "name": "Card number", "value": "4111"},
            {"ref": "e3", "tag": "input", "autocomplete": "one-time-code", "value": "123456"},
            {"ref": "e4", "tag": "input", "secret": True, "value": "x"},
            {"ref": "e5", "tag": "input", "name": "Search", "value": "shoes"},
        ]
    )
    values = {e["ref"]: e["value"] for e in elements}
    assert values == {
        "e1": "[redacted]",
        "e2": "[redacted]",
        "e3": "[redacted]",
        "e4": "[redacted]",
        "e5": "shoes",
    }


# --------------------------------------------------------------------------- #
# D-28: one factory picks drivers from the envelope; the toolset uses the port
# --------------------------------------------------------------------------- #
class FakeDriver:
    def __init__(self, session: Any, *, controller: Any, app_home: Any = None) -> None:
        self.session = session
        self.controller = controller
        self.actions: list[BrowserAction] = []
        self.detached = False

    profile = "user"
    port_version = "1.0"

    def perform(self, action: BrowserAction) -> BrowserObservation:
        self.actions.append(action)
        return BrowserObservation(profile="user", outcome="done", text=f"did {action.op}")

    def detach(self) -> None:
        self.detached = True


def test_factory_selects_drivers_by_envelope_tools(controller: cu.ComputerUseController) -> None:
    def agent_factory(*a: Any, **k: Any) -> Any:
        return FakeDriver(*a, **k)

    both = build_browser_drivers(
        ["browser_read", "user_browser_observe"],
        session="s",
        controller=controller,
        agent_factory=agent_factory,
        user_factory=FakeDriver,
    )
    assert set(both) == {"agent", "user"}
    only_user = build_browser_drivers(
        ["user_browser_tabs"], session="s", controller=controller, user_factory=FakeDriver
    )
    assert set(only_user) == {"user"}
    assert build_browser_drivers(["execute_bash"], session="s", controller=controller) == {}


def test_toolset_routes_user_browser_tools_through_the_port(
    tmp_path: Path, controller: cu.ComputerUseController
) -> None:
    toolset = build_run_toolset(
        tools=["user_browser_tabs", "user_browser_act", "user_browser_navigate"],
        workspace=Workspace(run_id="r1", executor=LocalDirectExecutor(tmp_path)),
        session="session",
        user_browser_factory=FakeDriver,
    )
    assert isinstance(toolset, ComputerUseToolset)
    driver = toolset.user_browser
    assert isinstance(driver, FakeDriver) and toolset.browser is None
    assert driver.session == "session" and driver.controller is controller
    names = {schema["function"]["name"] for schema in toolset.schemas()}
    assert {"user_browser_tabs", "user_browser_act"} <= names
    assert "browser_read" not in names
    assert toolset._dispatch(
        "user_browser_act", {"action": "click", "ref": "e1", "tab_id": "3"}
    ) == (  # noqa: SLF001
        "did act"
    )
    assert driver.actions[-1] == BrowserAction(op="act", tab_id="3", control="click", ref="e1")
    assert "action must be" in toolset._dispatch("user_browser_act", {"action": "hover"})  # noqa: SLF001
    assert "invalid" in toolset._dispatch(  # noqa: SLF001
        "user_browser_navigate", {"url": "x" * 5000}
    )
    no_agent = toolset._dispatch("browser_read", {})  # noqa: SLF001
    assert "no agent browser" in no_agent
    release_run_toolset(toolset)
    assert driver.detached


# --------------------------------------------------------------------------- #
# Native-messaging host
# --------------------------------------------------------------------------- #
def _frame(message: dict[str, Any]) -> bytes:
    data = json.dumps(message).encode("utf-8")
    return struct.pack("=I", len(data)) + data


def _frames(raw: bytes) -> list[dict[str, Any]]:
    stream = io.BytesIO(raw)
    out = []
    while (message := nh.read_message(stream)) is not None:
        out.append(message)
    return out


def test_framing_round_trip_and_bounds() -> None:
    buffer = io.BytesIO()
    nh.write_message(buffer, {"type": "status", "connected": True})
    assert _frames(buffer.getvalue()) == [{"type": "status", "connected": True}]
    with pytest.raises(nh.NativeMessagingError):
        nh.write_message(io.BytesIO(), {"blob": "x" * (nh.MAX_TO_EXTENSION + 1)})
    with pytest.raises(nh.NativeMessagingError):
        nh.read_message(io.BytesIO(struct.pack("=I", nh.MAX_FROM_EXTENSION + 1)))
    with pytest.raises(nh.NativeMessagingError):
        nh.read_message(io.BytesIO(struct.pack("=I", 10) + b"[1]"))
    with pytest.raises(nh.NativeMessagingError):
        nh.read_message(io.BytesIO(_frame([1, 2])))  # type: ignore[arg-type]


def test_invocation_detection_and_origins() -> None:
    chrome = ["host.exe", pairing.CHROMIUM_ORIGIN, "--parent-window=0"]
    firefox = ["host", "/path/io.lattix.locus_browser.json", pairing.FIREFOX_EXTENSION_ID]
    assert nh.caller_origin(chrome) == pairing.CHROMIUM_ORIGIN
    assert nh.caller_origin(firefox) == pairing.FIREFOX_EXTENSION_ID
    assert nh.is_native_messaging_invocation(chrome)
    assert nh.is_native_messaging_invocation(firefox)
    assert not nh.is_native_messaging_invocation(["locus-backend"])
    assert not nh.is_native_messaging_invocation(["x", "chrome-extension://evil/"])
    from locus_tooling.desktop_main import _looks_like_native_messaging

    assert _looks_like_native_messaging(chrome) and _looks_like_native_messaging(firefox)
    assert not _looks_like_native_messaging(["locus-backend", "--self-check"])


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        ("http://127.0.0.1:9000", True),
        ("http://localhost:8000/", True),
        ("https://127.0.0.1:8000", False),
        ("http://10.0.0.5:8000", False),
        ("http://locus.example.com", False),
    ],
)
def test_backend_url_is_loopback_http_only(value: str, ok: bool) -> None:
    if ok:
        assert nh.backend_url({nh.BACKEND_URL_ENV: value}).startswith("http://")
    else:
        with pytest.raises(nh.NativeMessagingError):
            nh.backend_url({nh.BACKEND_URL_ENV: value})


class FakeRelay:
    def __init__(self, *, refuse: bool = False) -> None:
        self.refuse = refuse
        self.hellos: list[tuple[str, str, str, str]] = []
        self.results: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.polls = 0
        self.closed = False

    def hello(self, origin: str, key: str, browser: str, version: str) -> None:
        if self.refuse:
            raise nh.RelayAuthRefused("no")
        self.hellos.append((origin, key, browser, version))

    def next(self, wait_s: float) -> list[dict[str, Any]]:
        self.polls += 1
        time.sleep(0.05)
        return (
            [{"type": "command", "id": "c1", "op": "list_tabs", "args": {}}]
            if self.polls == 1
            else []
        )

    def result(self, message: dict[str, Any]) -> None:
        self.results.append(message)

    def event(self, message: dict[str, Any]) -> None:
        self.events.append(message)

    def bye(self) -> None:
        self.closed = True


def _host(
    stdin: bytes, relay: FakeRelay, *, origin: str = pairing.CHROMIUM_ORIGIN, key: str | None = KEY
) -> tuple[int, list[dict[str, Any]]]:
    out = io.BytesIO()
    host = nh.NativeHost(
        stdin=io.BytesIO(stdin),
        stdout=out,
        origin=origin,
        relay_factory=lambda: relay,
        key_loader=lambda: key,
        poll_wait_s=0.01,
    )
    code = host.run()
    return code, _frames(out.getvalue())


def test_host_relays_commands_results_and_panic() -> None:
    relay = FakeRelay()
    stdin = (
        _frame({"type": "hello", "browser": "edge", "version": "0.1.0"})
        + _frame({"type": "result", "id": "c1", "ok": True, "result": {"tabs": []}})
        + _frame({"type": "event", "event": "panic"})
        + _frame({"type": "command", "op": "evil"})  # the extension cannot send commands
    )
    code, sent = _host(stdin, relay)
    assert code == 0 and relay.closed
    assert relay.hellos == [(pairing.CHROMIUM_ORIGIN, KEY, "edge", "0.1.0")]
    assert sent[0] == {"type": "status", "connected": True, "error": ""}
    assert {"type": "command", "id": "c1", "op": "list_tabs", "args": {}} in sent
    assert relay.results == [{"type": "result", "id": "c1", "ok": True, "result": {"tabs": []}}]
    assert relay.events == [{"event": "panic"}]
    assert KEY not in json.dumps(sent)  # the key never reaches the extension


@pytest.mark.parametrize(
    ("origin", "key", "refuse", "code", "error"),
    [
        (
            "chrome-extension://abcdefghijklmnopabcdefghijklmnop/",
            KEY,
            False,
            2,
            "origin_not_pinned",
        ),
        (pairing.CHROMIUM_ORIGIN, None, False, 3, "not_paired"),
        (pairing.CHROMIUM_ORIGIN, KEY, True, 4, "pairing_refused"),
    ],
)
def test_host_refuses_unpinned_unpaired_and_refused(
    origin: str, key: str | None, refuse: bool, code: int, error: str
) -> None:
    relay = FakeRelay(refuse=refuse)
    exit_code, sent = _host(_frame({"type": "hello"}), relay, origin=origin, key=key)
    assert exit_code == code
    assert sent[-1] == {"type": "status", "connected": False, "error": error}
    assert relay.polls == 0


# --------------------------------------------------------------------------- #
# Host registration (temp dirs and a fake registry only)
# --------------------------------------------------------------------------- #
class FakeRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set_default_value(self, key_path: str, value: str) -> None:
        self.values[key_path] = value

    def delete_key(self, key_path: str) -> None:
        self.values.pop(key_path, None)


def test_register_on_windows_writes_hkcu_keys_via_the_given_writer(tmp_path: Path) -> None:
    registry = FakeRegistry()
    host = tmp_path / "bin" / "locus-backend.exe"
    done = nm.register_host(host, platform="windows", app_home=tmp_path / "home", registry=registry)
    assert {r.browser for r in done} == {"chrome", "edge", "firefox"}
    chrome_key = "Software\\Google\\Chrome\\NativeMessagingHosts\\io.lattix.locus_browser"
    assert registry.values[chrome_key] == str(done[0].manifest_path)
    manifests = {r.browser: json.loads(r.manifest_path.read_text()) for r in done}
    assert manifests["chrome"]["allowed_origins"] == [pairing.CHROMIUM_ORIGIN]
    assert manifests["edge"]["allowed_origins"] == [pairing.CHROMIUM_ORIGIN]
    assert manifests["firefox"]["allowed_extensions"] == [pairing.FIREFOX_EXTENSION_ID]
    assert all(m["path"] == str(host) and m["type"] == "stdio" for m in manifests.values())
    assert all(str(r.manifest_path).startswith(str(tmp_path)) for r in done)
    nm.unregister_host(platform="windows", app_home=tmp_path / "home", registry=registry)
    assert registry.values == {} and not done[0].manifest_path.exists()


def test_register_requires_an_explicit_registry_on_windows_and_absolute_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="registry"):
        nm.register_host(tmp_path / "h.exe", platform="windows", app_home=tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        nm.register_host(Path("h"), platform="linux", home=tmp_path, app_home=tmp_path)


@pytest.mark.parametrize(
    ("platform", "chrome_dir", "firefox_dir"),
    [
        (
            "darwin",
            "Library/Application Support/Google/Chrome/NativeMessagingHosts",
            "Library/Application Support/Mozilla/NativeMessagingHosts",
        ),
        ("linux", ".config/google-chrome/NativeMessagingHosts", ".mozilla/native-messaging-hosts"),
    ],
)
def test_register_on_posix_uses_per_user_browser_dirs(
    tmp_path: Path, platform: str, chrome_dir: str, firefox_dir: str
) -> None:
    done = nm.register_host(
        tmp_path / "locus-backend",
        platform=platform,  # type: ignore[arg-type]
        home=tmp_path,
        app_home=tmp_path / "app",
        browsers=["chrome", "firefox"],
    )
    paths = {r.browser: r.manifest_path for r in done}
    assert paths["chrome"] == tmp_path / chrome_dir / "io.lattix.locus_browser.json"
    assert paths["firefox"] == tmp_path / firefox_dir / "io.lattix.locus_browser.json"
    assert all(r.registry_key == "" for r in done)
