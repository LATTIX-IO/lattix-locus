import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentStudioPage from "@/app/builder/agents/[id]/page";

const {
  getAgentDefinitionMock,
  getAgentDefinitionsMock,
} = vi.hoisted(() => ({
  getAgentDefinitionMock: vi.fn(),
  getAgentDefinitionsMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  getAgentDefinition: getAgentDefinitionMock,
  getAgentDefinitions: getAgentDefinitionsMock,
}));

vi.mock("@/app/builder/agents/[id]/page.client", () => ({
  AgentStudioClient: ({ agentId, returnHref }: { agentId: string; returnHref?: string }) => (
    <div>
      <div data-testid="agent-id">{agentId}</div>
      <div data-testid="return-href">{returnHref ?? ""}</div>
    </div>
  ),
}));

describe("AgentStudioPage", () => {
  beforeEach(() => {
    getAgentDefinitionMock.mockReset();
    getAgentDefinitionsMock.mockReset();

    getAgentDefinitionsMock.mockResolvedValue([{ id: "agent-123", name: "Responder" }]);
    getAgentDefinitionMock.mockResolvedValue({
      id: "agent-123",
      name: "Responder",
      config_json: { graph_json: { nodes: [], links: [] } },
    });
  });

  it("passes through a safe internal return path", async () => {
    render(
      await AgentStudioPage({
        params: Promise.resolve({ id: "agent-123" }),
        searchParams: Promise.resolve({ returnTo: "/builder/agents?view=archived" }),
      }),
    );

    expect(screen.getByTestId("agent-id")).toHaveTextContent("agent-123");
    expect(screen.getByTestId("return-href")).toHaveTextContent("/builder/agents?view=archived");
  });

  it("uses the first returnTo entry when search params provide an array", async () => {
    render(
      await AgentStudioPage({
        params: Promise.resolve({ id: "agent-123" }),
        searchParams: Promise.resolve({ returnTo: ["/builder/agents", "/builder/agents?view=archived"] }),
      }),
    );

    expect(screen.getByTestId("return-href")).toHaveTextContent("/builder/agents");
  });

  it("drops unsafe external return paths", async () => {
    render(
      await AgentStudioPage({
        params: Promise.resolve({ id: "agent-123" }),
        searchParams: Promise.resolve({ returnTo: "https://example.com/phish" }),
      }),
    );

    expect(screen.getByTestId("return-href")).toHaveTextContent("");
  });

  it("drops protocol-relative return paths", async () => {
    render(
      await AgentStudioPage({
        params: Promise.resolve({ id: "agent-123" }),
        searchParams: Promise.resolve({ returnTo: "//example.com/phish" }),
      }),
    );

    expect(screen.getByTestId("return-href")).toHaveTextContent("");
  });
});