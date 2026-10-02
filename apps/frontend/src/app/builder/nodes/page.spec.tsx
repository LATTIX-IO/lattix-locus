import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NodeLibraryPage from "@/app/builder/nodes/page";

const { getNodeDefinitionsMock } = vi.hoisted(() => ({
  getNodeDefinitionsMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("@/components/typed-delete-button", () => ({
  TypedDeleteButton: ({ itemName }: { itemName: string }) => <button type="button">Delete {itemName}</button>,
}));

vi.mock("@/lib/api", () => ({
  getNodeDefinitions: getNodeDefinitionsMock,
}));

describe("NodeLibraryPage", () => {
  beforeEach(() => {
    getNodeDefinitionsMock.mockReset();
    getNodeDefinitionsMock.mockResolvedValue([
      {
        type_key: "frontier/router",
        title: "Router",
        description: "Make deterministic routing decisions.",
        category: "Logic",
        color: "#3158a4",
      },
      {
        type_key: "frontier/iterator",
        title: "Iterator",
        description: "Process list payloads in loop and done branches.",
        category: "Logic",
        color: "#5670d9",
      },
      {
        type_key: "frontier/data-store",
        title: "Data Store",
        description: "Persist business records.",
        category: "Integration",
        color: "#6e7c2d",
      },
      {
        type_key: "frontier/wait",
        title: "Wait",
        description: "Delay or timeout orchestrated work.",
        category: "Control",
        color: "#8c6a13",
      },
    ]);
  });

  it("renders the fetched frontier node kit including newly added enterprise families", async () => {
    render(<NodeLibraryPage />);

    await waitFor(() => {
      expect(screen.getByText("Showing 4 reusable node templates in the Frontier kit.")).toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: /node library/i })).toBeInTheDocument();
    expect(screen.getByText("Router")).toBeInTheDocument();
    expect(screen.getByText("Iterator")).toBeInTheDocument();
    expect(screen.getByText("Data Store")).toBeInTheDocument();
    expect(screen.getByText("Wait")).toBeInTheDocument();
    expect(screen.getByLabelText("Available node templates")).toHaveClass("flex-1", "overflow-y-auto");
    expect(screen.getAllByRole("link", { name: /^open$/i })[0]).toHaveAttribute("href", "/builder/nodes/frontier/router");
  });

  it("updates the custom builder form when a fetched node template is selected", async () => {
    render(<NodeLibraryPage />);

    await screen.findByText("Iterator");
    fireEvent.click(screen.getAllByRole("button", { name: /iterator/i })[0]);

    await waitFor(() => {
      expect(screen.getByDisplayValue("frontier/iterator")).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue("Process list payloads in loop and done branches.")).toBeInTheDocument();
  });
});