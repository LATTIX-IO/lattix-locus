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
  getModelsOverviewMock,
  selectedModel,
} = vi.hoisted(() => ({
  createWorkflowRunMock: vi.fn(),
  getAgentDefinitionsMock: vi.fn(),
  getPublishedWorkflowsMock: vi.fn(),
  getModelsOverviewMock: vi.fn(),
  selectedModel: { value: "ollama/gpt-oss:20b-ctx32k" },
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

vi.mock("@/components/composer-controls", async () => {
  const React = await import("react");
  return {
    ComposerControls: ({ onChange }: { onChange: (value: { model?: string }) => void }) => {
      React.useEffect(() => {
        onChange(selectedModel.value ? { model: selectedModel.value } : {});
      }, [onChange]);
      return null;
    },
  };
});

vi.mock("@/lib/api", () => ({
  createWorkflowRun: createWorkflowRunMock,
  getAgentDefinitions: getAgentDefinitionsMock,
  getPublishedWorkflows: getPublishedWorkflowsMock,
  getModelsOverview: getModelsOverviewMock,
}));

import { TaskKickoffComposer } from "@/components/task-kickoff-composer";

beforeEach(() => {
  refreshMock.mockReset();
  pushMock.mockReset();
  createWorkflowRunMock.mockReset();
  getAgentDefinitionsMock.mockReset();
  getPublishedWorkflowsMock.mockReset();
  getModelsOverviewMock.mockReset();
  selectedModel.value = "ollama/gpt-oss:20b-ctx32k";
  getModelsOverviewMock.mockResolvedValue({
    providers: {
      ollama: {
        available: true,
        installed_models: [{ id: "gpt-oss:20b-ctx32k" }],
      },
    },
  });

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
      model: "ollama/gpt-oss:20b-ctx32k",
    }), { timeoutMs: 120000 });
    expect(pushMock).toHaveBeenCalledWith("/activity?session=run-2");
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

  it("rejects a stale Ollama preference before creating a run", async () => {
    selectedModel.value = "ollama/llama3.2:3b";
    render(<TaskKickoffComposer />);
    fireEvent.change(screen.getByPlaceholderText(/draft outreach sequence/i), {
      target: { value: "Draft an incident summary" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start task/i }));
    expect(await screen.findByText(/ollama model "llama3\.2:3b" is not installed/i)).toBeInTheDocument();
    expect(createWorkflowRunMock).not.toHaveBeenCalled();
  });

  it("rejects empty model rather than silently routing to OpenAI", async () => {
    selectedModel.value = "";
    render(<TaskKickoffComposer />);
    fireEvent.change(screen.getByPlaceholderText(/draft outreach sequence/i), {
      target: { value: "Prepare the security review" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start task/i }));
    expect(await screen.findByText(/no execution model selected/i)).toBeInTheDocument();
    expect(getModelsOverviewMock).not.toHaveBeenCalled();
    expect(createWorkflowRunMock).not.toHaveBeenCalled();
  });

  it("rejects the local model when Ollama is down", async () => {
    getModelsOverviewMock.mockResolvedValue({
      providers: { ollama: { available: false, installed_models: [] } },
    });
    render(<TaskKickoffComposer />);
    fireEvent.change(screen.getByPlaceholderText(/draft outreach sequence/i), {
      target: { value: "Prepare the security review" },
    });
    fireEvent.click(screen.getByRole("button", { name: /start task/i }));
    expect(await screen.findByText(/ollama is not running/i)).toBeInTheDocument();
    expect(createWorkflowRunMock).not.toHaveBeenCalled();
  });
});