import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getPlatformSettings: vi.fn(),
  getPlatformSecurityPolicy: vi.fn(),
  savePlatformSettings: vi.fn(),
  getGuardrailRulesets: vi.fn(),
  getUserSkills: vi.fn(),
  saveUserSkills: vi.fn(),
  getModelsOverview: vi.fn(),
  getProviderModels: vi.fn(),
  getUserSettings: vi.fn(),
  getWorkspaceFolders: vi.fn(),
  saveUserSettings: vi.fn(),
  pullLocalModel: vi.fn(),
  setProviderKey: vi.fn(),
  clearProviderKey: vi.fn(),
  getTelemetrySummary: vi.fn(),
  getLoopStatus: vi.fn(),
  enableLoop: vi.fn(),
  disableLoop: vi.fn(),
  enableLoopAutostart: vi.fn(),
  disableLoopAutostart: vi.fn(),
  getComputerUseStatus: vi.fn(),
  triggerComputerUsePanic: vi.fn(),
  resetComputerUse: vi.fn(),
  getUserBrowserTier: vi.fn(),
  setUserBrowserTier: vi.fn(),
  getUserBrowserStatus: vi.fn(),
  pairUserBrowser: vi.fn(),
  unpairUserBrowser: vi.fn(),
  getPlatformVersionStatus: vi.fn(),
  getSystemUpdateStatus: vi.fn(),
  getMcpConnections: vi.fn(),
  getIntegrations: vi.fn(),
  getMemoryLayers: vi.fn(),
  getKnowledgeVectorStores: vi.fn(),
  getPlatformHealthDetails: vi.fn(),
}));
const searchState = vi.hoisted(() => ({ current: new URLSearchParams() }));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...api,
    BROWSER_TIERS: actual.BROWSER_TIERS,
    isBrowserTierWidening: actual.isBrowserTierWidening,
    DesktopConfirmationCancelledError: actual.DesktopConfirmationCancelledError,
    SecurityChangeConfirmationRequired: actual.SecurityChangeConfirmationRequired,
  };
});

vi.mock("next/navigation", () => ({ useSearchParams: () => searchState.current }));
vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: { children: ReactNode; href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("@/components/first-run-wizard", () => ({ openFirstRunWizard: vi.fn() }));

import { ComputerUseSection } from "@/components/settings/computer-use-section";
import { DesktopConfirmationCancelledError } from "@/lib/desktop-confirmation";
import { SecurityChangeConfirmationRequired } from "@/lib/api";
import { LoopSection } from "@/components/settings/loop-section";
import { MemorySection } from "@/components/settings/memory-section";
import { ObservabilitySection } from "@/components/settings/observability-section";
import { PoliciesSection } from "@/components/settings/policies-section";
import { SETTINGS_SECTIONS, SettingsWorkspace, resolveSettingsSection } from "@/components/settings/settings-workspace";

const baseSettings = {
  local_only_mode: true,
  mask_secrets_in_events: true,
  require_human_approval: false,
  require_human_approval_for_high_risk_tools: true,
  default_guardrail_ruleset_id: null,
  global_blocked_keywords: [],
  collaboration_max_agents: 8,
  allowed_runtime_engines: ["native"],
};

const loopStatus = {
  enabled: false,
  disabled_reason: "kill-switch file exists",
  disabled_by_environment: false,
  runs_today: 1,
  max_runs_per_day: 5,
  active_run: null,
  last_run: null,
  open_prs: [],
  autostart: { enabled: false, repo_path: "" },
  linear: { api_key_configured: true },
};

beforeEach(() => {
  Object.values(api).forEach((mock) => mock.mockReset());
  searchState.current = new URLSearchParams();
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
  api.getPlatformSettings.mockResolvedValue(baseSettings);
  api.getPlatformSecurityPolicy.mockResolvedValue({ control_status: { controls: [], summary: {} } });
  api.savePlatformSettings.mockResolvedValue({ ok: true });
  api.getGuardrailRulesets.mockResolvedValue([]);
  api.getUserSkills.mockResolvedValue({ principal_id: "me", skills: [] });
  api.getModelsOverview.mockResolvedValue({
    providers: { openai: { configured: false, default_model: "" }, nim: {}, ollama: { available: false, base_url: "", default_model: "", installed_models: [] } },
    external: [],
    catalog: [],
  });
  api.getUserSettings.mockResolvedValue({ default_working_folder: "", preferred_model: "", preferred_reasoning_effort: "", default_mode: "chat" });
  api.getWorkspaceFolders.mockResolvedValue({ folders: [] });
  api.getTelemetrySummary.mockResolvedValue({ since_ns: 0, until_ns: 1, runs: 0, empty: true });
  api.getLoopStatus.mockResolvedValue(loopStatus);
  api.getComputerUseStatus.mockResolvedValue({ mode: "observe", panicked: false, panic_source: "", inflight_actions: 0 });
  api.getUserBrowserTier.mockResolvedValue({
    tier: "strict",
    effective_tier: "strict",
    allowlisted_sites: [],
    granted_sites: [],
    consent: null,
    tier_risks: { assisted: "Assisted risk text from the backend." },
  });
  api.getPlatformVersionStatus.mockResolvedValue({ current_version: "1.0.0", status: "up_to_date" });
  api.getUserBrowserStatus.mockResolvedValue({ paired: false, connected: false });
  api.getMemoryLayers.mockResolvedValue([
    { id: "short_term", name: "Short-term memory", backend: "Redis", scope: "Per-session.", enabled: true, healthy: true, stats: {} },
    {
      id: "long_term",
      name: "Long-term memory",
      backend: "Postgres + pgvector",
      scope: "Durable.",
      enabled: false,
      healthy: false,
      stats: { vector_search: false, embedding_model: "nomic-embed-text" },
    },
  ]);
  api.getKnowledgeVectorStores.mockResolvedValue([
    { id: "platform", name: "Platform vector store (pgvector)", kind: "builtin", ready: false, status: "unavailable", embedding_model: "nomic-embed-text", note: "Long-term memory store is not available." },
  ]);
  api.getPlatformHealthDetails.mockResolvedValue({ long_term_memory: "disabled", long_term_memory_reason: "POSTGRES_DSN is not set" });
});

describe("SettingsWorkspace", () => {
  it("lists every section in one sub-nav and opens Engines by default", async () => {
    render(<SettingsWorkspace />);

    const nav = screen.getByRole("navigation", { name: /settings sections/i });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(SETTINGS_SECTIONS.map((section) => section.label));
    expect(within(nav).getByRole("link", { name: "Engines" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByRole("heading", { name: "Engines", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /run setup again/i })).toBeInTheDocument();
  });

  it("opens the section named in the URL and falls back for unknown ones", () => {
    expect(resolveSettingsSection("policies")).toBe("policies");
    expect(resolveSettingsSection("governance")).toBe("engines");
    expect(resolveSettingsSection(null)).toBe("engines");
  });
});

describe("Policies & autonomy", () => {
  it("saves only its own fields through savePlatformSettings", async () => {
    render(<PoliciesSection />);

    const toggle = await screen.findByRole("switch", { name: /approve every run/i });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    fireEvent.click(toggle);
    fireEvent.click(screen.getAllByRole("button", { name: /save changes/i })[0]);

    await waitFor(() => expect(api.savePlatformSettings).toHaveBeenCalledTimes(1));
    const patch = api.savePlatformSettings.mock.calls[0][0];
    expect(patch.require_human_approval).toBe(true);
    expect(patch.allow_local_network_hostnames).toEqual([]);
    expect(patch).not.toHaveProperty("org_name");
    expect(patch).not.toHaveProperty("ai_providers");
    expect(await screen.findByText(/^saved\.$/i)).toBeInTheDocument();
  });

  it("reports a cancelled desktop confirmation without treating it as saved", async () => {
    api.savePlatformSettings.mockRejectedValueOnce(new DesktopConfirmationCancelledError());
    render(<PoliciesSection />);

    fireEvent.click(await screen.findByRole("switch", { name: /enforce the egress allowlist/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /save changes/i })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent(/cancelled in the confirmation dialog/i);
  });

  it("no longer hosts personal skills and points to Library → Skills", async () => {
    render(<PoliciesSection />);

    await screen.findByRole("switch", { name: /approve every run/i });
    expect(screen.queryByRole("group", { name: /personal skills/i })).not.toBeInTheDocument();
    expect(api.getUserSkills).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /library → skills → personal/i })).toHaveAttribute("href", "/library/skills?tab=personal");
  });

  it("shows a retryable error when settings cannot load", async () => {
    api.getPlatformSettings.mockRejectedValueOnce(new Error("Request failed (500)"));
    render(<PoliciesSection />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/request failed \(500\)/i);
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByRole("switch", { name: /approve every run/i })).toBeInTheDocument();
  });
});

describe("Observability", () => {
  it("shows content capture and exporters off by default and saves a change", async () => {
    render(<ObservabilitySection />);

    const capture = await screen.findByRole("switch", { name: /capture prompt and output content/i });
    expect(capture).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: /opentelemetry/i })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: /langsmith/i })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/no traced runs in the last 24 hours/i)).toBeInTheDocument();

    fireEvent.click(capture);
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(api.savePlatformSettings).toHaveBeenCalledTimes(1));
    expect(api.savePlatformSettings.mock.calls[0][0]).toMatchObject({ telemetry_capture_content: true, telemetry_otlp_enabled: false });
  });

  it("explains content capture, then resends with confirm_security_change when the backend flags it", async () => {
    api.savePlatformSettings
      .mockRejectedValueOnce(new SecurityChangeConfirmationRequired(["telemetry_capture_content"]))
      .mockResolvedValueOnce({ ok: true });
    render(<ObservabilitySection />);

    fireEvent.click(await screen.findByRole("switch", { name: /capture prompt and output content/i }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    const dialog = await screen.findByRole("dialog", { name: /confirm a security change/i });
    expect(within(dialog).getByText("Capture prompt and output content")).toBeInTheDocument();
    expect(dialog).toHaveTextContent(/prompts and outputs of every run on this machine/i);
    expect(dialog).toHaveTextContent(/redacted/i);
    expect(api.savePlatformSettings).toHaveBeenCalledTimes(1);

    fireEvent.click(within(dialog).getByRole("button", { name: /confirm and save/i }));
    await waitFor(() => expect(api.savePlatformSettings).toHaveBeenCalledTimes(2));
    expect(api.savePlatformSettings.mock.calls[1][0]).toMatchObject({ telemetry_capture_content: true });
    expect(api.savePlatformSettings.mock.calls[1][1]).toEqual({ confirmSecurityChange: true });
    expect(await screen.findByText(/^saved\.$/i)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not save when the security change is declined", async () => {
    api.savePlatformSettings.mockRejectedValueOnce(new SecurityChangeConfirmationRequired(["telemetry_capture_content"]));
    render(<ObservabilitySection />);

    fireEvent.click(await screen.findByRole("switch", { name: /capture prompt and output content/i }));
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /^cancel$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/security change was not confirmed/i);
    expect(api.savePlatformSettings).toHaveBeenCalledTimes(1);
  });
});

describe("sensitive settings in other sections", () => {
  it("lists every flagged key with a friendly label (Policies & autonomy)", async () => {
    api.savePlatformSettings
      .mockRejectedValueOnce(new SecurityChangeConfirmationRequired(["block_new_runs", "some_future_key"]))
      .mockResolvedValueOnce({ ok: true });
    render(<PoliciesSection />);

    fireEvent.click(await screen.findByRole("switch", { name: /approve every run/i }));
    fireEvent.click(screen.getAllByRole("button", { name: /save changes/i })[0]);

    const dialog = await screen.findByRole("dialog", { name: /confirm a security change/i });
    expect(within(dialog).getByText("Block new runs")).toBeInTheDocument();
    expect(within(dialog).getByText("some_future_key")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: /confirm and save/i }));
    await waitFor(() => expect(api.savePlatformSettings.mock.calls[1]?.[1]).toEqual({ confirmSecurityChange: true }));
  });
});

describe("Memory & knowledge", () => {
  it("is a Settings section that shows the configuration and says clearly when long-term memory is unavailable", async () => {
    expect(SETTINGS_SECTIONS.map((section) => section.id)).toContain("memory");
    expect(resolveSettingsSection("memory")).toBe("memory");
    render(<MemorySection />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/long-term memory is unavailable/i);
    expect(alert).toHaveTextContent(/postgres_dsn is not set/i);
    expect(alert).toHaveTextContent(/knowledge collections cannot index or search/i);
    expect(alert).toHaveTextContent(/set up:/i);

    const layers = screen.getByRole("list", { name: /memory layers/i });
    expect(within(layers).getByText("Short-term memory")).toBeInTheDocument();
    expect(within(layers).getByText("Long-term memory")).toBeInTheDocument();
    expect(screen.getByText("nomic-embed-text")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: /vector stores/i })).getByText(/unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open knowledge/i })).toHaveAttribute("href", "/library/knowledge");

    fireEvent.click(within(alert).getByRole("button", { name: /check again/i }));
    await waitFor(() => expect(api.getMemoryLayers).toHaveBeenCalledTimes(2));
  });

  it("shows no warning when long-term memory is on", async () => {
    api.getMemoryLayers.mockResolvedValue([
      { id: "long_term", name: "Long-term memory", backend: "Postgres + pgvector", scope: "Durable.", enabled: true, healthy: true, stats: { vector_search: true } },
    ]);
    render(<MemorySection />);

    expect(await screen.findByText("Long-term memory")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("Loop & Linear", () => {
  it("shows Linear key presence (never a value) and turns the loop on", async () => {
    api.enableLoop.mockResolvedValue({ ...loopStatus, enabled: true, disabled_reason: "" });
    render(<LoopSection />);

    expect(await screen.findByText(/api key stored/i)).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /linear/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /turn on/i }));

    await waitFor(() => expect(api.enableLoop).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: /turn off/i })).toBeInTheDocument();
  });
});

describe("Computer use", () => {
  it("asks for an explicit acknowledgement before widening the browser tier on the web profile", async () => {
    api.setUserBrowserTier.mockResolvedValue({ tier: "assisted", effective_tier: "assisted", allowlisted_sites: [], granted_sites: [], consent: null });
    render(<ComputerUseSection />);

    expect(await screen.findByText(/ctrl\+alt\+shift\+esc|cmd\+alt\+shift\+esc/i)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("radio", { name: /assisted/i }));
    fireEvent.click(screen.getByRole("button", { name: /review and save/i }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/assisted risk text from the backend/i);
    expect(api.setUserBrowserTier).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: /i understand/i }));

    await waitFor(() => expect(api.setUserBrowserTier).toHaveBeenCalledTimes(1));
    expect(api.setUserBrowserTier.mock.calls[0][2]).toEqual({ acknowledgeRisk: true });
  });

  it("lets a widened tier start with empty site lists", async () => {
    api.setUserBrowserTier.mockResolvedValue({ tier: "trusted", effective_tier: "trusted", allowlisted_sites: [], granted_sites: [], consent: null });
    render(<ComputerUseSection />);

    fireEvent.click(await screen.findByRole("radio", { name: /trusted/i }));
    expect(screen.getByRole("textbox", { name: /granted sites/i })).toHaveValue("");
    expect(screen.getAllByText(/may stay empty/i).length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: /review and save/i }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: /i understand/i }));

    await waitFor(() => expect(api.setUserBrowserTier).toHaveBeenCalledTimes(1));
    expect(api.setUserBrowserTier.mock.calls[0][1]).toEqual({ tier: "trusted", allowlisted_sites: [], granted_sites: [] });
  });

  it("hands a widening to the desktop shell without an in-app dialog", async () => {
    (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: vi.fn() } };
    api.setUserBrowserTier.mockResolvedValue({ tier: "assisted", effective_tier: "assisted", allowlisted_sites: [], granted_sites: [], consent: null });
    render(<ComputerUseSection />);

    fireEvent.click(await screen.findByRole("radio", { name: /assisted/i }));
    fireEvent.click(screen.getByRole("button", { name: /confirm in locus/i }));

    await waitFor(() => expect(api.setUserBrowserTier).toHaveBeenCalledTimes(1));
    expect(api.setUserBrowserTier.mock.calls[0][2]).toEqual({ acknowledgeRisk: false });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("pairs and unpairs the browser from Operating system access", async () => {
    api.pairUserBrowser.mockResolvedValue({ paired: true, connected: false, rotated: false });
    api.unpairUserBrowser.mockResolvedValue({ paired: false, connected: false });
    render(<ComputerUseSection />);

    const group = await screen.findByRole("group", { name: /operating system access/i });
    expect(await within(group).findByText(/not paired/i)).toBeInTheDocument();
    const pair = within(group).getByRole("button", { name: /pair browser/i });
    expect(pair).toBeEnabled();
    fireEvent.click(pair);

    await waitFor(() => expect(api.pairUserBrowser).toHaveBeenCalledTimes(1));
    expect(await within(group).findByText(/browser paired/i)).toBeInTheDocument();
    fireEvent.click(within(group).getByRole("button", { name: /^unpair$/i }));
    await waitFor(() => expect(api.unpairUserBrowser).toHaveBeenCalledTimes(1));
    expect(await within(group).findByText(/^not paired\.$/i)).toBeInTheDocument();
  });

  it("reports a cancelled pairing dialog without claiming success", async () => {
    api.pairUserBrowser.mockRejectedValue(new DesktopConfirmationCancelledError());
    render(<ComputerUseSection />);

    const group = await screen.findByRole("group", { name: /operating system access/i });
    fireEvent.click(await within(group).findByRole("button", { name: /pair browser/i }));
    expect(await within(group).findByRole("alert")).toHaveTextContent(/cancelled in the confirmation dialog/i);
  });

  it("offers a recheck when desktop control is not enforced", async () => {
    render(<ComputerUseSection />);

    const group = await screen.findByRole("group", { name: /operating system access/i });
    fireEvent.click(await within(group).findByRole("button", { name: /check again/i }));
    await waitFor(() => expect(api.getPlatformSecurityPolicy).toHaveBeenCalledTimes(2));
  });

  it("says the policy engine is missing and re-checks the OS access status", async () => {
    api.getPlatformSecurityPolicy.mockResolvedValue({
      control_status: {
        controls: [
          { id: "policy_engine_rego", label: "Policy engine (Rego)", state: "off", evidence: "No Rego engine is available." },
          { id: "computer_use", label: "Computer use containment", state: "unverified", evidence: "every UI action is denied" },
        ],
        summary: {},
      },
    });
    render(<ComputerUseSection />);

    expect(await screen.findByText(/policy engine missing: reinstall lattix locus/i)).toBeInTheDocument();
    const calls = api.getPlatformSecurityPolicy.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /check again/i }));
    await waitFor(() => expect(api.getPlatformSecurityPolicy.mock.calls.length).toBeGreaterThan(calls));
  });

  it("stops computer use from the panic button", async () => {
    api.triggerComputerUsePanic.mockResolvedValue({ panicked: true });
    render(<ComputerUseSection />);

    fireEvent.click(await screen.findByRole("button", { name: /stop all now/i }));
    await waitFor(() => expect(api.triggerComputerUsePanic).toHaveBeenCalledTimes(1));
  });
});
