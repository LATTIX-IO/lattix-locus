import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/** Shape + text, not colour alone. Interaction blue is never used as a status colour. */
export type OperationalTone = "normal" | "warning" | "critical" | "unknown" | "simulation";

const STATUS: Record<OperationalTone, { glyph: string; className: string }> = {
  normal: { glyph: "●", className: "text-[hsl(var(--state-success))]" },
  warning: { glyph: "◆", className: "text-[hsl(var(--state-warning))]" },
  critical: { glyph: "▲", className: "text-[hsl(var(--state-critical))]" },
  unknown: { glyph: "◌", className: "text-[var(--fx-muted)]" },
  simulation: { glyph: "□", className: "text-[var(--lattix-simulation)]" },
};

type Props = Omit<ComponentProps<"span">, "children"> & { tone: OperationalTone; label: string };

export function OperationalStatus({ tone, label, className, ...props }: Props) {
  const state = STATUS[tone];
  return (
    <span
      data-slot="operational-status"
      data-tone={tone}
      className={cn("inline-flex min-w-0 items-center gap-1.5 rounded-[var(--lattix-radius-label)] border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-1 text-[11px] font-medium text-[hsl(var(--foreground))]", className)}
      {...props}
    >
      <span className={cn("text-xs font-bold", state.className)} aria-hidden="true">{state.glyph}</span>
      <span className="truncate">{label}</span>
    </span>
  );
}
