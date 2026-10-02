from locus_runtime.agents import build_default_registry


def test_agent_registry_lists_builtin_agents() -> None:
    registry = build_default_registry()
    agent_ids = {record.agent_id for record in registry.list_agents()}
    assert {"research", "code", "review", "coordinator"}.issubset(agent_ids)
