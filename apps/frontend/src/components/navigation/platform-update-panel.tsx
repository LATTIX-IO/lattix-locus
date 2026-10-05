"use client";

import { DownloadIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getSystemUpdateStatus } from "@/lib/api";
import {
  checkDesktopUpdate,
  getDesktopUpdateStatus,
  getTauriGlobal,
  installDesktopUpdate,
  setDesktopUpdateChannel,
  useIsDesktopShell,
  type DesktopUpdateState,
  type DesktopUpdateStatus,
  type UpdateChannel,
} from "@/lib/desktop-shell";
import type { PlatformVersionStatus } from "@/types/locus";

/**
 * Platform version, update channel and update control (LOCUS-349, D-26).
 *
 * In the **desktop shell** the Tauri updater is the source of truth. Two
 * channels, persisted by the shell: **Stable** (default) and **Dev** (updates
 * published from main). Both channels only check in the background; deployment
 * always starts with the user's click. The channel maps to the shell's
 * compiled-in feeds, never a URL from here.
 *
 * In the **web / hosted** context the backend version manifest applies
 * (`lattix update` guidance).
 */
const STATE_LABEL: Record<DesktopUpdateState, string> = {
  idle: "Idle",
  checking: "Checking",
  up_to_date: "Up to date",
  available: "Update found",
  downloading: "Downloading",
  waiting_for_idle: "Waiting for runs to finish",
  installing: "Installing",
  error: "Update check failed",
};

/** Explain that a confirmed install waits for active work to finish. */
async function activeRunWarning(): Promise<string> {
  try {
    const data = await getSystemUpdateStatus();
    const runs = Number(data?.active_runs) || 0;
    const parts: string[] = [];
    if (runs > 0) parts.push(`${runs} agent run${runs === 1 ? "" : "s"} in progress`);
    if (data?.loop?.lock_owner) parts.push("a self-improvement loop run in progress");
    return parts.length
      ? `\n\n${parts.join(" and ")} will finish before the update installs. If the app cannot become idle within 30 minutes, the update will stop.`
      : "";
  } catch {
    return "\n\nLocus will wait for current work to finish before installing. If the app cannot become idle within 30 minutes, the update will stop.";
  }
}

export function usePlatformUpdates(platformVersion?: PlatformVersionStatus | null) {
  const isDesktop = useIsDesktopShell();
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [status, setStatus] = useState<DesktopUpdateStatus | null>(null);
  // undefined = not yet checked, null = up to date, string = update available.
  const [checkedUpdate, setCheckedUpdate] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isDesktop) return;
    const tauri = getTauriGlobal();
    let cancelled = false;
    const unlisteners: Array<() => void> = [];

    tauri?.app
      ?.getVersion?.()
      .then((version) => {
        if (!cancelled) setAppVersion(version);
      })
      .catch(() => {
        /* fall back to the backend-reported version */
      });
    getDesktopUpdateStatus()
      .then((next) => {
        if (!cancelled && next) setStatus(next);
      })
      .catch(() => {
        /* older shell without channels: the check below still works */
      });
    checkDesktopUpdate()
      .then((version) => {
        if (!cancelled) setCheckedUpdate(version);
      })
      .catch(() => {
        // No signed metadata published yet, or offline: nothing to install.
        if (!cancelled) setCheckedUpdate(null);
      });
    tauri?.event
      ?.listen?.("update-status", (event) => {
        if (!cancelled) setStatus(event.payload as DesktopUpdateStatus);
      })
      .then((unlisten) => {
        if (cancelled) unlisten();
        else unlisteners.push(unlisten);
      })
      .catch(() => {
        /* events unavailable: the status from mount stays */
      });

    return () => {
      cancelled = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, [isDesktop]);

  const channel: UpdateChannel = status?.channel ?? "stable";

  const changeChannel = useCallback(
    async (next: UpdateChannel, { confirm = true }: { confirm?: boolean } = {}) => {
      if (!isDesktop || next === channel) return;
      if (
        confirm &&
        next === "dev" &&
        !window.confirm(
          "Switch to the Dev channel?\n\nDev builds are published from main. Locus will let you know when one is available; it will only install after you choose Update & Restart.",
        )
      ) {
        return;
      }
      setError(null);
      try {
        const updated = await setDesktopUpdateChannel(next);
        if (updated) setStatus(updated);
        setCheckedUpdate(undefined);
      } catch (reason) {
        setError(`Could not change the update channel: ${String(reason)}`);
      }
    },
    [channel, isDesktop],
  );

  const checkNow = useCallback(async () => {
    setError(null);
    try {
      setCheckedUpdate(await checkDesktopUpdate());
    } catch (reason) {
      setError(`Update check failed: ${String(reason)}`);
    }
  }, []);

  const updateNow = useCallback(async (version: string) => {
    const warning = await activeRunWarning();
    if (
      !window.confirm(
        `Update Lattix Locus to v${version}?\n\nThe app will close, install the update, and restart automatically.\nYour workflows, agents, and settings stay intact.${warning}`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await installDesktopUpdate();
      // On success the app relaunches; this line is typically never reached.
    } catch (reason) {
      setBusy(false);
      setError(`Update failed: ${String(reason)}`);
    }
  }, []);

  const currentLabel = appVersion
    ? `v${appVersion}`
    : platformVersion?.current_version
      ? `v${platformVersion.current_version}`
      : "Version unavailable";
  const backendUpdate = !isDesktop && platformVersion?.status === "update_available" ? platformVersion : null;
  const availableUpdate = isDesktop
    ? status?.state === "available" && status.version
      ? status.version
      : typeof checkedUpdate === "string"
        ? checkedUpdate
        : null
    : null;

  return {
    isDesktop,
    status,
    channel,
    checkedUpdate,
    availableUpdate,
    backendUpdate,
    currentLabel,
    busy,
    error,
    changeChannel,
    checkNow,
    updateNow,
  };
}

/** Bottom-of-sidebar version line, or a one-click "Update & Restart" banner. */
export function PlatformUpdatePanel({ platformVersion }: { platformVersion?: PlatformVersionStatus | null }) {
  const updates = usePlatformUpdates(platformVersion);

  if (updates.availableUpdate) {
    const version = updates.availableUpdate;
    return (
      <button
        type="button"
        onClick={() => void updates.updateNow(version)}
        disabled={updates.busy}
        title={`Update to v${version}: the app restarts and applies it`}
        className="flex w-full items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--fx-primary-strong)_45%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_14%,var(--fx-sidebar))] px-2.5 py-2 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--fx-primary)_22%,var(--fx-sidebar))] disabled:opacity-60"
      >
        <DownloadIcon aria-hidden="true" className="size-4 shrink-0 text-[var(--fx-primary-strong)]" />
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block text-[11px] font-semibold text-[var(--foreground)]">
            {updates.busy ? "Updating…" : "Update available: Update & Restart"}
          </span>
          <span className="block truncate text-[10px] text-[var(--fx-muted)]">
            {updates.currentLabel} → v{version}
          </span>
        </span>
      </button>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2 px-1.5 py-1">
      <span className="truncate text-[11px] text-[var(--fx-muted)]">
        {updates.backendUpdate
          ? `Update v${updates.backendUpdate.latest_version} available`
          : updates.isDesktop && updates.channel === "dev"
            ? `Dev: ${STATE_LABEL[updates.status?.state ?? "idle"]}`
            : "Lattix Locus"}
      </span>
      <span
        className="shrink-0 rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[var(--foreground)]"
        title={updates.backendUpdate ? `Run: ${updates.backendUpdate.update_command}` : "Current build"}
      >
        {updates.currentLabel}
      </span>
    </div>
  );
}

/** The full update control used by Settings → Updates and the setup wizard. */
export function UpdatesPanel({ platformVersion }: { platformVersion?: PlatformVersionStatus | null }) {
  const updates = usePlatformUpdates(platformVersion);

  if (!updates.isDesktop) {
    return (
      <div className="flex flex-col gap-2 text-[13px]">
        <p>
          Running <span className="font-mono">{updates.currentLabel}</span>
          {platformVersion?.latest_version ? (
            <>
              ; latest <span className="font-mono">v{platformVersion.latest_version}</span>
            </>
          ) : null}
          .
        </p>
        {updates.backendUpdate ? (
          <p className="text-muted-foreground">
            Update with <code className="font-mono">{updates.backendUpdate.update_command}</code> on the host.
          </p>
        ) : (
          <p className="text-muted-foreground">{platformVersion?.summary ?? "Version metadata is unavailable."}</p>
        )}
        <p className="text-xs text-muted-foreground">Update channels are managed by the desktop app.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-[13px] font-medium">Channel</legend>
        {(
          [
            { value: "stable", label: "Stable", hint: "Recommended releases. Locus checks in the background and waits for you to deploy." },
            { value: "dev", label: "Dev", hint: "Previews from main. Locus checks in the background and waits for you to deploy." },
          ] as const
        ).map((option) => (
          <label
            key={option.value}
            className="flex cursor-pointer items-start gap-3 rounded-[10px] border border-border px-3 py-2.5 has-[:checked]:border-primary has-[:checked]:bg-primary/5"
          >
            <input
              type="radio"
              name="update-channel"
              value={option.value}
              checked={updates.channel === option.value}
              onChange={() => void updates.changeChannel(option.value)}
              className="mt-1"
            />
            <span>
              <span className="text-[13px] font-medium">{option.label}</span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{option.hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{updates.currentLabel}</span>
          <Badge variant={updates.availableUpdate ? "warning" : "secondary"}>
            {updates.availableUpdate
              ? `v${updates.availableUpdate} available`
              : updates.status
                ? STATE_LABEL[updates.status.state]
                : updates.checkedUpdate === null
                  ? "Up to date"
                  : "Not checked"}
          </Badge>
          {updates.status?.detail ? <span className="text-xs text-muted-foreground">{updates.status.detail}</span> : null}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => void updates.checkNow()}>
            Check now
          </Button>
          {updates.availableUpdate ? (
            <Button size="sm" disabled={updates.busy} onClick={() => void updates.updateNow(updates.availableUpdate as string)}>
              {updates.busy ? "Updating…" : "Update & Restart"}
            </Button>
          ) : null}
        </div>
      </div>
      {updates.error ? (
        <p role="alert" className="text-xs text-destructive">
          {updates.error}
        </p>
      ) : null}
    </div>
  );
}
