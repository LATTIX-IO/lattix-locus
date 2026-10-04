import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getRunEscalations: vi.fn(),
  approveRunEscalation: vi.fn(),
  denyRunEscalation: vi.fn(),
  allowSiteInBrowserTier: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...api,
    DesktopConfirmationCancelledError: actual.DesktopConfirmationCancelledError,
    SecurityChangeConfirmationRequired: actual.SecurityChangeConfirmationRequired,
  };
});

import { RunEscalations } from "@/components/run-escalations";
import { DesktopConfirmationCancelledError } from "@/lib/desktop-confirmation";

const BROWSER_ASK = {
  id: "esc-1",
  kind: "gateway",
  path: "https://mail.example.com/compose",
  workspace_root: "",
  policy: "ask",
  status: "pending",
  action_kind: "user_browser_act",
  tool: "user_browser.click",
  risk: "R2",
  site: "example.com",
  browser_tier: "trusted",
  site_list: "granted_sites" as const,
};

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.getRunEscalations.mockResolvedValue([BROWSER_ASK]);
  api.approveRunEscalation.mockResolvedValue({ ok: true });
  api.denyRunEscalation.mockResolvedValue({ ok: true, escalation: { ...BROWSER_ASK, status: "denied" } });
  api.allowSiteInBrowserTier.mockResolvedValue({ tier: "trusted", granted_sites: ["example.com"] });
});

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

async function request() {
  render(<RunEscalations runId="run-1" />);
  return screen.findByRole("article", { name: /click or type on example\.com/i });
}

describe("RunEscalations", () => {
  it("offers Allow once, Always allow on the site, and Deny for a user-browser ask", async () => {
    const item = await request();
    expect(within(item).getByRole("button", { name: "Allow once" })).toBeInTheDocument();
    expect(within(item).getByRole("button", { name: /^always allow on example\.com/i })).toBeInTheDocument();
    expect(within(item).getByRole("button", { name: "Deny" })).toBeInTheDocument();
  });

  it("allows once without touching the browser tier", async () => {
    const item = await request();
    api.getRunEscalations.mockResolvedValue([{ ...BROWSER_ASK, status: "approved" }]);
    fireEvent.click(within(item).getByRole("button", { name: "Allow once" }));

    await waitFor(() => expect(api.approveRunEscalation).toHaveBeenCalledWith("run-1", "esc-1"));
    expect(api.allowSiteInBrowserTier).not.toHaveBeenCalled();
    // The reload settles the request; its outcome stays visible.
    await waitFor(() => expect(screen.queryByRole("article")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent(/allowed once/i);
  });

  it("on the web, shows the risk, then adds the site to the tier's list and allows the action", async () => {
    const item = await request();
    fireEvent.click(within(item).getByRole("button", { name: /^always allow on example\.com/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/trusted grant list/i);
    expect(dialog).toHaveTextContent(/irreversible actions still ask/i);
    expect(api.allowSiteInBrowserTier).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Always allow on example.com" }));

    await waitFor(() => expect(api.approveRunEscalation).toHaveBeenCalledWith("run-1", "esc-1"));
    expect(api.allowSiteInBrowserTier).toHaveBeenCalledWith("example.com", "granted_sites", { acknowledgeRisk: true });
    expect(api.allowSiteInBrowserTier.mock.invocationCallOrder[0]).toBeLessThan(
      api.approveRunEscalation.mock.invocationCallOrder[0],
    );
  });

  it("on the desktop, leaves the confirmation to the shell and stops when it is cancelled", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } };
    api.allowSiteInBrowserTier.mockRejectedValueOnce(new DesktopConfirmationCancelledError());
    const item = await request();
    fireEvent.click(within(item).getByRole("button", { name: /^always allow on example\.com/i }));

    expect(await within(item).findByRole("alert")).toHaveTextContent(/cancelled in the confirmation dialog/i);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.allowSiteInBrowserTier).toHaveBeenCalledWith("example.com", "granted_sites", { acknowledgeRisk: false });
    expect(api.approveRunEscalation).not.toHaveBeenCalled();
  });

  it("denies without allowing anything", async () => {
    const item = await request();
    fireEvent.click(within(item).getByRole("button", { name: "Deny" }));

    await waitFor(() => expect(api.denyRunEscalation).toHaveBeenCalledWith("run-1", "esc-1"));
    expect(api.approveRunEscalation).not.toHaveBeenCalled();
    expect(api.allowSiteInBrowserTier).not.toHaveBeenCalled();
  });

  it("offers no site option when the tier has no list for it", async () => {
    api.getRunEscalations.mockResolvedValue([{ ...BROWSER_ASK, browser_tier: "strict", site_list: null }]);
    const item = await request();
    expect(within(item).queryByRole("button", { name: /always allow/i })).not.toBeInTheDocument();
  });

  it("renders nothing when no request waits", async () => {
    api.getRunEscalations.mockResolvedValue([{ ...BROWSER_ASK, status: "approved" }]);
    const { container } = render(<RunEscalations runId="run-1" />);
    await waitFor(() => expect(api.getRunEscalations).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
