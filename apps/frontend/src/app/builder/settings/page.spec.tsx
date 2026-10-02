import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import BuilderSettingsOverviewPage from "@/app/builder/settings/page";

const addToastMock = vi.fn();

const { getOperatorSessionMock, getPlatformSettingsMock, getPlatformSecurityPolicyMock } = vi.hoisted(() => ({
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
  savePlatformSettings: vi.fn(async () => ({ ok: true })),
}));

describe("BuilderSettingsOverviewPage", () => {
  it("renders overview cards for each builder settings route", async () => {
    render(<BuilderSettingsOverviewPage />);

    expect(await screen.findByText(/builder operating picture/i)).toBeInTheDocument();
    expect(screen.getByText(/control surfaces/i)).toBeInTheDocument();
    expect(screen.getByText(/attention queue/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /guardrails & approvals/i })).toHaveAttribute("href", "/builder/settings/guardrails");
    expect(screen.getByRole("link", { name: /network & retrieval/i })).toHaveAttribute("href", "/builder/settings/network");
    expect(screen.getByRole("link", { name: /runtime & inference/i })).toHaveAttribute("href", "/builder/settings/runtime");
    expect(screen.getByRole("link", { name: /approvals & governance/i })).toHaveAttribute("href", "/builder/settings/governance");
    expect(screen.getByText(/current envelope/i)).toBeInTheDocument();
    expect(screen.getByText(/builder heuristics/i)).toBeInTheDocument();
  });
});