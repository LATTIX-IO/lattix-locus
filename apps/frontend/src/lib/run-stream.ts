// Run-stream lifecycle helpers (LOCUS-310).
//
// Backend contract: every run stream ends with exactly one lifecycle frame —
// `end` (the stream delivered everything; payload carries the run status) or
// `stream_closed` (NOT terminal; the server rotated the connection and the
// client must reconnect with ?after=<cursor>). A stream that closes without
// either frame is a dropped connection and must never be read as completion.

/** Live-connection state surfaced to the operator. */
export type RunStreamConnectionState = "live" | "reconnecting" | "polling";

/** Reconnect attempts before a consumer gives up on SSE and polls the run. */
export const RUN_STREAM_MAX_RECONNECT_ATTEMPTS = 5;

/** Interval for the polling fallback once reconnects are exhausted. */
export const RUN_STREAM_POLL_INTERVAL_MS = 3000;

const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 15000;

/** Bounded exponential backoff: 1s, 2s, 4s, 8s, 15s, 15s, ... */
export function runStreamReconnectDelayMs(attempt: number): number {
  const exponent = Math.max(0, Math.floor(attempt));
  return Math.min(RECONNECT_BASE_DELAY_MS * 2 ** exponent, RECONNECT_MAX_DELAY_MS);
}

const TERMINAL_RUN_STATUSES = new Set([
  "done",
  "completed",
  "complete",
  "failed",
  "blocked",
  "canceled",
  "cancelled",
  "archived",
]);

/** True when a fetched run status means the run will not progress further. */
export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return TERMINAL_RUN_STATUSES.has(String(status ?? "").trim().toLowerCase());
}

/** Thrown when a run stream closes without an `end` or `stream_closed` frame. */
export class RunStreamInterruptedError extends Error {
  constructor(message = "Run stream closed without a terminal event") {
    super(message);
    this.name = "RunStreamInterruptedError";
  }
}
