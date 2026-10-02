import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlaybookStudioClient } from "@/app/builder/playbooks/[id]/page.client";

const routerReplaceSpy = vi.fn();
const saveGraph = {
  nodes: [{ id: "workflow-node", title: "Workflow", type: "workflow", x: 10, y: 20, config: { workflow_id: "wf-2" } }],
  links: [],
};

const {
  getPlaybookMock,
  getWorkflowDefinitionsMock,
  savePlaybookMock,
} = vi.hoisted(() => ({
  getPlaybookMock: vi.fn(),
  getWorkflowDefinitionsMock: vi.fn(),
  savePlaybookMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: routerReplaceSpy,
  }),
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    return function MockStudioFullCanvas(props: {
      entityName: string;
      initialNodes?: Array<{ id: string }>;
      initialLinks?: Array<{ from: string; to: string }>;
      externalWidgetOptionOverrides?: { workflow?: { workflow_id?: string[] } };
      rightSidebarSlot?: ReactNode;
      onSave?: (graph: typeof saveGraph) => Promise<void>;
    }) {
      return (
        <div data-testid="studio-full-canvas">
          <div data-testid="studio-entity-name">{props.entityName}</div>
          <div data-testid="initial-node-count">{props.initialNodes?.length ?? -1}</div>
          <div data-testid="initial-link-count">{props.initialLinks?.length ?? -1}</div>
          <div data-testid="workflow-options">{props.externalWidgetOptionOverrides?.workflow?.workflow_id?.join(",") ?? ""}</div>
          {props.rightSidebarSlot}
          <button type="button" onClick={() => void props.onSave?.(saveGraph)}>
            Save Graph
          </button>
        </div>
      );
    };
  },
}));

vi.mock("@/lib/api", () => ({
  getPlaybook: getPlaybookMock,
  getWorkflowDefinitions: getWorkflowDefinitionsMock,
  savePlaybook: savePlaybookMock,
}));

describe("PlaybookStudioClient", () => {
  beforeEach(() => {
    routerReplaceSpy.mockReset();
    getPlaybookMock.mockReset();
    getWorkflowDefinitionsMock.mockReset();
    savePlaybookMock.mockReset();

    getWorkflowDefinitionsMock.mockResolvedValue([
      { id: "wf-1", name: "Intake", description: "", version: 1, status: "draft" },
      { id: "wf-2", name: "Remediation", description: "", version: 1, status: "published" },
    ]);
    getPlaybookMock.mockResolvedValue({
      id: "pb-1",
      name: "Ops Escalation Updated",
      description: "Updated orchestration instructions.",
      category: "operations",
      status: "published",
      metadata_json: {},
      graph_json: { nodes: [], links: [] },
    });
    savePlaybookMock.mockResolvedValue({ ok: true, id: "pb-1" });
  });

  it("loads workflow options and saves an existing playbook from the studio route", async () => {
    render(
      <PlaybookStudioClient
        playbookId="pb-1"
        isNew={false}
        initialPlaybook={{
          id: "pb-1",
          name: "Ops Escalation",
          description: "Coordinate an escalation path.",
          category: "operations",
          status: "published",
          metadata_json: {},
          graph_json: { nodes: [], links: [] },
        }}
      />,
    );

    expect(await screen.findByDisplayValue("Ops Escalation")).toBeInTheDocument();
    expect(screen.getByTestId("workflow-options")).toHaveTextContent("wf-1,wf-2");

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Ops Escalation Updated" } });
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "Updated orchestration instructions." } });
    fireEvent.click(screen.getByRole("button", { name: /save graph/i }));

    await waitFor(() =>
      expect(savePlaybookMock).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "pb-1",
          name: "Ops Escalation Updated",
          description: "Updated orchestration instructions.",
          category: "operations",
          status: "published",
          graph_json: saveGraph,
        }),
      ),
    );
    await waitFor(() => expect(getPlaybookMock).toHaveBeenCalledWith("pb-1"));
    expect(routerReplaceSpy).not.toHaveBeenCalled();
  });

  it("preserves intentionally empty persisted graph arrays", async () => {
    render(
      <PlaybookStudioClient
        playbookId="pb-1"
        isNew={false}
        initialPlaybook={{
          id: "pb-1",
          name: "Ops Escalation",
          description: "Coordinate an escalation path.",
          category: "operations",
          status: "published",
          metadata_json: {},
          graph_json: { nodes: [], links: [] },
        }}
      />,
    );

    expect(await screen.findByDisplayValue("Ops Escalation")).toBeInTheDocument();
    expect(screen.getByTestId("initial-node-count")).toHaveTextContent("0");
    expect(screen.getByTestId("initial-link-count")).toHaveTextContent("0");
  });

  it("keeps a successful draft save even if the immediate playbook readback fails", async () => {
    getPlaybookMock.mockRejectedValueOnce(new Error("temporary read failure"));

    render(
      <PlaybookStudioClient
        playbookId="pb-1"
        isNew={false}
        initialPlaybook={{
          id: "pb-1",
          name: "Ops Escalation",
          description: "Coordinate an escalation path.",
          category: "operations",
          status: "published",
          metadata_json: {},
          graph_json: { nodes: [], links: [] },
        }}
      />,
    );

    expect(await screen.findByDisplayValue("Ops Escalation")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "draft" } });
    fireEvent.click(screen.getByRole("button", { name: /save graph/i }));

    await waitFor(() =>
      expect(savePlaybookMock).toHaveBeenCalledWith(
        expect.objectContaining({
          id: "pb-1",
          status: "draft",
        }),
      ),
    );
    await waitFor(() => expect(getPlaybookMock).toHaveBeenCalledWith("pb-1"));
    expect(screen.queryByText(/unable to save this playbook/i)).not.toBeInTheDocument();
  });
});