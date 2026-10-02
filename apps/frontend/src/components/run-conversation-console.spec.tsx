import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RunConversationConsole } from "@/components/run-conversation-console";

const routerRefreshMock = vi.fn();

const {
  getAtfAlignmentReportMock,
  submitApprovalMock,
  getWorkflowRunLiveMock,
  getWorkflowRunEventsLiveMock,
  streamWorkflowRunEventsMock,
} = vi.hoisted(() => ({
  // Never resolves: keeps the console in "stream" mode without state churn.
  streamWorkflowRunEventsMock: vi.fn(() => new Promise<string>(() => {})),
  getWorkflowRunLiveMock: vi.fn(async () => ({
    artifacts: [],
    status: "Running",
    graph: { nodes: [], links: [] },
    agent_traces: [],
    approvals: { required: false, pending: false },
  })),
  getWorkflowRunEventsLiveMock: vi.fn(async () => []),
  getAtfAlignmentReportMock: vi.fn(async () => ({
    generated_at: new Date().toISOString(),
    framework: "CSA Agentic Trust Framework",
    coverage_percent: 87,
    maturity_estimate: "senior",
    pillars: {
      identity: { status: "strong", controls: {}, gaps: [] },
      behavior_monitoring: { status: "strong", controls: {}, gaps: ["Expand anomaly scoring coverage"] },
      data_governance: { status: "partial", controls: {}, gaps: ["Tighten data retention windows"] },
      segmentation: { status: "strong", controls: {}, gaps: [] },
      incident_response: { status: "partial", controls: {}, gaps: ["Increase tabletop drill cadence"] },
    },
    evidence: {
      audit_window_hours: 24,
      audit_event_count_24h: 21,
      audit_allowed_24h: 17,
      audit_blocked_24h: 3,
      audit_error_24h: 1,
      total_audit_events: 95,
      run_count_total: 14,
    },
  })),
  submitApprovalMock: vi.fn(async () => ({})),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    refresh: routerRefreshMock,
    push: vi.fn(),
  }),
}));

vi.mock("@/components/reactflow-canvas", () => ({
  ReactFlowCanvas: () => <div data-testid="rf-canvas" />,
}));

vi.mock("@/components/run-archive-button", () => ({
  RunArchiveButton: () => <button type="button">Archive</button>,
}));

vi.mock("@/components/run-followup-composer", () => ({
  RunFollowupComposer: () => <div data-testid="followup-composer" />,
}));

vi.mock("@/lib/api", () => ({
  getAtfAlignmentReport: getAtfAlignmentReportMock,
  submitApproval: submitApprovalMock,
  getWorkflowRunLive: getWorkflowRunLiveMock,
  getWorkflowRunEventsLive: getWorkflowRunEventsLiveMock,
  streamWorkflowRunEvents: streamWorkflowRunEventsMock,
  createWorkflowRun: vi.fn(async () => ({ id: "regen-run", status: "started" })),
}));

describe("RunConversationConsole", () => {
  const run = {
    artifacts: [{ id: "artifact-1", name: "Decision Memo", status: "Draft" as const, version: 1 }],
    status: "Running",
    graph: {
      nodes: [{ id: "node-1", title: "Agent", type: "frontier/agent", x: 100, y: 120, config: {} }],
      links: [],
    },
    agent_traces: [],
    approvals: { required: false, pending: false },
    cognitive: {
      assembly: {
        assembly_id: "assembly-1",
        consensus_policy: "weighted-support",
        inference_mode: "bounded",
        columns: ["goal", "evidence", "synthesis"],
      },
      commitment: {
        decision: "Proceed with the release recommendation",
        confidence: 0.72,
        supporting_columns: ["goal", "evidence", "synthesis"],
        dissenting_columns: ["evidence"],
        blockers: ["Missing required evidence: approval memo"],
        next_actions: ["Escalate to human checkpoint."],
        rationale: "Evidence is incomplete for autonomous release.",
        status: "escalated",
      },
      states: {
        goal: { column_id: "goal", confidence: 0.8 },
        evidence: { column_id: "evidence", confidence: 0.5 },
      },
      messages: [
        { message_type: "belief_update", column_id: "goal" },
        { message_type: "evidence_claim", column_id: "evidence" },
      ],
    },
  };

  const events = [
    {
      id: "evt-1",
      type: "user_message" as const,
      title: "User prompt",
      summary: "Please generate the draft.",
      createdAt: new Date().toISOString(),
    },
  ];

  it("renders ATF posture metrics and top gaps in the details flyout", async () => {
    getAtfAlignmentReportMock.mockClear();

    render(<RunConversationConsole runId="run-1" run={run} events={events} />);

    fireEvent.click(screen.getByRole("button", { name: /open run details/i }));

    expect(await screen.findByText(/ATF posture at execution time/i)).toBeInTheDocument();
    expect(await screen.findByText("87%")).toBeInTheDocument();
    expect(await screen.findByText("senior")).toBeInTheDocument();

    const blockedLabel = await screen.findByText(/Blocked \(24h\)/i);
    const blockedCard = blockedLabel.closest("div");
    expect(blockedCard).not.toBeNull();
    expect(within(blockedCard as HTMLElement).getByText("3")).toBeInTheDocument();

    const errorsLabel = await screen.findByText(/Errors \(24h\)/i);
    const errorsCard = errorsLabel.closest("div");
    expect(errorsCard).not.toBeNull();
    expect(within(errorsCard as HTMLElement).getByText("1")).toBeInTheDocument();

    expect(await screen.findByText(/Tighten data retention windows/i)).toBeInTheDocument();

    expect(getAtfAlignmentReportMock).toHaveBeenCalledTimes(1);
  });

  it("shows ATF loading error message when report fetch fails", async () => {
    getAtfAlignmentReportMock.mockReset();
    getAtfAlignmentReportMock.mockRejectedValueOnce(new Error("network"));

    render(<RunConversationConsole runId="run-2" run={run} events={events} />);

    fireEvent.click(screen.getByRole("button", { name: /open run details/i }));

    expect(await screen.findByText(/Unable to load ATF posture/i)).toBeInTheDocument();
    expect(getAtfAlignmentReportMock).toHaveBeenCalledTimes(1);
  });

  it("renders cognitive commitment details when present", async () => {
    getAtfAlignmentReportMock.mockClear();

    render(<RunConversationConsole runId="run-3" run={run} events={events} />);

    fireEvent.click(screen.getByRole("button", { name: /open run details/i }));

    expect(await screen.findByText(/Cognitive artifacts/i)).toBeInTheDocument();
    expect(screen.getByText(/Proceed with the release recommendation/i)).toBeInTheDocument();
    expect(screen.getByText(/Missing required evidence: approval memo/i)).toBeInTheDocument();
    expect(screen.getByText(/Escalate to human checkpoint\./i)).toBeInTheDocument();
    expect(screen.getByText(/weighted-support/i)).toBeInTheDocument();
    expect(screen.getByText(/belief_update · goal/i)).toBeInTheDocument();
  });

  it("keeps the details flyout collapsed by default with the chat visible", () => {
    render(<RunConversationConsole runId="run-4" run={run} events={events} />);

    expect(screen.queryByText(/ATF posture at execution time/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /run details/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Please generate the draft\./i)).toBeInTheDocument();
  });

  it("shows no attention bubble for a healthy running run", () => {
    render(<RunConversationConsole runId="run-5" run={run} events={events} />);

    expect(screen.queryByTestId("flyout-attention")).not.toBeInTheDocument();
  });

  it("shows an action bubble when an approval is pending", () => {
    const approvalRun = {
      ...run,
      status: "Needs Review",
      approvals: { required: true, pending: true, artifact_id: "artifact-1", version: 1 },
    };

    render(<RunConversationConsole runId="run-6" run={approvalRun} events={events} />);

    const bubble = screen.getByTestId("flyout-attention");
    expect(bubble).toBeInTheDocument();
    expect(bubble).toHaveAttribute("aria-label", "Action required");
  });

  it("shows an alert bubble when the run failed", () => {
    const failedRun = { ...run, status: "Failed" };

    render(<RunConversationConsole runId="run-7" run={failedRun} events={events} />);

    const bubble = screen.getByTestId("flyout-attention");
    expect(bubble).toBeInTheDocument();
    expect(bubble).toHaveAttribute("aria-label", "Issues detected");
  });
});
