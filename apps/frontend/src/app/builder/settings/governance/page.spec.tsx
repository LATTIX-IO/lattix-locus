import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import BuilderGovernanceSettingsPage from "@/app/builder/settings/governance/page";

const addToastMock = vi.fn();

const { getOperatorSessionMock, getPlatformSettingsMock, getPlatformSecurityPolicyMock, savePlatformSettingsMock } = vi.hoisted(() => ({
  getOperatorSessionMock: vi.fn(async () => ({
    authenticated: true,
    actor: "operator",
    principal_id: "user-1",
    principal_type: "user",
    display_name: "Operator",
    subject: "user-1",
    email: "operator@example.com",
    preferred_username: "operator",
    auth_mode: "shared-token",
    provider: "local",
    roles: ["admin"],
    capabilities: { can_admin: true, can_builder: true },
    allowed_modes: ["builder", "user"],
    default_mode: "builder",
    oidc: { configured: false, issuer: "", audience: "", provider: "" },
  })),
  getPlatformSettingsMock: vi.fn(async () => ({
    console_classification_banner_enabled: true,
    console_classification_banner_text: "Internal • Operational Console",
    console_classification_banner_background_color: "#2e2a28",
    console_classification_banner_text_color: "#e7dcc0",
    default_guardrail_ruleset_id: null,
    global_blocked_keywords: ["secret"],
    allowed_egress_hosts: ["localhost"],
    allowed_retrieval_sources: ["kb://default"],
    allowed_mcp_server_urls: ["http://localhost:8787"],
    allowed_runtime_engines: ["native", "langgraph"],
    high_risk_tool_patterns: ["shell.exec"],
    max_tool_calls_per_run: 8,
    max_retrieval_items: 8,
    collaboration_max_agents: 8,
    require_human_approval: false,
    require_human_approval_for_high_risk_tools: true,
    emergency_read_only_mode: false,
    block_new_runs: false,
    block_graph_runs: false,
    block_tool_calls: false,
    block_retrieval_calls: false,
    enforce_egress_allowlist: true,
    enforce_local_network_only: true,
    allow_local_network_hostnames: true,
    retrieval_require_local_source_url: false,
    mcp_require_local_server: true,
    default_runtime_engine: "native",
    allow_runtime_engine_override: false,
    require_authenticated_requests: true,
    require_a2a_runtime_headers: true,
    a2a_require_signed_messages: true,
    a2a_replay_protection: true,
    tenant_scoped_skills: ["/tenant-oncall"],
    enable_foss_guardrail_signals: true,
    foss_guardrail_signal_enforcement: "block_high",
  })),
  getPlatformSecurityPolicyMock: vi.fn(async () => ({
    immutable_baseline: {
      enforce_policy_gate: true,
      fail_closed_policy_decisions: true,
    },
    platform_defaults: {
      classification: "internal",
      allowed_memory_scopes: ["run", "session", "workflow"],
      require_human_approval_for_high_risk_tools: true,
    },
    effective: {
      classification: "internal",
      max_tool_calls_per_run: 8,
      max_retrieval_items: 8,
      allowed_runtime_engines: ["native", "langgraph"],
      enable_platform_signals: true,
      platform_signal_enforcement: "block_high",
    },
    backend_enforced_controls: ["policy_gate_filter"],
    configurable_controls: ["allowed_runtime_engines", "max_tool_calls_per_run"],
  })),
  savePlatformSettingsMock: vi.fn<(payload: Record<string, unknown>) => Promise<{ ok: boolean }>>(async () => ({ ok: true })),
}));

vi.mock("@/components/toast", () => ({
  useToast: () => ({
    addToast: addToastMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  getOperatorSession: getOperatorSessionMock,
  getPlatformSettings: getPlatformSettingsMock,
  getPlatformSecurityPolicy: getPlatformSecurityPolicyMock,
  savePlatformSettings: savePlatformSettingsMock,
}));

describe("BuilderGovernanceSettingsPage", () => {
  it("persists governance control changes from the admin-only route", async () => {
    savePlatformSettingsMock.mockClear();
    addToastMock.mockClear();

    render(<BuilderGovernanceSettingsPage />);

    await screen.findByText(/approvals and operational governance/i);

    fireEvent.click(screen.getByLabelText(/emergency read-only mode/i));
    fireEvent.click(screen.getByLabelText(/show console classification banner/i));
    fireEvent.change(screen.getByLabelText(/tenant-scoped \/skills/i), { target: { value: "/tenant-oncall\n/tenant-research" } });
    fireEvent.change(screen.getByLabelText(/console banner text/i), { target: { value: "Restricted • Incident Console" } });
    fireEvent.change(screen.getByLabelText(/banner background color/i), { target: { value: "#1d4ed8" } });
    fireEvent.change(screen.getByLabelText(/banner text color/i), { target: { value: "#eff6ff" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(savePlatformSettingsMock).toHaveBeenCalledTimes(1));
    expect(savePlatformSettingsMock.mock.calls.at(0)?.[0]).toEqual(expect.objectContaining({
      allow_local_network_hostnames: ["localhost", ".local"],
      emergency_read_only_mode: true,
      console_classification_banner_enabled: false,
      console_classification_banner_text: "Restricted • Incident Console",
      console_classification_banner_background_color: "#1d4ed8",
      console_classification_banner_text_color: "#eff6ff",
      tenant_scoped_skills: ["/tenant-oncall", "/tenant-research"],
    }));
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("success", "Builder security settings saved."));
  });
});
