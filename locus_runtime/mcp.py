from __future__ import annotations

from locus_runtime.sandbox import (
    ExecutionPlanResult,
    ExecutionSpec,
    SandboxManager,
    SandboxPolicy,
    ToolJailService,
    detect_host_platform,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, str] = {}

    def register(self, tool_id: str, description: str) -> None:
        self._tools[tool_id] = description

    def contains(self, tool_id: str) -> bool:
        return tool_id in self._tools


class MCPGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        jail: ToolJailService | None = None,
        manager: SandboxManager | None = None,
    ) -> None:
        self._registry = registry
        self._manager = manager or SandboxManager()
        self._jail = jail or ToolJailService(manager=self._manager)

    @property
    def active_strategy(self) -> str:
        return self._manager.active_strategy.value

    async def plan_tool_execution(self, agent_id: str, spec: ExecutionSpec) -> ExecutionPlanResult:
        if not self._registry.contains(spec.tool_id):
            raise KeyError(f"Unknown tool: {spec.tool_id}")
        policy = SandboxPolicy(
            platform=detect_host_platform(),
            allow_network=False,
            allowed_executables=[spec.command[0]] if spec.command else [spec.tool_id],
        )
        return await self._jail.plan(spec, policy=policy)
