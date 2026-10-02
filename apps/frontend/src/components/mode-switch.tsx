"use client";

import Link from "next/link";
import type { AppMode } from "@/types/frontier";

export function ModeSwitch({
  activeMode = "user",
  canAccessBuilder = false,
}: {
  activeMode?: AppMode;
  canAccessBuilder?: boolean;
}) {
  const inBuilder = activeMode === "builder" && canAccessBuilder;

  return (
    <div
      role="group"
      aria-label="Mode switch"
      className="inline-flex shrink-0 items-center gap-0 overflow-hidden rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.7)]"
    >
      <Link
        href="/inbox"
        className={`whitespace-nowrap px-3 py-[5px] text-[11px] font-medium no-underline transition ${
          !inBuilder
            ? "bg-[hsl(var(--card))] text-[hsl(var(--foreground))] shadow-[0_1px_2px_rgba(0,0,0,0.08)]"
            : "text-[var(--fx-muted)] hover:text-[hsl(var(--foreground))] hover:bg-[var(--fx-nav-hover)]"
        }`}
      >
        User
      </Link>
      <span aria-hidden="true" className="h-5 w-px bg-[var(--ui-border)]" />
      <Link
        href="/builder/workflows"
        aria-disabled={!canAccessBuilder}
        className={`whitespace-nowrap px-3 py-[5px] text-[11px] font-medium no-underline transition ${
          !canAccessBuilder
            ? "cursor-not-allowed text-[var(--fx-muted)] opacity-70"
            : inBuilder
              ? "bg-[hsl(var(--card))] text-[hsl(var(--foreground))] shadow-[0_1px_2px_rgba(0,0,0,0.08)]"
              : "text-[var(--fx-muted)] hover:text-[hsl(var(--foreground))] hover:bg-[var(--fx-nav-hover)]"
        }`}
        title={canAccessBuilder ? "Switch to builder mode" : "Builder mode requires a builder-capable identity."}
        onClick={(event) => {
          if (!canAccessBuilder) {
            event.preventDefault();
          }
        }}
      >
        Builder
      </Link>
    </div>
  );
}
