import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const refreshMock = vi.fn();
const pushMock = vi.fn();
const modelState = vi.hoisted(() => ({
  ready: true,
  reason: "",
  selected: "ollama/gpt-oss:20b-ctx32k",
}));

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
  ComposerControls: ({
    onChange,
    onModelReadinessChange,
  }: {
    onChange: (options: { model?: string }) => void;
    onModelReadinessChange: (ready: boolean, reason: string) => void;
  }) => {
    useEffect(() => {
      onChange({ model: modelState.selected || undefined });
      onModelReadinessChange(modelState.ready, modelState.reason);
    }, [onChange, onModelReadinessChange]);
    return null;
  },
}));

vi.mock("@/lib/api", () => ({
  createWorkflowRun: createWorkflowRunMock,
  getAgentDefinitions: getAgentDefinitionsMock,
  getPublishedWorkflows: getPublishedWorkflowsMock,
}));

import { TaskKickoffComposer } from "@/components/task-kickoff-composer";

beforeEach(() => {
  modelState.ready = true;
  modelState.reason = "";
  modelState.selected = "ollama/gpt-oss:20b-ctx32k";
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
      model: "ollama/gpt-oss:20b-ctx32k",
      tokens: [],
    }), { timeoutMs: 120000 });
    expect(pushMock).toHaveBeenCalledWith("/activity?session=run-2");
    expect(refreshMock).toHaveBeenCalled();
    expect(screen.getByText("Task started. Opening run run-2...")).toBeInTheDocument();
  });

  it("refuses tasks while the model inventory is not ready", async () => {
    modelState.ready = false;
    modelState.reason = "Checking available models…";
    modelState.selected = "";
    render(<TaskKickoffComposer />);
    const textarea = screen.getByPlaceholderText(/draft outreach sequence/i);
    fireEvent.change(textarea, { target: { value: "Draft a research note" } });

    const button = screen.getByRole("button", { name: /start task/i });
    expect(button).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent(/checking available models/i);
    fireEvent.click(button);
    fireEvent.keyDown(textarea, { key: "Enter", ctrlKey: true });
    expect(createWorkflowRunMock).not.toHaveBeenCalled();
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