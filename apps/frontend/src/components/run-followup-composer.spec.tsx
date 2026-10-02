import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
const refreshMock = vi.fn();

const {
  createWorkflowRunMock,
  getAgentDefinitionsMock,
  getPublishedWorkflowsMock,
  getRuntimeProvidersMock,
  getUserRuntimeProvidersMock,
} = vi.hoisted(() => ({
  createWorkflowRunMock: vi.fn(),
  getAgentDefinitionsMock: vi.fn(),
  getPublishedWorkflowsMock: vi.fn(),
  getRuntimeProvidersMock: vi.fn(),
  getUserRuntimeProvidersMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: pushMock,
    refresh: refreshMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  createWorkflowRun: createWorkflowRunMock,
  getAgentDefinitions: getAgentDefinitionsMock,
  getPublishedWorkflows: getPublishedWorkflowsMock,
  getRuntimeProviders: getRuntimeProvidersMock,
  getUserRuntimeProviders: getUserRuntimeProvidersMock,
}));

import { RunFollowupComposer } from "@/components/run-followup-composer";

beforeEach(() => {
  pushMock.mockReset();
  refreshMock.mockReset();
  createWorkflowRunMock.mockReset();
  getAgentDefinitionsMock.mockReset();
  getPublishedWorkflowsMock.mockReset();
  getRuntimeProvidersMock.mockReset();
  getUserRuntimeProvidersMock.mockReset();

  getAgentDefinitionsMock.mockResolvedValue([
    { id: "agent-1", name: "Research Agent", version: 3, status: "published" },
  ]);
  getPublishedWorkflowsMock.mockResolvedValue([
    { id: "workflow-1", name: "Risk Review", version: 2, status: "published" },
  ]);
  getRuntimeProvidersMock.mockResolvedValue({
    providers: [{ provider: "openai", configured: true, model: "gpt-5.4", mode: "live" }],
  });
  getUserRuntimeProvidersMock.mockResolvedValue([
    {
      provider: "openai",
      configured: true,
      model: "gpt-5.4",
      available_models: ["gpt-5.4", "gpt-5.4-mini"],
      base_url: "",
      api_key_masked: "sk-***",
      preferred: true,
      updated_at: "2026-04-04T08:00:00Z",
      source: "user",
    },
  ]);
  createWorkflowRunMock.mockResolvedValue({ id: "run-2" });
});

describe("RunFollowupComposer", () => {
  it("starts as a single-line composer and can collapse after expanding", async () => {
    render(<RunFollowupComposer runId="run-1" recentContext="User: Need a follow-up" />);

    const textarea = screen.getByRole("textbox", { name: /message this run/i }) as HTMLTextAreaElement;
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      get: () => (textarea.value.includes("\n") ? 144 : 44),
    });

    await waitFor(() => expect(textarea.style.height).toBe("44px"));

    fireEvent.change(textarea, { target: { value: "Line one\nLine two\nLine three" } });

    await waitFor(() => expect(textarea.style.height).toBe("144px"));
    const collapseButton = screen.getByRole("button", { name: /collapse message composer/i });
    fireEvent.click(collapseButton);

    await waitFor(() => expect(textarea.style.height).toBe("44px"));
    expect(screen.getByRole("button", { name: /expand message composer/i })).toBeInTheDocument();
  });

  it("uses enter for newline and cmd/ctrl+enter to submit", async () => {
    render(<RunFollowupComposer runId="run-1" recentContext="User: Need a follow-up" />);

    const textarea = screen.getByRole("textbox", { name: /message this run/i });
    fireEvent.change(textarea, { target: { value: "Line one" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(createWorkflowRunMock).not.toHaveBeenCalled();

    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true });

    await waitFor(() => expect(createWorkflowRunMock).toHaveBeenCalledTimes(1));
  });

  it("submits plain prompt text while passing hidden recent context and selected runtime", async () => {
    render(
      <RunFollowupComposer
        runId="run-1"
        recentContext="User: Summarize the current risks.\nAgent: I drafted the review plan."
        initialRuntime={{ provider: "openai", model: "gpt-5.4" }}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /show follow-up controls/i }));
    expect(screen.getByRole("button", { name: /current follow-up model/i })).toHaveTextContent("OpenAI · gpt-5.4");
    fireEvent.change(screen.getByRole("textbox", { name: /message this run/i }), {
      target: { value: "Continue with the mitigations." },
    });
    fireEvent.change(screen.getByRole("combobox", { name: /follow-up current model/i }), {
      target: { value: "openai::gpt-5.4-mini" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => expect(createWorkflowRunMock).toHaveBeenCalledTimes(1));
    const [payload, options] = createWorkflowRunMock.mock.calls[0] ?? [];
    expect(payload?.prompt).toBe("Continue with the mitigations.");
    expect(payload?.provider).toBe("openai");
    expect(payload?.model).toBe("gpt-5.4-mini");
    expect(payload?.runtime).toEqual({
      provider: "openai",
      model: "gpt-5.4-mini",
    });
    expect(payload?.context?.mode).toBe("follow_up");
    expect(payload?.context?.source_run_id).toBe("run-1");
    expect(payload?.context?.recent_context).toContain("User: Summarize the current risks.");
    expect(payload?.context?.recent_context).toContain("Agent: I drafted the review plan.");
    expect(options).toEqual({ timeoutMs: 120000 });
    expect(payload?.prompt).not.toMatch(/Previous run context/i);
  });

  it("filters the command list and inserts a selected token into the draft", async () => {
    render(<RunFollowupComposer runId="run-1" recentContext="User: Need a follow-up" />);

    fireEvent.click(await screen.findByRole("button", { name: /show follow-up controls/i }));
    fireEvent.change(screen.getByRole("textbox", { name: /search follow-up commands/i }), {
      target: { value: "risk" },
    });

    expect(screen.getByRole("button", { name: /\/risk-review/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /\/risk-review/i }));

    expect(screen.getByRole("textbox", { name: /message this run/i })).toHaveValue("/risk-review ");
  });
});