import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import GuardrailsBuilderPage from "@/app/builder/guardrails/page";

const { getGuardrailRulesetsMock } = vi.hoisted(() => ({
  getGuardrailRulesetsMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("@/components/typed-delete-button", () => ({
  TypedDeleteButton: ({ itemName }: { itemName: string }) => <button type="button">Delete {itemName}</button>,
}));

vi.mock("@/lib/api", () => ({
  getGuardrailRulesets: getGuardrailRulesetsMock,
}));

describe("GuardrailsBuilderPage", () => {
  beforeEach(() => {
    getGuardrailRulesetsMock.mockReset();
    getGuardrailRulesetsMock.mockResolvedValue([
      { id: "gr-1", name: "Core Safety", version: 4, status: "published" },
      { id: "gr-2", name: "Retired Safety", version: 2, status: "archived" },
    ]);
  });

  it("filters archived guardrails out of the library view and shows lifecycle counts", async () => {
    render(await GuardrailsBuilderPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("Core Safety")).toBeInTheDocument();
    expect(screen.queryByText("Retired Safety")).not.toBeInTheDocument();
    expect(screen.getByText("Draft 0")).toBeInTheDocument();
    expect(screen.getByText("Published 1")).toBeInTheDocument();
    expect(screen.getByText("Archived 1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^archived$/i })).toHaveAttribute("href", "/builder/guardrails?view=archived");
  });

  it("shows archived guardrails in the archived view", async () => {
    render(await GuardrailsBuilderPage({ searchParams: Promise.resolve({ view: "archived" }) }));

    expect(screen.getByText("Retired Safety")).toBeInTheDocument();
    expect(screen.queryByText("Core Safety")).not.toBeInTheDocument();
  });
});