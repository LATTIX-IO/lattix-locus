import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const {
  archiveWorkflowRunMock,
  getInboxMock,
  getWorkflowRunsMock,
  refreshMock,
  replaceMock,
  updateWorkflowRunTitleMock,
} = vi.hoisted(() => ({
  archiveWorkflowRunMock: vi.fn(),
  getWorkflowRunsMock: vi.fn(),
  getInboxMock: vi.fn(),
  refreshMock: vi.fn(),
  replaceMock: vi.fn(),
  updateWorkflowRunTitleMock: vi.fn(),
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

vi.mock("@/lib/api", () => ({
  archiveWorkflowRun: archiveWorkflowRunMock,
  getWorkflowRuns: getWorkflowRunsMock,
  getInbox: getInboxMock,
  updateWorkflowRunTitle: updateWorkflowRunTitleMock,
}));

import { UserConsoleSidebar } from "@/components/navigation/user-console-sidebar";

const runs = [
  {
    id: "run-chat",
    title: "Quarterly conversation",
    status: "Done",
    updatedAt: "2026-04-04T08:00:00Z",
    progressLabel: "Completed",
    kind: "chat",
  },
  {
    id: "run-playbook",
    title: "Security playbook review",
    status: "Needs Review",
    updatedAt: "2026-04-04T09:00:00Z",
    progressLabel: "Needs approval",
    kind: "playbook",
  },
  {
    id: "run-task",
    title: "Customer escalation task",
    status: "Blocked",
    updatedAt: "2026-04-04T10:00:00Z",
    progressLabel: "Blocked by policy",
    kind: "task",
  },
] as const;

const inbox = [
  {
    id: "inbox-1",
    runId: "run-chat",
    runName: "Quarterly conversation",
    artifactType: "summary",
    reason: "Conversation needs approval",
    queue: "Needs Approval",
  },
  {
    id: "inbox-2",
    runId: "run-task",
    runName: "Customer escalation task",
    artifactType: "brief",
    reason: "Task blocked by legal review",
    queue: "Blocked by Guardrails",
  },
] as const;

beforeEach(() => {
  archiveWorkflowRunMock.mockReset();
  getWorkflowRunsMock.mockReset();
  getInboxMock.mockReset();
  refreshMock.mockReset();
  replaceMock.mockReset();
  updateWorkflowRunTitleMock.mockReset();
  archiveWorkflowRunMock.mockResolvedValue(undefined);
  getWorkflowRunsMock.mockResolvedValue(runs);
  getInboxMock.mockResolvedValue(inbox);
  updateWorkflowRunTitleMock.mockImplementation(async (id: string, title: string) => {
    const run = runs.find((item) => item.id === id);
    return {
      ...run,
      id,
      title,
      title_source: "user",
    };
  });
});

describe("UserConsoleSidebar", () => {
  it("renders lightweight workspace navigation without session kind chips", async () => {
    render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId="run-playbook"
        expanded
        platformVersion={{
          current_version: "0.2.0",
          latest_version: "0.2.1",
          update_available: true,
          status: "update_available",
          install_mode: "wheel",
          update_command: "lattix update",
          checked_at: "2026-04-04T10:00:00Z",
          summary: "Update available.",
          release_notes_url: "",
          source: "",
        }}
      />,
    );

    expect(await screen.findByRole("link", { name: /^conversations$/i })).toHaveAttribute("href", "/inbox");
    expect(screen.getByRole("link", { name: /^workflows$/i })).toHaveAttribute("href", "/workflows/start");
    expect(screen.getByRole("link", { name: /^artifacts$/i })).toHaveAttribute("href", "/artifacts");

    expect(screen.getByRole("link", { name: /security playbook review/i })).toHaveAttribute("href", "/inbox?session=run-playbook");
    expect(screen.queryByText("chat")).not.toBeInTheDocument();
    expect(screen.queryByText("playbook")).not.toBeInTheDocument();
    expect(screen.queryByText("task")).not.toBeInTheDocument();
    expect(screen.getAllByText("1")).toHaveLength(2);
    expect(screen.getByText(/v0\.2\.0/i)).toBeInTheDocument();
    expect(screen.getByText(/update 0\.2\.1/i)).toBeInTheDocument();
  });

  it("filters the session list from the compact search box", async () => {
    render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId={null}
        expanded
        platformVersion={null}
      />,
    );

    expect(await screen.findByText("Quarterly conversation")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(/search sessions/i), { target: { value: "security" } });

    await waitFor(() => {
      expect(screen.queryByText("Quarterly conversation")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Security playbook review")).toBeInTheDocument();
    expect(screen.queryByText("Customer escalation task")).not.toBeInTheDocument();
  });

  it("renames a session from the sidebar", async () => {
    render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId="run-chat"
        expanded
        platformVersion={null}
      />,
    );

    expect(await screen.findByText("Quarterly conversation")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /more actions for quarterly conversation/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^rename$/i }));
    fireEvent.change(screen.getByPlaceholderText(/rename session/i), { target: { value: "Board prep thread" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(updateWorkflowRunTitleMock).toHaveBeenCalledWith("run-chat", "Board prep thread");
    });
    expect(screen.getByText("Board prep thread")).toBeInTheDocument();
  });

  it("opens session actions from a conversation right-click", async () => {
    render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId="run-chat"
        expanded
        platformVersion={null}
      />,
    );

    expect(await screen.findByText("Quarterly conversation")).toBeInTheDocument();
    fireEvent.contextMenu(screen.getByText("Quarterly conversation"));

    expect(screen.getByRole("menu", { name: /session actions/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^rename$/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /^archive$/i })).toBeInTheDocument();
  });

  it("archives the active session from the sidebar actions", async () => {
    render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId="run-chat"
        expanded
        platformVersion={null}
      />,
    );

    expect(await screen.findByText("Quarterly conversation")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /more actions for quarterly conversation/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^archive$/i }));

    await waitFor(() => {
      expect(archiveWorkflowRunMock).toHaveBeenCalledWith("run-chat");
    });
    await waitFor(() => {
      expect(screen.queryByText("Quarterly conversation")).not.toBeInTheDocument();
    });
    expect(replaceMock).toHaveBeenCalledWith("/inbox");
    expect(refreshMock).toHaveBeenCalled();
  });

  it("reloads sessions when the selected inbox session changes", async () => {
    const view = render(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId={null}
        expanded
        platformVersion={null}
      />,
    );

    await screen.findByText("Quarterly conversation");
    expect(getWorkflowRunsMock).toHaveBeenCalledTimes(1);
    expect(getInboxMock).toHaveBeenCalledTimes(1);

    view.rerender(
      <UserConsoleSidebar
        pathname="/inbox"
        selectedSessionId="run-chat"
        expanded
        platformVersion={null}
      />,
    );

    await waitFor(() => {
      expect(getWorkflowRunsMock).toHaveBeenCalledTimes(2);
      expect(getInboxMock).toHaveBeenCalledTimes(2);
    });
  });
});