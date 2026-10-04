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
