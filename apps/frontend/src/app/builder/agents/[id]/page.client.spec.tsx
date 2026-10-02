import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStudioClient } from "@/app/builder/agents/[id]/page.client";

const saveGraph = {
  nodes: [{ id: "tool-node", title: "Tool", type: "tool-call", x: 10, y: 20 }],
  links: [],
};

const { publishAgentDefinitionMock, saveAgentDefinitionMock } = vi.hoisted(() => ({
  publishAgentDefinitionMock: vi.fn(),
  saveAgentDefinitionMock: vi.fn(),
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    return function MockStudioFullCanvas(props: {
      onSave?: (graph: typeof saveGraph) => Promise<void>;
      onPublish?: () => Promise<void>;
    }) {
      return (
        <div data-testid="studio-full-canvas">
          <button type="button" onClick={() => void props.onSave?.(saveGraph)}>
            Save Agent
          </button>
          <button type="button" onClick={() => void props.onPublish?.()}>
            Publish Agent
          </button>
        </div>
      );
    };
  },
}));

vi.mock("@/lib/api", () => ({
  publishAgentDefinition: publishAgentDefinitionMock,
  saveAgentDefinition: saveAgentDefinitionMock,
}));

describe("AgentStudioClient", () => {
  beforeEach(() => {
    publishAgentDefinitionMock.mockReset();
    saveAgentDefinitionMock.mockReset();
    publishAgentDefinitionMock.mockResolvedValue(undefined);
    saveAgentDefinitionMock.mockResolvedValue(undefined);
  });

  it("persists agent saves and publish actions from the full-canvas studio", async () => {
    render(<AgentStudioClient agentId="agent-123" agentName="Responder" />);

    fireEvent.click(screen.getByRole("button", { name: /save agent/i }));

    await waitFor(() =>
      expect(saveAgentDefinitionMock).toHaveBeenCalledWith({
        id: "agent-123",
        name: "Responder",
        config_json: {
          schema_version: "frontier-agent-definition/1.0",
          source_agent_id: "agent-123",
          meta: {
            name: "Responder",
          },
          graph_json: saveGraph,
        },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /publish agent/i }));
    await waitFor(() => expect(publishAgentDefinitionMock).toHaveBeenCalledWith("agent-123"));
  });
});