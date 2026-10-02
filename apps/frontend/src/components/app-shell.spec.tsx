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
  pathnameState: { current: "/inbox" },
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

const builderSession = {
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

const updateAvailableVersion = {
  ...currentVersion,
  latest_version: "0.1.1",
  update_available: true,
  status: "update_available",
  release_notes_url: "https://github.com/LATTIX-IO/lattix-locus",
  summary: "Version 0.1.1 is available.",
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
  pathnameState.current = "/inbox";

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

describe("AppShell", () => {
  it("redirects unauthenticated users away from protected routes without rendering protected content", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue(guestSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>protected child</div></AppShell>);

    expect(screen.queryByText(/protected child/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/login required/i)).toBeInTheDocument();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/auth"));
  });

  it("redirects authenticated non-builders away from builder routes", async () => {
    pathnameState.current = "/builder/workflows";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getOperatorSessionMock.mockResolvedValue({
      authenticated: true,
      actor: "member-user",
      principal_id: "member-user",
      principal_type: "user",
      display_name: "Member User",
      subject: "member-user",
      roles: ["member"],
      auth_mode: "jwt",
      provider: "casdoor",
      capabilities: { can_admin: false, can_builder: false },
      allowed_modes: ["user"],
      default_mode: "user",
      oidc: { configured: true, issuer: "http://casdoor.localhost", audience: "locus-ui", provider: "casdoor", validation_error: "" },
    });

    render(<AppShell><div>builder child</div></AppShell>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/inbox"));
  });

  it("shows builder navigation when the operator session allows builder mode", async () => {
    pathnameState.current = "/builder/workflows";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue(builderSession);
    getPlatformVersionStatusMock.mockResolvedValue(updateAvailableVersion);

    render(<AppShell><div>builder child</div></AppShell>);

    expect(await screen.findByText(/workflow studio/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^settings$/i })).toHaveAttribute("href", "/builder/settings");
    expect(screen.getByText(/v0\.1\.0\s*→\s*v0\.1\.1/i)).toBeInTheDocument();
    expect(screen.getByText(/update available/i)).toBeInTheDocument();
    expect(screen.getByText(/lattix update/i)).toBeInTheDocument();
    expect(screen.getByText(/internal • operational console/i)).toBeInTheDocument();
    expect(screen.getByText(/builder child/i)).toBeInTheDocument();
    expect(screen.getByText(/^db ok$/i)).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("renders the configured classification banner and never shows the removed soft launch strip", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getPlatformSettingsMock.mockResolvedValue({
      console_classification_banner_enabled: true,
      console_classification_banner_text: "Confidential • Red Team Console",
      console_classification_banner_background_color: "#7f1d1d",
      console_classification_banner_text_color: "#fef2f2",
    });

    render(<AppShell><div>user child</div></AppShell>);

    expect(await screen.findByText(/confidential • red team console/i)).toBeInTheDocument();
    expect(screen.queryByText(/soft launch in progress/i)).not.toBeInTheDocument();
  });

  it("updates the classification banner when platform settings are saved", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getPlatformSettingsMock.mockResolvedValue({
      console_classification_banner_enabled: true,
      console_classification_banner_text: "Internal • Operational Console",
      console_classification_banner_background_color: "#2e2a28",
      console_classification_banner_text_color: "#e7dcc0",
    });

    render(<AppShell><div>user child</div></AppShell>);
    expect(await screen.findByText(/internal • operational console/i)).toBeInTheDocument();

    await act(async () => {
      window.dispatchEvent(new CustomEvent("locus:platform-settings-updated", {
        detail: {
          console_classification_banner_enabled: true,
          console_classification_banner_text: "Restricted • Incident Console",
          console_classification_banner_background_color: "#1d4ed8",
          console_classification_banner_text_color: "#eff6ff",
        },
      }));
    });

    expect(await screen.findByText(/restricted • incident console/i)).toBeInTheDocument();
    expect(screen.queryByText(/internal • operational console/i)).not.toBeInTheDocument();
  });

  it("shows user navigation with shared settings destination", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });

    render(<AppShell><div>user child</div></AppShell>);

    expect(await screen.findByRole("link", { name: /^workflows$/i })).toHaveAttribute("href", "/workflows/start");
    expect(screen.getByRole("link", { name: /^preferences$/i })).toHaveAttribute("href", "/settings");
    expect(screen.getByText(/user child/i)).toBeInTheDocument();
  });

  it("keeps the fixed user sidebar above inbox workspace content", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams("session=run-1");
    replaceMock.mockReset();
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });

    render(<AppShell><div data-testid="session-workspace">session workspace</div></AppShell>);

    const conversationsLink = await screen.findByRole("link", { name: /^conversations$/i });
    expect(conversationsLink).toHaveAttribute("href", "/inbox");
    expect(conversationsLink.closest("aside")).toHaveClass("z-[70]");

    const toggleSidebarButton = screen.getByRole("button", { name: /toggle sidebar/i });
    expect(toggleSidebarButton.closest("header")).toHaveClass("z-[80]");
  });

  it("does not let a stale session request resolve a later navigation", async () => {
    pathnameState.current = "/builder/workflows";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    const firstRequest = deferred<typeof guestSession>();
    const secondRequest = deferred<typeof builderSession>();

    getOperatorSessionMock
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(() => secondRequest.promise);

    const view = render(<AppShell><div>builder child</div></AppShell>);

    pathnameState.current = "/builder/agents";
    view.rerender(<AppShell><div>builder child</div></AppShell>);

    secondRequest.resolve(builderSession);

    expect(await screen.findByText(/agent studio/i)).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();

    firstRequest.resolve(guestSession);

    await waitFor(() => expect(replaceMock).not.toHaveBeenCalled());
  });

  it("does not expose a skip-to-console link on auth routes", async () => {
    pathnameState.current = "/auth";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue(guestSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>auth child</div></AppShell>);

    expect(await screen.findByText(/auth child/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /skip to console/i })).not.toBeInTheDocument();
  });

  it("redirects authenticated operators away from the public auth route", async () => {
    pathnameState.current = "/auth";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue(builderSession);
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>auth child</div></AppShell>);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/inbox"));
  });

  it("shows the resolved operator identity and builder access in the user menu", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      display_name: "James Booth",
      email: "james@locus.localhost",
      preferred_username: "james",
      roles: ["builder-admin", "member"],
      default_mode: "user",
    });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);

    render(<AppShell><div>user child</div></AppShell>);

    expect(await screen.findByText(/user child/i)).toBeInTheDocument();
    const menuButton = screen.getByRole("button", { name: /user menu/i });
    expect(menuButton).toHaveTextContent("JB");
    fireEvent.click(menuButton);

    await waitFor(() => expect(screen.getByText(/signed in as/i)).toBeInTheDocument());
    expect(screen.getByText("James Booth")).toBeInTheDocument();
    expect(screen.getByText("james@locus.localhost")).toBeInTheDocument();
    expect(screen.getByText(/builder access enabled/i)).toBeInTheDocument();
    expect(screen.getByText("builder-admin")).toBeInTheDocument();
  });

  it("does not claim the current build is up to date when version status is unavailable", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });
    getPlatformVersionStatusMock.mockRejectedValue(new Error("version lookup failed"));

    render(<AppShell><div>user child</div></AppShell>);

    expect(await screen.findByText(/unchecked/i)).toBeInTheDocument();
    expect(screen.queryByText(/current/i)).not.toBeInTheDocument();
  });

  it("shows a degraded db badge when the backend health endpoint reports postgres issues", async () => {
    pathnameState.current = "/inbox";
    searchParamsState.current = new URLSearchParams();
    replaceMock.mockReset();
    getOperatorSessionMock.mockResolvedValue({
      ...builderSession,
      default_mode: "user",
    });
    getPlatformVersionStatusMock.mockResolvedValue(currentVersion);
    getPlatformHealthDetailsMock.mockResolvedValue({
      ...healthyPlatform,
      postgres: "error",
      postgres_reason: "connection refused",
    });

    render(<AppShell><div>user child</div></AppShell>);

    expect(await screen.findByText(/^db degraded$/i)).toBeInTheDocument();
  });
});
