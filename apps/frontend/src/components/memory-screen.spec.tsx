import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getMemoryLayers: vi.fn(), getMemorySession: vi.fn() }));
vi.mock("@/lib/api", () => api);

import { MemoryScreen } from "@/components/memory-screen";
import type { WorkflowRunSummary } from "@/types/locus";

const runs: WorkflowRunSummary[] = [{ id: "run-1", title: "Draft follow-up", status: "Done", updatedAt: "now", progressLabel: "done" }];

beforeEach(() => {
  api.getMemoryLayers.mockReset();
  api.getMemorySession.mockReset();
});

describe("MemoryScreen", () => {
  it("shows the backend's memory layers and a run's entries, with no placeholder clusters or figures", async () => {
    api.getMemoryLayers.mockResolvedValue([
      { id: "short", name: "Short-term", backend: "sqlite", scope: "session", enabled: true, healthy: true, stats: { entries: 12 } },
      { id: "graph", name: "World graph", backend: "neo4j", scope: "global", enabled: false, healthy: false, stats: {} },
    ]);
    api.getMemorySession.mockResolvedValue({ session_id: "run-1", count: 1, entries: [{ id: "m1", at: "", node_id: "plan", content: "remember this" }] });

    render(<MemoryScreen initialRuns={runs} />);

    expect(await screen.findByText("Short-term")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Off")).toBeInTheDocument();
    expect(await screen.findByText("remember this")).toBeInTheDocument();
    expect(screen.queryByText(/threat intel/i)).not.toBeInTheDocument();
    expect(screen.queryByText("1,284")).not.toBeInTheDocument();
  });

  it("surfaces a failed layer load with a retry", async () => {
    api.getMemoryLayers.mockRejectedValueOnce(new Error("Request failed (403)")).mockResolvedValueOnce([]);
    api.getMemorySession.mockResolvedValue({ session_id: "run-1", count: 0, entries: [] });

    render(<MemoryScreen initialRuns={runs} />);

    expect(await screen.findByText(/could not load memory layers: request failed \(403\)/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/no memory layers are configured/i)).toBeInTheDocument();
  });

  it("has an honest empty state without runs", async () => {
    api.getMemoryLayers.mockResolvedValue([]);
    render(<MemoryScreen initialRuns={[]} />);
    expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument();
    expect(api.getMemorySession).not.toHaveBeenCalled();
  });
});
