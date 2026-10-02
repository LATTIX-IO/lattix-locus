"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  FX_ACCENT,
  FX_STATUS,
  FxKicker,
  FxMono,
  FxPanel,
  FxPriorityDot,
  FxSectionHeader,
  FxStat,
  FxStatusBadge,
  statusFromRunStatus,
} from "@/components/fx-ui";
import { getInbox, getWorkflowRuns } from "@/lib/api";
import type { InboxItem, WorkflowRunSummary } from "@/types/frontier";

const PIPELINE_STEPS = [
  { id: "s1", label: "Ingest Request", status: "complete" as const },
  { id: "s2", label: "Classify Assets", status: "complete" as const },
  { id: "s3", label: "Encrypt Payload", status: "complete" as const },
  { id: "s4", label: "Provision Room", status: "running" as const },
  { id: "s5", label: "Verify Access", status: "pending" as const },
  { id: "s6", label: "Notify Owners", status: "pending" as const },
  { id: "s7", label: "Audit Log", status: "pending" as const },
];

function priorityFromRun(run: WorkflowRunSummary): "critical" | "high" | "normal" {
  if (run.status === "Blocked" || run.status === "Failed") return "critical";
  if (run.status === "Needs Review" || run.status === "Running") return "high";
  return "normal";
}

function activityKindFromRun(run: WorkflowRunSummary): {
  status: keyof typeof FX_STATUS;
  message: string;
} {
  switch (run.status) {
    case "Running":
      return { status: "running", message: `Workflow "${run.title}" — ${run.progressLabel}` };
    case "Done":
      return { status: "complete", message: `Workflow "${run.title}" completed` };
    case "Failed":
      return { status: "failed", message: `Workflow "${run.title}" failed — review trace` };
    case "Blocked":
      return { status: "warning", message: `Workflow "${run.title}" blocked — ${run.progressLabel}` };
    case "Needs Review":
      return { status: "pending", message: `Workflow "${run.title}" needs review` };
    default:
      return { status: "idle", message: `Workflow "${run.title}" — ${run.progressLabel}` };
  }
}

export function CommandCenter({
  initialRuns,
  initialInbox,
}: {
  initialRuns: WorkflowRunSummary[];
  initialInbox: InboxItem[];
}) {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>(initialRuns);
  const [inbox, setInbox] = useState<InboxItem[]>(initialInbox);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getWorkflowRuns(), getInbox()])
      .then(([nextRuns, nextInbox]) => {
        if (cancelled) return;
        setRuns(nextRuns);
        setInbox(nextInbox);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const runningCount = useMemo(
    () => runs.filter((r) => r.status === "Running").length,
    [runs],
  );
  const pendingCount = useMemo(
    () =>
      runs.filter(
        (r) => r.status === "Blocked" || r.status === "Needs Review",
      ).length,
    [runs],
  );
  const inboxCount = inbox.length;

  const activeRuns = useMemo(
    () =>
      [...runs]
        .sort((a, b) => {
          const order: Record<string, number> = {
            Running: 0,
            Blocked: 1,
            "Needs Review": 2,
            Failed: 3,
            Done: 4,
          };
          return (order[a.status] ?? 9) - (order[b.status] ?? 9);
        })
        .slice(0, 4),
    [runs],
  );

  const activities = useMemo(
    () =>
      runs.slice(0, 5).map((run, index) => {
        const kind = activityKindFromRun(run);
        return {
          id: `${run.id}-${index}`,
          status: kind.status,
          message: kind.message,
          time: run.updatedAt,
        };
      }),
    [runs],
  );

  return (
    <div className="flex flex-col gap-5">
      <FxSectionHeader
        label="Command Center"
        index="/00 — Overview"
        sub="Your active operations at a glance"
        action={
          <Link
            href="/workflows/start"
            className="fx-btn-primary inline-flex items-center px-3 py-1.5 text-[12px] font-medium no-underline"
          >
            + New Task
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <FxStat
          label="Active Tasks"
          value={runningCount + pendingCount}
          sub={`${runningCount} running · ${pendingCount} pending`}
          accent={FX_ACCENT.info}
        />
        <FxStat
          label="Running Workflows"
          value={runningCount}
          sub="Live execution"
          accent={FX_ACCENT.primary}
        />
        <FxStat
          label="Inbox"
          value={inboxCount}
          sub="Awaiting your input"
          accent={FX_ACCENT.purple}
        />
        <FxStat
          label="Memory Health"
          value="98%"
          sub="Short + long-term nominal"
          accent={FX_ACCENT.success}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-3">
          <FxPanel>
            <div className="flex items-center justify-between border-b border-[var(--ui-border)] px-4 py-3">
              <p className="text-[12px] font-semibold text-[hsl(var(--foreground))]">
                Active Tasks
              </p>
              <Link
                href="/inbox"
                className="text-[11px] font-medium text-[var(--fx-muted)] no-underline hover:text-[hsl(var(--foreground))]"
              >
                View all →
              </Link>
            </div>
            {activeRuns.length === 0 ? (
              <div className="px-4 py-6 text-center text-[12px] text-[var(--fx-muted)]">
                No active tasks. Start a workflow to see it here.
              </div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="bg-[hsl(var(--muted))]">
                    {["Task", "Progress", "Status"].map((c) => (
                      <th
                        key={c}
                        className="font-mono px-3.5 text-left text-[10px] font-semibold uppercase tracking-[0.06em] text-[var(--fx-muted)]"
                        style={{ height: 36 }}
                      >
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {activeRuns.map((run) => (
                    <tr
                      key={run.id}
                      className="border-t border-[var(--ui-border)] hover:bg-[hsl(var(--muted)/0.55)]"
                      style={{ height: 44 }}
                    >
                      <td className="px-3.5">
                        <Link
                          href={`/inbox?session=${encodeURIComponent(run.id)}`}
                          className="flex items-center gap-2 no-underline"
                        >
                          <FxPriorityDot priority={priorityFromRun(run)} />
                          <span className="block max-w-[280px] overflow-hidden text-ellipsis whitespace-nowrap text-[12px] font-medium text-[hsl(var(--foreground))]">
                            {run.title}
                          </span>
                        </Link>
                      </td>
                      <td className="whitespace-nowrap px-3.5 text-[11px] text-[var(--fx-muted)]">
                        {run.progressLabel}
                      </td>
                      <td className="px-3.5">
                        <FxStatusBadge status={statusFromRunStatus(run.status)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </FxPanel>

          <FxPanel padding={16}>
            <div className="mb-3 flex items-center justify-between">
              <div>
                <p className="text-[12px] font-semibold text-[hsl(var(--foreground))]">
                  Active Workflow Pipeline
                </p>
                <p className="mt-0.5 text-[11px] text-[var(--fx-muted)]">
                  Data Room Provisioning · Step 4/7
                </p>
              </div>
              <Link
                href="/workflows/start"
                className="text-[11px] font-medium text-[var(--fx-muted)] no-underline hover:text-[hsl(var(--foreground))]"
              >
                View →
              </Link>
            </div>
            <div className="flex items-start gap-0 overflow-x-auto pb-1">
              {PIPELINE_STEPS.map((step, i) => {
                const spec = FX_STATUS[step.status];
                const isLast = i === PIPELINE_STEPS.length - 1;
                return (
                  <div key={step.id} className="flex flex-none items-start">
                    <div className="flex min-w-[100px] flex-col items-center gap-1.5">
                      <div
                        className="flex h-8 w-8 items-center justify-center rounded-md border-2"
                        style={{
                          borderColor: spec.border,
                          background: spec.bg,
                        }}
                      >
                        {step.status === "complete" ? (
                          <svg
                            width="14"
                            height="14"
                            viewBox="0 0 16 16"
                            fill="none"
                            stroke={spec.text}
                            strokeWidth="2"
                          >
                            <path d="M3 8l4 4 6-6" />
                          </svg>
                        ) : step.status === "running" ? (
                          <span
                            className="h-2 w-2 animate-pulse rounded-full"
                            style={{ background: spec.dot }}
                          />
                        ) : (
                          <span
                            className="h-2 w-2 rounded-full"
                            style={{ background: "var(--ui-border)" }}
                          />
                        )}
                      </div>
                      <p
                        className="max-w-[88px] text-center text-[10px] leading-tight"
                        style={{
                          color:
                            step.status === "pending"
                              ? "var(--fx-muted)"
                              : "hsl(var(--foreground))",
                          fontWeight: step.status === "running" ? 600 : 400,
                        }}
                      >
                        {step.label}
                      </p>
                    </div>
                    {!isLast ? (
                      <div
                        className="mt-3.5 h-px w-6 flex-shrink-0"
                        style={{
                          background: `linear-gradient(to right, ${spec.dot}, var(--ui-border))`,
                        }}
                      />
                    ) : null}
                  </div>
                );
              })}
            </div>
          </FxPanel>
        </div>

        <div className="flex flex-col gap-3">
          <FxPanel padding={16}>
            <p className="mb-3 text-[12px] font-semibold text-[hsl(var(--foreground))]">
              System Status
            </p>
            {[
              { label: "Short-term Memory", pct: 42, color: FX_ACCENT.info },
              { label: "Long-term Memory", pct: 68, color: FX_ACCENT.purple },
              { label: "Agent Pool", pct: 31, color: FX_ACCENT.success },
              { label: "Policy Engine", pct: 100, color: FX_ACCENT.success },
            ].map((s) => (
              <div key={s.label} className="mb-2.5">
                <div className="mb-1 flex justify-between">
                  <span className="text-[11px] text-[var(--fx-muted)]">{s.label}</span>
                  <FxMono style={{ fontSize: 11, color: "hsl(var(--foreground))" }}>
                    {s.pct}%
                  </FxMono>
                </div>
                <div className="h-1 rounded-full bg-[hsl(var(--muted))]">
                  <div
                    className="h-full rounded-full transition-[width] duration-500"
                    style={{ width: `${s.pct}%`, background: s.color }}
                  />
                </div>
              </div>
            ))}
          </FxPanel>

          <FxPanel className="flex-1">
            <p className="border-b border-[var(--ui-border)] px-3.5 py-3 text-[12px] font-semibold text-[hsl(var(--foreground))]">
              Activity Feed
            </p>
            <div className="py-2">
              {activities.length === 0 ? (
                <div className="px-3.5 py-4 text-center text-[11px] text-[var(--fx-muted)]">
                  No recent activity
                </div>
              ) : (
                activities.map((a, i) => (
                  <div
                    key={a.id}
                    className="flex items-start gap-2.5 px-3.5 py-2"
                    style={{
                      borderBottom:
                        i < activities.length - 1
                          ? "1px solid var(--ui-border)"
                          : "none",
                    }}
                  >
                    <span
                      className="mt-1 h-[7px] w-[7px] flex-shrink-0 rounded-full"
                      style={{
                        background:
                          FX_STATUS[a.status as keyof typeof FX_STATUS]?.dot ??
                          "var(--ui-border)",
                      }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] leading-snug text-[hsl(var(--foreground))]">
                        {a.message}
                      </p>
                      <p className="mt-0.5 text-[10px] text-[var(--fx-muted)]">
                        {a.time}
                      </p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </FxPanel>

          <FxPanel padding={16}>
            <FxKicker>Inbox</FxKicker>
            <p className="mt-2 text-[12px] text-[hsl(var(--foreground))]">
              {inboxCount === 0
                ? "Nothing waiting on you."
                : `${inboxCount} item${inboxCount === 1 ? "" : "s"} waiting on a decision.`}
            </p>
            <Link
              href="/inbox"
              className="fx-btn-secondary mt-3 inline-flex items-center px-3 py-1.5 text-[11px] font-medium no-underline"
            >
              Open inbox →
            </Link>
          </FxPanel>
        </div>
      </div>
    </div>
  );
}
