from __future__ import annotations

from typing import Any, Callable, Literal
from uuid import NAMESPACE_URL, uuid5


class GeneratedArtifactService:
    def __init__(
        self,
        *,
        store: Any,
        artifact_factory: Callable[..., Any],
        now_iso: Callable[[], str],
        python_literal: Callable[[Any], str],
        safe_python_identifier: Callable[[str], str],
        artifact_slug: Callable[[str, str], str],
        hydrate_graph_for_codegen: Callable[
            [dict[str, Any]], tuple[list[Any], list[Any], list[str], dict[str, list[str]]]
        ],
        node_blueprints_for_codegen: Callable[
            [list[Any], list[str], dict[str, list[str]]], dict[str, dict[str, Any]]
        ],
        resolve_effective_security_policy: Callable[..., dict[str, Any]],
        workflow_runtime_policy_snapshot: Callable[[Any], dict[str, Any]],
        agent_runtime_policy_snapshot: Callable[[Any], dict[str, Any]],
    ) -> None:
        self._store = store
        self._artifact_factory = artifact_factory
        self._now_iso = now_iso
        self._python_literal = python_literal
        self._safe_python_identifier = safe_python_identifier
        self._artifact_slug = artifact_slug
        self._hydrate_graph_for_codegen = hydrate_graph_for_codegen
        self._node_blueprints_for_codegen = node_blueprints_for_codegen
        self._resolve_effective_security_policy = resolve_effective_security_policy
        self._workflow_runtime_policy_snapshot = workflow_runtime_policy_snapshot
        self._agent_runtime_policy_snapshot = agent_runtime_policy_snapshot

    def build_langgraph_artifact(
        self,
        *,
        entity_type: Literal["agent", "workflow"],
        entity_id: str,
        entity_name: str,
        version: int,
        graph_json: dict[str, Any],
        effective_security_policy: dict[str, Any],
        runtime_policy: dict[str, Any],
    ) -> Any:
        nodes, links, execution_order, upstream_node_ids = self._hydrate_graph_for_codegen(
            graph_json
        )
        blueprints = self._node_blueprints_for_codegen(nodes, execution_order, upstream_node_ids)
        slug = self._artifact_slug(entity_name, entity_id)
        kind_name = "agent" if entity_type == "agent" else "workflow"
        lines = [
            "from __future__ import annotations",
            "",
            "from typing import Any, TypedDict",
            "",
            "from langgraph.graph import END, START, StateGraph",
            "",
            f"{entity_type.upper()}_ID = {entity_id!r}",
            f"{entity_type.upper()}_NAME = {entity_name!r}",
            f"VERSION = {version}",
            f"GRAPH_SPEC = {self._python_literal(graph_json)}",
            f"NODE_BLUEPRINTS = {self._python_literal(blueprints)}",
            f"EXECUTION_ORDER = {self._python_literal(execution_order)}",
            f"UPSTREAM_NODE_IDS = {self._python_literal(upstream_node_ids)}",
            f"EFFECTIVE_SECURITY_POLICY = {self._python_literal(effective_security_policy)}",
            f"RUNTIME_POLICY = {self._python_literal(runtime_policy)}",
            "",
            "class FrontierState(TypedDict, total=False):",
            "    input: dict[str, Any]",
            "    node_results: dict[str, dict[str, Any]]",
            "    last_output: Any",
            "",
            "def _record_node_result(state: FrontierState, node_id: str, payload: dict[str, Any]) -> FrontierState:",
            '    node_results = dict(state.get("node_results", {}))',
            "    node_results[node_id] = payload",
            "    next_state = dict(state)",
            '    next_state["node_results"] = node_results',
            '    next_state["last_output"] = payload.get("response") or payload.get("published") or payload',
            "    return next_state",
            "",
            "def _execute_generated_node(node_id: str, state: FrontierState) -> FrontierState:",
            "    blueprint = NODE_BLUEPRINTS[node_id]",
            '    node_type = blueprint["type"]',
            '    config = blueprint["config"]',
            "    upstream_ids = UPSTREAM_NODE_IDS.get(node_id, [])",
            '    upstream_results = {upstream_id: state.get("node_results", {}).get(upstream_id) for upstream_id in upstream_ids}',
            '    if node_type == "frontier/trigger":',
            '        payload = {"trigger": config, "input": state.get("input", {})}',
            '    elif node_type == "frontier/goal":',
            '        payload = {"goal": config, "confidence": 0.8}',
            '    elif node_type == "frontier/evidence":',
            '        payload = {"evidence": [state.get("input", {}).get("message", "")], "confidence": 0.6}',
            '    elif node_type == "frontier/assembly":',
            '        payload = {"commitment": {"decision": "Generated cognitive scaffold commitment", "confidence": config.get("confidence_threshold", 0.6)}}',
            '    elif node_type == "frontier/commitment":',
            '        payload = {"published": {"status": "committed", "threshold": config.get("confidence_threshold", 0.6)}}',
            '    elif node_type == "frontier/prompt":',
            '        payload = {"system_prompt": config.get("system_prompt_text", ""), "profile": config}',
            '    elif node_type == "frontier/agent":',
            "        payload = {",
            '            "agent_id": config.get("agent_id") or node_id,',
            '            "response": f"Generated LangGraph stub for {blueprint["title"]}",',
            '            "model": config.get("model") or RUNTIME_POLICY.get("default_runtime_engine"),',
            '            "upstream": upstream_results,',
            "        }",
            '    elif node_type == "frontier/retrieval":',
            '        payload = {"documents": [], "grounding_context": "Attach retriever implementation here.", "config": config}',
            '    elif node_type == "frontier/tool-call":',
            '        payload = {"tool_request": config, "status": "stubbed", "upstream": upstream_results}',
            '    elif node_type == "frontier/memory":',
            '        payload = {"memory_state": {"scope": config.get("scope", "session")}, "config": config}',
            '    elif node_type == "frontier/guardrail":',
            '        payload = {"decision": "allow", "config": config, "effective_security_policy": EFFECTIVE_SECURITY_POLICY}',
            '    elif node_type == "frontier/human-review":',
            '        payload = {"approval_required": True, "reviewer_group": config.get("reviewer_group", "reviewers")}',
            '    elif node_type == "frontier/manifold":',
            '        payload = {"logic_mode": config.get("logic_mode", "OR"), "upstream": upstream_results}',
            '    elif node_type == "frontier/output":',
            '        payload = {"published": {"destination": config.get("destination", "artifact_store"), "payload": upstream_results}}',
            "    else:",
            '        payload = {"note": f"Unhandled node type {node_type}", "upstream": upstream_results}',
            "    return _record_node_result(state, node_id, payload)",
            "",
        ]
        for node_id in execution_order:
            function_name = f"node_{self._safe_python_identifier(node_id)}"
            lines.extend(
                [
                    f"def {function_name}(state: FrontierState) -> FrontierState:",
                    f"    return _execute_generated_node({node_id!r}, state)",
                    "",
                ]
            )
        lines.extend(
            [
                "def build_graph() -> Any:",
                "    builder = StateGraph(FrontierState)",
            ]
        )
        for node_id in execution_order:
            function_name = f"node_{self._safe_python_identifier(node_id)}"
            lines.append(f"    builder.add_node({node_id!r}, {function_name})")
        lines.extend(
            [
                "    if EXECUTION_ORDER:",
                "        builder.add_edge(START, EXECUTION_ORDER[0])",
                "        for current_node, next_node in zip(EXECUTION_ORDER, EXECUTION_ORDER[1:]):",
                "            builder.add_edge(current_node, next_node)",
                "        builder.add_edge(EXECUTION_ORDER[-1], END)",
                "    return builder.compile()",
                "",
                "def run_graph(input_payload: dict[str, Any] | None = None) -> dict[str, Any]:",
                "    graph = build_graph()",
                '    return graph.invoke({"input": input_payload or {}, "node_results": {}})',
                "",
                'if __name__ == "__main__":',
                '    result = run_graph({"message": "Generated scaffold smoke test"})',
                "    print(result)",
            ]
        )

        return self._artifact_factory(
            id=str(
                uuid5(NAMESPACE_URL, f"generated:{entity_type}:{entity_id}:langgraph:v{version}")
            ),
            name=f"{entity_name} · LangGraph scaffold",
            version=version,
            framework="langgraph",
            path=f"generated/{entity_type}s/{slug}/v{version}/langgraph_{kind_name}.py",
            summary=f"LangGraph scaffold generated from {len(nodes)} nodes and {len(links)} links.",
            content="\n".join(lines) + "\n",
            generated_at=self._now_iso(),
            entity_type=entity_type,
            entity_id=entity_id,
        )

    def build_agent_framework_artifact(
        self,
        *,
        entity_type: Literal["agent", "workflow"],
        entity_id: str,
        entity_name: str,
        version: int,
        graph_json: dict[str, Any],
        effective_security_policy: dict[str, Any],
        runtime_policy: dict[str, Any],
    ) -> Any:
        nodes, links, execution_order, upstream_node_ids = self._hydrate_graph_for_codegen(
            graph_json
        )
        blueprints = self._node_blueprints_for_codegen(nodes, execution_order, upstream_node_ids)
        slug = self._artifact_slug(entity_name, entity_id)
        kind_name = "agent" if entity_type == "agent" else "workflow"
        lines = [
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from agent_framework import Message",
            "from agent_framework.azure import AzureAIClient",
            "from azure.identity.aio import DefaultAzureCredential",
            "",
            f"{entity_type.upper()}_ID = {entity_id!r}",
            f"{entity_type.upper()}_NAME = {entity_name!r}",
            f"VERSION = {version}",
            f"GRAPH_SPEC = {self._python_literal(graph_json)}",
            f"NODE_BLUEPRINTS = {self._python_literal(blueprints)}",
            f"EXECUTION_ORDER = {self._python_literal(execution_order)}",
            f"UPSTREAM_NODE_IDS = {self._python_literal(upstream_node_ids)}",
            f"EFFECTIVE_SECURITY_POLICY = {self._python_literal(effective_security_policy)}",
            f"RUNTIME_POLICY = {self._python_literal(runtime_policy)}",
            "",
            "def _node_instructions(blueprint: dict[str, Any]) -> str:",
            '    config = blueprint.get("config", {})',
            '    title = blueprint.get("title") or blueprint.get("id") or "Frontier node"',
            "    return (",
            '        f"You are the generated executor for {title}. "',
            '        "Respect EFFECTIVE_SECURITY_POLICY, keep outputs deterministic, and use GRAPH_SPEC as the source of truth. "',
            '        f"Node config: {config}"',
            "    )",
            "",
            "async def instantiate_frontier_agents() -> dict[str, Any]:",
            "    credential = DefaultAzureCredential()",
            "    instantiated: dict[str, Any] = {}",
            "    for node_id in EXECUTION_ORDER:",
            "        blueprint = NODE_BLUEPRINTS[node_id]",
            '        if blueprint.get("type") != "frontier/agent":',
            "            continue",
            "        client = AzureAIClient(credential=credential)",
            "        instantiated[node_id] = client.as_agent(",
            '            name=f"{blueprint.get("title", node_id)}Agent",',
            "            instructions=_node_instructions(blueprint),",
            "        )",
            "    return instantiated",
            "",
            "async def build_frontier_blueprint() -> dict[str, Any]:",
            "    agents = await instantiate_frontier_agents()",
            "    return {",
            f'        "entity_id": {entity_id!r},',
            f'        "entity_name": {entity_name!r},',
            '        "graph_spec": GRAPH_SPEC,',
            '        "execution_order": EXECUTION_ORDER,',
            '        "security_policy": EFFECTIVE_SECURITY_POLICY,',
            '        "runtime_policy": RUNTIME_POLICY,',
            '        "instantiated_agents": list(agents.keys()),',
            "    }",
            "",
            "async def run_frontier_task(task: str) -> dict[str, Any]:",
            "    blueprint = await build_frontier_blueprint()",
            '    user_message = Message("user", [task])',
            "    return {",
            '        "blueprint": blueprint,',
            '        "initial_message": user_message,',
            '        "note": "Wire WorkflowBuilder / custom Executors here using EXECUTION_ORDER and UPSTREAM_NODE_IDS.",',
            "    }",
            "",
            'if __name__ == "__main__":',
            "    import asyncio",
            "",
            '    print(asyncio.run(run_frontier_task("Generated scaffold smoke test")))',
        ]
        return self._artifact_factory(
            id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"generated:{entity_type}:{entity_id}:microsoft-agent-framework:v{version}",
                )
            ),
            name=f"{entity_name} · Microsoft Agent Framework scaffold",
            version=version,
            framework="microsoft-agent-framework",
            path=f"generated/{entity_type}s/{slug}/v{version}/agent_framework_{kind_name}.py",
            summary=f"Microsoft Agent Framework scaffold generated from {len(nodes)} nodes and {len(links)} links.",
            content="\n".join(lines) + "\n",
            generated_at=self._now_iso(),
            entity_type=entity_type,
            entity_id=entity_id,
        )

    def build_generated_artifacts_for_workflow(self, item: Any, *, version: int) -> list[Any]:
        effective_policy = self._resolve_effective_security_policy(
            platform=self._store.platform_settings,
            workflow_config=item.security_config,
        )
        runtime_policy = self._workflow_runtime_policy_snapshot(self._store.platform_settings)
        graph_json = item.graph_json if isinstance(item.graph_json, dict) else {}
        return [
            self.build_langgraph_artifact(
                entity_type="workflow",
                entity_id=item.id,
                entity_name=item.name,
                version=version,
                graph_json=graph_json,
                effective_security_policy=effective_policy,
                runtime_policy=runtime_policy,
            ),
            self.build_agent_framework_artifact(
                entity_type="workflow",
                entity_id=item.id,
                entity_name=item.name,
                version=version,
                graph_json=graph_json,
                effective_security_policy=effective_policy,
                runtime_policy=runtime_policy,
            ),
        ]

    def build_generated_artifacts_for_agent(self, item: Any, *, version: int) -> list[Any]:
        config_json = item.config_json if isinstance(item.config_json, dict) else {}
        workflow_id = str(config_json.get("workflow_definition_id") or "").strip()
        workflow = self._store.workflow_definitions.get(workflow_id) if workflow_id else None
        effective_policy = self._resolve_effective_security_policy(
            platform=self._store.platform_settings,
            workflow_config=workflow.security_config if workflow else None,
            agent_config=config_json.get("security")
            if isinstance(config_json.get("security"), dict)
            else None,
        )
        runtime_policy = self._agent_runtime_policy_snapshot(item)
        graph_json = (
            config_json.get("graph_json") if isinstance(config_json.get("graph_json"), dict) else {}
        )
        return [
            self.build_langgraph_artifact(
                entity_type="agent",
                entity_id=item.id,
                entity_name=item.name,
                version=version,
                graph_json=graph_json,
                effective_security_policy=effective_policy,
                runtime_policy=runtime_policy,
            ),
            self.build_agent_framework_artifact(
                entity_type="agent",
                entity_id=item.id,
                entity_name=item.name,
                version=version,
                graph_json=graph_json,
                effective_security_policy=effective_policy,
                runtime_policy=runtime_policy,
            ),
        ]

    def iter_generated_artifacts(self) -> list[Any]:
        artifacts: list[Any] = []
        for workflow in self._store.workflow_definitions.values():
            artifacts.extend(workflow.generated_artifacts)
        for agent in self._store.agent_definitions.values():
            artifacts.extend(agent.generated_artifacts)
        return artifacts

    def find_generated_artifact(self, artifact_id: str) -> Any | None:
        target = str(artifact_id or "").strip()
        if not target:
            return None
        for artifact in self.iter_generated_artifacts():
            if artifact.id == target:
                return artifact
        return None
