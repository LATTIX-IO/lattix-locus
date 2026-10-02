import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PlaybooksPage from "@/app/builder/playbooks/page";

const { getPlaybooksMock } = vi.hoisted(() => ({
  getPlaybooksMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
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
  getPlaybooks: getPlaybooksMock,
}));

describe("PlaybooksPage", () => {
  beforeEach(() => {
    getPlaybooksMock.mockReset();

    getPlaybooksMock.mockResolvedValue([
      {
        id: "pb-1",
        name: "Ops Escalation",
        description: "Coordinate an escalation path.",
        category: "operations",
        status: "published",
      },
      {
        id: "pb-2",
        name: "Retired Playbook",
        description: "No longer in rotation.",
        category: "operations",
        status: "archived",
      },
    ]);
  });

  it("renders a workflow-style playbook library with open links", async () => {
    render(await PlaybooksPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("heading", { name: /playbooks/i })).toBeInTheDocument();
    expect(screen.getByText("Ops Escalation")).toBeInTheDocument();
    expect(screen.getByText("operations")).toBeInTheDocument();
    expect(screen.getByText("Draft 0")).toBeInTheDocument();
    expect(screen.getByText("Published 1")).toBeInTheDocument();
    expect(screen.getByText("Archived 1")).toBeInTheDocument();
    expect(screen.getAllByText("published")).toHaveLength(2);
    expect(screen.getByRole("link", { name: /new playbook/i })).toHaveAttribute("href", "/builder/playbooks/new");
    expect(screen.getByRole("link", { name: /^library$/i })).toHaveAttribute("href", "/builder/playbooks");
    expect(screen.getByRole("link", { name: /^archived$/i })).toHaveAttribute("href", "/builder/playbooks?view=archived");
    expect(screen.getByRole("link", { name: /open/i })).toHaveAttribute("href", "/builder/playbooks/pb-1");
    expect(screen.queryByText("Retired Playbook")).not.toBeInTheDocument();
  });

  it("renders archived playbooks in the archived view", async () => {
    render(await PlaybooksPage({ searchParams: Promise.resolve({ view: "archived" }) }));

    expect(screen.getByText("Retired Playbook")).toBeInTheDocument();
    expect(screen.queryByText("Ops Escalation")).not.toBeInTheDocument();
  });

  it("renders an empty-state row when there are no playbooks", async () => {
    getPlaybooksMock.mockResolvedValue([]);

    render(await PlaybooksPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText(/no playbooks available/i)).toBeInTheDocument();
  });
});