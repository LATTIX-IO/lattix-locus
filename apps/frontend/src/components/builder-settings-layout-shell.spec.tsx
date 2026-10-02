import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BuilderSettingsLayoutShell } from "@/components/builder-settings-layout-shell";

const usePathnameMock = vi.fn(() => "/builder/settings/runtime");

const { getOperatorSessionMock, getPlatformSettingsMock, getPlatformSecurityPolicyMock } = vi.hoisted(() => ({
  getOperatorSessionMock: vi.fn(),
  getPlatformSettingsMock: vi.fn(),
  getPlatformSecurityPolicyMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => usePathnameMock(),
}));

vi.mock("@/lib/api", () => ({
  getOperatorSession: getOperatorSessionMock,
  getPlatformSettings: getPlatformSettingsMock,
  getPlatformSecurityPolicy: getPlatformSecurityPolicyMock,
}));

describe("BuilderSettingsLayoutShell", () => {
  beforeEach(() => {
    getPlatformSettingsMock.mockResolvedValue({
      global_blocked_keywords: ["secret", "token"],
      allowed_egress_hosts: ["localhost"],
      allowed_runtime_engines: ["native", "langgraph"],
      enforce_local_network_only: true,
      enforce_egress_allowlist: true,
      enable_foss_guardrail_signals: true,
      emergency_read_only_mode: false,
      require_human_approval: false,
      block_new_runs: false,
      block_graph_runs: false,
      block_tool_calls: false,
      block_retrieval_calls: false,
    });
    getPlatformSecurityPolicyMock.mockResolvedValue({
      platform_defaults: { require_human_approval_for_high_risk_tools: true },
    });
  });

  it("shows governance only for admin-capable builders and renders live nav badges", async () => {
    getOperatorSessionMock.mockResolvedValue({
      capabilities: { can_admin: true, can_builder: true },
      roles: ["admin"],
    });

    render(<BuilderSettingsLayoutShell><div>child</div></BuilderSettingsLayoutShell>);

    await waitFor(() => expect(screen.getByRole("link", { name: /governance/i })).toBeInTheDocument());
    expect(screen.getByText(/2 blocked/i)).toBeInTheDocument();
    expect(screen.getByText(/local only/i)).toBeInTheDocument();
    expect(screen.getByText(/2 engines/i)).toBeInTheDocument();
    expect(screen.getByText(/review gated/i)).toBeInTheDocument();
  });

  it("hides governance for non-admin builders", async () => {
    getOperatorSessionMock.mockResolvedValue({
      capabilities: { can_admin: false, can_builder: true },
      roles: ["builder"],
    });

    render(<BuilderSettingsLayoutShell><div>child</div></BuilderSettingsLayoutShell>);

    await waitFor(() => expect(screen.getByRole("link", { name: /runtime/i })).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /governance/i })).not.toBeInTheDocument();
  });
});