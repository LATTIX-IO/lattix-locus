"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { PlatformVersionStatus } from "@/types/frontier";

/**
 * Bottom-of-sidebar platform version + update control.
 *
 * In the **desktop (Tauri) shell** the Tauri updater is the source of truth:
 * we ask the shell whether an update is available (`check_for_update`) and, if
 * so, render a single "Update & Restart" button. Clicking it confirms, then the
 * shell silently downloads + installs the signed update and relaunches
 * (`install_update_and_restart`) — no terminal, no manual steps.
 *
 * In the **hosted / browser** context there is no Tauri shell, so we fall back
 * to the backend version manifest (which carries the `lattix update` CLI guidance
 * for the Docker-stack install). The backend manifest is intentionally ignored in
 * desktop mode because the frozen sidecar can't report its own packaged version.
 */
type TauriApi = {
  core?: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
  app?: { getVersion?: () => Promise<string> };
};

function noopSubscribe(): () => void {
  return () => {};
}

function getTauri(): TauriApi | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { __TAURI__?: TauriApi }).__TAURI__ ?? null;
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
  // undefined = not yet checked, null = up to date, string = update available.
  const [tauriUpdate, setTauriUpdate] = useState<string | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const tauri = getTauri();
    if (!tauri?.core?.invoke) return;
    let cancelled = false;
    tauri.app
      ?.getVersion?.()
      .then((v) => {
        if (!cancelled) setAppVersion(v);
      })
      .catch(() => {
        /* ignore — fall back to backend-reported version */
      });
    tauri.core
      .invoke("check_for_update")
      .then((v) => {
        if (!cancelled) setTauriUpdate((v as string | null) ?? null);
      })
      .catch(() => {
        // No updater reachable / offline — treat as up to date, stay quiet.
        if (!cancelled) setTauriUpdate(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function updateNow(version: string) {
    const tauri = getTauri();
    if (!tauri?.core?.invoke) return;
    if (
      !window.confirm(
        `Update Lattix xFrontier to v${version}?\n\n` +
          `The app will close, install the update, and restart automatically.\n` +
          `Your workflows, agents, and settings stay intact.`,
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
  const desktopUpdate = isDesktop && typeof tauriUpdate === "string" ? tauriUpdate : null;

  // Compact bottom-of-nav control: a single muted version line, or a one-click
  // "Update & Restart" row when an update is available (Claude-Code style).
  if (desktopUpdate) {
    return (
      <button
        type="button"
        onClick={() => updateNow(desktopUpdate)}
        disabled={busy}
        title={`Update to v${desktopUpdate} — the app restarts and applies it silently`}
        className="flex w-full items-center gap-2 rounded-lg border border-[color-mix(in_srgb,var(--fx-primary-strong)_45%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_14%,var(--fx-sidebar))] px-2.5 py-2 text-left transition-colors hover:bg-[color-mix(in_srgb,var(--fx-primary)_22%,var(--fx-sidebar))] disabled:opacity-60"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4 shrink-0 text-[var(--fx-primary-strong)]" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
          <path d="M12 4v10M8 10l4 4 4-4M5 19h14" />
        </svg>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block text-[11px] font-semibold text-[var(--foreground)]">
            {busy ? "Updating…" : "Update & Restart"}
          </span>
          <span className="block truncate text-[10px] text-[var(--fx-muted)]">
            {currentLabel} → v{desktopUpdate}
          </span>
        </span>
      </button>
    );
  }

  return (
    <div className="flex items-center justify-between gap-2 px-1.5 py-1">
      <span className="truncate text-[11px] text-[var(--fx-muted)]">
        {backendUpdate ? `Update v${backendUpdate.latest_version} available` : "Lattix xFrontier"}
      </span>
      <span
        className="shrink-0 rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1.5 py-0.5 font-mono text-[10px] font-semibold text-[var(--foreground)]"
        title={backendUpdate ? `Run: ${backendUpdate.update_command}` : "Current build"}
      >
        {currentLabel}
      </span>
    </div>
  );
}
