"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createWorkflowSchedule,
  createWorkflowTrigger,
  deleteWorkflowSchedule,
  getWorkflowSchedules,
  getWorkflowTriggers,
  revokeWorkflowTrigger,
  toggleWorkflowSchedule,
  type WorkflowSchedule,
  type WorkflowTrigger,
} from "@/lib/api";

/**
 * Webhook trigger management for a workflow (reference-plan Phase D).
 * Creating a trigger returns a token exactly once; the manager surfaces the
 * full webhook URL only in that moment, then keeps fingerprints thereafter.
 */
export function WorkflowTriggersManager({
  workflowId,
  apiBaseHint,
}: {
  workflowId: string;
  apiBaseHint?: string;
}) {
  const [triggers, setTriggers] = useState<WorkflowTrigger[]>([]);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newSecret, setNewSecret] = useState<{ url: string; token: string } | null>(null);
  const [schedules, setSchedules] = useState<WorkflowSchedule[]>([]);
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [cronLabel, setCronLabel] = useState("");
  const [scheduleBusy, setScheduleBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [triggerList, scheduleList] = await Promise.all([
        getWorkflowTriggers(workflowId),
        getWorkflowSchedules(workflowId),
      ]);
      setTriggers(triggerList);
      setSchedules(scheduleList);
      setError(null);
    } catch {
      setError("Unable to load triggers.");
    }
  }, [workflowId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function addTrigger() {
    setBusy(true);
    setError(null);
    try {
      const result = await createWorkflowTrigger(workflowId, label.trim() || "Webhook trigger");
      const base = apiBaseHint ?? "";
      setNewSecret({ url: `${base}${result.webhook_url}`, token: result.token });
      setLabel("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create trigger.");
    } finally {
      setBusy(false);
    }
  }

  async function addSchedule() {
    setScheduleBusy(true);
    setError(null);
    try {
      await createWorkflowSchedule(workflowId, cron.trim(), cronLabel.trim() || "Scheduled run");
      setCronLabel("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to create schedule (check the cron expression).");
    } finally {
      setScheduleBusy(false);
    }
  }

  return (
    <>
    <article className="fx-panel p-3 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Webhook triggers</h2>
        <span className="fx-muted text-[11px]">Start this workflow from an external system</span>
      </div>
      <p className="fx-muted mb-3 leading-5">
        Each token is a standalone credential — a <code className="font-mono">POST</code> to its URL
        starts a run as the trigger&apos;s owner, with all guardrails applied. The full URL is shown once
        at creation. Requires the workflow to be published.
      </p>

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <label className="block flex-1">
          <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Label</span>
          <input
            className="fx-field h-8 w-full px-2"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="CI pipeline, Zapier, ..."
          />
        </label>
        <button
          type="button"
          disabled={busy}
          onClick={() => void addTrigger()}
          className="fx-btn-primary px-3 py-1.5 font-medium disabled:opacity-60"
        >
          {busy ? "Creating..." : "Create trigger"}
        </button>
      </div>

      {newSecret ? (
        <div className="mb-3 rounded border border-[hsl(var(--state-warning)/0.45)] bg-[hsl(var(--state-warning)/0.1)] p-2">
          <p className="font-semibold text-[var(--foreground)]">Copy this webhook URL now — it won&apos;t be shown again:</p>
          <code className="mt-1 block break-all font-mono text-[11px] text-[var(--foreground)]">
            {newSecret.url}
          </code>
          <button
            type="button"
            className="fx-btn-secondary mt-2 px-2 py-0.5 text-[11px]"
            onClick={() => {
              void navigator.clipboard?.writeText(newSecret.url);
            }}
          >
            Copy URL
          </button>
        </div>
      ) : null}

      {error ? <p className="mb-2 text-[hsl(var(--state-critical))]">{error}</p> : null}

      {triggers.length === 0 ? (
        <p className="fx-muted">No triggers configured.</p>
      ) : (
        <ul className="space-y-1.5">
          {triggers.map((trigger, index) => (
            <li
              key={`${trigger.token_fingerprint}-${index}`}
              className="flex items-center justify-between gap-2 border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-2 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-[var(--foreground)]">{trigger.label}</p>
                <p className="fx-muted">
                  token {trigger.token_fingerprint} · {trigger.created_at}
                </p>
              </div>
              <button
                type="button"
                className="fx-btn-secondary shrink-0 px-2 py-1 text-[11px]"
                onClick={async () => {
                  try {
                    await revokeWorkflowTrigger(trigger.id);
                    await refresh();
                  } catch {
                    setError("Unable to revoke trigger.");
                  }
                }}
              >
                Revoke
              </button>
            </li>
          ))}
        </ul>
      )}
    </article>

    <article className="fx-panel p-3 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Scheduled triggers</h2>
        <span className="fx-muted text-[11px]">Run this workflow on a cron cadence</span>
      </div>
      <p className="fx-muted mb-3 leading-5">
        Cron format: <code className="font-mono">minute hour day month weekday</code> (UTC). The
        scheduler fires at minute resolution; the workflow must be published.
      </p>

      <div className="mb-3 flex flex-wrap items-end gap-2">
        <label className="block">
          <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Cron</span>
          <input
            className="fx-field h-8 w-40 px-2 font-mono"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            placeholder="0 9 * * 1-5"
          />
        </label>
        <label className="block flex-1">
          <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Label</span>
          <input
            className="fx-field h-8 w-full px-2"
            value={cronLabel}
            onChange={(e) => setCronLabel(e.target.value)}
            placeholder="Weekday morning digest"
          />
        </label>
        <button
          type="button"
          disabled={scheduleBusy}
          onClick={() => void addSchedule()}
          className="fx-btn-primary px-3 py-1.5 font-medium disabled:opacity-60"
        >
          {scheduleBusy ? "Creating..." : "Add schedule"}
        </button>
      </div>

      {schedules.length === 0 ? (
        <p className="fx-muted">No schedules configured.</p>
      ) : (
        <ul className="space-y-1.5">
          {schedules.map((schedule) => (
            <li
              key={schedule.id}
              className="flex items-center justify-between gap-2 border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-2 py-1.5"
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-[var(--foreground)]">
                  {schedule.label} <code className="font-mono text-[11px]">{schedule.cron}</code>
                </p>
                <p className="fx-muted">
                  {schedule.enabled ? "enabled" : "disabled"}
                  {schedule.last_fired_minute ? ` · last fired ${schedule.last_fired_minute}` : ""}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  type="button"
                  className="fx-btn-secondary px-2 py-1 text-[11px]"
                  onClick={async () => {
                    try {
                      await toggleWorkflowSchedule(schedule.id, !schedule.enabled);
                      await refresh();
                    } catch {
                      setError("Unable to toggle schedule.");
                    }
                  }}
                >
                  {schedule.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  type="button"
                  className="fx-btn-secondary px-2 py-1 text-[11px]"
                  onClick={async () => {
                    try {
                      await deleteWorkflowSchedule(schedule.id);
                      await refresh();
                    } catch {
                      setError("Unable to delete schedule.");
                    }
                  }}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </article>
    </>
  );
}
