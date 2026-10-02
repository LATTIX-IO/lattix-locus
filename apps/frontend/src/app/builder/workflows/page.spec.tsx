import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkflowsPage from "@/app/builder/workflows/page";

const { getWorkflowDefinitionsMock } = vi.hoisted(() => ({
  getWorkflowDefinitionsMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("@/components/typed-delete-button", () => ({
  TypedDeleteButton: ({ itemName }: { itemName: string }) => <button type="button">Delete {itemName}</button>,
}));

vi.mock("@/components/import-export-controls", () => ({
  ImportExportControls: () => null,
}));

vi.mock("@/components/builder-library-actions", () => ({
  BuilderLibraryActions: ({ status, openHref }: { status: string; openHref: string }) => (
    <div>
      <span>{status}</span>
      <a href={openHref}>Open</a>
      <span>Publish</span>
      <span>Archive</span>
    </div>
  ),
}));

vi.mock("@/lib/api", () => ({
  getWorkflowDefinitions: getWorkflowDefinitionsMock,
}));

describe("WorkflowsPage", () => {
  beforeEach(() => {
    getWorkflowDefinitionsMock.mockReset();
    getWorkflowDefinitionsMock.mockResolvedValue([
      { id: "wf-1", name: "Incident Intake", description: "Triages alerts.", version: 2, status: "published" },
      { id: "wf-2", name: "Old Workflow", description: "Retired.", version: 4, status: "archived" },
    ]);
  });

  it("hides archived workflows from the default library view", async () => {
    render(await WorkflowsPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("Incident Intake")).toBeInTheDocument();
    expect(screen.queryByText("Old Workflow")).not.toBeInTheDocument();
    expect(screen.getByText("Draft 0")).toBeInTheDocument();
    expect(screen.getByText("Published 1")).toBeInTheDocument();
    expect(screen.getByText("Archived 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^archived$/i })).toHaveAttribute("href", "/builder/workflows?view=archived");
  });

  it("shows archived workflows in the archived view", async () => {
    render(await WorkflowsPage({ searchParams: Promise.resolve({ view: "archived" }) }));

    expect(screen.getByText("Old Workflow")).toBeInTheDocument();
    expect(screen.queryByText("Incident Intake")).not.toBeInTheDocument();
  });
});