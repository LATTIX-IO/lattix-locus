import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
const refreshMock = vi.fn();

const { archiveWorkflowRunMock } = vi.hoisted(() => ({
  archiveWorkflowRunMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceMock,
    refresh: refreshMock,
  }),
  usePathname: () => "/inbox",
  useSearchParams: () => new URLSearchParams("session=run-1"),
}));

vi.mock("@/lib/api", () => ({
  archiveWorkflowRun: archiveWorkflowRunMock,
}));

import { RunArchiveButton } from "@/components/run-archive-button";

beforeEach(() => {
  archiveWorkflowRunMock.mockReset();
  replaceMock.mockReset();
  refreshMock.mockReset();
  archiveWorkflowRunMock.mockResolvedValue({ ok: true });
});

describe("RunArchiveButton", () => {
  it("archives the active inbox session and returns to the inbox index", async () => {
    render(<RunArchiveButton runId="run-1" />);

    fireEvent.click(screen.getByRole("button", { name: /archive run/i }));

    await waitFor(() => {
      expect(archiveWorkflowRunMock).toHaveBeenCalledWith("run-1");
    });
    expect(replaceMock).toHaveBeenCalledWith("/inbox");
    expect(refreshMock).toHaveBeenCalledTimes(1);
  });
});