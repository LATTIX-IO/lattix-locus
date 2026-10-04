import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
const refreshMock = vi.fn();

const {
  getOperatorSessionMock,
  getPlatformHealthDetailsMock,
  getPlatformSettingsMock,
  getPlatformVersionStatusMock,
  getWorkflowRunsMock,
  getInboxMock,
  logoutOperatorMock,
  pathnameState,
  searchParamsState,
} = vi.hoisted(() => ({
  getOperatorSessionMock: vi.fn(),
  getPlatformHealthDetailsMock: vi.fn(),
  getPlatformSettingsMock: vi.fn(),
  getPlatformVersionStatusMock: vi.fn(),
  getWorkflowRunsMock: vi.fn(),
  getInboxMock: vi.fn(),
  logoutOperatorMock: vi.fn(),
  pathnameState: { current: "/home" },
  searchParamsState: { current: new URLSearchParams() },
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => <a href={href} {...props}>{children}</a>,
}));

vi.mock("next/navigation", () => ({
  usePathname: () => pathnameState.current,
  useSearchParams: () => searchParamsState.current,
  useRouter: () => ({
    replace: replaceMock,
    refresh: refreshMock,
  }),
}));

vi.mock("@/components/api-status-banner", () => ({
  ApiStatusBanner: () => <div data-testid="api-status-banner" />,
}));

vi.mock("@/components/first-run-wizard", () => ({
  FirstRunWizard: () => null,
}));

vi.mock("@/lib/api", () => ({
  PLATFORM_SETTINGS_UPDATED_EVENT: "locus:platform-settings-updated",
  getOperatorSession: getOperatorSessionMock,
  getPlatformHealthDetails: getPlatformHealthDetailsMock,
  getPlatformSettings: getPlatformSettingsMock,
  getPlatformVersionStatus: getPlatformVersionStatusMock,
  getWorkflowRuns: getWorkflowRunsMock,
  getInbox: getInboxMock,
  logoutOperator: logoutOperatorMock,
}));

import { AppShell } from "@/components/app-shell";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const guestSession = {
  authenticated: false,
  actor: "guest",
  principal_id: "guest",
  principal_type: "user",
  display_name: "Guest",
  subject: "guest",
  roles: [],
  auth_mode: "jwt",
  provider: "casdoor",
  capabilities: { can_admin: false, can_builder: false },
  allowed_modes: ["user"],
  default_mode: "user",
  oidc: { configured: true, issuer: "http://casdoor.localhost", audience: "locus-ui", provider: "casdoor", validation_error: "" },
} as const;

const operatorSession = {
  authenticated: true,
  actor: "locus-admin",
  principal_id: "locus-admin",
  principal_type: "user",
  display_name: "Locus Admin",
  subject: "locus-admin",
  roles: ["builder-admin"],
  auth_mode: "oidc",
  provider: "casdoor",
  capabilities: { can_admin: true, can_builder: true },
  allowed_modes: ["user", "builder"],
  default_mode: "builder",
  oidc: { configured: true, issuer: "http://casdoor.localhost", audience: "locus-ui", provider: "casdoor", validation_error: "" },
} as const;

const currentVersion = {
  current_version: "0.1.0",
  latest_version: "0.1.0",
  update_available: false,
  status: "up_to_date",
  install_mode: "wheel",
  update_command: "lattix update",
  release_notes_url: "",
  checked_at: "2026-03-26T00:00:00Z",
  source: "",
  summary: "Your local app is up to date.",
} as const;

const healthyPlatform = {
  status: "ok",
  timestamp: "2026-03-26T00:00:00Z",
  postgres: "connected",
  redis: "disabled",
  long_term_memory: "disabled",
  memory_consolidation: "disabled",
  memory_hybrid_retrieval: "disabled",
  memory_world_graph: "disabled",
  neo4j: "disabled",
} as const;

const userSidebarRuns = [
  {
    id: "run-1",
    title: "Quarterly review",
    status: "Done",
    updatedAt: "2026-03-26T00:00:00Z",
    progressLabel: "Completed",
    kind: "workflow",
  },
] as const;

const userSidebarInbox = [
  {
    id: "inbox-1",
    runId: "run-1",
    runName: "Quarterly review",
    artifactType: "summary",
    reason: "Needs approval",
    queue: "Needs Approval",
  },
] as const;

beforeEach(() => {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: 1280,
  });
  replaceMock.mockReset();
  refreshMock.mockReset();
  logoutOperatorMock.mockReset();
  getOperatorSessionMock.mockReset();
  getPlatformHealthDetailsMock.mockReset();
  getPlatformSettingsMock.mockReset();
  getPlatformVersionStatusMock.mockReset();
  getWorkflowRunsMock.mockReset();
  getInboxMock.mockReset();
  searchParamsState.current = new URLSearchParams();
  pathnameState.current = "/home";
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;

  getWorkflowRunsMock.mockResolvedValue(userSidebarRuns);
  getInboxMock.mockResolvedValue(userSidebarInbox);
  logoutOperatorMock.mockResolvedValue({ ok: true });
  getPlatformHealthDetailsMock.mockResolvedValue(healthyPlatform);
  getPlatformSettingsMock.mockResolvedValue({
    console_classification_banner_enabled: true,
    console_classification_banner_text: "Internal • Operational Console",
    console_classification_banner_background_color: "#2e2a28",
    console_classification_banner_text_color: "#e7dcc0",
  });
});

function enterDesktopShell() {
  (window as unknown as { __TAURI__?: unknown }).__TAURI__ = {
    core: { invoke: vi.fn(() => Promise.resolve(null)) },
  };
}

describe("AppShell (web profile)", () => {
  it("redirects a visitor without a session to /auth without rendering protected content", async () => {
    getOperatorSessionMock.mockResolvedValue(guestSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>protected child</div></AppShell>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/auth"));
    expect(screen.queryByText(/protected child/i)).not.toBeInTheDocument();
    expect(screen.getByText(/redirecting to sign in/i)).toBeInTheDocument();
  });

  it("sends a signed-in operator from /auth to Home", async () => {
    pathnameState.current = "/auth";
    getOperatorSessionMock.mockResolvedValue(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>auth child</div></AppShell>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/home"));
  });

  it("keeps /auth reachable without a skip link for a signed-out visitor", async () => {
    pathnameState.current = "/auth";
    getOperatorSessionMock.mockResolvedValue(guestSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>auth child</div></AppShell>);

    expect(await screen.findByText(/auth child/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /skip to content/i })).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("shows one navigation with no mode switch, workspace switcher or role gating", async () => {
    pathnameState.current = "/library/workflows";
    getOperatorSessionMock.mockResolvedValue({ ...operatorSession, capabilities: { can_admin: false, can_builder: false } });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>library child</div></AppShell>);

    const primary = await screen.findByRole("navigation", { name: /primary/i });
    const labels = Array.from(primary.querySelectorAll(":scope > ul > li > a")).map((link) => link.textContent);
    expect(labels).toEqual(["Home", "Activity", "Memory", "Library", "Settings"]);
    expect(screen.getByRole("link", { name: /^settings$/i })).toHaveAttribute("href", "/settings");
    // Library is the active area: its pages are listed under it.
    expect(screen.getByRole("link", { name: /^workflows$/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /^skills$/i })).toHaveAttribute("href", "/library/skills");
    expect(screen.getByText(/library child/i)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: /mode switch/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/lattix corporation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/builder access/i)).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("lists sessions under Activity and keeps the sidebar under the header", async () => {
    pathnameState.current = "/activity";
    searchParamsState.current = new URLSearchParams("session=run-1");
    getOperatorSessionMock.mockResolvedValue(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>activity child</div></AppShell>);

    const session = await screen.findByRole("link", { name: /quarterly review/i });
    expect(session).toHaveAttribute("href", "/activity?session=run-1");
    expect(session.closest("aside")).toHaveClass("z-[70]");
    expect(screen.getByRole("link", { name: /^runs$/i })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: /toggle sidebar/i }).closest("header")).toHaveClass("z-[80]");
  });

  it("renders the configured classification banner and follows saved settings", async () => {
    getOperatorSessionMock.mockResolvedValue(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getPlatformSettingsMock.mockResolvedValue({
      console_classification_banner_enabled: true,
      console_classification_banner_text: "Confidential • Red Team Console",
      console_classification_banner_background_color: "#7f1d1d",
      console_classification_banner_text_color: "#fef2f2",
    });

    render(<AppShell><div>child</div></AppShell>);
    expect(await screen.findByText(/confidential • red team console/i)).toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("locus:platform-settings-updated", {
          detail: { console_classification_banner_enabled: true, console_classification_banner_text: "Restricted • Incident Console" },
        }),
      );
    });

    expect(await screen.findByText(/restricted • incident console/i)).toBeInTheDocument();
  });

  it("shows the operator and Sign out in the account menu, without role badges", async () => {
    getOperatorSessionMock.mockResolvedValue({
      ...operatorSession,
      display_name: "James Booth",
      email: "james@locus.localhost",
      roles: ["builder-admin", "member"],
    });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>child</div></AppShell>);

    const menuButton = await screen.findByRole("button", { name: /account menu/i });
    expect(menuButton).toHaveTextContent("JB");
    fireEvent.click(menuButton);

    expect(await screen.findByText("James Booth")).toBeInTheDocument();
    expect(screen.getByText("james@locus.localhost")).toBeInTheDocument();
    expect(screen.queryByText("builder-admin")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(logoutOperatorMock).toHaveBeenCalled());
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/auth"));
  });

  it("does not let a stale session request resolve a later navigation", async () => {
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    const firstRequest = deferred<typeof guestSession>();
    const secondRequest = deferred<typeof operatorSession>();
    getOperatorSessionMock
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(() => secondRequest.promise);

    const view = render(<AppShell><div>child</div></AppShell>);
    pathnameState.current = "/memory";
    view.rerender(<AppShell><div>child</div></AppShell>);

    secondRequest.resolve(operatorSession);
    expect(await screen.findByText("child")).toBeInTheDocument();

    firstRequest.resolve(guestSession);
    await waitFor(() => expect(replaceMock).not.toHaveBeenCalled());
  });

  it("shows a degraded DB badge when the backend reports postgres issues", async () => {
    getOperatorSessionMock.mockResolvedValue(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getPlatformHealthDetailsMock.mockResolvedValue({ ...healthyPlatform, postgres: "error", postgres_reason: "connection refused" });

    render(<AppShell><div>child</div></AppShell>);

    expect(await screen.findByText(/^db degraded$/i)).toBeInTheDocument();
  });
});

describe("AppShell (desktop app)", () => {
  it("never shows /auth: it lands on Home", async () => {
    enterDesktopShell();
    pathnameState.current = "/auth";
    getOperatorSessionMock.mockResolvedValue(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>auth child</div></AppShell>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/home"));
    expect(screen.queryByText(/auth child/i)).not.toBeInTheDocument();
  });

  it("hides sign-out, account and org UI and the classification banner", async () => {
    enterDesktopShell();
    getOperatorSessionMock.mockResolvedValue({ ...operatorSession, roles: ["builder-admin"] });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>home child</div></AppShell>);

    expect(await screen.findByText(/home child/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /account menu/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/sign out/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/internal • operational console/i)).not.toBeInTheDocument();
    expect(screen.queryByText("builder-admin")).not.toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("says the backend is unreachable (and retries) instead of sending you to /auth", async () => {
    enterDesktopShell();
    getOperatorSessionMock.mockRejectedValueOnce(new Error("connect ECONNREFUSED")).mockResolvedValueOnce(operatorSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>home child</div></AppShell>);

    expect(await screen.findByRole("alert")).toHaveTextContent(/can't reach the locus backend/i);
    expect(replaceMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/home child/i)).toBeInTheDocument();
  });
});
