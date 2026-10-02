import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import BuilderRuntimeSettingsPage from "@/app/builder/settings/runtime/page";

const addToastMock = vi.fn();

const {
  deleteUserRuntimeProviderMock,
  getOperatorSessionMock,
  getPlatformSettingsMock,
  getPlatformSecurityPolicyMock,
  getRuntimeProvidersMock,
  getUserRuntimeProvidersMock,
  savePlatformSettingsMock,
  saveUserRuntimeProviderMock,
} = vi.hoisted(() => ({
  deleteUserRuntimeProviderMock: vi.fn(async () => ({ ok: true })),
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
  getRuntimeProvidersMock: vi.fn(async () => ({
    providers: [{ provider: "openai", configured: true, model: "gpt-5.4", mode: "live" }],
    framework_adapters: {},
  })),
  getUserRuntimeProvidersMock: vi.fn<
    () => Promise<Array<{
      provider: string;
      configured: boolean;
      model: string;
      available_models?: string[];
      base_url: string;
      api_key_masked: string;
      preferred?: boolean;
      updated_at: string;
      source: "user" | "environment";
    }>>
  >(async () => []),
  savePlatformSettingsMock: vi.fn<(payload: Record<string, unknown>) => Promise<{ ok: boolean }>>(async () => ({ ok: true })),
  saveUserRuntimeProviderMock: vi.fn(async (provider: string, payload: { model?: string; available_models?: string[]; base_url?: string; preferred?: boolean }) => ({
    provider,
    configured: true,
    model: payload.model ?? "",
    available_models: payload.available_models ?? [payload.model ?? ""].filter(Boolean),
    base_url: payload.base_url ?? "",
    api_key_masked: "sk-***1234",
    preferred: Boolean(payload.preferred),
    updated_at: "2026-01-01T00:00:00Z",
    source: "user",
  })),
}));

vi.mock("@/components/toast", () => ({
  useToast: () => ({
    addToast: addToastMock,
  }),
}));

vi.mock("@/lib/api", () => ({
  deleteUserRuntimeProvider: deleteUserRuntimeProviderMock,
  getOperatorSession: getOperatorSessionMock,
  getPlatformSettings: getPlatformSettingsMock,
  getPlatformSecurityPolicy: getPlatformSecurityPolicyMock,
  getRuntimeProviders: getRuntimeProvidersMock,
  getUserRuntimeProviders: getUserRuntimeProvidersMock,
  savePlatformSettings: savePlatformSettingsMock,
  saveUserRuntimeProvider: saveUserRuntimeProviderMock,
}));

describe("BuilderRuntimeSettingsPage", () => {
  it("persists runtime ceiling changes from the dedicated route", async () => {
    savePlatformSettingsMock.mockClear();
    addToastMock.mockClear();

    render(<BuilderRuntimeSettingsPage />);

    await screen.findByRole("heading", { name: /runtime ceilings/i });

    fireEvent.change(screen.getByLabelText(/max tool calls per run/i), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(savePlatformSettingsMock).toHaveBeenCalledTimes(1));
    expect(savePlatformSettingsMock.mock.calls.at(0)?.[0]).toEqual(expect.objectContaining({ max_tool_calls_per_run: 12 }));
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("success", "Builder security settings saved."));
  });

  it("surfaces backend settings save failures from the dedicated route", async () => {
    savePlatformSettingsMock.mockClear();
    addToastMock.mockClear();
    savePlatformSettingsMock.mockRejectedValueOnce(new Error("400 invalid guardrail reference"));

    render(<BuilderRuntimeSettingsPage />);

    await screen.findByRole("heading", { name: /runtime ceilings/i });

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("error", "400 invalid guardrail reference"));
  });

  it("saves an OpenAI-compatible inference backend", async () => {
    saveUserRuntimeProviderMock.mockClear();
    addToastMock.mockClear();
    getUserRuntimeProvidersMock.mockResolvedValue([]);

    render(<BuilderRuntimeSettingsPage />);

    await screen.findByRole("heading", { name: /inference backends/i });

    expect(screen.queryByText(/select which models builders can choose for this provider/i)).not.toBeInTheDocument();

    const openAiCompatibleCard = screen.getByText(/^OpenAI-compatible$/i).closest("section");
    expect(openAiCompatibleCard).not.toBeNull();

    const card = within(openAiCompatibleCard as HTMLElement);
    fireEvent.click(card.getByRole("button", { name: /1 model selected/i }));
    expect(card.getByLabelText(/phi-4:14b/i)).toBeInTheDocument();
    fireEvent.click(card.getByLabelText(/qwen2.5-coder:14b/i));
    fireEvent.click(card.getByLabelText(/llama3.3:70b-instruct/i));
    fireEvent.change(card.getByLabelText(/default model/i), { target: { value: "llama3.3:70b-instruct" } });
    fireEvent.click(card.getByLabelText(/qwen2.5-coder:32b/i));
    expect(card.queryByLabelText(/base url/i)).not.toBeInTheDocument();
    expect(card.queryByText("http://localhost:11434/v1")).not.toBeInTheDocument();
    fireEvent.change(card.getByLabelText(/api key/i), { target: { value: "local-token" } });
    fireEvent.click(card.getByLabelText(/set as preferred backend/i));
    fireEvent.click(card.getByRole("button", { name: /save backend/i }));

    await waitFor(() => expect(saveUserRuntimeProviderMock).toHaveBeenCalledTimes(1));
    expect(saveUserRuntimeProviderMock).toHaveBeenCalledWith(
      "openai-compatible",
      expect.objectContaining({
        model: "llama3.3:70b-instruct",
        available_models: ["qwen2.5-coder:14b", "llama3.3:70b-instruct"],
        base_url: "http://localhost:11434/v1",
        api_key: "local-token",
        preferred: true,
      }),
    );
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("success", "Inference backend saved."));
  });

  it("surfaces backend inference save failures", async () => {
    saveUserRuntimeProviderMock.mockClear();
    addToastMock.mockClear();
    getUserRuntimeProvidersMock.mockResolvedValue([]);
    saveUserRuntimeProviderMock.mockRejectedValueOnce(new Error("403 provider rejected"));

    render(<BuilderRuntimeSettingsPage />);

    await screen.findByRole("heading", { name: /inference backends/i });

    const openAiCompatibleCard = screen.getByText(/^OpenAI-compatible$/i).closest("section");
    expect(openAiCompatibleCard).not.toBeNull();

    fireEvent.click(within(openAiCompatibleCard as HTMLElement).getByRole("button", { name: /save backend/i }));

    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("error", "403 provider rejected"));
  });

  it("removes a saved inference backend", async () => {
    deleteUserRuntimeProviderMock.mockClear();
    addToastMock.mockClear();
    getUserRuntimeProvidersMock.mockResolvedValue([
      {
        provider: "anthropic",
        configured: true,
        model: "claude-3-7-sonnet-latest",
        available_models: ["claude-3-7-sonnet-latest"],
        base_url: "https://api.anthropic.com/v1",
        api_key_masked: "sk-ant-***9876",
        preferred: false,
        updated_at: "2026-01-01T00:00:00Z",
        source: "user",
      },
    ]);

    render(<BuilderRuntimeSettingsPage />);

    const anthropicCard = await screen.findByText(/^Anthropic$/i);
    const card = within(anthropicCard.closest("section") as HTMLElement);
    fireEvent.click(card.getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(deleteUserRuntimeProviderMock).toHaveBeenCalledWith("anthropic"));
    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("success", "Inference backend removed."));
  });

  it("surfaces backend inference delete failures", async () => {
    deleteUserRuntimeProviderMock.mockClear();
    addToastMock.mockClear();
    deleteUserRuntimeProviderMock.mockRejectedValueOnce(new Error("409 provider still referenced"));
    getUserRuntimeProvidersMock.mockResolvedValue([
      {
        provider: "anthropic",
        configured: true,
        model: "claude-3-7-sonnet-latest",
        available_models: ["claude-3-7-sonnet-latest"],
        base_url: "https://api.anthropic.com/v1",
        api_key_masked: "sk-ant-***9876",
        preferred: false,
        updated_at: "2026-01-01T00:00:00Z",
        source: "user",
      },
    ]);

    render(<BuilderRuntimeSettingsPage />);

    const anthropicCard = await screen.findByText(/^Anthropic$/i);
    const card = within(anthropicCard.closest("section") as HTMLElement);
    fireEvent.click(card.getByRole("button", { name: /remove/i }));

    await waitFor(() => expect(addToastMock).toHaveBeenCalledWith("error", "409 provider still referenced"));
  });
});
