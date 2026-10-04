import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { MouseEvent, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Regression (desktop UX batch): with a session open (/activity?session=…) the
// left nav could not be used. The follow-up composer re-reported its status on
// every parent render, the parent re-rendered on every report, and that endless
// update loop starved Next's navigation transitions. This renders the real
// workspace and composer next to the real left nav.

const { navigateMock, getWorkflowRunMock } = vi.hoisted(() => ({
  navigateMock: vi.fn(),
  getWorkflowRunMock: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a
      href={href}
      {...props}
      onClick={(event: MouseEvent<HTMLAnchorElement>) => {
        event.preventDefault();
        navigateMock(href);
      }}
    >
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), refresh: vi.fn(), push: vi.fn() }),
  usePathname: () => "/activity",
  useSearchParams: () => new URLSearchParams("session=run-1"),
}));

vi.mock("@/components/reactflow-canvas", () => ({ ReactFlowCanvas: () => <div data-testid="reactflow-canvas" /> }));
vi.mock("@/components/navigation/platform-update-panel", () => ({ PlatformUpdatePanel: () => <div>updates</div> }));

const RUN = {
  id: "run-1",
  title: "Quarterly review",
  status: "Running",
  artifacts: [],
  approvals: { required: false, pending: false },
  runtime: { provider: "ollama", model: "llama3" },
};

vi.mock("@/lib/api", () => ({
  WORKFLOW_RUN_UPDATED_EVENT: "locus:workflow-run-updated",
  getAtfAlignmentReport: vi.fn(async () => null),
  getWorkflowRun: (id: string) => {
    getWorkflowRunMock(id);
    return Promise.resolve(RUN);
  },
  getWorkflowRunEvents: vi.fn(async () => []),
  getWorkflowRunLive: vi.fn(async () => RUN),
  getWorkflowRunEventsLive: vi.fn(async () => []),
  submitApproval: vi.fn(),
  streamWorkflowRun: vi.fn(() => () => {}),
  archiveWorkflowRun: vi.fn(),
  updateWorkflowRunTitle: vi.fn(),
  getWorkflowRuns: vi.fn(async () => [
    { id: "run-1", title: "Quarterly review", status: "Running", updatedAt: "now", progressLabel: "Running", kind: "chat" },
  ]),
  getInbox: vi.fn(async () => []),
  createWorkflowRun: vi.fn(),
  sendRunMessage: vi.fn(),
  getAgentDefinitions: vi.fn(async () => []),
  getPlaybooks: vi.fn(async () => []),
  getPublishedWorkflows: vi.fn(async () => []),
  getRuntimeProviders: vi.fn(async () => ({ providers: [{ provider: "ollama", configured: true, model: "llama3", mode: "live" }] })),
  getUserRuntimeProviders: vi.fn(async () => []),
  getRunEscalations: vi.fn(async () => []),
  approveRunEscalation: vi.fn(),
  denyRunEscalation: vi.fn(),
  allowSiteInBrowserTier: vi.fn(),
}));

import { LeftNav } from "@/components/navigation/left-nav";
import { PRIMARY_NAV } from "@/components/navigation/nav-config";
import { UserChatWorkspace } from "@/components/user-chat-workspace";

function blockedByAncestor(element: HTMLElement): string | null {
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    if (node.hasAttribute("inert")) return "inert";
    if (node.getAttribute("aria-hidden") === "true") return "aria-hidden";
    if (window.getComputedStyle(node).pointerEvents === "none") return "pointer-events: none";
  }
  return null;
}

describe("left nav with a session open", () => {
  it("keeps every primary nav link focusable and clickable, and the session settles", async () => {
    render(
      <>
        <aside aria-label="Sidebar">
          <LeftNav pathname="/activity" selectedSessionId="run-1" platformVersion={null} />
        </aside>
        <main>
          <UserChatWorkspace initialRuns={[]} initialInbox={[]} initialSelectedRunId="run-1" initialDetailsOpen initialTab="chat" />
        </main>
      </>,
    );

    expect(await screen.findByTestId("session-workspace")).toBeInTheDocument();
    await screen.findByLabelText("Message this run");
    await waitFor(() => expect(getWorkflowRunMock).toHaveBeenCalled());
    // Give a runaway update loop the chance to show itself: the run must not
    // be re-fetched over and over once the page has settled.
    await new Promise((resolve) => setTimeout(resolve, 50));
    const settledFetches = getWorkflowRunMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(getWorkflowRunMock.mock.calls.length).toBe(settledFetches);
    expect(settledFetches).toBeLessThanOrEqual(2);

    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const item of PRIMARY_NAV) {
      const link = within(nav).getByRole("link", { name: item.label });
      expect(blockedByAncestor(link)).toBeNull();
      link.focus();
      expect(link).toHaveFocus();
      fireEvent.click(link);
      expect(navigateMock).toHaveBeenLastCalledWith(item.href);
    }
  });
});
