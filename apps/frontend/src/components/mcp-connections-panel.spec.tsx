import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MCPConnectionDefinition, MCPStarterTemplate } from "@/types/frontier";

const {
  approveMcpConnectionMock,
  getMcpConnectionsMock,
  getMcpStarterTemplatesMock,
  saveMcpConnectionMock,
  validateMcpConnectionMock,
} = vi.hoisted(() => ({
  approveMcpConnectionMock: vi.fn(),
  getMcpConnectionsMock: vi.fn(),
  getMcpStarterTemplatesMock: vi.fn(),
  saveMcpConnectionMock: vi.fn(),
  validateMcpConnectionMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  approveMcpConnection: approveMcpConnectionMock,
  getMcpConnections: getMcpConnectionsMock,
  getMcpStarterTemplates: getMcpStarterTemplatesMock,
  saveMcpConnection: saveMcpConnectionMock,
  validateMcpConnection: validateMcpConnectionMock,
}));

import { McpConnectionsPanel } from "@/components/mcp-connections-panel";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const starterTemplates: MCPStarterTemplate[] = [
  {
    id: "starter-github",
    wave: 1,
    name: "GitHub Starter",
    summary: "GitHub MCP starter",
    transport: "streamable_http",
    auth_type: "bearer",
    secret_ref: "secret/mcp/github",
    capabilities: ["/repo-read"],
    permission_scopes: ["repo:read"],
    data_access: ["source-code-metadata"],
    egress_allowlist: ["api.github.com"],
    publisher: "first_party",
    execution_mode: "local",
  },
  {
    id: "starter-linear",
    wave: 1,
    name: "Linear Starter",
    summary: "Linear MCP starter",
    transport: "streamable_http",
    auth_type: "bearer",
    secret_ref: "secret/mcp/linear",
    capabilities: ["/issue-triage"],
    permission_scopes: ["issues:read"],
    data_access: ["issue-data"],
    egress_allowlist: ["api.linear.app"],
    publisher: "first_party",
    execution_mode: "local",
  },
];

const connections: MCPConnectionDefinition[] = [
  {
    id: "mcp-1",
    starter_id: "starter-linear",
    wave: 1,
    name: "Existing Linear Connection",
    status: "draft",
    server_url: "http://localhost:7071/mcp/linear",
    transport: "streamable_http",
    auth_type: "bearer",
    secret_ref: "secret/mcp/linear/custom",
    secret_configured: true,
    capabilities: ["/issue-triage"],
    permission_scopes: ["issues:read"],
    data_access: ["issue-data"],
    egress_allowlist: ["api.linear.app"],
    publisher: "first_party",
    execution_mode: "local",
  },
];

describe("McpConnectionsPanel", () => {
  beforeEach(() => {
    approveMcpConnectionMock.mockReset();
    getMcpConnectionsMock.mockReset();
    getMcpStarterTemplatesMock.mockReset();
    saveMcpConnectionMock.mockReset();
    validateMcpConnectionMock.mockReset();

    getMcpConnectionsMock.mockResolvedValue(connections);
    approveMcpConnectionMock.mockResolvedValue({ ok: true });
    saveMcpConnectionMock.mockResolvedValue({ ok: true });
    validateMcpConnectionMock.mockResolvedValue({
      ok: true,
      id: "mcp-1",
      status: "validated",
      validation: {
        ok: true,
        errors: [],
        warnings: [],
        checked_server_url: "http://localhost:7071/mcp/linear",
      },
    });
    getMcpStarterTemplatesMock.mockResolvedValue(starterTemplates);
  });

  it("renders a connection load error instead of the empty state when fetching saved connections fails", async () => {
    getMcpConnectionsMock.mockRejectedValueOnce(new Error("Unable to load staged MCP connections."));

    render(<McpConnectionsPanel />);

    expect(await screen.findByText("Unable to load staged MCP connections.")).toBeInTheDocument();
    expect(screen.queryByText("No staged MCP connections configured yet.")).not.toBeInTheDocument();
  });

  it("does not overwrite edit state when starter templates resolve after editing begins", async () => {
    const startersRequest = deferred<MCPStarterTemplate[]>();
    getMcpStarterTemplatesMock.mockImplementation(() => startersRequest.promise);

    render(<McpConnectionsPanel />);

    expect(await screen.findByText("Existing Linear Connection")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

    expect(screen.getByLabelText(/mcp name/i)).toHaveValue("Existing Linear Connection");
    expect(screen.getByRole("button", { name: /cancel edit/i })).toBeInTheDocument();

    await act(async () => {
      startersRequest.resolve(starterTemplates);
    });

    await waitFor(() => {
      expect(screen.getByLabelText(/mcp name/i)).toHaveValue("Existing Linear Connection");
      expect(screen.getByLabelText(/mcp starter/i)).toHaveValue("starter-linear");
      expect(screen.getByRole("button", { name: /cancel edit/i })).toBeInTheDocument();
    });

    expect(screen.queryByText(/github starter mcp starter loaded\./i)).not.toBeInTheDocument();
  });
});