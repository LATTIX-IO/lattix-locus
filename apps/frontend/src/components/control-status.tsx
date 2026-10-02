"use client";

import { FxStatusBadge, type FxStatus } from "@/components/fx-ui";
import type { ControlState, ControlStatusReport } from "@/types/locus";

/**
 * Security-control status, rendered exactly as the backend reports it
 * (LOCUS-313, P9). The UI never upgrades a state: unknown values render as
 * "Unverified", never as "Enforced".
 */
const CONTROL_STATE_SPEC: Record<ControlState, { status: FxStatus; label: string }> = {
  enforced: { status: "complete", label: "Enforced" },
  degraded: { status: "warning", label: "Degraded" },
  off: { status: "idle", label: "Off" },
  unverified: { status: "running", label: "Unverified" },
};

export function normalizeControlState(value: unknown): ControlState {
  return typeof value === "string" && value in CONTROL_STATE_SPEC ? (value as ControlState) : "unverified";
}

export function ControlStatusBadge({ state }: { state: ControlState | string }) {
  const spec = CONTROL_STATE_SPEC[normalizeControlState(state)];
  return <FxStatusBadge status={spec.status} label={spec.label} />;
}

type ControlStatusListProps = {
  report: ControlStatusReport | null | undefined;
  showEvidence?: boolean;
  emptyMessage?: string;
};

export function ControlStatusList({
  report,
  showEvidence = true,
  emptyMessage = "Control status unavailable.",
}: ControlStatusListProps) {
  const controls = report?.controls ?? [];
  if (!controls.length) {
    return <p className="text-xs fx-muted">{emptyMessage}</p>;
  }
  return (
    <ul className="space-y-2 text-xs text-[var(--foreground)]" aria-label="Security control status">
      {controls.map((control) => (
        <li
          key={control.id}
          data-control-id={control.id}
          data-control-state={normalizeControlState(control.state)}
          className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-2.5"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="min-w-0 break-words font-medium">{control.label}</span>
            <ControlStatusBadge state={control.state} />
          </div>
          {showEvidence && control.evidence ? (
            <p className="mt-1 break-words leading-5 fx-muted">{control.evidence}</p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
