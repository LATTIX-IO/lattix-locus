"use client";

import { FX_STATUS } from "@/components/fx-ui";
import type { RunStreamConnectionState } from "@/lib/run-stream";

const NOTICE_COPY: Record<Exclude<RunStreamConnectionState, "live">, { title: string; detail: string }> = {
  reconnecting: {
    title: "Connection lost — reconnecting",
    detail: "Live updates are paused. The run is not finished until its status says so.",
  },
  polling: {
    title: "Live connection unavailable",
    detail: "Checking this run for updates periodically.",
  },
};

/**
 * Inline notice for a live (SSE) connection that is not healthy. Renders nothing
 * while `live`. Design-system tokens only (FX_STATUS.warning) so every live
 * surface reports a dropped stream the same way.
 */
export function ConnectionNotice({
  state,
  className,
}: {
  state: RunStreamConnectionState;
  className?: string;
}) {
  if (state === "live") {
    return null;
  }
  const copy = NOTICE_COPY[state];
  const spec = FX_STATUS.warning;
  return (
    <div
      role="status"
      aria-live="polite"
      data-connection-state={state}
      className={`flex flex-wrap items-center gap-x-2 gap-y-1 border-b px-4 py-2 text-xs ${className ?? ""}`.trim()}
      style={{ background: spec.bg, borderColor: spec.border, color: "hsl(var(--foreground))" }}
    >
      <span
        aria-hidden="true"
        className={`inline-block h-[6px] w-[6px] shrink-0 rounded-full ${state === "reconnecting" ? "animate-pulse" : ""}`.trim()}
        style={{ background: spec.dot }}
      />
      <span className="font-medium">{copy.title}</span>
      <span className="text-[var(--fx-muted)]">{copy.detail}</span>
    </div>
  );
}
