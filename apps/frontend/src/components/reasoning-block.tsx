"use client";

import { useState } from "react";
import { FX_ACCENT } from "@/components/fx-ui";

export type ReasoningStep = {
  id?: string;
  type: "system" | "thought" | "tool";
  text: string;
};

const TYPE_META: Record<
  ReasoningStep["type"],
  { color: string; label: string; mono: boolean }
> = {
  system: { color: "hsl(220 9% 56%)", label: "SYSTEM", mono: false },
  thought: { color: FX_ACCENT.info, label: "THINKING", mono: false },
  tool: { color: FX_ACCENT.success, label: "TOOL", mono: true },
};

function StepRow({ step }: { step: ReasoningStep }) {
  const meta = TYPE_META[step.type] ?? TYPE_META.system;
  return (
    <div className="flex items-start gap-2.5 py-1">
      <span
        className="font-mono w-14 flex-shrink-0 pt-0.5 text-[8px] font-bold uppercase tracking-[0.08em]"
        style={{ color: meta.color }}
      >
        {meta.label}
      </span>
      <p
        className="m-0 flex-1 break-words pl-2.5 text-[12px] leading-relaxed"
        style={{
          color: step.type === "thought" ? "var(--fx-muted)" : "hsl(var(--foreground))",
          fontStyle: step.type === "thought" ? "italic" : "normal",
          fontFamily: meta.mono ? "var(--font-space-mono), monospace" : "inherit",
          borderLeft: `2px solid ${meta.color}`,
        }}
      >
        {step.text}
      </p>
    </div>
  );
}

export function ReasoningBlock({
  steps,
  streaming,
  streamIdx,
}: {
  steps: ReasoningStep[];
  streaming?: boolean;
  streamIdx?: number;
}) {
  const total = steps.length;
  const visibleCount = streaming ? Math.min(streamIdx ?? 0, total) : total;
  const visibleSteps = steps.slice(0, visibleCount);

  const [override, setOverride] = useState<{ open: boolean } | null>(null);
  const expanded = override ? override.open : (streaming ?? false);
  const userOverride = override !== null;

  const WINDOW = 6;
  const windowed =
    streaming && !userOverride && visibleSteps.length > WINDOW
      ? visibleSteps.slice(-WINDOW)
      : visibleSteps;

  const toggle = () => {
    setOverride({ open: !expanded });
  };

  return (
    <div className="my-1">
      <button
        type="button"
        onClick={toggle}
        className="flex items-center gap-2 rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3 py-1.5 text-[11px] font-medium text-[var(--fx-muted)] hover:bg-[hsl(var(--muted))]"
      >
        {streaming ? (
          <span
            className="h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ background: FX_ACCENT.info }}
          />
        ) : (
          <svg
            width="12"
            height="12"
            viewBox="0 0 12 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.6"
            style={{
              transform: expanded ? "rotate(0deg)" : "rotate(-90deg)",
              transition: "transform 120ms",
            }}
          >
            <path d="M3 4l3 4 3-4" />
          </svg>
        )}
        <span>
          {streaming
            ? `Thinking… (${visibleSteps.length}${visibleCount < total ? `/${total}` : ""} steps)`
            : `Reasoned over ${total} step${total === 1 ? "" : "s"}`}
        </span>
        <span className="font-mono text-[10px]">·</span>
        <span
          className="text-[10px] font-semibold"
          style={{ color: FX_ACCENT.primary }}
        >
          {expanded ? "Collapse" : "Expand"}
        </span>
      </button>
      {expanded ? (
        <div
          className="mt-2 max-h-[360px] overflow-y-auto rounded-lg border border-[var(--ui-border)] px-3.5 py-2.5"
          style={{ background: "hsl(215 20% 98%)" }}
        >
          {(userOverride ? visibleSteps : windowed).map((s, i) => (
            <StepRow key={s.id ?? i} step={s} />
          ))}
          {streaming && visibleCount < total ? (
            <div className="flex items-center gap-2 pl-16 pt-1.5">
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full"
                style={{ background: FX_ACCENT.primary }}
              />
              <span
                className="text-[11px] italic"
                style={{ color: "var(--fx-muted)" }}
              >
                working…
              </span>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
