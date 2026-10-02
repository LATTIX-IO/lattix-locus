import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";

const { getPlatformSettingsMock, savePlatformSettingsMock, getAtfAlignmentReportMock, getUserSkillsMock, saveUserSkillsMock } = vi.hoisted(() => ({
  getPlatformSettingsMock: vi.fn(async () => ({
    console_classification_banner_enabled: true,
    console_classification_banner_text: "Internal • Operational Console",
    console_classification_banner_background_color: "#2e2a28",
    console_classification_banner_text_color: "#e7dcc0",
    local_only_mode: true,
    mask_secrets_in_events: true,
    require_human_approval: false,
    default_guardrail_ruleset_id: null,
    global_blocked_keywords: [],
    tenant_scoped_skills: [],
    collaboration_max_agents: 8,
    default_runtime_engine: "native",
    default_runtime_strategy: "single",
    default_hybrid_runtime_routing: {
      default: "native",
      orchestration: "native",
      retrieval: "native",
      tooling: "native",
      collaboration: "native",
    },
    allowed_runtime_engines: ["native", "langgraph", "langchain", "semantic-kernel", "autogen"],
    allow_runtime_engine_override: true,
    enforce_runtime_engine_allowlist: true,
  })),
  getAtfAlignmentReportMock: vi.fn(async () => ({
    generated_at: new Date().toISOString(),
    framework: "CSA Agentic Trust Framework",
    coverage_percent: 80,
    maturity_estimate: "senior",
    pillars: {
      identity: { status: "strong", controls: {}, gaps: [] },
      behavior_monitoring: { status: "strong", controls: {}, gaps: [] },
      data_governance: { status: "partial", controls: {}, gaps: ["Enable stricter retention controls"] },
      segmentation: { status: "strong", controls: {}, gaps: [] },
      incident_response: { status: "partial", controls: {}, gaps: ["Exercise incident playbooks regularly"] },
    },
    evidence: {
      audit_window_hours: 24,
      audit_event_count_24h: 12,
      audit_allowed_24h: 9,
      audit_blocked_24h: 2,
      audit_error_24h: 1,
      total_audit_events: 28,
      run_count_total: 6,
    },
  })),
  getUserSkillsMock: vi.fn(async () => ({
    principal_id: "user-1",
    skills: [],
    updated_at: new Date().toISOString(),
  })),
  saveUserSkillsMock: vi.fn(async (payload: { skills: string[] }) => ({
    principal_id: "user-1",
    skills: payload.skills,
    updated_at: new Date().toISOString(),
  })),
  savePlatformSettingsMock: vi.fn<(payload: Record<string, unknown>) => Promise<{ ok: boolean }>>(async () => ({ ok: true })),
}));

vi.mock("@/components/composer-defaults-settings", () => ({
  ComposerDefaultsSettings: () => null,
}));

vi.mock("@/lib/api", () => ({
  getPlatformSettings: getPlatformSettingsMock,
  getAtfAlignmentReport: getAtfAlignmentReportMock,
  getUserSkills: getUserSkillsMock,
  savePlatformSettings: savePlatformSettingsMock,
  saveUserSkills: saveUserSkillsMock,
}));

describe("SettingsPage", () => {
  it("saves policy-managed hybrid runtime profile", async () => {
    savePlatformSettingsMock.mockClear();

    render(<SettingsPage />);

    await screen.findByText(/max collaborating agents per run/i);

    fireEvent.change(screen.getByLabelText(/default runtime strategy/i), { target: { value: "hybrid" } });
    fireEvent.change(screen.getByLabelText(/^retrieval$/i), { target: { value: "langchain" } });
    fireEvent.change(screen.getByLabelText(/^collaboration$/i), { target: { value: "autogen" } });
    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(savePlatformSettingsMock).toHaveBeenCalledTimes(1));
    const firstCall = savePlatformSettingsMock.mock.calls.at(0);
    expect(firstCall).toBeDefined();

    const payload = firstCall?.[0] as {
      default_runtime_strategy?: string;
      default_hybrid_runtime_routing?: {
        retrieval?: string;
        collaboration?: string;
      };
    };

    expect(payload.default_runtime_strategy).toBe("hybrid");
    expect(payload.default_hybrid_runtime_routing?.retrieval).toBe("langchain");
    expect(payload.default_hybrid_runtime_routing?.collaboration).toBe("autogen");
  });

  it("resets to recommended hybrid profile with one click", async () => {
    savePlatformSettingsMock.mockClear();

    render(<SettingsPage />);

    await screen.findByText(/global hybrid role/i);

    fireEvent.click(screen.getByRole("button", { name: /reset to recommended profile/i }));
    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(savePlatformSettingsMock).toHaveBeenCalledTimes(1));
    const payload = savePlatformSettingsMock.mock.calls.at(0)?.[0] as {
      default_runtime_strategy?: string;
      default_hybrid_runtime_routing?: {
        default?: string;
        orchestration?: string;
        retrieval?: string;
        tooling?: string;
        collaboration?: string;
      };
    };

    expect(payload.default_runtime_strategy).toBe("hybrid");
    expect(payload.default_hybrid_runtime_routing?.default).toBe("native");
    expect(payload.default_hybrid_runtime_routing?.orchestration).toBe("langgraph");
    expect(payload.default_hybrid_runtime_routing?.retrieval).toBe("langchain");
    expect(payload.default_hybrid_runtime_routing?.tooling).toBe("semantic-kernel");
    expect(payload.default_hybrid_runtime_routing?.collaboration).toBe("autogen");
  });

  it("saves classification banner changes from shared settings", async () => {
    savePlatformSettingsMock.mockClear();

    render(<SettingsPage />);

    await screen.findByLabelText(/console banner text/i);

    fireEvent.click(screen.getByLabelText(/show console classification banner/i));
    fireEvent.change(screen.getByLabelText(/console banner text/i), { target: { value: "Restricted • Incident Console" } });
    fireEvent.change(screen.getByLabelText(/banner background color/i), { target: { value: "#1d4ed8" } });
    fireEvent.change(screen.getByLabelText(/banner text color/i), { target: { value: "#eff6ff" } });
    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    await waitFor(() => expect(savePlatformSettingsMock).toHaveBeenCalledTimes(1));
    const payload = savePlatformSettingsMock.mock.calls.at(0)?.[0] as {
      console_classification_banner_enabled?: boolean;
      console_classification_banner_text?: string;
      console_classification_banner_background_color?: string;
      console_classification_banner_text_color?: string;
    };

    expect(payload.console_classification_banner_enabled).toBe(false);
    expect(payload.console_classification_banner_text).toBe("Restricted • Incident Console");
    expect(payload.console_classification_banner_background_color).toBe("#1d4ed8");
    expect(payload.console_classification_banner_text_color).toBe("#eff6ff");
  });

  it("surfaces backend save failures in the shared settings shell", async () => {
    savePlatformSettingsMock.mockClear();
    savePlatformSettingsMock.mockRejectedValueOnce(new Error("503 backend unavailable"));

    render(<SettingsPage />);

    await screen.findByRole("button", { name: /save preferences/i });

    fireEvent.click(screen.getByRole("button", { name: /save preferences/i }));

    expect(await screen.findByText("503 backend unavailable")).toBeInTheDocument();
  });

  it("saves personal slash skills independently from platform settings", async () => {
    saveUserSkillsMock.mockClear();

    render(<SettingsPage />);

    await screen.findByRole("button", { name: /save \/skills/i });

    fireEvent.change(screen.getByLabelText(/personal \/skills/i), {
      target: { value: "/incident-triage\nresearch-brief" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save \/skills/i }));

    await waitFor(() => expect(saveUserSkillsMock).toHaveBeenCalledTimes(1));
    expect(saveUserSkillsMock.mock.calls.at(0)?.[0]).toEqual({
      skills: ["/incident-triage", "research-brief"],
    });
  });
});
