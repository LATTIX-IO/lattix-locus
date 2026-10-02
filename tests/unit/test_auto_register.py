from __future__ import annotations

import json
from pathlib import Path

from apps.workers.runtime.layer2.auto_register import auto_register_by_tags
from apps.workers.runtime.layer2.contracts import Envelope
from apps.workers.runtime.layer2.event_bus import EventBus
from apps.workers.runtime.layer2.registry import AgentsRegistry


def _write_registry(path: Path, agents: list[dict[str, object]]) -> AgentsRegistry:
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return AgentsRegistry(path)


def test_auto_register_by_tags_skips_placeholder_subscribers_in_strict_profile(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "hosted")
    monkeypatch.setenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", "true")

    registry = _write_registry(
        tmp_path / "registry.json",
        [
            {
                "id": "planner",
                "name": "Planner",
                "tags": ["gtm"],
            }
        ],
    )
    topic_map = tmp_path / "topic-map.json"
    topic_map.write_text(json.dumps({"gtm": ["gtm.content"]}), encoding="utf-8")

    bus = EventBus()
    count = auto_register_by_tags(bus=bus, registry=registry, topic_map_path=topic_map)

    assert count == 0
    env = Envelope(topic="gtm.content", sender="backend")
    bus.publish("gtm.content", env)
    assert env.payload.get("participants") is None
    assert env.payload.get("logs") is None


def test_auto_register_by_tags_preserves_lightweight_placeholder_registration(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_RUNTIME_PROFILE", "local-lightweight")
    monkeypatch.delenv("LOCUS_REQUIRE_A2A_RUNTIME_HEADERS", raising=False)

    registry = _write_registry(
        tmp_path / "registry.json",
        [
            {
                "id": "planner",
                "name": "Planner",
                "tags": ["gtm"],
            }
        ],
    )
    topic_map = tmp_path / "topic-map.json"
    topic_map.write_text(json.dumps({"gtm": ["gtm.content"]}), encoding="utf-8")

    bus = EventBus()
    count = auto_register_by_tags(bus=bus, registry=registry, topic_map_path=topic_map)

    assert count == 1
    env = Envelope(topic="gtm.content", sender="backend")
    bus.publish("gtm.content", env)
    assert env.payload["participants"] == [{"agent": "planner", "name": "Planner"}]
    assert any(
        item.get("auto") == "planner observed gtm.content"
        for item in env.payload.get("logs", [])
        if isinstance(item, dict)
    )
