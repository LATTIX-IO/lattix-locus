import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getIntegrationCatalog: vi.fn(),
  getIntegrations: vi.fn(),
  installCatalogIntegration: vi.fn(),
  connectIntegrationOAuth: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...api, DesktopConfirmationCancelledError: actual.DesktopConfirmationCancelledError, SecurityChangeConfirmationRequired: actual.SecurityChangeConfirmationRequired };
});

import { CATALOG_OAUTH_RETURN_PATH, ConnectionCatalog } from "@/components/connection-catalog";
import { DesktopConfirmationCancelledError, DesktopConfirmationError } from "@/lib/desktop-confirmation";

const GITHUB = {
  catalog_id: "mcp-github",
  name: "GitHub MCP",
  type: "custom",
  auth_type: "bearer",
  base_url: "https://api.githubcopilot.com/mcp/",
  publisher: "third_party",
  capabilities: ["repos", "issues"],
  egress_allowlist: ["api.githubcopilot.com"],
  metadata_json: { protocol: "mcp" },
  installed: false,
};
const LINEAR = {
  ...GITHUB,
  catalog_id: "mcp-linear",
  name: "Linear MCP",
  auth_type: "oauth2",
  capabilities: ["issues", "projects"],
  egress_allowlist: ["mcp.linear.app"],
};

const assignMock = vi.fn();
const originalLocation = window.location;

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  api.getIntegrationCatalog.mockResolvedValue([GITHUB, LINEAR]);
  api.getIntegrations.mockResolvedValue([]);
  assignMock.mockReset();
  Object.defineProperty(window, "location", { configurable: true, value: { ...originalLocation, assign: assignMock } });
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
});

async function openReview(name: string) {
  render(<ConnectionCatalog />);
  const card = (await screen.findByText(name)).closest("li") as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: "Add" }));
  return screen.findByRole("dialog");
}

describe("ConnectionCatalog", () => {
  it("shows what an entry will access and how it signs in before adding", async () => {
    const dialog = await openReview("GitHub MCP");

    expect(dialog).toHaveTextContent(/repos, issues/);
    expect(dialog).toHaveTextContent(/api\.githubcopilot\.com/);
    expect(dialog).toHaveTextContent(/access token/i);
    expect(api.installCatalogIntegration).not.toHaveBeenCalled();
  });

  it("adds a token entry, says what is left, and refreshes the status", async () => {
    api.installCatalogIntegration.mockResolvedValue({ ok: true, id: "int-1", already_installed: false });
    const dialog = await openReview("GitHub MCP");
    api.getIntegrations.mockResolvedValue([
      { id: "int-1", name: "GitHub MCP", type: "custom", status: "draft", base_url: "", auth_type: "bearer", secret_ref: "", metadata_json: { catalog_id: "mcp-github" } },
    ]);

    fireEvent.click(within(dialog).getByRole("button", { name: /add github mcp/i }));

    await waitFor(() => expect(api.installCatalogIntegration).toHaveBeenCalledWith("mcp-github"));
    expect(await within(dialog).findByText(/name its credential secret in advanced/i)).toBeInTheDocument();
    expect(api.connectIntegrationOAuth).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    expect(await screen.findByLabelText("GitHub MCP: Needs a credential")).toBeInTheDocument();
  });

  it("opens the provider sign-in for an OAuth server after adding it", async () => {
    api.installCatalogIntegration.mockResolvedValue({ ok: true, id: "int-2", already_installed: false });
    api.connectIntegrationOAuth.mockResolvedValue({
      ok: true,
      mode: "authorization_code",
      connect_url: "https://mcp.linear.app/authorize?state=x",
      status: { connected: false, pending: true },
    });
    const dialog = await openReview("Linear MCP");

    fireEvent.click(within(dialog).getByRole("button", { name: /add and sign in/i }));

    await waitFor(() => expect(api.connectIntegrationOAuth).toHaveBeenCalledWith("int-2", { return_to: CATALOG_OAUTH_RETURN_PATH }));
    expect(assignMock).toHaveBeenCalledWith("https://mcp.linear.app/authorize?state=x");
  });

  it("says an OAuth server needs an OAuth app when the backend has none configured", async () => {
    api.installCatalogIntegration.mockResolvedValue({ ok: true, id: "int-2", already_installed: false });
    api.connectIntegrationOAuth.mockRejectedValue(new Error('Request failed (400): {"detail":"oauth2 auth metadata is missing"}'));
    const dialog = await openReview("Linear MCP");

    fireEvent.click(within(dialog).getByRole("button", { name: /add and sign in/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/needs an oauth app/i);
    expect(assignMock).not.toHaveBeenCalled();
  });

  it("explains that a catalog OAuth integration needs a client ID", async () => {
    api.installCatalogIntegration.mockResolvedValue({
      ok: true,
      id: "int-2",
      already_installed: false,
    });
    api.connectIntegrationOAuth.mockRejectedValue(
      new Error('Request failed (400): {"detail":"oauth2 client_id is required"}'),
    );
    const dialog = await openReview("Linear MCP");

    fireEvent.click(within(dialog).getByRole("button", { name: /add and sign in/i }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/needs an oauth app/i);
    expect(assignMock).not.toHaveBeenCalled();
  });

  it("shows a cancelled or failed desktop confirmation instead of doing nothing", async () => {
    api.installCatalogIntegration.mockRejectedValueOnce(new DesktopConfirmationCancelledError());
    const dialog = await openReview("GitHub MCP");

    fireEvent.click(within(dialog).getByRole("button", { name: /add github mcp/i }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/cancelled in the confirmation dialog/i);

    fireEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    api.installCatalogIntegration.mockRejectedValueOnce(new DesktopConfirmationError("Command confirm_action not allowed by ACL"));
    const again = await openReview("GitHub MCP");
    fireEvent.click(within(again).getByRole("button", { name: /add github mcp/i }));
    expect(await within(again).findByRole("alert")).toHaveTextContent(/not allowed by acl/i);
  });

  it("shows a retryable error when the catalog cannot load", async () => {
    api.getIntegrationCatalog.mockRejectedValueOnce(new Error("Request failed (500)"));
    render(<ConnectionCatalog />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/request failed \(500\)/i);
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText("GitHub MCP")).toBeInTheDocument();
  });
});
