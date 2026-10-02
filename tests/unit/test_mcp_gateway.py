import asyncio

from locus_runtime.mcp import MCPGateway, ToolRegistry
from locus_runtime.sandbox import ExecutionSpec


def test_mcp_gateway_plans_tool_execution() -> None:
    registry = ToolRegistry()
    registry.register("python", "Python interpreter")
    gateway = MCPGateway(registry=registry)
    result = asyncio.run(
        gateway.plan_tool_execution(
            agent_id="orchestrator",
            spec=ExecutionSpec(tool_id="python", command=["python", "-c", "print('hi')"]),
        )
    )
    valid_backends = {
        "kernel-bwrap",
        "kernel-seatbelt",
        "hardened-docker",
        "k8s-gvisor",
        "k8s-kata",
        "restricted-process",
    }
    assert result.plan.backend in valid_backends
