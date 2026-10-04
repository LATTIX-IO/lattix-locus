"use client";

import { ChevronRightIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { StatusChip } from "@/components/status-chip";
import { TaskKickoffComposer } from "@/components/task-kickoff-composer";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getWorkflowRuns } from "@/lib/api";
import { normalizeRunKind } from "@/lib/run-kind";
import type { RunKind, WorkflowRunSummary } from "@/types/locus";

const KIND_LABEL: Record<RunKind, string> = {
  individual: "Chat",
  agent: "Agent",
  workflow: "Workflow",
  playbook: "Playbook",
};

const ACTIVE_STATUSES = new Set(["Running", "Needs Review", "Blocked"]);

function RunList({ runs, label }: { runs: WorkflowRunSummary[]; label: string }) {
  return (
    <ul aria-label={label} className="divide-y divide-border overflow-hidden rounded-[12px] border border-border">
      {runs.map((run) => (
        <li key={run.id}>
          <Link
            href={`/activity?session=${encodeURIComponent(run.id)}`}
            className="flex items-center gap-3 px-3.5 py-2.5 no-underline transition-colors hover:bg-muted"
          >
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium text-foreground">{run.title}</span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">{run.progressLabel}</span>
            </span>
            <span className="hidden shrink-0 text-[11px] text-muted-foreground sm:block">{KIND_LABEL[normalizeRunKind(run.kind)]}</span>
            <StatusChip status={run.status} />
            <ChevronRightIcon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

/**
 * Home (LOCUS-353): start a task, and see what the agents are doing now. Runs
 * come from the backend only; a failed load says so instead of showing zeros.
 */
export function HomeScreen() {
  const [runs, setRuns] = useState<WorkflowRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRuns(await getWorkflowRuns());
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load runs.");
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(refresh);
    const onChanged = () => void refresh();
    window.addEventListener("locus:runs-changed", onChanged);
    return () => window.removeEventListener("locus:runs-changed", onChanged);
  }, [refresh]);

  const active = useMemo(() => (runs ?? []).filter((run) => ACTIVE_STATUSES.has(run.status)), [runs]);
  const recent = useMemo(() => (runs ?? []).filter((run) => !ACTIVE_STATUSES.has(run.status)).slice(0, 5), [runs]);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 py-2">
      <h1 className="text-[1.6rem] font-semibold tracking-tight text-foreground">What should Locus do?</h1>

      <TaskKickoffComposer />

      {error ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px]">
          <span>Could not load runs: {error}</span>
          <Button variant="secondary" size="sm" onClick={() => void refresh()}>
            Retry
          </Button>
        </div>
      ) : null}

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Running now</CardTitle>
            <CardDescription>Agents at work, and runs waiting on you.</CardDescription>
          </div>
          <Button asChild variant="ghost" size="sm">
            <Link href="/activity">All activity</Link>
          </Button>
        </CardHeader>
        {runs === null && !error ? (
          <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>
        ) : active.length ? (
          <RunList runs={active} label="Running now" />
        ) : runs ? (
          <p className="text-[13px] text-muted-foreground">Nothing is running. Start a task above.</p>
        ) : null}
      </Card>

      {recent.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Recent</CardTitle>
          </CardHeader>
          <RunList runs={recent} label="Recent runs" />
        </Card>
      ) : null}
    </div>
  );
}
