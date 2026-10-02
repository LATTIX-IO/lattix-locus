import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentsPage from "@/app/builder/agents/page";

const { getAgentDefinitionsMock } = vi.hoisted(() => ({
  getAgentDefinitionsMock: vi.fn(),
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
  getAgentDefinitions: getAgentDefinitionsMock,
}));

describe("AgentsPage", () => {
  beforeEach(() => {
    getAgentDefinitionsMock.mockReset();
    getAgentDefinitionsMock.mockResolvedValue([
      { id: "agent-1", name: "Responder", type: "graph", version: 3, status: "draft" },
      { id: "agent-2", name: "Retired Agent", type: "form", version: 7, status: "archived" },
    ]);
  });

  it("filters archived agents out of the library view", async () => {
    render(await AgentsPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("Responder")).toBeInTheDocument();
    expect(screen.queryByText("Retired Agent")).not.toBeInTheDocument();
    expect(screen.getByText("Draft 1")).toBeInTheDocument();
    expect(screen.getByText("Published 0")).toBeInTheDocument();
    expect(screen.getByText("Archived 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^archived$/i })).toHaveAttribute("href", "/builder/agents?view=archived");
  });

  it("shows only archived agents in the archived view", async () => {
    render(await AgentsPage({ searchParams: Promise.resolve({ view: "archived" }) }));

    expect(screen.getByText("Retired Agent")).toBeInTheDocument();
    expect(screen.queryByText("Responder")).not.toBeInTheDocument();
  });
});