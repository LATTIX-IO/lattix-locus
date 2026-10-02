"use client";

import type { CSSProperties, ReactNode } from "react";

export type FxStatus =
  | "running"
  | "pending"
  | "complete"
  | "failed"
  | "active"
  | "paused"
  | "idle"
  | "warning";

type StatusSpec = {
  bg: string;
  border: string;
  text: string;
  dot: string;
  label: string;
};

export const FX_STATUS: Record<FxStatus, StatusSpec> = {
  running: {
    bg: "hsl(205 90% 56% / 0.12)",
    border: "hsl(205 90% 56% / 0.4)",
    text: "hsl(202 88% 40%)",
    dot: "hsl(205 90% 56%)",
    label: "Running",
  },
  pending: {
    bg: "hsl(35 95% 52% / 0.12)",
    border: "hsl(35 95% 52% / 0.4)",
    text: "hsl(35 80% 36%)",
    dot: "hsl(35 95% 52%)",
    label: "Pending",
  },
  complete: {
    bg: "hsl(142 55% 45% / 0.12)",
    border: "hsl(142 55% 45% / 0.4)",
    text: "hsl(142 55% 30%)",
    dot: "hsl(142 55% 45%)",
    label: "Complete",
  },
  failed: {
    bg: "hsl(2 84% 60% / 0.12)",
    border: "hsl(2 84% 60% / 0.4)",
    text: "hsl(2 70% 40%)",
    dot: "hsl(2 84% 60%)",
    label: "Failed",
  },
  active: {
    bg: "hsl(142 55% 45% / 0.12)",
    border: "hsl(142 55% 45% / 0.4)",
    text: "hsl(142 55% 30%)",
    dot: "hsl(142 55% 45%)",
    label: "Active",
  },
  paused: {
    bg: "hsl(220 9% 56% / 0.12)",
    border: "hsl(220 9% 56% / 0.4)",
    text: "hsl(220 9% 46%)",
    dot: "hsl(220 9% 56%)",
    label: "Paused",
  },
  idle: {
    bg: "hsl(220 9% 56% / 0.12)",
    border: "hsl(220 9% 56% / 0.4)",
    text: "hsl(220 9% 46%)",
    dot: "hsl(220 9% 56%)",
    label: "Idle",
  },
  warning: {
    bg: "hsl(35 95% 52% / 0.12)",
    border: "hsl(35 95% 52% / 0.4)",
    text: "hsl(35 80% 36%)",
    dot: "hsl(35 95% 52%)",
    label: "Warning",
  },
};

export const FX_ACCENT = {
  primary: "hsl(35 95% 52%)",
  primaryDark: "hsl(35 80% 36%)",
  info: "hsl(205 90% 56%)",
  success: "hsl(142 55% 45%)",
  danger: "hsl(2 84% 60%)",
  purple: "hsl(270 60% 60%)",
  warn: "hsl(35 95% 52%)",
  muted: "hsl(220 9% 56%)",
};

export function FxPanel({
  children,
  className,
  padding,
  style,
}: {
  children: ReactNode;
  className?: string;
  padding?: number | string;
  style?: CSSProperties;
}) {
  const padStyle: CSSProperties =
    padding !== undefined ? { padding } : {};
  return (
    <div
      className={`fx-panel ${className ?? ""}`.trim()}
      style={{ overflow: "hidden", ...padStyle, ...style }}
    >
      {children}
    </div>
  );
}

export function FxSectionHeader({
  label,
  index,
  sub,
  action,
}: {
  label: string;
  index?: string;
  sub?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-5 flex items-start justify-between gap-3">
      <div className="min-w-0">
        {index ? (
          <p className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-[var(--fx-muted)]">
            {index}
          </p>
        ) : null}
        <h1 className="text-[18px] font-bold leading-tight text-[hsl(var(--foreground))]">
          {label}
        </h1>
        {sub ? (
          <p className="mt-1 text-[12px] text-[var(--fx-muted)]">{sub}</p>
        ) : null}
      </div>
      {action ? (
        <div className="flex flex-shrink-0 items-center gap-2">{action}</div>
      ) : null}
    </div>
  );
}

export function FxStat({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  accent?: string;
}) {
  return (
    <div
      className="fx-panel px-5 py-4"
      style={accent ? { borderLeft: `3px solid ${accent}` } : undefined}
    >
      <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
        {label}
      </p>
      <p
        className="mt-2 text-[28px] font-bold leading-none"
        style={{ color: accent ?? "hsl(var(--foreground))" }}
      >
        {value}
      </p>
      {sub ? (
        <p className="mt-2 text-[11px] text-[var(--fx-muted)]">{sub}</p>
      ) : null}
    </div>
  );
}

export function FxStatusBadge({ status }: { status: FxStatus }) {
  const spec = FX_STATUS[status] ?? FX_STATUS.idle;
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-[10px] font-medium"
      style={{
        background: spec.bg,
        borderColor: spec.border,
        color: spec.text,
      }}
    >
      <span
        className="inline-block h-[5px] w-[5px] flex-shrink-0 rounded-full"
        style={{ background: spec.dot }}
      />
      {spec.label}
    </span>
  );
}

export function FxTag({
  label,
  color,
}: {
  label: string;
  color?: string;
}) {
  return (
    <span
      className="inline-flex rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.05em]"
      style={{ color: color ?? "var(--fx-muted)" }}
    >
      {label}
    </span>
  );
}

export function FxMono({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`font-mono text-[11px] text-[var(--fx-muted)] ${className ?? ""}`.trim()}
      style={style}
    >
      {children}
    </span>
  );
}

export function FxKicker({ children }: { children: ReactNode }) {
  return (
    <p className="font-mono text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
      {children}
    </p>
  );
}

export function FxPriorityDot({
  priority,
}: {
  priority: "critical" | "high" | "normal";
}) {
  const colors: Record<"critical" | "high" | "normal", string> = {
    critical: "hsl(2 84% 60%)",
    high: "hsl(35 95% 52%)",
    normal: "hsl(220 9% 80%)",
  };
  return (
    <span
      className="inline-block h-[7px] w-[7px] flex-shrink-0 rounded-full"
      style={{ background: colors[priority] }}
    />
  );
}

export function statusFromRunStatus(
  status: string | null | undefined,
): FxStatus {
  switch ((status || "").toLowerCase()) {
    case "running":
      return "running";
    case "blocked":
      return "warning";
    case "needs review":
    case "needs_review":
      return "pending";
    case "done":
    case "completed":
    case "complete":
      return "complete";
    case "failed":
      return "failed";
    case "paused":
      return "paused";
    case "idle":
    case "queued":
    case "pending":
      return "pending";
    default:
      return "idle";
  }
}
