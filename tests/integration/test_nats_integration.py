import asyncio

from locus_runtime.events import AgentEvent, get_event_bus


def test_event_bus_publishes() -> None:
    bus = get_event_bus()
    event = asyncio.run(bus.publish(AgentEvent(event_type="demo", source="tester")))
    assert event.event_hash
