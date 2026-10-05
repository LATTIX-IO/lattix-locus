/* ------------------------------------------------------------------ */
/*  Desktop shell confirmation for capability-widening calls           */
/*  (LOCUS-357)                                                        */
/* ------------------------------------------------------------------ */
//
// On the desktop install the backend treats every loopback request as the
// operator, so a request that widens what the agents may do (approving an
// agent's request, loosening settings, adding integrations, skills, keys,
// triggers or schedules, clearing panic, changing the active guardrails) also
// needs a proof that only the Tauri shell can produce after the human confirms
// a native dialog. Inside the shell, these calls therefore go through the
// `confirm_action` command: the shell shows the dialog, signs the request and
// sends it itself, then relays the backend's response. The webview never sees
// the secret or the proof. Outside the shell (web / hosted) nothing changes.
//
// SHELL_ACTIONS mirrors the widening and conditional rules of
// apps/backend/app/request_security.py; tests/backend/test_desktop_packaging.py
// fails when they differ.

export type ShellActionWhen =
  /** Always widening: confirm in the shell first. */
  | "always"
  /** Decided from stored state by the backend: plain request first, and the
   * shell only when the backend refuses it for lack of a proof. */
  | "on-refusal"
  /** Decided from the request body (mirrors app/capability_widening.py). */
  | "approval_decision_approves"
  | "schedule_enabled"
  | "schedule_toggle_enables"
  | "skill_save_enables";

export type ShellActionSpec = {
  id: string;
  method: string;
  path: string;
  when: ShellActionWhen;
};

export const SHELL_ACTIONS: readonly ShellActionSpec[] = [
  { id: "skills.user.write", method: "PUT", path: "/skills/user", when: "on-refusal" },
  { id: "skill.save", method: "POST", path: "/skills", when: "skill_save_enables" },
  { id: "skill.promote", method: "POST", path: "/skills/{skill_id}/promote", when: "always" },
  { id: "skill.import", method: "POST", path: "/skills/import", when: "always" },
  { id: "runtime.user_providers.write", method: "PUT", path: "/runtime/user-providers/{provider}", when: "always" },
  { id: "models.provider.key.set", method: "PUT", path: "/models/providers/{provider_id}/key", when: "always" },
  { id: "user.settings.save", method: "PUT", path: "/user/settings", when: "on-refusal" },
  { id: "platform.settings.save", method: "POST", path: "/platform/settings", when: "on-refusal" },
  { id: "workflow.run.escalations.approve", method: "POST", path: "/workflow-runs/{run_id}/escalations/{escalation_id}/approve", when: "always" },
  { id: "approval.submit", method: "POST", path: "/approvals", when: "approval_decision_approves" },
  { id: "computer_use.reset", method: "POST", path: "/computer-use/reset", when: "always" },
  { id: "workflow.trigger.create", method: "POST", path: "/workflow-definitions/{item_id}/triggers", when: "always" },
  { id: "workflow.schedule.create", method: "POST", path: "/workflow-definitions/{item_id}/schedules", when: "schedule_enabled" },
  { id: "workflow.schedule.toggle", method: "POST", path: "/schedules/{schedule_id}/toggle", when: "schedule_toggle_enables" },
  { id: "integration.catalog.install", method: "POST", path: "/integrations/catalog/{catalog_id}/install", when: "always" },
  { id: "integration.mcp.save", method: "POST", path: "/integrations/mcp", when: "always" },
  { id: "integration.mcp.approve", method: "POST", path: "/integrations/mcp/{connection_id}/approve", when: "always" },
  { id: "integration.oauth.connect", method: "POST", path: "/integrations/{integration_id}/oauth/connect", when: "always" },
  { id: "integration.save", method: "POST", path: "/integrations", when: "always" },
  { id: "guardrail.ruleset.publish", method: "POST", path: "/guardrail-rulesets/{item_id}/publish", when: "always" },
  { id: "guardrail.ruleset.activate", method: "POST", path: "/guardrail-rulesets/{item_id}/activate", when: "always" },
  { id: "guardrail.ruleset.rollback", method: "POST", path: "/guardrail-rulesets/{item_id}/rollback", when: "always" },
  { id: "guardrail.ruleset.archive", method: "POST", path: "/guardrail-rulesets/{item_id}/archive", when: "always" },
  { id: "guardrail.ruleset.delete", method: "DELETE", path: "/guardrail-rulesets/{item_id}", when: "always" },
  { id: "loop.enable", method: "POST", path: "/loop/enable", when: "always" },
  { id: "loop.autostart.enable", method: "POST", path: "/loop/autostart", when: "always" },
];

export const CONFIRMATION_CANCELLED_MESSAGE = "Cancelled in the confirmation dialog";

/** The human chose Cancel in the shell's native confirmation dialog. */
export class DesktopConfirmationCancelledError extends Error {
  constructor() {
    super(CONFIRMATION_CANCELLED_MESSAGE);
    this.name = "DesktopConfirmationCancelledError";
  }
}

/** The shell could not confirm or send the request (it says why). */
export class DesktopConfirmationError extends Error {
  constructor(reason: string) {
    super(`Desktop confirmation failed: ${reason}`);
    this.name = "DesktopConfirmationError";
  }
}

type TauriInvoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

/** The Tauri IPC `invoke` when running inside the desktop shell, else null. */
export function getDesktopInvoke(): TauriInvoke | null {
  if (typeof window === "undefined") {
    return null;
  }
  const tauri = (window as unknown as { __TAURI__?: { core?: { invoke?: TauriInvoke } } }).__TAURI__;
  return typeof tauri?.core?.invoke === "function" ? tauri.core.invoke : null;
}

function templateMatches(template: string, path: string): boolean {
  const expected = template.split("/");
  const actual = path.split("/");
  if (expected.length !== actual.length) {
    return false;
  }
  return expected.every((segment, index) => {
    if (segment.startsWith("{") && segment.endsWith("}")) {
      return actual[index].length > 0;
    }
    return segment === actual[index];
  });
}

/** The shell action for a backend call, or null when it never needs one. */
export function matchShellAction(method: string, path: string): ShellActionSpec | null {
  const verb = method.toUpperCase();
  const bare = path.split("?")[0];
  return SHELL_ACTIONS.find((spec) => spec.method === verb && templateMatches(spec.path, bare)) ?? null;
}

/** Python truthiness, so the body predicates agree with the backend's. */
function truthy(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length > 0;
  }
  if (value !== null && typeof value === "object") {
    return Object.keys(value).length > 0;
  }
  return Boolean(value);
}

/** Whether to confirm in the shell before sending (mirrors the backend's
 * body predicates; the backend stays the authority, see `isShellProofRefusal`). */
export function needsConfirmationUpfront(spec: ShellActionSpec, body: Record<string, unknown> | null): boolean {
  const fields = body ?? {};
  switch (spec.when) {
    case "always":
      return true;
    case "on-refusal":
      return false;
    case "approval_decision_approves":
      return String(fields.decision || "").trim().toLowerCase() === "approved";
    case "schedule_enabled":
      return "enabled" in fields ? truthy(fields.enabled) : true;
    case "schedule_toggle_enables":
      return "enabled" in fields ? truthy(fields.enabled) : true;
    case "skill_save_enables":
      return String(fields.status || "").trim() !== "disabled";
  }
}

/** The JSON object a request carries, or null when it has no body. */
export function requestBodyObject(body: BodyInit | null | undefined): Record<string, unknown> | null {
  if (body === undefined || body === null || body === "") {
    return null;
  }
  if (typeof body !== "string") {
    throw new DesktopConfirmationError("only JSON request bodies can be confirmed");
  }
  const parsed: unknown = JSON.parse(body);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new DesktopConfirmationError("the request body must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

/** A 403 from the backend because the desktop shell's proof was missing. */
export function isShellProofRefusal(status: number, details: string): boolean {
  return status === 403 && details.includes("Locus desktop app (missing_proof)");
}

/** Send the request through the shell's native confirmation dialog and
 * return the backend response the shell relays. */
export async function confirmViaDesktopShell<T>(
  invoke: TauriInvoke,
  spec: ShellActionSpec,
  path: string,
  body: Record<string, unknown> | null,
): Promise<T> {
  let relayed: unknown;
  try {
    relayed = await invoke("confirm_action", { action: spec.id, path, body });
  } catch (error) {
    const reason = typeof error === "string" ? error : error instanceof Error ? error.message : String(error);
    if (reason === "cancelled") {
      throw new DesktopConfirmationCancelledError();
    }
    throw new DesktopConfirmationError(reason);
  }
  const text = typeof relayed === "string" ? relayed : "";
  return (text.trim() ? JSON.parse(text) : {}) as T;
}
