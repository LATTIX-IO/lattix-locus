"use client";

import { useEffect } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to an error reporting service in the future
    console.error("[Locus] Unhandled error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <div
        className="flex h-14 w-14 items-center justify-center rounded-full"
        style={{ background: "hsl(var(--state-critical) / 0.12)" }}
      >
        <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="hsl(var(--state-critical))" strokeWidth="1.8">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 8v4M12 16h.01" />
        </svg>
      </div>

      <h1 className="text-lg font-semibold" style={{ color: "hsl(var(--foreground))" }}>
        Something went wrong
      </h1>

      <p className="max-w-md text-sm" style={{ color: "var(--fx-muted)" }}>
        {error.message || "An unexpected error occurred. Please try again."}
      </p>

      {error.digest && (
        <p className="font-mono text-xs" style={{ color: "var(--fx-muted)" }}>
          Error ID: {error.digest}
        </p>
      )}

      <div className="mt-2 flex gap-3">
        <button onClick={reset} className="fx-btn-primary px-4 py-2 text-sm font-medium">
          Try again
        </button>
        <a href="/inbox" className="fx-btn-secondary px-4 py-2 text-sm font-medium no-underline">
          Go to Inbox
        </a>
      </div>
    </div>
  );
}
