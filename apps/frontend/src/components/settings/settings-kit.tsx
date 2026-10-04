"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  DesktopConfirmationCancelledError,
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

/** A save error in words. A cancelled shell dialog is not a failure. */
export function describeSaveError(error: unknown, fallback = "Could not save the change."): string {
  if (error instanceof DesktopConfirmationCancelledError) {
    return "Not saved: cancelled in the confirmation dialog.";
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

/* ----------------------------- data hooks ---------------------------- */

export type PlatformResource = {
  settings: PlatformSettings | null;
  policy: SecurityPolicyResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  /** Save only the given fields (the backend merges). Widening changes are
   * confirmed in the desktop shell by lib/api.ts. */
  save: (patch: Partial<PlatformSettings>) => Promise<void>;
};

export function usePlatformResource({ withPolicy = false }: { withPolicy?: boolean } = {}): PlatformResource {
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

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
      await savePlatformSettings(patch as Record<string, unknown>);
      setSettings((current) => (current ? { ...current, ...patch } : current));
      if (withPolicy) {
        setPolicy(await getPlatformSecurityPolicy());
      }
    },
    [withPolicy],
  );

  return { settings, policy, loading, error, reload, save };
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
