import Link from "next/link";
import { getWorkflowRuns } from "@/lib/api";
import { StatusChip } from "@/components/status-chip";
import type { WorkflowRunSummary } from "@/types/frontier";

export async function RunsSidebar() {
  let runs: WorkflowRunSummary[] = [];
  let loadError: string | null = null;

  try {
    runs = await getWorkflowRuns();
  } catch (error) {
    loadError = error instanceof Error ? error.message : "Unable to load runs.";
  }

  return (
    <aside className="hidden min-w-80 max-w-80 border-r border-[var(--fx-border)] bg-[var(--fx-sidebar)] p-4 xl:block">
      <div className="mb-4 rounded-[1.35rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_94%,hsl(var(--background))_6%)] px-4 py-3 shadow-[0_14px_34px_rgba(15,23,42,0.05)]">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">History</p>
            <h2 className="mt-2 text-[1rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Runs</h2>
          </div>
          <span className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Codex-style history</span>
        </div>
      </div>
      <div className="space-y-2">
        {loadError ? (
          <div className="fx-panel rounded-[1.15rem] p-3 text-sm text-[var(--foreground)] shadow-[0_12px_30px_rgba(15,23,42,0.04)]">
            Unable to load runs right now. {loadError}
          </div>
        ) : runs.map((run) => (
          <Link
            key={run.id}
            href={`/inbox?session=${encodeURIComponent(run.id)}`}
            className="fx-panel block rounded-[1.2rem] p-3 transition hover:-translate-y-0.5 hover:bg-[var(--fx-nav-hover)] hover:shadow-[0_18px_36px_rgba(15,23,42,0.05)]"
          >
            <p className="truncate text-[0.88rem] font-medium tracking-[0.01em] text-[var(--foreground)]">{run.title}</p>
            <div className="mt-2 flex items-center justify-between gap-2">
              <StatusChip status={run.status} />
              <span className="fx-pill px-2.5 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{run.progressLabel}</span>
            </div>
            <p className="fx-muted mt-2 text-[0.72rem] font-medium">Updated {run.updatedAt}</p>
          </Link>
        ))}
      </div>
    </aside>
  );
}
