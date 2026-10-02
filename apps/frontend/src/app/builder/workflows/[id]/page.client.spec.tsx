import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkflowStudioClient } from "@/app/builder/workflows/[id]/page.client";

const routerPushSpy = vi.fn();
const studioPropsSpy = vi.fn();
const saveGraph = {
  nodes: [{ id: "agent-node", title: "Assigned Agent", type: "agent", x: 25, y: 40, config: { agent_id: "agent-2" } }],
  links: [],
};

const { getAgentDefinitionsMock, publishWorkflowDefinitionMock, saveWorkflowDefinitionMock } = vi.hoisted(() => ({
  getAgentDefinitionsMock: vi.fn(),
  publishWorkflowDefinitionMock: vi.fn(),
  saveWorkflowDefinitionMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPushSpy,
  }),
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    return function MockStudioFullCanvas(props: {
      externalWidgetOptionOverrides?: { agent?: { agent_id?: string[] } };
      rightSidebarSlot?: ReactNode;
      onNodeSelected?: (node: { id: string; title: string; type: string; config?: Record<string, unknown> }) => void;
      onSave?: (graph: typeof saveGraph) => Promise<void>;
      onPublish?: () => Promise<void>;
    }) {
      studioPropsSpy(props);
      return (
        <div data-testid="studio-full-canvas">
          <div data-testid="agent-options">{props.externalWidgetOptionOverrides?.agent?.agent_id?.join(",") ?? ""}</div>
          {props.rightSidebarSlot}
          <button
            type="button"
            onClick={() =>
              props.onNodeSelected?.({
                id: "agent-node",
                title: "Assigned Agent",
                type: "agent",
                config: { agent_id: "agent-2" },
              })
            }
          >
            Select Agent Node
          </button>
          <button type="button" onClick={() => void props.onSave?.(saveGraph)}>
            Save Workflow
          </button>
          <button type="button" onClick={() => void props.onPublish?.()}>
            Publish Workflow
          </button>
        </div>
      );
    };
  },
}));

vi.mock("@/components/security-scope-editor", () => ({
  SecurityScopeEditor: ({ entityId, entityName }: { entityId: string; entityName: string }) => (
    <div>{`Security editor:${entityId}:${entityName}`}</div>
  ),
}));

vi.mock("@/lib/api", () => ({
  getAgentDefinitions: getAgentDefinitionsMock,
  publishWorkflowDefinition: publishWorkflowDefinitionMock,
  saveWorkflowDefinition: saveWorkflowDefinitionMock,
}));

describe("WorkflowStudioClient", () => {
  beforeEach(() => {
    routerPushSpy.mockReset();
    studioPropsSpy.mockClear();
    getAgentDefinitionsMock.mockReset();
    publishWorkflowDefinitionMock.mockReset();
    saveWorkflowDefinitionMock.mockReset();

    getAgentDefinitionsMock.mockResolvedValue([
      { id: "agent-1", name: "Planner", version: 2, status: "published", type: "graph" },
      { id: "agent-2", name: "Research Agent", version: 4, status: "published", type: "graph" },
    ]);
    saveWorkflowDefinitionMock.mockResolvedValue(undefined);
    publishWorkflowDefinitionMock.mockResolvedValue(undefined);
  });

  it("loads agent definitions, exposes them to the canvas, and routes into the agent builder", async () => {
    render(
      <WorkflowStudioClient
        workflowId="wf-123"
        workflowName="Revenue Workflow"
        initialSecurity={{ classification: "internal", allowed_runtime_engines: ["native"] }}
      />,
    );

    await waitFor(() => expect(getAgentDefinitionsMock).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("agent-options")).toHaveTextContent("agent-1,agent-2");

    fireEvent.click(screen.getByRole("button", { name: /select agent node/i }));
    expect(await screen.findByText("Assigned Agent")).toBeInTheDocument();
    expect(screen.getByText("Research Agent")).toBeInTheDocument();
    expect(screen.getByText("v4 · published")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /edit agent in builder/i }));

    await waitFor(() =>
      expect(routerPushSpy).toHaveBeenCalledWith(
        "/builder/agents/agent-2?returnTo=%2Fbuilder%2Fworkflows%2Fwf-123",
      ),
    );

    window.dispatchEvent(new Event("focus"));
    await waitFor(() => expect(getAgentDefinitionsMock).toHaveBeenCalledTimes(2));
  });

  it("persists workflow saves and publish actions from the page shell", async () => {
    render(
      <WorkflowStudioClient
        workflowId="wf-123"
        workflowName="Revenue Workflow"
        initialSecurity={{ classification: "internal", allowed_runtime_engines: ["native"] }}
      />,
    );

    await waitFor(() => expect(getAgentDefinitionsMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /save workflow/i }));

    await waitFor(() =>
      expect(saveWorkflowDefinitionMock).toHaveBeenCalledWith({
        id: "wf-123",
        name: "Revenue Workflow",
        graph_json: saveGraph,
        security_config: { classification: "internal", allowed_runtime_engines: ["native"] },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /publish workflow/i }));
    await waitFor(() => expect(publishWorkflowDefinitionMock).toHaveBeenCalledWith("wf-123"));
  });
});