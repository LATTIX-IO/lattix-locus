import { fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getWorkflowRunsMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ getWorkflowRuns: getWorkflowRunsMock }));
vi.mock("@/components/task-kickoff-composer", () => ({ TaskKickoffComposer: () => <div>task composer</div> }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

import { HomeScreen } from "@/components/home-screen";

const run = (id: string, status: string) => ({ id, title: `Run ${id}`, status, updatedAt: "now", progressLabel: `${status} step`, kind: "workflow" });

beforeEach(() => {
  getWorkflowRunsMock.mockReset();
});

describe("HomeScreen", () => {
  it("shows the composer and the runs that are active now, from the backend only", async () => {
    getWorkflowRunsMock.mockResolvedValue([run("a", "Running"), run("b", "Needs Review"), run("c", "Done")]);
    render(<HomeScreen />);

    expect(screen.getByText("task composer")).toBeInTheDocument();
    const active = await screen.findByRole("list", { name: "Running now" });
    expect(within(active).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([
      "/activity?session=a",
      "/activity?session=b",
    ]);
    expect(within(screen.getByRole("list", { name: "Recent runs" })).getByText("Run c")).toBeInTheDocument();
    // The old demo pipeline and placeholder health figures are gone.
    expect(screen.queryByText(/data room provisioning/i)).not.toBeInTheDocument();
    expect(screen.queryByText("98%")).not.toBeInTheDocument();
  });

  it("says when nothing is running", async () => {
    getWorkflowRunsMock.mockResolvedValue([]);
    render(<HomeScreen />);
    expect(await screen.findByText(/nothing is running/i)).toBeInTheDocument();
  });

  it("surfaces a failed load with a retry instead of an empty dashboard", async () => {
    getWorkflowRunsMock.mockRejectedValueOnce(new Error("Request failed (503)")).mockResolvedValueOnce([run("a", "Running")]);
    render(<HomeScreen />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/request failed \(503\)/i);
    expect(screen.queryByText(/nothing is running/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("Run a")).toBeInTheDocument();
  });
});
