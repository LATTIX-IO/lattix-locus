import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SecurityScopeEditor } from "@/components/security-scope-editor";

const addToastMock = vi.fn();
const onSaveMock = vi.fn(async () => undefined);
const onChangeMock = vi.fn();

vi.mock("@/components/toast", () => ({
  useToast: () => ({
    addToast: addToastMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  getAgentSecurityPolicy: vi.fn(async () => ({
    immutable_baseline: {
      enforce_capability_filter: true,
      enforce_policy_gate: true,
      fail_closed_policy_decisions: true,
      enforce_signed_a2a_messages: true,
      enforce_a2a_replay_protection: true,
      require_readonly_rootfs_for_sandbox: true,
      require_non_root_sandbox_user: true,
      require_egress_mediation_when_network_enabled: true,
      allow_filter_chain_reordering: false,
      allow_custom_policy_code: false,
    },
    platform_defaults: {
      classification: "internal",
      guardrail_ruleset_id: null,
      blocked_keywords: [],
      allowed_egress_hosts: ["localhost", "api.openai.com"],
      allowed_retrieval_sources: [],
      allowed_mcp_server_urls: [],
      allowed_runtime_engines: ["native"],
      allowed_memory_scopes: ["run", "session", "workflow"],
      max_tool_calls_per_run: 8,
      max_retrieval_items: 5,
      max_collaboration_agents: 4,
      require_human_approval: false,
      require_human_approval_for_high_risk_tools: true,
      allow_runtime_override: false,
      enable_platform_signals: true,
      platform_signal_enforcement: "block_high",
    },
    workflow_overrides: {},
    agent_overrides: {
      classification: "restricted",
    },
    effective: {
      classification: "restricted",
      guardrail_ruleset_id: null,
      blocked_keywords: ["secrets"],
      allowed_egress_hosts: ["localhost"],
      allowed_retrieval_sources: [],
      allowed_mcp_server_urls: [],
      allowed_runtime_engines: ["native"],
      allowed_memory_scopes: ["run", "workflow"],
      max_tool_calls_per_run: 3,
      max_retrieval_items: 2,
      max_collaboration_agents: 2,
      require_human_approval: true,
      require_human_approval_for_high_risk_tools: true,
      allow_runtime_override: false,
      enable_platform_signals: true,
      platform_signal_enforcement: "audit",
    },
    backend_enforced_controls: ["policy_gate_filter"],
    configurable_controls: ["allowed_runtime_engines"],
  })),
  getWorkflowSecurityPolicy: vi.fn(async () => null),
  getGuardrailRulesets: vi.fn(async () => [
    { id: "ruleset-1", name: "Default Published", version: 1, status: "published" },
    { id: "ruleset-2", name: "Draft Ignored", version: 1, status: "draft" },
  ]),
}));

describe("SecurityScopeEditor", () => {
  it("renders effective policy information and persists changes", async () => {
    onSaveMock.mockClear();
    onChangeMock.mockClear();
    addToastMock.mockClear();

    render(
      <SecurityScopeEditor
        entityType="agent"
        entityId="agent-1"
        entityName="Threat Analyst"
        value={{
          classification: "internal",
          allowed_runtime_engines: ["native"],
          require_human_approval: false,
        }}
        onChange={onChangeMock}
        onSave={onSaveMock}
      />,
    );

    expect(await screen.findByText(/Classification resolves to restricted/i)).toBeInTheDocument();
    expect(screen.getByText(/Backend-enforced rails:/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/classification/i), { target: { value: "restricted" } });
    expect(onChangeMock).toHaveBeenCalledWith(expect.objectContaining({ classification: "restricted" }));

    fireEvent.change(screen.getByLabelText(/guardrail ruleset/i), { target: { value: "ruleset-1" } });
    expect(onChangeMock).toHaveBeenCalledWith(expect.objectContaining({ guardrail_ruleset_id: "ruleset-1" }));

    fireEvent.click(screen.getByRole("button", { name: /save agent policy/i }));

    await waitFor(() => expect(onSaveMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("success", "Agent security policy saved."));
  });
});
