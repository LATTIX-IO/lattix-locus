import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// LOCUS-357: inside the desktop shell, capability-widening calls go through the
// shell's `confirm_action` command; on the web they stay plain fetches.

const fetchMock = vi.fn();
vi.stubGlobal("fetch", fetchMock);
const invokeMock = vi.fn();

function enterDesktopShell() {
  (window as unknown as { __TAURI__?: unknown }).__TAURI__ = { core: { invoke: invokeMock } };
}

function okJson(value: unknown) {
  return { ok: true, status: 200, json: async () => value, text: async () => JSON.stringify(value) };
}

function refused(detail: string) {
  const body = JSON.stringify({ detail });
  return { ok: false, status: 403, json: async () => JSON.parse(body), text: async () => body };
}

beforeEach(() => {
  fetchMock.mockReset();
  invokeMock.mockReset();
  vi.resetModules();
});

afterEach(() => {
  delete (window as unknown as { __TAURI__?: unknown }).__TAURI__;
});

describe("desktop shell confirmation", () => {
  it("approves an escalation through confirm_action with the exact action, path and body", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ok: true, escalation: { status: "approved" } }));

    const { approveRunEscalation } = await import("@/lib/api");
    const result = await approveRunEscalation("run-1", "esc-1");

    expect(invokeMock).toHaveBeenCalledWith("confirm_action", {
      action: "workflow.run.escalations.approve",
      path: "/workflow-runs/run-1/escalations/esc-1/approve",
      body: {},
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(result).toEqual({ ok: true, escalation: { status: "approved" } });
  });

  it("sends a body-less widening request with a null body", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ok: true }));

    const { publishGuardrailRuleset } = await import("@/lib/api");
    await publishGuardrailRuleset("gr-1");

    expect(invokeMock).toHaveBeenCalledWith("confirm_action", {
      action: "guardrail.ruleset.publish",
      path: "/guardrail-rulesets/gr-1/publish",
      body: null,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses a plain fetch outside the desktop shell", async () => {
    fetchMock.mockResolvedValueOnce(okJson({ ok: true }));

    const { approveRunEscalation } = await import("@/lib/api");
    await approveRunEscalation("run-1", "esc-1");

    expect(invokeMock).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/workflow-runs/run-1/escalations/esc-1/approve"),
      expect.objectContaining({ method: "POST", body: "{}" }),
    );
  });

  it("raises a clear error when the human cancels the dialog, without falling back", async () => {
    enterDesktopShell();
    invokeMock.mockRejectedValueOnce("cancelled");

    const api = await import("@/lib/api");
    const attempt = api.approveRunEscalation("run-1", "esc-1");

    await expect(attempt).rejects.toBeInstanceOf(api.DesktopConfirmationCancelledError);
    await expect(attempt).rejects.toThrow("Cancelled in the confirmation dialog");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reports other shell failures with the shell's reason", async () => {
    enterDesktopShell();
    invokeMock.mockRejectedValueOnce("the backend refused the request (HTTP 409)");

    const api = await import("@/lib/api");
    await expect(api.promoteSkill("skill-1")).rejects.toThrow(
      "Desktop confirmation failed: the backend refused the request (HTTP 409)",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps narrowing variants as plain fetches inside the shell", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValue(okJson({ ok: true }));

    const api = await import("@/lib/api");
    await api.submitApproval({ run_id: "run-1", decision: "changes_requested" });
    await api.toggleWorkflowSchedule("sch-1", false);
    await api.revokeWorkflowTrigger("tok-1");
    await api.deleteUserRuntimeProvider("openai");

    expect(invokeMock).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("confirms the widening variants of body-decided calls", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValue(JSON.stringify({ ok: true }));

    const api = await import("@/lib/api");
    await api.submitApproval({ run_id: "run-1", decision: "approved" });
    await api.toggleWorkflowSchedule("sch-1", true);
    await api.createWorkflowSchedule("wf-1", "0 9 * * 1", "weekly");

    expect(invokeMock.mock.calls.map(([, args]) => (args as { action: string }).action)).toEqual([
      "approval.submit",
      "workflow.schedule.toggle",
      "workflow.schedule.create",
    ]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends a settings save plainly and confirms it only when the backend says it widens", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(
      refused("Confirm this change in the Locus desktop app (missing_proof)"),
    );
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ok: true }));

    const { savePlatformSettings } = await import("@/lib/api");
    const payload = { allowed_egress_hosts: ["localhost", "api.example.com"] };
    await expect(savePlatformSettings(payload)).resolves.toEqual({ ok: true });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledWith("confirm_action", {
      action: "platform.settings.save",
      path: "/platform/settings",
      body: payload,
    });
  });

  it("does not open the dialog for other refusals", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(refused("Confirm this change in the Locus desktop app (no_shell)"));

    const { saveUserSettings } = await import("@/lib/api");
    await expect(saveUserSettings({ default_mode: "execute" })).rejects.toThrow("Request failed (403)");
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("narrowing settings saves stay plain fetches", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(okJson({ default_mode: "chat" }));

    const { saveUserSettings } = await import("@/lib/api");
    await expect(saveUserSettings({ default_mode: "chat" })).resolves.toEqual({ default_mode: "chat" });
    expect(invokeMock).not.toHaveBeenCalled();
  });
});

describe("matchShellAction", () => {
  it("maps widening calls to their action ids and leaves the rest alone", async () => {
    const { matchShellAction } = await import("@/lib/desktop-confirmation");
    expect(matchShellAction("post", "/computer-use/reset")?.id).toBe("computer_use.reset");
    expect(matchShellAction("POST", "/integrations/mcp")?.id).toBe("integration.mcp.save");
    expect(matchShellAction("POST", "/integrations/mcp/c-1/approve")?.id).toBe("integration.mcp.approve");
    expect(matchShellAction("POST", "/integrations/i-1/oauth/connect")?.id).toBe("integration.oauth.connect");
    expect(matchShellAction("DELETE", "/guardrail-rulesets/g-1")?.id).toBe("guardrail.ruleset.delete");
    expect(matchShellAction("PUT", "/models/providers/openai/key")?.id).toBe("models.provider.key.set");
    expect(matchShellAction("POST", "/computer-use/panic")).toBeNull();
    expect(matchShellAction("DELETE", "/models/providers/openai/key")).toBeNull();
    expect(matchShellAction("POST", "/integrations/mcp/c-1/validate")).toBeNull();
    expect(matchShellAction("GET", "/platform/settings")).toBeNull();
    expect(matchShellAction("POST", "/guardrail-rulesets")).toBeNull();
  });

  it("has one entry per action id", async () => {
    const { SHELL_ACTIONS } = await import("@/lib/desktop-confirmation");
    expect(new Set(SHELL_ACTIONS.map((spec) => spec.id)).size).toBe(SHELL_ACTIONS.length);
  });
});

// LOCUS-353: Settings routes its own widening changes through the shell too.
describe("settings widening on the desktop", () => {
  const strictTier = {
    tier: "strict" as const,
    effective_tier: "strict" as const,
    allowlisted_sites: [],
    granted_sites: [],
    consent: null,
  };

  it("widens the browser tier only through confirm_browser_tier (camelCase args), never a plain request", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ...strictTier, tier: "assisted", effective_tier: "assisted", allowlisted_sites: ["docs.example.com"] }));

    const { setUserBrowserTier } = await import("@/lib/api");
    const saved = await setUserBrowserTier(strictTier, { tier: "assisted", allowlisted_sites: ["docs.example.com"], granted_sites: [] });

    expect(invokeMock).toHaveBeenCalledWith("confirm_browser_tier", {
      tier: "assisted",
      allowlistedSites: ["docs.example.com"],
      grantedSites: [],
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(saved.tier).toBe("assisted");
  });

  it("reports a cancelled tier dialog as a cancellation", async () => {
    enterDesktopShell();
    invokeMock.mockRejectedValueOnce("cancelled");

    const { setUserBrowserTier, DesktopConfirmationCancelledError } = await import("@/lib/api");
    await expect(
      setUserBrowserTier(strictTier, { tier: "open", allowlisted_sites: [], granted_sites: [] }),
    ).rejects.toBeInstanceOf(DesktopConfirmationCancelledError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("narrows the browser tier with a plain request and no dialog", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(okJson(strictTier));

    const { setUserBrowserTier } = await import("@/lib/api");
    await setUserBrowserTier(
      { ...strictTier, tier: "trusted", effective_tier: "trusted", granted_sites: ["a.example.com"] },
      { tier: "strict", allowlisted_sites: [], granted_sites: [] },
    );

    expect(invokeMock).not.toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/user-browser\/tier$/);
    expect(JSON.parse(init.body)).toEqual({ tier: "strict", allowlisted_sites: [], granted_sites: [] });
  });

  it("on the web, refuses a widening without an acknowledgement and sends acknowledge_risk with one", async () => {
    fetchMock.mockResolvedValueOnce(okJson({ ...strictTier, tier: "assisted" }));

    const { setUserBrowserTier } = await import("@/lib/api");
    const widening = { tier: "assisted" as const, allowlisted_sites: [], granted_sites: [] };
    await expect(setUserBrowserTier(strictTier, widening)).rejects.toThrow(/acknowledge its risk/);
    expect(fetchMock).not.toHaveBeenCalled();

    await setUserBrowserTier(strictTier, widening, { acknowledgeRisk: true });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ tier: "assisted", acknowledge_risk: true });
  });

  it("turns the loop on and autostarts it through confirm_action; turning it off is a plain request", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValue(JSON.stringify({ enabled: true }));
    fetchMock.mockResolvedValue(okJson({ enabled: false }));

    const { disableLoop, enableLoop, enableLoopAutostart } = await import("@/lib/api");
    await enableLoop();
    await enableLoopAutostart("C:/src/locus");
    await disableLoop();

    expect(invokeMock).toHaveBeenNthCalledWith(1, "confirm_action", { action: "loop.enable", path: "/loop/enable", body: {} });
    expect(invokeMock).toHaveBeenNthCalledWith(2, "confirm_action", {
      action: "loop.autostart.enable",
      path: "/loop/autostart",
      body: { repo_path: "C:/src/locus" },
    });
    expect(invokeMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/loop\/disable$/);
  });

  it("stores a provider key through confirm_action", async () => {
    enterDesktopShell();
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ok: true }));

    const { setProviderKey } = await import("@/lib/api");
    await setProviderKey("nim", "nvapi-secret");

    expect(invokeMock).toHaveBeenCalledWith("confirm_action", {
      action: "models.provider.key.set",
      path: "/models/providers/nim/key",
      body: { api_key: "nvapi-secret" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("sensitive platform settings (confirm_security_change)", () => {
  function sensitiveRefusal(keys: string[]) {
    const body = JSON.stringify({
      detail: { message: "Sensitive platform security changes require confirm_security_change=true", changed_sensitive_keys: keys },
    });
    return { ok: false, status: 400, json: async () => JSON.parse(body), text: async () => body };
  }

  it("turns the 400 into SecurityChangeConfirmationRequired carrying the keys", async () => {
    fetchMock.mockResolvedValueOnce(sensitiveRefusal(["telemetry_capture_content"]));

    const api = await import("@/lib/api");
    const failure = await api.savePlatformSettings({ telemetry_capture_content: true }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(api.SecurityChangeConfirmationRequired);
    expect((failure as InstanceType<typeof api.SecurityChangeConfirmationRequired>).keys).toEqual(["telemetry_capture_content"]);
    expect(invokeMock).not.toHaveBeenCalled();
  });

  it("resends with confirm_security_change and routes the widening through the shell", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(refused("Confirm this change in the Locus desktop app (missing_proof)"));
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ok: true }));

    const { savePlatformSettings } = await import("@/lib/api");
    await expect(savePlatformSettings({ telemetry_capture_content: true }, { confirmSecurityChange: true })).resolves.toEqual({ ok: true });

    const sent = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(sent).toEqual({ telemetry_capture_content: true, confirm_security_change: true });
    expect(invokeMock).toHaveBeenCalledWith("confirm_action", {
      action: "platform.settings.save",
      path: "/platform/settings",
      body: { telemetry_capture_content: true, confirm_security_change: true },
    });
  });

  it("does not cache the confirmation flag as a setting", async () => {
    fetchMock.mockResolvedValueOnce(okJson({ ok: true }));
    const events: unknown[] = [];
    const listener = (event: Event) => events.push((event as CustomEvent).detail);
    window.addEventListener("locus:platform-settings-updated", listener);

    const { savePlatformSettings } = await import("@/lib/api");
    await savePlatformSettings({ block_new_runs: true }, { confirmSecurityChange: true });
    window.removeEventListener("locus:platform-settings-updated", listener);

    expect(events.at(-1)).toMatchObject({ block_new_runs: true });
    expect(events.at(-1)).not.toHaveProperty("confirm_security_change");
  });
});

describe("Always allow on <site> (browser tier lists)", () => {
  const TRUSTED = {
    tier: "trusted",
    effective_tier: "trusted",
    allowlisted_sites: ["docs.example.org"],
    granted_sites: [],
    consent: { tier: "trusted" },
  };

  it("sends the full new list through confirm_browser_tier on the desktop", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(okJson(TRUSTED));
    invokeMock.mockResolvedValueOnce(JSON.stringify({ ...TRUSTED, granted_sites: ["example.com"] }));

    const { allowSiteInBrowserTier } = await import("@/lib/api");
    const saved = await allowSiteInBrowserTier("example.com", "granted_sites");

    expect(invokeMock).toHaveBeenCalledWith("confirm_browser_tier", {
      tier: "trusted",
      allowlistedSites: ["docs.example.org"],
      grantedSites: ["example.com"],
    });
    expect(fetchMock).toHaveBeenCalledTimes(1); // only the read; the shell sends the PUT
    expect(saved.granted_sites).toEqual(["example.com"]);
  });

  it("does nothing when the site is already listed", async () => {
    fetchMock.mockResolvedValueOnce(okJson({ ...TRUSTED, granted_sites: ["example.com"] }));

    const { allowSiteInBrowserTier } = await import("@/lib/api");
    await allowSiteInBrowserTier("example.com", "granted_sites");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("starts from empty lists: the web profile sends the acknowledged full list", async () => {
    const assisted = { ...TRUSTED, tier: "assisted", effective_tier: "assisted", allowlisted_sites: [], consent: { tier: "assisted" } };
    fetchMock
      .mockResolvedValueOnce(okJson(assisted))
      .mockResolvedValueOnce(okJson({ ...assisted, allowlisted_sites: ["example.com"] }));

    const { allowSiteInBrowserTier } = await import("@/lib/api");
    await allowSiteInBrowserTier("example.com", "allowlisted_sites", { acknowledgeRisk: true });

    const put = fetchMock.mock.calls[1];
    expect((put[1] as RequestInit).method).toBe("PUT");
    expect(JSON.parse(String((put[1] as RequestInit).body))).toEqual({
      tier: "assisted",
      allowlisted_sites: ["example.com"],
      granted_sites: [],
      acknowledge_risk: true,
    });
  });

  it("denying an agent request is a plain request", async () => {
    enterDesktopShell();
    fetchMock.mockResolvedValueOnce(okJson({ ok: true }));

    const { denyRunEscalation } = await import("@/lib/api");
    await denyRunEscalation("run-1", "esc-1");
    expect(invokeMock).not.toHaveBeenCalled();
    expect(String(fetchMock.mock.calls[0][0])).toContain("/workflow-runs/run-1/escalations/esc-1/deny");
  });
});
