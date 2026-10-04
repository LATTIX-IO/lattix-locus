"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  DesktopConfirmationCancelledError,
  SecurityChangeConfirmationRequired,
  getPlatformSecurityPolicy,
  getPlatformSettings,
  savePlatformSettings,
} from "@/lib/api";
import type { ControlState, ControlStatusItem, PlatformSettings, SecurityPolicyResponse } from "@/types/locus";

/* ------------------------------ helpers ------------------------------ */

export function parseList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

export function toListText(values?: string[] | boolean | null): string {
  return Array.isArray(values) ? values.join("\n") : "";
}

export function positiveNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? Math.trunc(parsed) : fallback;
}

/** The person chose "Cancel" in the in-app security-change confirmation. */
export class SecurityChangeDeclinedError extends Error {
  constructor() {
    super("Not saved: the security change was not confirmed.");
    this.name = "SecurityChangeDeclinedError";
  }
}

/** A save error in words. A cancelled shell dialog is not a failure. */
export function describeSaveError(error: unknown, fallback = "Could not save the change."): string {
  if (error instanceof DesktopConfirmationCancelledError) {
    return "Not saved: cancelled in the confirmation dialog.";
  }
  if (error instanceof SecurityChangeDeclinedError) {
    return error.message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

/* ------------------------ sensitive settings ------------------------- */

/** Friendly names and risks for the platform settings the backend flags as
 * sensitive (`changed_sensitive_keys` on POST /platform/settings). An unknown
 * key is still shown, by its name, so nothing is confirmed unseen. */
export const SENSITIVE_SETTING_LABELS: Record<string, { label: string; risk: string }> = {
  require_authenticated_requests: { label: "Require signed-in requests", risk: "Changes who may call the Locus API." },
  a2a_require_signed_messages: { label: "Require signed agent-to-agent messages", risk: "Changes whether other agents must sign what they send." },
  a2a_replay_protection: { label: "Agent-to-agent replay protection", risk: "Changes whether replayed agent messages are refused." },
  require_signed_integrations: { label: "Require signed integrations", risk: "Changes whether unsigned integrations may be installed." },
  allow_local_unsigned_integrations: { label: "Allow local unsigned integrations", risk: "Unsigned integrations on this machine may run." },
  enforce_local_network_only: { label: "Local network only", risk: "Changes whether the agents may reach beyond the local network." },
  enforce_egress_allowlist: { label: "Enforce the egress allowlist", risk: "Changes whether the agents may reach hosts outside the allowlist." },
  mcp_require_local_server: { label: "Local MCP servers only", risk: "Changes whether the agents may use remote MCP servers." },
  retrieval_require_local_source_url: { label: "Local retrieval sources only", risk: "Changes where retrieval may read from." },
  emergency_read_only_mode: { label: "Emergency read-only mode", risk: "Changes whether the platform accepts any change at all." },
  block_new_runs: { label: "Block new runs", risk: "Changes whether new runs may start." },
  block_graph_runs: { label: "Block graph runs", risk: "Changes whether workflow graphs may run." },
  block_tool_calls: { label: "Block tool calls", risk: "Changes whether the agents may call tools." },
  block_retrieval_calls: { label: "Block retrieval", risk: "Changes whether the agents may retrieve knowledge." },
  telemetry_capture_content: {
    label: "Capture prompt and output content",
    risk: "Traces will record the prompts and outputs of every run on this machine, redacted for PII before they are stored, and keep them for the retention you set.",
  },
  telemetry_payload_retention_days: { label: "Content retention", risk: "Captured content is kept longer." },
  telemetry_otlp_enabled: { label: "OpenTelemetry (OTLP) export", risk: "Copies of traces are sent to the OTLP collector." },
  telemetry_otlp_endpoint: { label: "OTLP endpoint", risk: "Traces are sent to a new collector address." },
  telemetry_otlp_auth_secret_ref: { label: "OTLP auth secret", risk: "A stored secret is sent to the collector." },
  telemetry_langsmith_enabled: { label: "LangSmith export", risk: "Traces leave this machine for a hosted, proprietary service." },
  telemetry_langsmith_endpoint: { label: "LangSmith endpoint", risk: "Traces are sent to a new hosted address." },
  telemetry_langsmith_project: { label: "LangSmith project", risk: "Traces go to another LangSmith project." },
  telemetry_langsmith_api_key_ref: { label: "LangSmith API key secret", risk: "A stored secret is sent to LangSmith." },
};

export function describeSensitiveSetting(key: string): { label: string; risk: string } {
  return SENSITIVE_SETTING_LABELS[key] ?? { label: key, risk: "A security-relevant platform setting." };
}

type PendingSecurityChange = {
  keys: string[];
  confirm: () => void;
  decline: () => void;
};

/** The in-app confirmation for a sensitive platform-settings change. On the
 * desktop a widening change is then confirmed again in the shell's native
 * dialog (lib/api.ts), which is the authority; this dialog explains the risk. */
export function SecurityChangeDialog({ pending }: { pending: PendingSecurityChange | null }) {
  return (
    <Dialog open={pending !== null} onOpenChange={(open) => (!open ? pending?.decline() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Confirm a security change</DialogTitle>
          <DialogDescription>
            These settings change what the agents may do or what is recorded. Review them before you save.
          </DialogDescription>
        </DialogHeader>
        <ul className="flex flex-col gap-2 text-[13px]" aria-label="Sensitive settings in this change">
          {(pending?.keys ?? []).map((key) => {
            const { label, risk } = describeSensitiveSetting(key);
            return (
              <li key={key} className="rounded-[10px] border border-border px-3 py-2">
                <p className="font-medium">{label}</p>
                <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{risk}</p>
              </li>
            );
          })}
        </ul>
        <DialogFooter>
          <Button variant="secondary" onClick={() => pending?.decline()}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={() => pending?.confirm()}>
            Confirm and save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Save platform settings; when the backend answers that the change touches
 * sensitive settings (`changed_sensitive_keys`), ask in `SecurityChangeDialog`
 * and resend with `confirm_security_change: true`. Declining rejects with
 * `SecurityChangeDeclinedError`. Returns the saver and the dialog to render.
 */
export function useConfirmedPlatformSave(): {
  savePatch: (patch: Record<string, unknown>) => Promise<void>;
  confirmationDialog: ReactNode;
} {
  const [pending, setPending] = useState<PendingSecurityChange | null>(null);
  const pendingRef = useRef<PendingSecurityChange | null>(null);

  const savePatch = useCallback(async (patch: Record<string, unknown>) => {
    try {
      await savePlatformSettings(patch);
      return;
    } catch (error) {
      if (!(error instanceof SecurityChangeConfirmationRequired)) {
        throw error;
      }
      const confirmed = await new Promise<boolean>((resolve) => {
        const next: PendingSecurityChange = {
          keys: error.keys,
          confirm: () => resolve(true),
          decline: () => resolve(false),
        };
        pendingRef.current = next;
        setPending(next);
      });
      pendingRef.current = null;
      setPending(null);
      if (!confirmed) {
        throw new SecurityChangeDeclinedError();
      }
    }
    await savePlatformSettings(patch, { confirmSecurityChange: true });
  }, []);

  // An unmounted section must not leave a save waiting on a dialog nobody sees.
  useEffect(() => () => pendingRef.current?.decline(), []);

  return { savePatch, confirmationDialog: <SecurityChangeDialog pending={pending} /> };
}

/* ----------------------------- data hooks ---------------------------- */

export type PlatformResource = {
  settings: PlatformSettings | null;
  policy: SecurityPolicyResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** Save only the given fields (the backend merges). A change to sensitive
   * settings is confirmed in `confirmationDialog` first; widening changes are
   * then confirmed in the desktop shell by lib/api.ts. */
  save: (patch: Partial<PlatformSettings>) => Promise<void>;
  /** Render this in the section: the sensitive-change confirmation. */
  confirmationDialog: ReactNode;
};

export function usePlatformResource({ withPolicy = false }: { withPolicy?: boolean } = {}): PlatformResource {
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const { savePatch, confirmationDialog } = useConfirmedPlatformSave();

  useEffect(() => {
    let cancelled = false;
    Promise.all([getPlatformSettings(), withPolicy ? getPlatformSecurityPolicy() : Promise.resolve(null)])
      .then(([nextSettings, nextPolicy]) => {
        if (cancelled) return;
        setSettings(nextSettings);
        setPolicy(nextPolicy);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Could not load settings.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, withPolicy]);

  const reload = useCallback(() => {
    setLoading(true);
    setToken((value) => value + 1);
  }, []);

  const save = useCallback(
    async (patch: Partial<PlatformSettings>) => {
      await savePatch(patch as Record<string, unknown>);
      setSettings((current) => (current ? { ...current, ...patch } : current));
      if (withPolicy) {
        setPolicy(await getPlatformSecurityPolicy());
      }
    },
    [savePatch, withPolicy],
  );

  return { settings, policy, loading, error, reload, save, confirmationDialog };
}

/* ------------------------------ layout ------------------------------- */

export function SectionHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">{title}</h2>
        {description ? <p className="mt-0.5 text-[13px] text-muted-foreground">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function SettingsGroup({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card role="group" aria-label={title}>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle>{title}</CardTitle>
          {description ? <CardDescription>{description}</CardDescription> : null}
        </div>
        {actions}
      </CardHeader>
      {children}
    </Card>
  );
}

export function LoadState({ loading, error, onRetry }: { loading: boolean; error: string | null; onRetry: () => void }) {
  if (loading) {
    return (
      <p role="status" className="text-[13px] text-muted-foreground">
        Loading…
      </p>
    );
  }
  if (error) {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px]">
        <span>Could not load these settings: {error}</span>
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  return null;
}

export function SaveBar({
  dirty,
  saving,
  message,
  onSave,
  onReset,
  label = "Save changes",
}: {
  dirty: boolean;
  saving: boolean;
  message: { tone: "success" | "error"; text: string } | null;
  onSave: () => void;
  onReset?: () => void;
  label?: string;
}) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {message ? (
        <p role={message.tone === "error" ? "alert" : "status"} className={message.tone === "error" ? "text-[13px] text-destructive" : "text-[13px] text-muted-foreground"}>
          {message.text}
        </p>
      ) : null}
      {onReset ? (
        <Button variant="ghost" size="sm" onClick={onReset} disabled={!dirty || saving}>
          Reset
        </Button>
      ) : null}
      <Button size="sm" onClick={onSave} disabled={!dirty || saving} aria-busy={saving}>
        {saving ? "Saving…" : label}
      </Button>
    </div>
  );
}

/** Draft state for a group of fields, with save/reset and a status line. */
export function useDraft<T extends Record<string, unknown>>(initial: T | null) {
  const [draft, setDraft] = useState<T | null>(initial);
  const [baseline, setBaseline] = useState<T | null>(initial);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  // Adopt a newly loaded baseline (the render-time update pattern, no effect).
  const [seen, setSeen] = useState<T | null>(initial);
  if (initial !== seen) {
    setSeen(initial);
    setDraft(initial);
    setBaseline(initial);
  }

  const dirty = Boolean(draft && baseline && JSON.stringify(draft) !== JSON.stringify(baseline));

  function update<K extends keyof T>(key: K, value: T[K]) {
    setMessage(null);
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  }

  async function commit(persist: (draft: T) => Promise<void>, successText = "Saved.") {
    if (!draft) return;
    setSaving(true);
    setMessage(null);
    try {
      await persist(draft);
      setBaseline(draft);
      setMessage({ tone: "success", text: successText });
    } catch (error) {
      setMessage({ tone: "error", text: describeSaveError(error) });
    } finally {
      setSaving(false);
    }
  }

  function reset() {
    setDraft(baseline);
    setMessage(null);
  }

  return { draft, dirty, saving, message, update, commit, reset };
}

/* ------------------------------ fields ------------------------------- */

export function ToggleRow({
  id,
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
}: {
  id: string;
  label: string;
  description?: string;
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-[10px] border border-border px-3 py-2.5">
      <div className="min-w-0">
        <Label htmlFor={id}>{label}</Label>
        {description ? (
          <p id={`${id}-description`} className="mt-1 text-xs leading-5 text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-describedby={description ? `${id}-description` : undefined}
      />
    </div>
  );
}

export function TextField({
  id,
  label,
  description,
  value,
  onChange,
  placeholder,
  type = "text",
  inputMode,
  autoComplete,
}: {
  id: string;
  label: string;
  description?: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  type?: string;
  inputMode?: "numeric" | "text" | "url";
  autoComplete?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        inputMode={inputMode}
        autoComplete={autoComplete}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={description ? `${id}-description` : undefined}
      />
      {description ? (
        <p id={`${id}-description`} className="text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      ) : null}
    </div>
  );
}

export function ListField({
  id,
  label,
  description,
  value,
  onChange,
  placeholder,
}: {
  id: string;
  label: string;
  description?: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={`${id}-description`}
        className="min-h-20 font-mono text-[12px]"
      />
      <p id={`${id}-description`} className="text-xs leading-5 text-muted-foreground">
        {description ? `${description} ` : ""}One per line.
      </p>
    </div>
  );
}

export function SelectField({
  id,
  label,
  description,
  value,
  onValueChange,
  options,
}: {
  id: string;
  label: string;
  description?: string;
  value: string;
  onValueChange: (next: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select value={value} onValueChange={onValueChange}>
        <SelectTrigger id={id} aria-describedby={description ? `${id}-description` : undefined}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {description ? (
        <p id={`${id}-description`} className="text-xs leading-5 text-muted-foreground">
          {description}
        </p>
      ) : null}
    </div>
  );
}

/* --------------------------- control status -------------------------- */

const CONTROL_BADGE: Record<ControlState, { label: string; variant: "success" | "warning" | "secondary" | "outline"; glyph: string }> = {
  enforced: { label: "Enforced", variant: "success", glyph: "●" },
  degraded: { label: "Degraded", variant: "warning", glyph: "◐" },
  off: { label: "Off", variant: "secondary", glyph: "○" },
  unverified: { label: "Unverified", variant: "outline", glyph: "?" },
};

/** One backend-reported control, never upgraded by the UI (P9). */
export function ControlRow({ control, fallbackLabel }: { control: ControlStatusItem | undefined; fallbackLabel: string }) {
  const state: ControlState = control && control.state in CONTROL_BADGE ? control.state : "unverified";
  const badge = CONTROL_BADGE[state];
  return (
    <div className="rounded-[10px] border border-border px-3 py-2.5" data-control-id={control?.id}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium">{control?.label ?? fallbackLabel}</span>
        <Badge variant={badge.variant}>
          <span aria-hidden="true">{badge.glyph}</span>
          {badge.label}
        </Badge>
      </div>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{control?.evidence || "Not reported by the backend."}</p>
    </div>
  );
}

export function findControl(policy: SecurityPolicyResponse | null, id: string): ControlStatusItem | undefined {
  return policy?.control_status?.controls?.find((control) => control.id === id);
}
