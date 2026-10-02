"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { TaskKickoffComposer } from "@/components/task-kickoff-composer";
import { getOperatorSession, getWorkflowRuns } from "@/lib/api";
import type { RunKind, WorkflowRunSummary } from "@/types/frontier";
import { normalizeRunKind } from "@/lib/run-kind";

const KIND_LABEL: Record<RunKind, string> = {
  individual: "Chat",
  agent: "Agent",
  workflow: "Workflow",
  playbook: "Playbook",
};

function statusDot(status: string): { color: string; label: string } {
  if (status === "Done") return { color: "hsl(var(--state-success))", label: "Done" };
  if (status === "Failed" || status === "Blocked") return { color: "hsl(var(--state-critical))", label: status };
  if (status === "Needs Review") return { color: "hsl(var(--state-warning))", label: "Needs review" };
  if (status === "Running") return { color: "hsl(var(--accent))", label: "Running" };
  return { color: "var(--fx-muted)", label: status || "Idle" };
}

// Codex-style quick-starts that prefill the composer with structured intent —
// showcasing workflow routing (/), agent assignment (@), and data/tags ($ #).
const SUGGESTIONS: Array<{ title: string; hint: string; prompt: string }> = [
  {
    title: "Ship a change with the full team",
    hint: "Product → Tech Lead → eng → security → QA",
    prompt: "/production-readiness-team Implement and harden ",
  },
  {
    title: "Cross-functional build",
    hint: "Design, build, and verify together",
    prompt: "/cross-functional-development ",
  },
  {
    title: "Hand a coding task to an agent",
    hint: "Bind a repo and let an agent implement it",
    prompt: "@orchestration-agent ",
  },
  {
    title: "Summarize a data source",
    hint: "Structure intent with delimiters",
    prompt: "Summarize $crm_q1_pipeline #need-review ",
  },
];

export function InboxWorkspace() {
  const router = useRouter();
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [firstName, setFirstName] = useState<string>("");
  const [prefill, setPrefill] = useState<{ text: string; nonce: number }>({ text: "", nonce: 0 });

  const refreshRuns = useCallback(async () => {
    try {
      setRuns(await getWorkflowRuns());
    } catch {
      /* landing degrades to no recent sessions */
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(refreshRuns);
    getOperatorSession()
      .then((session) => {
        const name = (session.display_name || session.preferred_username || "").trim();
        if (name && name.toLowerCase() !== "anonymous") {
          setFirstName(name.split(/\s+/)[0] ?? "");
        }
      })
      .catch(() => {
        /* anonymous — fall back to a neutral greeting */
      });
    const onChanged = () => void refreshRuns();
    window.addEventListener("frontier:runs-changed", onChanged);
    return () => window.removeEventListener("frontier:runs-changed", onChanged);
  }, [refreshRuns]);

  const recentRuns = useMemo(() => runs.slice(0, 6), [runs]);
  const suggest = (prompt: string) => setPrefill((previous) => ({ text: prompt, nonce: previous.nonce + 1 }));

  return (
    <section className="mx-auto w-full max-w-4xl space-y-8 py-2">
      <header>
        <h1 className="text-[1.7rem] font-semibold tracking-tight text-[var(--foreground)]">
          {firstName ? `Welcome back, ${firstName}` : "What should we run today?"}
        </h1>
        <p className="fx-muted mt-1 text-sm">
          Kick off a task below — route it to a workflow, an agent, or a playbook using delimiters.
        </p>
      </header>

      <TaskKickoffComposer prefill={prefill} />

      <div className="flex flex-wrap gap-2">
        {SUGGESTIONS.map((item) => (
          <button
            key={item.title}
            type="button"
            onClick={() => suggest(item.prompt)}
            title={item.prompt}
            className="group flex max-w-full items-center gap-2 rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3 py-1.5 text-left text-xs transition-colors hover:border-[var(--fx-primary)] hover:bg-[var(--fx-nav-hover)]"
          >
            <span className="truncate font-medium text-[var(--foreground)]">{item.title}</span>
            <span className="fx-muted hidden truncate md:inline">· {item.hint}</span>
          </button>
        ))}
      </div>

      {recentRuns.length > 0 ? (
        <div>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide fx-muted">Recent sessions</h2>
          <ul className="divide-y divide-[var(--ui-border)] overflow-hidden rounded-xl border border-[var(--ui-border)]">
            {recentRuns.map((run) => {
              const dot = statusDot(run.status);
              return (
                <li key={run.id}>
                  <button
                    type="button"
                    onClick={() => router.push(`/runs/${run.id}`)}
                    className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition-colors hover:bg-[var(--fx-nav-hover)]"
                  >
                    <span
                      aria-hidden
                      className={`h-2 w-2 shrink-0 rounded-full ${run.status === "Running" ? "animate-pulse" : ""}`}
                      style={{ background: dot.color }}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="truncate text-sm font-medium text-[var(--foreground)]">{run.title}</span>
                      <span className="fx-muted mt-0.5 block truncate text-xs">{run.progressLabel || dot.label}</span>
                    </span>
                    <span className="fx-muted hidden shrink-0 text-[11px] sm:block">
                      {KIND_LABEL[normalizeRunKind(run.kind)]}
                    </span>
                    <span className="fx-muted shrink-0 text-[11px]">{run.updatedAt}</span>
                    <svg
                      viewBox="0 0 24 24"
                      className="h-4 w-4 shrink-0 text-[var(--fx-muted)]"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      aria-hidden
                    >
                      <path d="M9 6l6 6-6 6" />
                    </svg>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <p className="fx-muted text-sm">No sessions yet — start your first task above.</p>
      )}
    </section>
  );
}
