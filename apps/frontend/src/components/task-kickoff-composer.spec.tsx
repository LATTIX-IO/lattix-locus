import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const refreshMock = vi.fn();
const pushMock = vi.fn();

const {
  createWorkflowRunMock,
  getAgentDefinitionsMock,
  getPublishedWorkflowsMock,
} = vi.hoisted(() => ({
  createWorkflowRunMock: vi.fn(),
  getAgentDefinitionsMock: vi.fn(),
  getPublishedWorkflowsMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh: refreshMock,
    push: pushMock,
  }),
}));

vi.mock("@/components/composer-controls", () => ({
  ComposerControls: () => null,
}));

vi.mock("@/lib/api", () => ({
  createWorkflowRun: createWorkflowRunMock,
  getAgentDefinitions: getAgentDefinitionsMock,
  getPublishedWorkflows: getPublishedWorkflowsMock,
}));

import { TaskKickoffComposer } from "@/components/task-kickoff-composer";

beforeEach(() => {
  refreshMock.mockReset();
  pushMock.mockReset();
  createWorkflowRunMock.mockReset();
  getAgentDefinitionsMock.mockReset();
  getPublishedWorkflowsMock.mockReset();

  getAgentDefinitionsMock.mockResolvedValue([]);
  getPublishedWorkflowsMock.mockResolvedValue([]);
  createWorkflowRunMock.mockResolvedValue({ id: "run-2" });
});

describe("TaskKickoffComposer", () => {
  it("uses enter for newline and cmd/ctrl+enter to submit", async () => {
    render(<TaskKickoffComposer />);

    const textarea = screen.getByPlaceholderText(/draft outreach sequence/i);
    fireEvent.change(textarea, { target: { value: "Line one" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(createWorkflowRunMock).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });

    await waitFor(() => expect(createWorkflowRunMock).toHaveBeenCalledTimes(1));
    expect(createWorkflowRunMock).toHaveBeenCalledWith(expect.objectContaining({
      session_kind: "task",
      prompt: "Line one",
      tokens: [],
    }), { timeoutMs: 120000 });
    expect(pushMock).toHaveBeenCalledWith("/inbox?session=run-2");
    expect(refreshMock).toHaveBeenCalled();
    expect(screen.getByText("Task started. Opening run run-2...")).toBeInTheDocument();
  });

  it("surfaces backend run-creation failures with the original error detail", async () => {
    createWorkflowRunMock.mockRejectedValue(new Error("Failed to create run (412): missing provider credentials"));

    render(<TaskKickoffComposer />);

    const textarea = screen.getByPlaceholderText(/draft outreach sequence/i);
    fireEvent.change(textarea, { target: { value: "Start the launch checklist" } });
    fireEvent.click(screen.getByRole("button", { name: /start task/i }));

    expect(await screen.findByText(/Failed to create run \(412\): missing provider credentials/i)).toBeInTheDocument();
    expect(pushMock).not.toHaveBeenCalled();
  });
});