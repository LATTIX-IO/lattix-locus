from __future__ import annotations

from dataclasses import dataclass

from locus_tooling.common import discover_agent_records


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    name: str
    path: str
    source: str


class AgentRegistry:
    def __init__(self, records: list[AgentRecord]) -> None:
        self._records = records

    def list_agents(self) -> list[AgentRecord]:
        return list(self._records)


_DEFAULT_AGENT_IDS = ("research", "code", "review", "coordinator")


def build_default_registry() -> AgentRegistry:
    discovered = [
        AgentRecord(
            agent_id=record["id"],
            name=record["name"],
            path=record["path"],
            source=record["source"],
        )
        for record in discover_agent_records()
    ]
    known_ids = {record.agent_id for record in discovered}
    for agent_id in _DEFAULT_AGENT_IDS:
        if agent_id not in known_ids:
            discovered.append(
                AgentRecord(agent_id=agent_id, name=agent_id.title(), path="", source="fallback")
            )
    return AgentRegistry(discovered)


async def discover_agents() -> list[AgentRecord]:
    return build_default_registry().list_agents()
