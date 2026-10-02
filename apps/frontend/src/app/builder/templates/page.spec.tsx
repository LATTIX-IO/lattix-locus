import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TemplatesPage from "@/app/builder/templates/page";

const pushMock = vi.fn();

const { getTemplateCatalogMock, instantiateAgentTemplateMock, instantiateWorkflowTemplateMock, instantiatePlaybookMock } = vi.hoisted(() => ({
  getTemplateCatalogMock: vi.fn(),
  instantiateAgentTemplateMock: vi.fn(),
  instantiateWorkflowTemplateMock: vi.fn(),
  instantiatePlaybookMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  getTemplateCatalog: getTemplateCatalogMock,
  instantiateAgentTemplate: instantiateAgentTemplateMock,
  instantiateWorkflowTemplate: instantiateWorkflowTemplateMock,
  instantiatePlaybook: instantiatePlaybookMock,
}));

describe("TemplatesPage", () => {
  beforeEach(() => {
    pushMock.mockReset();
    getTemplateCatalogMock.mockReset();
    instantiateAgentTemplateMock.mockReset();
    instantiateWorkflowTemplateMock.mockReset();
    instantiatePlaybookMock.mockReset();
    getTemplateCatalogMock.mockResolvedValue([
      {
        id: "tpl-1",
        source_id: "agent-template-1",
        name: "SOC Analyst",
        description: "Triage inbound alerts.",
        category: "operations",
        template_type: "agent",
        status: "active",
        version: 3,
      },
      {
        id: "tpl-2",
        source_id: "workflow-template-1",
        name: "Legacy Escalation",
        description: "Deprecated coordination path.",
        category: "operations",
        template_type: "workflow",
        status: "deprecated",
        version: 1,
      },
      {
        id: "tpl-3",
        source_id: "playbook-template-1",
        name: "Playbook Starter",
        description: "Operational coordination starter.",
        category: "operations",
        template_type: "playbook",
        status: "active",
        version: 2,
      },
    ]);
    instantiatePlaybookMock.mockResolvedValue({ ok: true, id: "playbook-42" });
  });

  it("defaults to the library view and shows active or archived counts", async () => {
    render(<TemplatesPage />);

    expect(await screen.findByText("SOC Analyst")).toBeInTheDocument();
    expect(screen.queryByText("Legacy Escalation")).not.toBeInTheDocument();
    expect(screen.getByText("Active 2")).toBeInTheDocument();
    expect(screen.getByText("Archived 1")).toBeInTheDocument();
  });

  it("switches to the archived view for deprecated templates", async () => {
    render(<TemplatesPage />);

    await screen.findByText("SOC Analyst");
    fireEvent.click(screen.getByRole("button", { name: /^archived$/i }));

    await waitFor(() => {
      expect(screen.getByText("Legacy Escalation")).toBeInTheDocument();
    });
    expect(screen.queryByText("SOC Analyst")).not.toBeInTheDocument();
  });

  it("routes playbook instantiation to the playbook builder", async () => {
    render(<TemplatesPage />);

    await screen.findByText("Playbook Starter");
    const row = screen.getByText("Playbook Starter").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(within(row as HTMLElement).getByRole("button", { name: /^instantiate$/i }));

    await waitFor(() => {
      expect(instantiatePlaybookMock).toHaveBeenCalledWith("playbook-template-1", { name: "Playbook Starter Instance" });
    });
    expect(pushMock).toHaveBeenCalledWith("/builder/playbooks/playbook-42");
  });
});
