import asyncio

from locus_runtime.events import AgentEvent, get_event_bus
from locus_runtime.security import verify_event_signature


def test_event_bus_signs_events() -> None:
    bus = get_event_bus()
    event = asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))
    assert event.signature is not None
    assert event.signer == "tester"
    assert verify_event_signature(event) is True


def test_event_signature_breaks_when_signer_changes() -> None:
    bus = get_event_bus()
    event = asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))
    event.signer = "other-signer"
    assert verify_event_signature(event) is False
