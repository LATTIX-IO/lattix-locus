import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { IntegrationsManager } from "@/components/integrations-manager";

const {
  approveMcpConnectionMock,
  connectIntegrationOAuthMock,
  disconnectIntegrationOAuthMock,
  getIntegrationStarterTemplatesMock,
  getIntegrationOAuthStatusMock,
  getIntegrationsMock,
  getMcpConnectionsMock,
  getMcpStarterTemplatesMock,
  refreshIntegrationOAuthMock,
  saveMcpConnectionMock,
  saveIntegrationMock,
  testIntegrationMock,
  validateMcpConnectionMock,
  deleteIntegrationMock,
} = vi.hoisted(() => ({
  approveMcpConnectionMock: vi.fn(),
  connectIntegrationOAuthMock: vi.fn(),
  disconnectIntegrationOAuthMock: vi.fn(),
  getIntegrationStarterTemplatesMock: vi.fn(),
  getIntegrationOAuthStatusMock: vi.fn(),
  getIntegrationsMock: vi.fn(),
  getMcpConnectionsMock: vi.fn(),
  getMcpStarterTemplatesMock: vi.fn(),
  refreshIntegrationOAuthMock: vi.fn(),
  saveMcpConnectionMock: vi.fn(),
  saveIntegrationMock: vi.fn(),
  testIntegrationMock: vi.fn(),
  validateMcpConnectionMock: vi.fn(),
  deleteIntegrationMock: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  approveMcpConnection: approveMcpConnectionMock,
  connectIntegrationOAuth: connectIntegrationOAuthMock,
  getIntegrationStarterTemplates: getIntegrationStarterTemplatesMock,
  getIntegrationOAuthStatus: getIntegrationOAuthStatusMock,
  getIntegrations: getIntegrationsMock,
  getMcpConnections: getMcpConnectionsMock,
  getMcpStarterTemplates: getMcpStarterTemplatesMock,
  refreshIntegrationOAuth: refreshIntegrationOAuthMock,
  saveMcpConnection: saveMcpConnectionMock,
  saveIntegration: saveIntegrationMock,
  testIntegration: testIntegrationMock,
  validateMcpConnection: validateMcpConnectionMock,
  deleteIntegration: deleteIntegrationMock,
  disconnectIntegrationOAuth: disconnectIntegrationOAuthMock,
}));

describe("IntegrationsManager", () => {
  beforeEach(() => {
    approveMcpConnectionMock.mockReset();
    connectIntegrationOAuthMock.mockReset();
    disconnectIntegrationOAuthMock.mockReset();
    getIntegrationStarterTemplatesMock.mockReset();
    getIntegrationOAuthStatusMock.mockReset();
    getIntegrationsMock.mockReset();
    getMcpConnectionsMock.mockReset();
    getMcpStarterTemplatesMock.mockReset();
    refreshIntegrationOAuthMock.mockReset();
    saveMcpConnectionMock.mockReset();
    saveIntegrationMock.mockReset();
    testIntegrationMock.mockReset();
    validateMcpConnectionMock.mockReset();
    deleteIntegrationMock.mockReset();

    window.history.replaceState({}, "", "/builder/integrations");

    getIntegrationStarterTemplatesMock.mockResolvedValue([
      {
        id: "github",
        wave: 1,
        name: "GitHub",
        summary: "Repository read, PR review, and issue automation.",
        type: "http",
        base_url: "https://api.github.com",
        auth_type: "bearer",
        secret_ref: "secret/integrations/github/token",
        capabilities: ["/repo-read", "/code-search", "/issue-triage", "/pr-review", "scm"],
        permission_scopes: ["repo:read", "pull_requests:write", "issues:write"],
        data_access: ["source-code-metadata", "issue-data", "pull-request-data"],
        egress_allowlist: ["api.github.com"],
        publisher: "third_party",
        execution_mode: "sandboxed",
        signature_verified: false,
        approved_for_marketplace: false,
        metadata_json: {
          auth: { method: "bearer", prefix: "Bearer" },
          template_id: "github",
          template_wave: 1,
        },
      },
      {
        id: "slack",
        wave: 1,
        name: "Slack",
        summary: "Notifications, approvals, and chatops flows.",
        type: "http",
        base_url: "https://slack.com/api",
        auth_type: "bearer",
        secret_ref: "secret/integrations/slack/token",
        capabilities: ["/chatops", "/notify"],
        permission_scopes: ["chat:write"],
        data_access: ["message-write"],
        egress_allowlist: ["slack.com"],
        publisher: "third_party",
        execution_mode: "sandboxed",
        signature_verified: false,
        approved_for_marketplace: false,
        metadata_json: {
          auth: { method: "bearer", prefix: "Bearer" },
          template_id: "slack",
          template_wave: 1,
        },
      },
      {
        id: "microsoft-graph",
        wave: 3,
        name: "Microsoft Graph",
        summary: "Mail, calendar, users, files, and Teams automation with delegated OAuth.",
        type: "http",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail", "/calendar", "/directory-read"],
        permission_scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
        data_access: ["mailbox-data", "calendar-data", "directory-metadata"],
        egress_allowlist: ["graph.microsoft.com", "login.microsoftonline.com"],
        publisher: "third_party",
        execution_mode: "sandboxed",
        signature_verified: false,
        approved_for_marketplace: false,
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
            scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
            tenant: "common",
            audience: "https://graph.microsoft.com",
            redirect_path: "/builder/integrations?oauth_panel=1",
            client_secret_ref: "secret/integrations/microsoft/client-secret",
            token_secret_ref: "secret/integrations/microsoft/access-token",
            refresh_token_secret_ref: "secret/integrations/microsoft/refresh-token",
            account_label: "Shared mailbox",
          },
          template_id: "microsoft-graph",
          template_wave: 3,
        },
      },
    ]);
    getIntegrationsMock.mockResolvedValue([]);
    getMcpStarterTemplatesMock.mockResolvedValue([
      {
        id: "github",
        wave: 1,
        name: "GitHub MCP",
        summary: "Repository automation through an approved GitHub MCP server.",
        transport: "streamable_http",
        auth_type: "bearer",
        secret_ref: "secret/integrations/mcp/github/token",
        capabilities: ["/repo-read", "/issue-triage"],
        permission_scopes: ["mcp.repo.read", "mcp.issues.write"],
        data_access: ["mcp-source-code", "mcp-issue-data"],
        egress_allowlist: ["localhost"],
        publisher: "third_party",
        execution_mode: "sandboxed",
      },
      {
        id: "slack",
        wave: 1,
        name: "Slack MCP",
        summary: "ChatOps automation over a staged Slack MCP gateway.",
        transport: "streamable_http",
        auth_type: "bearer",
        secret_ref: "secret/integrations/mcp/slack/token",
        capabilities: ["/chatops"],
        permission_scopes: ["mcp.chat.write"],
        data_access: ["mcp-message-write"],
        egress_allowlist: ["localhost"],
        publisher: "third_party",
        execution_mode: "sandboxed",
      },
    ]);
    getMcpConnectionsMock.mockResolvedValue([]);
    getIntegrationOAuthStatusMock.mockResolvedValue({
      id: "integration-1",
      provider: "microsoft",
      grant_type: "authorization_code",
      connected: false,
      pending: false,
      scopes: ["User.Read"],
      authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
      token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
      client_id: "frontier-microsoft-client",
      redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
      account_label: "Shared mailbox",
      expires_at: null,
      has_client_secret: true,
      has_refresh_token: false,
      has_access_token: false,
      last_error: "",
    });
    connectIntegrationOAuthMock.mockResolvedValue({
      ok: true,
      mode: "client_credentials",
      status: {
        id: "integration-1",
        provider: "microsoft",
        grant_type: "client_credentials",
        connected: true,
        pending: false,
        scopes: ["https://graph.microsoft.com/.default"],
        authorize_url: "",
        token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        client_id: "frontier-microsoft-client",
        redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
        account_label: "Tenant app",
        expires_at: null,
        has_client_secret: true,
        has_refresh_token: false,
        has_access_token: true,
        last_error: "",
      },
    });
    refreshIntegrationOAuthMock.mockResolvedValue({
      ok: true,
      status: {
        id: "integration-1",
        provider: "microsoft",
        grant_type: "authorization_code",
        connected: true,
        pending: false,
        scopes: ["User.Read"],
        authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        client_id: "frontier-microsoft-client",
        redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
        account_label: "Shared mailbox",
        expires_at: null,
        has_client_secret: true,
        has_refresh_token: true,
        has_access_token: true,
        last_error: "",
      },
    });
    disconnectIntegrationOAuthMock.mockResolvedValue({
      ok: true,
      status: {
        id: "integration-1",
        provider: "microsoft",
        grant_type: "authorization_code",
        connected: false,
        pending: false,
        scopes: ["User.Read"],
        authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        client_id: "frontier-microsoft-client",
        redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
        account_label: "Shared mailbox",
        expires_at: null,
        has_client_secret: true,
        has_refresh_token: false,
        has_access_token: false,
        last_error: "",
      },
    });
    saveMcpConnectionMock.mockResolvedValue({ ok: true, id: "mcp-1", status: "draft" });
    validateMcpConnectionMock.mockResolvedValue({
      ok: true,
      id: "mcp-1",
      status: "validated",
      validation: {
        ok: true,
        errors: [],
        warnings: ["egress_allowlist does not include the MCP server host"],
        checked_server_url: "http://localhost:7071/mcp/github",
      },
    });
    approveMcpConnectionMock.mockResolvedValue({ ok: true, id: "mcp-1", status: "approved" });
    saveIntegrationMock.mockResolvedValue({ ok: true, id: "integration-1" });
    testIntegrationMock.mockResolvedValue({
      ok: true,
      id: "integration-1",
      status: "configured",
      message: "Connectivity verified",
      diagnostics: { warnings: ["TLS certificate is self-signed"] },
    });
    deleteIntegrationMock.mockResolvedValue({ ok: true });
  });

  it("saves integrations and refreshes the list", async () => {
    getIntegrationsMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          id: "integration-1",
          name: "CRM API",
          type: "http",
          status: "draft",
          base_url: "https://crm.example.com/v1",
          auth_type: "api_key",
          secret_ref: "secret/crm/token",
          capabilities: ["/incident-triage", "/tenant-oncall"],
          metadata_json: {
            auth: { method: "api_key", location: "header", key_name: "x-api-key" },
          },
        },
      ]);

    render(<IntegrationsManager />);

    await screen.findByText(/no integrations configured yet/i);

    fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: "CRM API" } });
    fireEvent.change(screen.getByLabelText(/base url \/ dsn/i), { target: { value: "https://crm.example.com/v1" } });
  fireEvent.change(screen.getByLabelText(/capabilities \/ skill matches/i), { target: { value: "/incident-triage\n/tenant-oncall" } });
    fireEvent.change(screen.getByLabelText(/auth type/i), { target: { value: "api_key" } });
    fireEvent.change(screen.getByLabelText(/secret reference/i), { target: { value: "secret/crm/token" } });
    fireEvent.click(screen.getByRole("button", { name: /save integration/i }));

    await waitFor(() => expect(saveIntegrationMock).toHaveBeenCalledTimes(1));
    expect(saveIntegrationMock).toHaveBeenCalledWith(expect.objectContaining({
      name: "CRM API",
      type: "http",
      base_url: "https://crm.example.com/v1",
      capabilities: ["/incident-triage", "/tenant-oncall"],
      auth_type: "api_key",
      secret_ref: "secret/crm/token",
      metadata_json: {
        auth: {
          method: "api_key",
          location: "header",
          key_name: "x-api-key",
        },
      },
    }));
    expect(await screen.findByText("Integration saved.")).toBeInTheDocument();
    expect(await screen.findByText("CRM API")).toBeInTheDocument();
    expect((await screen.findAllByText("/incident-triage")).length).toBeGreaterThan(0);
  });

  it("saves, validates, and approves staged MCP connections", async () => {
    getMcpConnectionsMock
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([
        {
          id: "mcp-1",
          starter_id: "github",
          wave: 1,
          name: "GitHub MCP",
          status: "draft",
          server_url: "http://localhost:7071/mcp/github",
          transport: "streamable_http",
          auth_type: "bearer",
          secret_ref: "********",
          secret_configured: true,
          capabilities: ["/repo-read", "/issue-triage"],
          permission_scopes: ["repo:read"],
          data_access: ["source-code-metadata"],
          egress_allowlist: ["localhost"],
          approved_by: "",
          approved_at: "",
          last_validated_at: "",
          last_validation_error: "",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "mcp-1",
          starter_id: "github",
          wave: 1,
          name: "GitHub MCP",
          status: "validated",
          server_url: "http://localhost:7071/mcp/github",
          transport: "streamable_http",
          auth_type: "bearer",
          secret_ref: "********",
          secret_configured: true,
          capabilities: ["/repo-read", "/issue-triage"],
          permission_scopes: ["repo:read"],
          data_access: ["source-code-metadata"],
          egress_allowlist: ["localhost"],
          approved_by: "",
          approved_at: "",
          last_validated_at: "2026-04-04T12:00:00Z",
          last_validation_error: "",
        },
      ])
      .mockResolvedValueOnce([
        {
          id: "mcp-1",
          starter_id: "github",
          wave: 1,
          name: "GitHub MCP",
          status: "approved",
          server_url: "http://localhost:7071/mcp/github",
          transport: "streamable_http",
          auth_type: "bearer",
          secret_ref: "********",
          secret_configured: true,
          capabilities: ["/repo-read", "/issue-triage"],
          permission_scopes: ["repo:read"],
          data_access: ["source-code-metadata"],
          egress_allowlist: ["localhost"],
          approved_by: "admin@example.com",
          approved_at: "2026-04-04T12:01:00Z",
          last_validated_at: "2026-04-04T12:00:00Z",
          last_validation_error: "",
        },
      ]);

    render(<IntegrationsManager />);

    await screen.findByText(/no staged mcp connections configured yet/i);

    fireEvent.change(screen.getByLabelText(/^MCP server URL$/i), { target: { value: "http://localhost:7071/mcp/github" } });
    fireEvent.change(screen.getByLabelText(/^MCP capabilities$/i), { target: { value: "/repo-read\n/issue-triage" } });
    fireEvent.change(screen.getByLabelText(/mcp permission bundle/i), { target: { value: "repo:read" } });
    fireEvent.change(screen.getByLabelText(/data access/i), { target: { value: "source-code-metadata" } });
    fireEvent.change(screen.getByLabelText(/egress allowlist/i), { target: { value: "localhost" } });
    fireEvent.click(screen.getByRole("button", { name: /save mcp connection/i }));

    await waitFor(() => expect(saveMcpConnectionMock).toHaveBeenCalledTimes(1));
    expect(saveMcpConnectionMock).toHaveBeenCalledWith(expect.objectContaining({
      starter_id: "github",
      name: "GitHub MCP",
      server_url: "http://localhost:7071/mcp/github",
      transport: "streamable_http",
      auth_type: "bearer",
      secret_ref: "secret/integrations/mcp/github/token",
      capabilities: ["/repo-read", "/issue-triage"],
      permission_scopes: ["repo:read"],
      data_access: ["source-code-metadata"],
      egress_allowlist: ["localhost"],
    }));
    expect(await screen.findByText("MCP connection saved as draft.")).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /^Validate$/i }));
    await waitFor(() => expect(validateMcpConnectionMock).toHaveBeenCalledWith("mcp-1"));
    expect(await screen.findByText(/GitHub MCP: validation passed/i)).toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /^Approve$/i }));
    await waitFor(() => expect(approveMcpConnectionMock).toHaveBeenCalledWith("mcp-1"));
    expect(await screen.findByText(/GitHub MCP approved for runtime use./i)).toBeInTheDocument();
  });

  it("updates staged MCP connections without resending the preserved secret ref", async () => {
    getMcpConnectionsMock.mockResolvedValue([
      {
        id: "mcp-1",
        starter_id: "github",
        wave: 1,
        name: "GitHub MCP",
        status: "draft",
        server_url: "http://localhost:7071/mcp/github",
        transport: "streamable_http",
        auth_type: "bearer",
        secret_ref: "********",
        secret_configured: true,
        capabilities: ["/repo-read"],
        permission_scopes: ["repo:read"],
        data_access: ["source-code-metadata"],
        egress_allowlist: ["localhost"],
        approved_by: "",
        approved_at: "",
        last_validated_at: "",
        last_validation_error: "",
      },
    ]);

    render(<IntegrationsManager />);

    await screen.findByText(/saved staged mcp connections/i);
    await screen.findByText("http://localhost:7071/mcp/github");
    fireEvent.click(screen.getByRole("button", { name: /^Edit$/i }));

    expect(await screen.findByRole("button", { name: /update mcp connection/i })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^MCP server URL$/i), { target: { value: "http://localhost:7171/mcp/github" } });
    fireEvent.click(screen.getByRole("button", { name: /update mcp connection/i }));

    await waitFor(() => expect(saveMcpConnectionMock).toHaveBeenCalledTimes(1));
    expect(saveMcpConnectionMock.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
      id: "mcp-1",
      server_url: "http://localhost:7171/mcp/github",
    }));
    expect(saveMcpConnectionMock.mock.calls[0]?.[0]).not.toHaveProperty("secret_ref");
  });

  it("loads an existing integration into edit mode and updates capabilities", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "CRM API",
        type: "http",
        status: "configured",
        base_url: "https://crm.example.com/v1",
        auth_type: "bearer",
        secret_ref: "secret/crm/token",
        capabilities: ["/customer-followup"],
        metadata_json: {
          auth: { method: "bearer", prefix: "Bearer" },
        },
      },
    ]);

    render(<IntegrationsManager />);

    await screen.findByText("CRM API");
    fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));

    expect(screen.getByRole("button", { name: /update integration/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/^name$/i)).toHaveValue("CRM API");
    expect(screen.getByLabelText(/capabilities \/ skill matches/i)).toHaveValue("/customer-followup");

    fireEvent.change(screen.getByLabelText(/capabilities \/ skill matches/i), {
      target: { value: "/customer-followup\n/tenant-oncall" },
    });
    fireEvent.click(screen.getByRole("button", { name: /update integration/i }));

    await waitFor(() => expect(saveIntegrationMock).toHaveBeenCalledTimes(1));
    expect(saveIntegrationMock).toHaveBeenCalledWith(expect.objectContaining({
      id: "integration-1",
      capabilities: ["/customer-followup", "/tenant-oncall"],
    }));
  });

  it("surfaces backend test failures", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "CRM API",
        type: "http",
        status: "draft",
        base_url: "https://crm.example.com/v1",
        auth_type: "none",
        secret_ref: "",
        metadata_json: {},
      },
    ]);
    testIntegrationMock.mockRejectedValueOnce(new Error("503 integration backend unavailable"));

    render(<IntegrationsManager />);

    await screen.findByText("CRM API");
    fireEvent.click(screen.getByRole("button", { name: /^test$/i }));

    expect(await screen.findByText("503 integration backend unavailable")).toBeInTheDocument();
  });

  it("surfaces backend delete failures", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "CRM API",
        type: "http",
        status: "draft",
        base_url: "https://crm.example.com/v1",
        auth_type: "none",
        secret_ref: "",
        metadata_json: {},
      },
    ]);
    deleteIntegrationMock.mockRejectedValueOnce(new Error("409 integration is still referenced"));

    render(<IntegrationsManager />);

    await screen.findByText("CRM API");
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    expect(await screen.findByText("409 integration is still referenced")).toBeInTheDocument();
  });

  it("shows OAuth2 as a supported auth type with provider-aware fields", async () => {
    render(<IntegrationsManager />);

    await screen.findByText(/no integrations configured yet/i);
    expect(screen.getByRole("option", { name: /^oauth2$/i })).toBeInTheDocument();
    expect(screen.getByText(/oauth2 is now supported in the builder/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/auth type/i), { target: { value: "oauth2" } });

    expect(screen.getByLabelText(/oauth provider/i)).toHaveValue("custom");
    expect(screen.getByLabelText(/grant type/i)).toHaveValue("client_credentials");
    expect(screen.getByLabelText(/token url/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/client secret reference/i)).toBeInTheDocument();
  });

  it("applies provider-specific OAuth presets and grant guidance", async () => {
    render(<IntegrationsManager />);

    await screen.findByText(/no integrations configured yet/i);
    fireEvent.change(screen.getByLabelText(/auth type/i), { target: { value: "oauth2" } });

    fireEvent.change(screen.getByLabelText(/oauth provider/i), { target: { value: "microsoft" } });
    expect(screen.getByLabelText(/token url/i)).toHaveValue("https://login.microsoftonline.com/common/oauth2/v2.0/token");
    expect(screen.getByLabelText(/scopes/i)).toHaveValue("https://graph.microsoft.com/.default");
    expect(screen.getByText(/daemon-style tenant app permissions/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/grant type/i), { target: { value: "authorization_code" } });
    expect(screen.getByLabelText(/authorize url/i)).toHaveValue("https://login.microsoftonline.com/common/oauth2/v2.0/authorize");
    expect(screen.getByLabelText(/scopes/i)).toHaveValue("User.Read Mail.ReadWrite offline_access");
    expect(screen.getByText(/delegated mailbox, calendar, teams, and sharepoint access/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/oauth provider/i), { target: { value: "google" } });
    expect(screen.getByLabelText(/token url/i)).toHaveValue("https://oauth2.googleapis.com/token");
    expect(screen.getByText(/gmail, drive, calendar, and other workspace user data/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/oauth provider/i), { target: { value: "salesforce" } });
    expect(screen.getByLabelText(/authorize url/i)).toHaveValue("https://login.salesforce.com/services/oauth2/authorize");
    expect(screen.getByText(/act as a salesforce user and respect that user’s sharing model/i)).toBeInTheDocument();
  });

  it("shows starter template details before applying a starter", async () => {
    render(<IntegrationsManager />);

    await screen.findByRole("button", { name: /inspect github details/i });
    fireEvent.click(screen.getByRole("button", { name: /inspect github details/i }));

    expect(screen.getByText(/template details/i)).toBeInTheDocument();
    expect(screen.getByText("repo:read")).toBeInTheDocument();
    expect(screen.getByText("source-code-metadata")).toBeInTheDocument();
    expect(screen.getByText("api.github.com")).toBeInTheDocument();
  });

  it("applies a starter template and saves the template defaults", async () => {
    render(<IntegrationsManager />);

    await screen.findByText(/no integrations configured yet/i);
    fireEvent.click(screen.getByRole("button", { name: /use github starter/i }));

    expect(screen.getByLabelText(/^name$/i)).toHaveValue("GitHub");
    expect(screen.getByLabelText(/^type$/i)).toHaveValue("http");
    expect(screen.getByLabelText(/base url \/ dsn/i)).toHaveValue("https://api.github.com");
    expect(screen.getByLabelText(/capabilities \/ skill matches/i)).toHaveValue(
      "/repo-read\n/code-search\n/issue-triage\n/pr-review\nscm",
    );
    expect(screen.getByLabelText(/auth type/i)).toHaveValue("bearer");
    expect(screen.getByLabelText(/token prefix/i)).toHaveValue("Bearer");
    expect(screen.getByLabelText(/secret reference/i)).toHaveValue("secret/integrations/github/token");

    fireEvent.click(screen.getByRole("button", { name: /save integration/i }));

    await waitFor(() => expect(saveIntegrationMock).toHaveBeenCalledTimes(1));
    expect(saveIntegrationMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "GitHub",
        type: "http",
        base_url: "https://api.github.com",
        auth_type: "bearer",
        secret_ref: "secret/integrations/github/token",
        capabilities: ["/repo-read", "/code-search", "/issue-triage", "/pr-review", "scm"],
        permission_scopes: ["repo:read", "pull_requests:write", "issues:write"],
        data_access: ["source-code-metadata", "issue-data", "pull-request-data"],
        egress_allowlist: ["api.github.com"],
        publisher: "third_party",
        execution_mode: "sandboxed",
        signature_verified: false,
        approved_for_marketplace: false,
        metadata_json: {
          auth: {
            method: "bearer",
            prefix: "Bearer",
          },
          template_id: "github",
          template_wave: 1,
        },
      }),
    );
  });

  it("applies an OAuth starter template and saves OAuth metadata", async () => {
    render(<IntegrationsManager />);

    await screen.findByText(/no integrations configured yet/i);
    fireEvent.click(screen.getByRole("button", { name: /use microsoft graph starter/i }));

    expect(screen.getByLabelText(/^name$/i)).toHaveValue("Microsoft Graph");
    expect(screen.getByLabelText(/auth type/i)).toHaveValue("oauth2");
    expect(screen.getByLabelText(/oauth provider/i)).toHaveValue("microsoft");
    expect(screen.getByLabelText(/grant type/i)).toHaveValue("authorization_code");
    expect(screen.getByLabelText(/token url/i)).toHaveValue("https://login.microsoftonline.com/common/oauth2/v2.0/token");
    expect(screen.getByLabelText(/authorize url/i)).toHaveValue("https://login.microsoftonline.com/common/oauth2/v2.0/authorize");
    expect(screen.getByLabelText(/client secret reference/i)).toHaveValue("secret/integrations/microsoft/client-secret");

    fireEvent.click(screen.getByRole("button", { name: /save integration/i }));

    await waitFor(() => expect(saveIntegrationMock).toHaveBeenCalledTimes(1));
    expect(saveIntegrationMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Microsoft Graph",
        type: "http",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail", "/calendar", "/directory-read"],
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
            scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
            tenant: "common",
            audience: "https://graph.microsoft.com",
            resource: "",
            redirect_path: "/builder/integrations?oauth_panel=1",
            client_secret_ref: "secret/integrations/microsoft/client-secret",
            token_secret_ref: "secret/integrations/microsoft/access-token",
            refresh_token_secret_ref: "secret/integrations/microsoft/refresh-token",
            account_label: "Shared mailbox",
          },
          template_id: "microsoft-graph",
          template_wave: 3,
          oauth_preset: {
            source: "provider-default",
            provider: "microsoft",
            grant_type: "authorization_code",
            recommended_auth: {
              authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
              token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
              scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
              audience: "https://graph.microsoft.com",
              resource: "",
              tenant: "common",
              redirect_path: "/builder/integrations?oauth_panel=1",
            },
          },
        },
      }),
    );
  });

  it("opens the OAuth status panel and starts the connect flow", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "Microsoft Graph",
        type: "http",
        status: "configured",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail"],
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
            redirect_path: "/builder/integrations?oauth_panel=1",
          },
          oauth_preset: {
            source: "provider-default",
            provider: "microsoft",
            grant_type: "authorization_code",
            recommended_auth: {
              authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
              token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
              scopes: [],
              audience: "",
              resource: "",
              tenant: "",
              redirect_path: "/builder/integrations?oauth_panel=1",
            },
          },
        },
        oauth_status: {
          id: "integration-1",
          provider: "microsoft",
          grant_type: "authorization_code",
          connected: false,
          pending: false,
          scopes: ["User.Read"],
          authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
          token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
          client_id: "frontier-microsoft-client",
          redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
          account_label: "Shared mailbox",
          expires_at: null,
          has_client_secret: true,
          has_refresh_token: false,
          has_access_token: false,
          last_error: "",
        },
      },
    ]);

    render(<IntegrationsManager />);

    await screen.findAllByRole("button", { name: /manage oauth/i });
    fireEvent.click(screen.getAllByRole("button", { name: /manage oauth/i })[0]);

    expect(await screen.findByText(/oauth status panel/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^connect oauth$/i }));

    await waitFor(() => {
      expect(connectIntegrationOAuthMock).toHaveBeenCalledWith("integration-1", {
        return_to: "/builder/integrations?oauth_panel=1",
      });
    });
    expect(await screen.findByText("Microsoft Graph OAuth connection established.")).toBeInTheDocument();
  });

  it("surfaces OAuth connection overview cards outside the status panel", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "Microsoft Graph",
        type: "http",
        status: "configured",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail"],
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
            redirect_path: "/builder/integrations?oauth_panel=1",
          },
          oauth_preset: {
            source: "provider-default",
            provider: "microsoft",
            grant_type: "authorization_code",
            recommended_auth: {
              authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
              token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
              scopes: [],
              audience: "",
              resource: "",
              tenant: "",
              redirect_path: "/builder/integrations?oauth_panel=1",
            },
          },
        },
        oauth_status: {
          id: "integration-1",
          provider: "microsoft",
          grant_type: "authorization_code",
          connected: true,
          pending: false,
          scopes: ["User.Read"],
          authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
          token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
          client_id: "frontier-microsoft-client",
          redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
          account_label: "Shared mailbox",
          expires_at: null,
          has_client_secret: true,
          has_refresh_token: true,
          has_access_token: true,
          last_error: "",
        },
      },
    ]);

    render(<IntegrationsManager />);

    expect(await screen.findByText(/oauth connection overview/i)).toBeInTheDocument();
    expect(screen.getByText("Access token present")).toBeInTheDocument();
    expect(screen.getAllByText(/matches microsoft recommended preset/i).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /manage oauth/i })).toHaveLength(2);
  });

  it("shows drift labels when a saved OAuth connector diverges from the persisted preset", async () => {
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "Microsoft Graph",
        type: "http",
        status: "configured",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail"],
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
            scopes: ["User.Read", "Mail.ReadWrite", "offline_access", "Files.Read.All"],
            audience: "https://graph.microsoft.com",
            resource: "",
            tenant: "organizations",
            redirect_path: "/builder/integrations?oauth_panel=1",
          },
          oauth_preset: {
            source: "provider-default",
            provider: "microsoft",
            grant_type: "authorization_code",
            recommended_auth: {
              authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
              token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
              scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
              audience: "https://graph.microsoft.com",
              resource: "",
              tenant: "common",
              redirect_path: "/builder/integrations?oauth_panel=1",
            },
          },
        },
        oauth_status: {
          id: "integration-1",
          provider: "microsoft",
          grant_type: "authorization_code",
          connected: true,
          pending: false,
          scopes: ["User.Read", "Mail.ReadWrite", "offline_access", "Files.Read.All"],
          authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
          token_url: "https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
          client_id: "frontier-microsoft-client",
          redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
          account_label: "Shared mailbox",
          expires_at: null,
          has_client_secret: true,
          has_refresh_token: true,
          has_access_token: true,
          last_error: "",
        },
      },
    ]);

    render(<IntegrationsManager />);

    expect(await screen.findAllByText(/customized from microsoft recommended preset/i)).not.toHaveLength(0);
  });

  it("reopens the dedicated OAuth status panel from callback query params", async () => {
    window.history.replaceState({}, "", "/builder/integrations?oauth_panel=1&oauth=connected&integration_id=integration-1");
    getIntegrationsMock.mockResolvedValue([
      {
        id: "integration-1",
        name: "Microsoft Graph",
        type: "http",
        status: "configured",
        base_url: "https://graph.microsoft.com/v1.0",
        auth_type: "oauth2",
        secret_ref: "secret/integrations/microsoft/client-secret",
        capabilities: ["/mail"],
        metadata_json: {
          auth: {
            method: "oauth2",
            provider: "microsoft",
            grant_type: "authorization_code",
            authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            client_id: "frontier-microsoft-client",
          },
        },
        oauth_status: {
          id: "integration-1",
          provider: "microsoft",
          grant_type: "authorization_code",
          connected: true,
          pending: false,
          scopes: ["User.Read"],
          authorize_url: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
          token_url: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
          client_id: "frontier-microsoft-client",
          redirect_uri: "https://frontier.local/integrations/integration-1/oauth/callback",
          account_label: "Shared mailbox",
          expires_at: null,
          has_client_secret: true,
          has_refresh_token: true,
          has_access_token: true,
          last_error: "",
        },
      },
    ]);

    render(<IntegrationsManager />);

    expect(await screen.findByText(/oauth connection completed/i)).toBeInTheDocument();
    expect(getIntegrationOAuthStatusMock).toHaveBeenCalledWith("integration-1");
  });
});
