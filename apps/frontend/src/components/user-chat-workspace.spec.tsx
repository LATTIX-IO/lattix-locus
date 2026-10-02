import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
const refreshMock = vi.fn();

const {
  getAtfAlignmentReportMock,
  getWorkflowRunMock,
  getWorkflowRunEventsMock,
  reactFlowCanvasPropsMock,
  streamWorkflowRunMock,
  submitApprovalMock,
  streamHandlersState,
} = vi.hoisted(() => ({
  getAtfAlignmentReportMock: vi.fn(),
  getWorkflowRunMock: vi.fn(),
  getWorkflowRunEventsMock: vi.fn(),
  reactFlowCanvasPropsMock: vi.fn(),
  streamWorkflowRunMock: vi.fn(),
  submitApprovalMock: vi.fn(),
  streamHandlersState: {
    current: null as null | {
      onMessage: (event: { id: string; type: string; createdAt: string; payload: Record<string, unknown> }) => void;
      onError?: () => void;
    },
  },
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceMock,
    refresh: refreshMock,
  }),
}));

vi.mock("@/components/reactflow-canvas", () => ({
  ReactFlowCanvas: (props: { nodes: Array<{ id: string; title: string; type: string }>; links: Array<{ from: string; to: string }>; readOnly?: boolean }) => {
    reactFlowCanvasPropsMock(props);
    return <div data-testid="reactflow-canvas">{`nodes:${props.nodes.length} links:${props.links.length} readonly:${props.readOnly ? "yes" : "no"}`}</div>;
  },
}));

vi.mock("@/components/run-archive-button", () => ({
  RunArchiveButton: ({ runId }: { runId: string }) => <button type="button">Archive {runId}</button>,
}));

vi.mock("@/components/run-followup-composer", () => ({
  RunFollowupComposer: ({ runId, recentContext, onStatusChange }: { runId: string; recentContext: string; onStatusChange?: (status: { state: string; message: string | null; createdRunId: string | null; provider: string; model: string; source: string | null }) => void }) => (
    <div data-testid="followup-composer">
      <span>{runId}:{recentContext}</span>
      <button
        type="button"
        onClick={() => onStatusChange?.({
          state: "success",
          message: "Follow-up sent. Opening run run-2...",
          createdRunId: "run-2",
          provider: "openai",
          model: "gpt-5.4",
          source: "user",
        })}
      >
        Emit follow-up status
      </button>
    </div>
  ),
}));

vi.mock("@/components/task-kickoff-composer", () => ({
  TaskKickoffComposer: () => <div data-testid="task-kickoff-composer">kickoff</div>,
}));

vi.mock("@/lib/api", () => ({
  WORKFLOW_RUN_UPDATED_EVENT: "frontier:workflow-run-updated",
  getAtfAlignmentReport: getAtfAlignmentReportMock,
  getWorkflowRun: getWorkflowRunMock,
  getWorkflowRunEvents: getWorkflowRunEventsMock,
  submitApproval: submitApprovalMock,
  streamWorkflowRun: streamWorkflowRunMock,
}));

import { UserChatWorkspace } from "@/components/user-chat-workspace";

const initialRuns = [
  {
    id: "run-1",
    title: "Quarterly review",
    status: "Needs Review",
    updatedAt: "2026-04-04T08:00:00Z",
    progressLabel: "Awaiting approval",
    kind: "workflow",
  },
] as const;

const initialInbox = [
  {
    id: "inbox-1",
    runId: "run-1",
    runName: "Quarterly review",
    artifactType: "brief",
    reason: "Needs approval",
    queue: "Needs Approval",
  },
] as const;

const runDetail = {
  title: "Quarterly review",
  status: "Needs Review",
  artifacts: [
    { id: "artifact-1", name: "Review memo", status: "Needs Review", version: 2 },
    { id: "artifact-2", name: "Board notes", status: "Draft", version: 1 },
  ],
  graph: {
    nodes: [
      { id: "node-1", title: "Start", type: "frontier/trigger", x: 10, y: 10 },
      { id: "node-2", title: "Agent", type: "frontier/agent", x: 110, y: 10 },
    ],
    links: [{ from: "node-1", to: "node-2" }],
  },
  approvals: {
    required: true,
    pending: true,
    artifact_id: "artifact-1",
    version: 2,
  },
};

const runEvents = [
  {
    id: "event-user",
    type: "user_message",
    title: "You",
    summary: "Summarize the current risks.",
    createdAt: "2026-04-04T08:01:00Z",
  },
  {
    id: "event-agent",
    type: "agent_message",
    title: "Research Agent response",
    summary: "I drafted the review plan.",
    content: "## Review plan\n\n- Validate the current controls\n- Keep the full markdown response visible\n\n`review-plan`",
    createdAt: "2026-04-04T08:02:00Z",
    metadata: {
      selected_agent_name: "Research Agent",
    },
  },
  {
    id: "event-tool-start",
    type: "step_started",
    title: "Tool start",
    summary: "Gathering evidence",
    createdAt: "2026-04-04T08:03:00Z",
  },
  {
    id: "event-tool-done",
    type: "step_completed",
    title: "Tool done",
    summary: "Evidence gathered",
    createdAt: "2026-04-04T08:04:00Z",
  },
  {
    id: "event-guardrail",
    type: "guardrail_result",
    title: "Guardrail review",
    summary: "Sensitive export needs approval.",
    createdAt: "2026-04-04T08:05:00Z",
  },
] as const;

const atfReport = {
  generated_at: "2026-04-04T08:00:00Z",
  framework: "ATF",
  coverage_percent: 82,
  maturity_estimate: "senior",
  pillars: {
    identity: { status: "strong", controls: {}, gaps: ["review signoff"] },
    behavior_monitoring: { status: "strong", controls: {}, gaps: ["latency drill"] },
    data_governance: { status: "partial", controls: {}, gaps: ["artifact retention"] },
    segmentation: { status: "strong", controls: {}, gaps: ["network policy"] },
    incident_response: { status: "partial", controls: {}, gaps: ["playbook tabletop"] },
  },
  evidence: {
    audit_window_hours: 24,
    audit_event_count_24h: 10,
    audit_allowed_24h: 8,
    audit_blocked_24h: 2,
    audit_error_24h: 0,
    total_audit_events: 100,
    run_count_total: 12,
  },
} as const;

beforeEach(() => {
  replaceMock.mockReset();
  refreshMock.mockReset();
  getAtfAlignmentReportMock.mockReset();
  getWorkflowRunMock.mockReset();
  getWorkflowRunEventsMock.mockReset();
  reactFlowCanvasPropsMock.mockReset();
  streamWorkflowRunMock.mockReset();
  submitApprovalMock.mockReset();
  streamHandlersState.current = null;

  getWorkflowRunMock.mockResolvedValue(runDetail);
  getWorkflowRunEventsMock.mockResolvedValue(runEvents);
  getAtfAlignmentReportMock.mockResolvedValue(atfReport);
  submitApprovalMock.mockResolvedValue({ ok: true });
  streamWorkflowRunMock.mockImplementation((_id: string, handlers: { onMessage: (event: { id: string; type: string; createdAt: string; payload: Record<string, unknown> }) => void; onError?: () => void }) => {
    streamHandlersState.current = handlers;
    return vi.fn();
  });
});

describe("UserChatWorkspace", () => {
  it("shows the chat-first workspace with action items in the details rail", async () => {
    render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledWith("run-1"));
    expect(screen.getAllByText("Quarterly review")).toHaveLength(2);
    expect(screen.getByText(/session details/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /hide details/i })).toBeInTheDocument();
    expect(screen.getByText(/1 action items/i)).toBeInTheDocument();
    expect(await screen.findByText(/review plan/i)).toBeInTheDocument();
    expect(screen.getByText(/keep the full markdown response visible/i)).toBeInTheDocument();
    expect(screen.getByText("review-plan")).toBeInTheDocument();
    expect(screen.queryByText("Gathering evidence")).not.toBeInTheDocument();
    expect(screen.queryByText("Evidence gathered")).not.toBeInTheDocument();
    expect(screen.getAllByText("Needs approval")).toHaveLength(2);
    expect(screen.getByText("Approval decision is still pending.")).toBeInTheDocument();
    expect(screen.getByText("Sensitive export needs approval.")).toBeInTheDocument();
    expect(screen.getByTestId("followup-composer")).toHaveTextContent("run-1:User: Summarize the current risks.");
    expect(screen.getByText("Research Agent")).toBeInTheDocument();
    expect(screen.getByTestId("session-workspace")).toHaveClass("h-[calc(100vh-96px)]", "overflow-hidden");
    expect(screen.getByTestId("session-workspace")).not.toHaveClass("-m-5");
    expect(screen.getByTestId("session-followup-dock")).toHaveClass("mt-auto", "shrink-0");
    expect(screen.getByTestId("session-details-rail")).toHaveStyle({ width: "clamp(20rem, 26vw, 30rem)" });
    expect(screen.getByTestId("session-details-rail")).toHaveClass("z-10");
  });

  it("falls back to operational events when no transcript messages exist", async () => {
    getWorkflowRunEventsMock.mockResolvedValueOnce([
      {
        id: "event-tool-start-only",
        type: "step_started",
        title: "Tool start",
        summary: "Gathering evidence",
        createdAt: "2026-04-04T08:03:00Z",
      },
    ]);

    render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledWith("run-1"));
    expect(await screen.findByText("Gathering evidence")).toBeInTheDocument();
  });

  it("refetches the selected session when server props refresh the same run", async () => {
    const view = render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getWorkflowRunEventsMock).toHaveBeenCalledTimes(1));

    view.rerender(
      <UserChatWorkspace
        initialRuns={[{ ...initialRuns[0], progressLabel: "Responding", updatedAt: "2026-04-04T08:06:00Z" }]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(getWorkflowRunEventsMock).toHaveBeenCalledTimes(2));
  });

  it("toggles the details rail from both the header button and the close button", async () => {
    render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledWith("run-1"));
    fireEvent.click(screen.getByRole("button", { name: /hide details/i }));
    expect(screen.getByTestId("session-details-rail")).toHaveStyle({ transform: "translateX(100%)", pointerEvents: "none" });

    fireEvent.click(screen.getByRole("button", { name: /show details/i }));
    expect(screen.getByTestId("session-details-rail")).toHaveStyle({ transform: "translateX(0)", pointerEvents: "auto" });

    fireEvent.click(screen.getByRole("button", { name: "X" }));
    expect(screen.getByTestId("session-details-rail")).toHaveStyle({ transform: "translateX(100%)", pointerEvents: "none" });
  });

  it("moves follow-up status updates into the details rail", async () => {
    render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledWith("run-1"));
    fireEvent.click(screen.getByRole("button", { name: /emit follow-up status/i }));

    expect(screen.getByText(/follow-up activity/i)).toBeInTheDocument();
    expect(screen.getByText("Follow-up sent. Opening run run-2...")).toBeInTheDocument();
    expect(screen.getByText("Run created:")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "run-2" })).toHaveAttribute("href", "/inbox?session=run-2");
    expect(screen.getByText("openai")).toBeInTheDocument();
    expect(screen.getByText("gpt-5.4")).toBeInTheDocument();
  });

  it("shows a blank kickoff workspace when no session is selected", async () => {
    await act(async () => {
      render(
        <UserChatWorkspace
          initialRuns={[...initialRuns]}
          initialInbox={[...initialInbox]}
          initialSelectedRunId={null}
          initialDetailsOpen={false}
          initialTab="chat"
        />,
      );
    });

    expect(screen.getByText(/start a new chat or task/i)).toBeInTheDocument();
    expect(screen.getByTestId("task-kickoff-composer")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
    expect(getWorkflowRunMock).not.toHaveBeenCalled();
    expect(streamWorkflowRunMock).not.toHaveBeenCalled();
  });

  it("renders streamed chat deltas and keeps the graph tab available", async () => {
    render(
      <UserChatWorkspace
        initialRuns={[...initialRuns]}
        initialInbox={[...initialInbox]}
        initialSelectedRunId="run-1"
        initialDetailsOpen={false}
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(streamWorkflowRunMock).toHaveBeenCalled());

    await act(async () => {
      streamHandlersState.current?.onMessage({
        id: "stream-1",
        type: "delta",
        createdAt: "2026-04-04T08:06:00Z",
        payload: { text: "Streaming answer in progress" },
      });
    });

    expect(screen.getByText("Streaming answer in progress")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /execution graph/i }));
    expect(screen.getByTestId("reactflow-canvas")).toHaveTextContent("nodes:2 links:1 readonly:yes");
    expect(screen.getByText(/2 tool calls/i)).toBeInTheDocument();
    expect(screen.getByText(/2 artifacts/i)).toBeInTheDocument();

    const lastGraphProps = reactFlowCanvasPropsMock.mock.calls[reactFlowCanvasPropsMock.mock.calls.length - 1]?.[0] as {
      nodes: Array<{ title: string; type: string }>;
      readOnly?: boolean;
    };
    expect(lastGraphProps.readOnly).toBe(true);
    const graphTitles = lastGraphProps.nodes.map((node) => node.title);
    expect(graphTitles).toEqual(["Start", "Agent"]);
    expect(lastGraphProps.nodes.map((node) => node.type)).toEqual(["frontier/trigger", "frontier/agent"]);
  });

  it("renders a selected session even when its summary is not preloaded", async () => {
    render(
      <UserChatWorkspace
        initialRuns={[]}
        initialInbox={[]}
        initialSelectedRunId="run-1"
        initialDetailsOpen={false}
        initialTab="chat"
      />,
    );

    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalledWith("run-1"));
    expect(screen.getByTestId("session-workspace")).toBeInTheDocument();
    expect(await screen.findAllByText("Quarterly review")).not.toHaveLength(0);
  });
});
