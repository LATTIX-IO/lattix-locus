/* ------------------------------------------------------------------ */
/*  Desktop (Tauri) shell detection and commands                       */
/* ------------------------------------------------------------------ */
//
// The installed desktop app hosts this UI in a Tauri webview that exposes
// `window.__TAURI__`. On that profile the backend treats the loopback operator
// as signed in, so the UI skips /auth, hides sign-out, role and org UI, and
// routes capability widening through native shell dialogs (LOCUS-350/357).
// Outside the shell (web / hosted) none of this applies.

import { useSyncExternalStore } from "react";
import { getDesktopInvoke } from "@/lib/desktop-confirmation";

type TauriGlobal = {
  core?: { invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
  app?: { getVersion?: () => Promise<string> };
  event?: {
    listen?: (event: string, handler: (event: { payload: unknown }) => void) => Promise<() => void>;
  };
};

export function getTauriGlobal(): TauriGlobal | null {
  if (typeof window === "undefined") {
    return null;
  }
  return (window as unknown as { __TAURI__?: TauriGlobal }).__TAURI__ ?? null;
}

/** True inside the Locus desktop shell. */
export function isDesktopShell(): boolean {
  return getTauriGlobal() !== null;
}

const noopSubscribe = () => () => {};

/** Hydration-safe desktop detection: the server and first client render agree on `false`. */
export function useIsDesktopShell(): boolean {
  return useSyncExternalStore(noopSubscribe, isDesktopShell, () => false);
}

/** Invoke a shell command; throws when not running in the desktop shell. */
export async function invokeDesktop<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const invoke = getDesktopInvoke();
  if (!invoke) {
    throw new Error("This action needs the Locus desktop app.");
  }
  return (await invoke(command, args)) as T;
}

/* ---------------------------- updates (LOCUS-349) ------------------- */

export type UpdateChannel = "dev" | "stable";

export type DesktopUpdateState =
  | "idle"
  | "checking"
  | "up_to_date"
  | "available"
  | "downloading"
  | "waiting_for_idle"
  | "installing"
  | "error";

export type DesktopUpdateStatus = {
  channel: UpdateChannel;
  state: DesktopUpdateState;
  current_version: string;
  version?: string | null;
  detail?: string;
};

export function getDesktopUpdateStatus(): Promise<DesktopUpdateStatus> {
  return invokeDesktop<DesktopUpdateStatus>("get_update_status");
}

/** The shell maps the channel to its compiled-in feed; never a URL from here. */
export function setDesktopUpdateChannel(channel: UpdateChannel): Promise<DesktopUpdateStatus> {
  return invokeDesktop<DesktopUpdateStatus>("set_update_channel", { channel });
}

/** The available version on the current channel, or null when up to date. */
export async function checkDesktopUpdate(): Promise<string | null> {
  return (await invokeDesktop<string | null>("check_for_update")) ?? null;
}

export function installDesktopUpdate(): Promise<void> {
  return invokeDesktop<void>("install_update_and_restart");
}

/* -------------------------- panic hotkey (LOCUS-346) ---------------- */

/** The shell's default global panic chord. `LOCUS_PANIC_HOTKEY` may replace it. */
export function defaultPanicHotkeyLabel(): string {
  const platform = typeof navigator === "undefined" ? "" : navigator.userAgent;
  return /Mac OS X|Macintosh/i.test(platform) ? "Cmd+Alt+Shift+Esc" : "Ctrl+Alt+Shift+Esc";
}
