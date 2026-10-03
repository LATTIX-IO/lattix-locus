"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { PlatformVersionStatus } from "@/types/locus";

/**
 * Bottom-of-sidebar platform version, update channel and update control.
 *
 * In the **desktop (Tauri) shell** the Tauri updater is the source of truth
 * (LOCUS-349, D-26). The shell has two channels, persisted in its config dir:
 *
 * - **Stable** (default): the shell checks in the background and emits
 *   `update-status`; when an update is available we show an "Update & Restart"
 *   banner and install only on click (`install_update_and_restart`).
 * - **Dev** (every merge to main): the shell downloads updates itself, waits
 *   until no agent run is active and the self-improvement loop is held, then
 *   installs and restarts. Here we only show its status.
 *
 * The channel select calls `set_update_channel` with "dev" or "stable"; the
 * shell maps those to its two compiled-in GitHub URLs (never a URL from here).
 *
 * In the **hosted / browser** context there is no Tauri shell, so we fall back
 * to the backend version manifest (`lattix update` guidance for the Docker
 * install). A full Settings page is LOCUS-353.
 */
type Channel = "dev" | "stable";

type UpdateState =
  | "idle"
  | "checking"
  | "up_to_date"
  | "available"
  | "downloading"
  | "waiting_for_idle"
  | "installing"
  | "error";

type UpdateStatus = {
  channel: Channel;
  state: UpdateState;
  current_version: string;
  version?: string | null;
  detail?: string;
};

type TauriApi = {
  core?: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  app?: { getVersion?: () => Promise<string> };
  event?: {
    listen?: (
      event: string,
      handler: (event: { payload: unknown }) => void,
    ) => Promise<() => void>;
  };
};

function noopSubscribe(): () => void {
  return () => {};
}

function getTauri(): TauriApi | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { __TAURI__?: TauriApi }).__TAURI__ ?? null;
}

const DEV_STATE_LABEL: Record<UpdateState, string> = {
  idle: "Dev channel",
  checking: "Dev: checking",
  up_to_date: "Dev: up to date",
  available: "Dev: update found",
  downloading: "Dev: downloading",
  waiting_for_idle: "Dev: waiting for runs to finish",
  installing: "Dev: installing",
  error: "Dev: update check failed",
};

/** A warning line for the confirm dialog when runs would be interrupted. */
async function activeRunWarning(): Promise<string> {
  try {
    const res = await fetch("/api/system/update/status", { credentials: "include" });
    if (!res.ok) return "";
    const data = (await res.json()) as {
      active_runs?: number;
      loop?: { lock_owner?: string | null };
    };
    const runs = Number(data?.active_runs) || 0;
    const parts: string[] = [];
    if (runs > 0) parts.push(`${runs} agent run${runs === 1 ? "" : "s"} in progress`);
    if (data?.loop?.lock_owner) parts.push("a self-improvement loop run in progress");
    return parts.length
      ? `\n\nWarning: ${parts.join(" and ")}. Restarting now interrupts them.`
      : "";
  } catch {
    return "";
  }
}

export function PlatformUpdatePanel({
  platformVersion,
}: {
  platformVersion?: PlatformVersionStatus | null;
}) {
  // Server render and first client render agree on "not desktop"; the Tauri bridge is client-only.
  const isDesktop = useSyncExternalStore(
    noopSubscribe,
    () => Boolean(getTauri()?.core?.invoke),
    () => false,
  );
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  // undefined = not yet checked, null = up to date, string = update available.
  const [checkedUpdate, setCheckedUpdate] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const tauri = getTauri();
    if (!tauri?.core?.invoke) return;
    const invoke = tauri.core.invoke;
    let cancelled = false;
    const unlisteners: Array<() => void> = [];

    tauri.app
      ?.getVersion?.()
      .then((v) => {
        if (!cancelled) setAppVersion(v);
      })
      .catch(() => {
        /* ignore — fall back to backend-reported version */
      });
    invoke("get_update_status")
      .then((s) => {
        if (!cancelled && s) setStatus(s as UpdateStatus);
      })
      .catch(() => {
        /* older shell without channels — the check below still works */
      });
    invoke("check_for_update")
      .then((v) => {
        if (!cancelled) setCheckedUpdate((v as string | null) ?? null);
      })
      .catch(() => {
        // No signed metadata published yet / offline — treat as up to date, stay quiet.
        if (!cancelled) setCheckedUpdate(null);
      });
    tauri.event
      ?.listen?.("update-status", (event) => {
        if (!cancelled) setStatus(event.payload as UpdateStatus);
      })
      .then((unlisten) => {
        if (cancelled) unlisten();
        else unlisteners.push(unlisten);
      })
      .catch(() => {
        /* events unavailable — the status from mount stays */
      });

    return () => {
      cancelled = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, []);

  const channel: Channel = status?.channel ?? "stable";

  async function changeChannel(next: Channel) {
    const tauri = getTauri();
    if (!tauri?.core?.invoke || next === channel) return;
    if (
      next === "dev" &&
      !window.confirm(
        "Switch to the Dev channel?\n\n" +
          "Dev builds are published on every merge to main and install automatically: " +
          "the app waits until no agent run is active, then restarts.",
      )
    ) {
      return;
    }
    try {
      const updated = await tauri.core.invoke("set_update_channel", { channel: next });
      if (updated) setStatus(updated as UpdateStatus);
      setCheckedUpdate(undefined);
    } catch (err) {
      window.alert(`Could not change the update channel: ${String(err)}`);
    }
  }

  async function updateNow(version: string) {
    const tauri = getTauri();
    if (!tauri?.core?.invoke) return;
    const warning = await activeRunWarning();
    if (
      !window.confirm(
        `Update Lattix Locus to v${version}?\n\n` +
          `The app will close, install the update, and restart automatically.\n` +
          `Your workflows, agents, and settings stay intact.` +
          warning,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await tauri.core.invoke("install_update_and_restart");
      // On success the app relaunches; this line is typically never reached.
    } catch (err) {
      setBusy(false);
      window.alert(`Update failed: ${String(err)}`);
    }
  }

  const currentLabel = appVersion
    ? `v${appVersion}`
    : platformVersion?.current_version
      ? `v${platformVersion.current_version}`
      : "Version unavailable";

  const backendUpdate =
    !isDesktop && platformVersion?.status === "update_available" ? platformVersion : null;
  // Stable banner: from the background check's event, or the check on mount.
  let stableUpdate: string | null = null;
  if (isDesktop && channel === "stable") {
    if (status?.state === "available" && status.version) stableUpdate = status.version;
    else if (typeof checkedUpdate === "string") stableUpdate = checkedUpdate;
  }

  const devTitle =
    status && channel === "dev"
      ? [status.version ? `v${status.version}` : "", status.detail ?? ""].filter(Boolean).join(" — ")
      : "";

  const channelSelect = isDesktop ? (
    <label className="flex items-center gap-1.5 px-1.5 text-[10px] text-[var(--fx-muted)]">
      <span>Updates</span>
      <select
        aria-label="Update channel"
        value={channel}
        onChange={(e) => changeChannel(e.target.value === "dev" ? "dev" : "stable")}
        className="rounded border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1 py-0.5 text-[10px] text-[var(--foreground)]"
      >
        <option value="stable">Stable</option>
        <option value="dev">Dev</option>
      </select>
      {channel === "dev" && status ? (
        <span className="min-w-0 flex-1 truncate" title={devTitle}>
          {DEV_STATE_LABEL[status.state] ?? "Dev channel"}
          {status.version && status.state !== "up_to_date" ? ` v${status.version}` : ""}
        </span>
      ) : null}
    </label>
  ) : null;

  // Compact bottom-of-nav control: a single muted version line, or a one-click
  // "Update & Restart" banner when a Stable update is available.
  if (stableUpdate) {
    return (
      <div className="space-y-1">
        <button
          type="button"
          onClick={() => updateNow(stableUpdate)}
          disabled={busy}
          title={`Update to v${stableUpdate} — the app restarts and applies it silently`}
          className="flex w-full items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--fx-primary-strong)_45%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_14%,var(--fx-sidebar))] px-2.5 py-2 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--fx-primary)_22%,var(--fx-sidebar))] disabled:opacity-60"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-[var(--fx-primary-strong)]" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
            <path d="M12 4v10M8 10l4 4 4-4M5 19h14" />
          </svg>
          <span className="min-w-0 flex-1 leading-tight">
            <span className="block text-[11px] font-semibold text-[var(--foreground)]">
              {busy ? "Updating…" : "Update available: Update & Restart"}
            </span>
            <span className="block truncate text-[10px] text-[var(--fx-muted)]">
              {currentLabel} → v{stableUpdate}
            </span>
          </span>
        </button>
        {channelSelect}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 px-1.5 py-1">
        <span className="truncate text-[11px] text-[var(--fx-muted)]">
          {backendUpdate ? `Update v${backendUpdate.latest_version} available` : "Lattix Locus"}
        </span>
        <span
          className="shrink-0 rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[var(--foreground)]"
          title={backendUpdate ? `Run: ${backendUpdate.update_command}` : "Current build"}
        >
          {currentLabel}
        </span>
      </div>
      {channelSelect}
    </div>
  );
}
