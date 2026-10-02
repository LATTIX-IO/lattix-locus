"use client";

import { useEffect } from "react";

/**
 * Desktop (Tauri) shell lifecycle bridge.
 *
 * The tray "Quit" item emits `app-quit-requested`. Here we validate whether any
 * agent runs are in progress, confirm with the user if so, then ask the backend
 * to tear down all its child processes (/api/system/shutdown) and finally tell
 * the Tauri shell to exit (`quit_now`). No-op outside the desktop shell.
 */
type DesktopTauri = {
  event?: { listen?: (event: string, handler: () => void | Promise<void>) => Promise<() => void> };
  core: { invoke: (command: string) => Promise<unknown> };
};

export function DesktopLifecycle() {
  useEffect(() => {
    const tauri = (window as unknown as { __TAURI__?: DesktopTauri }).__TAURI__;
    if (!tauri?.event?.listen) return;

    let unlisten: (() => void) | undefined;
    let cancelled = false;

    tauri.event
      .listen("app-quit-requested", async () => {
        let active = 0;
        try {
          const res = await fetch("/api/system/active-agents", { credentials: "include" });
          if (res.ok) {
            const data = await res.json();
            active = Number(data?.active) || 0;
          }
        } catch {
          /* if we can't tell, fall through and let the user decide */
        }

        const proceed =
          active > 0
            ? window.confirm(
                `${active} agent run${active === 1 ? "" : "s"} still in progress.\n\n` +
                  `Quit Lattix xFrontier and stop ${active === 1 ? "it" : "them"}?`,
              )
            : true;
        if (!proceed) return;

        try {
          await fetch("/api/system/shutdown", { method: "POST", credentials: "include" });
        } catch {
          /* backend may exit before responding — expected */
        }
        try {
          await tauri.core.invoke("quit_now");
        } catch {
          /* shell already gone */
        }
      })
      .then((fn: () => void) => {
        if (cancelled) fn();
        else unlisten = fn;
      });

    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, []);

  return null;
}
