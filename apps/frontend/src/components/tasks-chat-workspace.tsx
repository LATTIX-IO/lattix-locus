"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  FX_ACCENT,
  FxMono,
  FxStatusBadge,
  statusFromRunStatus,
} from "@/components/fx-ui";
import {
  ReasoningBlock,
  type ReasoningStep,
} from "@/components/reasoning-block";
import { TasksComposer } from "@/components/tasks-composer";
import { getWorkflowRun, getWorkflowRunEvents, type WorkflowRunDetail } from "@/lib/api";
import type {
  WorkflowRunEvent,
  WorkflowRunSummary,
} from "@/types/frontier";

const FALLBACK_REASONING: ReasoningStep[] = [
  { type: "system", text: "Task initialized. Agent: Compliance Agent (gpt-4o)" },
  { type: "system", text: "Loading memory context — 247 relevant entries retrieved from long-term store" },
  { type: "thought", text: "I need to retrieve current data room access logs and cross-reference them with the ABAC policy matrix for Q4." },
  { type: "tool", text: "read_file(\"audit_logs/q4_access.csv\") → 12,847 rows returned" },
  { type: "tool", text: "query_policy_engine(\"data_room:*\", actor=\"*\") → 34 rules loaded" },
  { type: "thought", text: "Cross-referencing access events against policy rules. Looking for violations." },
  { type: "tool", text: "analyze_violations(logs, policy_matrix) → 3 potential violations" },
  { type: "thought", text: "Verifying these are not pre-approved exceptions before escalating." },
  { type: "tool", text: "check_exceptions_registry([\"svc-analytics\",\"pk_extpartner_42\"]) → no exceptions" },
  { type: "thought", text: "Confirmed violations. Drafting audit report with severity tiers and remediations." },
];

const FALLBACK_USER_MSG = {
  text:
    "Run a Q4 compliance audit on our EU data rooms. Cross-reference access logs against current ABAC policy and flag any violations.",
  t: "14:22:58",
};

const FALLBACK_FINAL = `## Q4 Compliance Audit — EU Data Rooms

**Scope:** 12,847 access events across 34 ABAC policy rules.
**Result:** 2 confirmed violations, 1 warning-level anomaly.

### Confirmed Violations

| # | Event | Actor | Resource | Policy Broken |
|---|---|---|---|---|
| 1 | 7,241 | \`svc-analytics\` | \`data-room-eu-003\` | Missing \`data_classification=confidential\` attribute |
| 2 | 9,102 | \`pk_extpartner_42\` | External partner namespace | Accessed beyond scoped permissions at 03:14 UTC |

### Anomaly (Warning)
- Off-hours API activity from service account \`svc-etl\` (03:00–05:00 UTC) — matches lateral-movement pattern but no policy violation.

### Recommended Actions
1. Revoke \`pk_extpartner_42\` immediately and rotate the partner token.
2. Apply missing classification attribute to \`data-room-eu-003\` and re-run the policy check.
3. Open a threat-intel ticket for the \`svc-etl\` off-hours pattern.

The full audit report has been written to \`reports/q4-audit-2025.pdf\`.`;

function reasoningFromEvents(events: WorkflowRunEvent[]): ReasoningStep[] {
  const reasoning: ReasoningStep[] = [];
  for (const e of events) {
    if (e.type === "step_started") {
      reasoning.push({ id: e.id, type: "tool", text: `${e.title} — ${e.summary}` });
    } else if (e.type === "step_completed") {
      reasoning.push({ id: e.id, type: "tool", text: `${e.title} ✓ ${e.summary}` });
    } else if (e.type === "guardrail_result") {
      reasoning.push({ id: e.id, type: "system", text: `${e.title}: ${e.summary}` });
    } else if (e.type === "agent_message") {
      reasoning.push({ id: e.id, type: "thought", text: e.summary || e.title });
    }
  }
  return reasoning;
}

export function TasksChatWorkspace({
  run,
  initialDetail,
  initialEvents,
}: {
  run: WorkflowRunSummary;
  initialDetail: WorkflowRunDetail | null;
  initialEvents: WorkflowRunEvent[];
}) {
  const [detail, setDetail] = useState<WorkflowRunDetail | null>(initialDetail);
  const [events, setEvents] = useState<WorkflowRunEvent[]>(initialEvents);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      getWorkflowRun(run.id).catch(() => null),
      getWorkflowRunEvents(run.id).catch(() => []),
    ]).then(([d, e]) => {
      if (cancelled) return;
      if (d) setDetail(d);
      if (e?.length) setEvents(e);
    });
    return () => {
      cancelled = true;
    };
  }, [run.id]);

  const userMessage = useMemo(() => {
    const um = events.find((e) => e.type === "user_message");
    if (um) return { text: um.summary || um.title, t: um.createdAt };
    return FALLBACK_USER_MSG;
  }, [events]);

  const reasoning = useMemo(() => {
    const fromEvents = reasoningFromEvents(events);
    return fromEvents.length > 0 ? fromEvents : FALLBACK_REASONING;
  }, [events]);

  const finalAnswer = useMemo(() => {
    const lastAgent = [...events]
      .reverse()
      .find((e) => e.type === "agent_message");
    return lastAgent?.summary && lastAgent.summary.length > 40
      ? lastAgent.summary
      : FALLBACK_FINAL;
  }, [events]);

  const isRunning = run.status === "Running";

  const [streamCursor, setStreamCursor] = useState(3);
  const streamIdx = isRunning ? Math.min(streamCursor, reasoning.length) : reasoning.length;
  useEffect(() => {
    if (!isRunning) return;
    if (streamCursor >= reasoning.length) return;
    const id = setTimeout(
      () => setStreamCursor((i) => i + 1),
      900,
    );
    return () => clearTimeout(id);
  }, [streamCursor, isRunning, reasoning.length]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [streamIdx]);

  const fxStatus = statusFromRunStatus(run.status);
  const stepsTotal = detail?.graph?.nodes?.length ?? 7;
  const stepsComplete = events.filter((e) => e.type === "step_completed").length;

  return (
    <div
      className="-m-5 flex w-[calc(100%+2.5rem)] flex-col overflow-hidden border border-[var(--ui-border)] bg-[hsl(var(--card))] md:-m-6 md:w-[calc(100%+3rem)]"
      style={{ height: "calc(100vh - 128px)" }}
    >
      <div className="flex flex-shrink-0 items-start justify-between gap-4 border-b border-[var(--ui-border)] bg-[hsl(var(--card))] px-7 pb-4 pt-5">
        <div className="min-w-0 flex-1">
          <p className="font-mono mb-1 text-[10px] font-bold uppercase tracking-[0.14em] text-[var(--fx-muted)]">
            /01 — Task
          </p>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="m-0 text-[18px] font-bold text-[hsl(var(--foreground))]">
              {run.title}
            </h1>
            <FxStatusBadge status={fxStatus} />
          </div>
          <div className="mt-1.5 flex flex-wrap gap-3.5">
            <span className="text-[11px] text-[var(--fx-muted)]">
              {run.progressLabel}
            </span>
            <span className="text-[11px] text-[var(--fx-muted)]">·</span>
            <FxMono style={{ fontSize: 11 }}>{run.id.slice(0, 12)}</FxMono>
            <span className="text-[11px] text-[var(--fx-muted)]">·</span>
            <span className="text-[11px] text-[var(--fx-muted)]">
              {stepsComplete}/{stepsTotal} steps
            </span>
            <span className="text-[11px] text-[var(--fx-muted)]">·</span>
            <span className="text-[11px] text-[var(--fx-muted)]">
              Updated {run.updatedAt}
            </span>
          </div>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {isRunning ? (
            <button
              type="button"
              className="fx-btn-secondary inline-flex items-center px-3 py-1.5 text-[12px]"
            >
              Pause
            </button>
          ) : null}
          <Link
            href={`/inbox?session=${encodeURIComponent(run.id)}`}
            className="fx-btn-secondary inline-flex items-center px-3 py-1.5 text-[12px] no-underline"
          >
            Open in Inbox
          </Link>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[hsl(var(--card))]">
        <div
          ref={scrollRef}
          className="mx-auto flex w-full max-w-[880px] flex-1 flex-col gap-5 overflow-y-auto px-7 pb-4 pt-7"
        >
          <div className="flex max-w-[82%] gap-3 self-end">
            <div
              className="rounded-[14px_14px_4px_14px] border px-4 py-2.5"
              style={{
                background: "hsl(35 95% 52% / 0.1)",
                borderColor: "hsl(35 95% 52% / 0.3)",
              }}
            >
              <p className="m-0 text-[13px] leading-relaxed text-[hsl(var(--foreground))]">
                {userMessage.text}
              </p>
              <p className="font-mono m-0 mt-1.5 text-[10px] text-[var(--fx-muted)]">
                {userMessage.t}
              </p>
            </div>
            <div
              className="font-mono flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-full border-2 text-[10px] font-bold"
              style={{
                borderColor: FX_ACCENT.primary,
                background: "hsl(35 95% 52% / 0.12)",
                color: FX_ACCENT.primaryDark,
              }}
            >
              LO
            </div>
          </div>

          <div className="flex max-w-[92%] gap-3">
            <div
              className="font-mono flex h-[30px] w-[30px] flex-shrink-0 items-center justify-center rounded-full border-2 text-[10px] font-bold"
              style={{
                borderColor: FX_ACCENT.info,
                background: "hsl(205 90% 56% / 0.12)",
                color: "hsl(202 88% 40%)",
              }}
            >
              CA
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-1.5 flex items-center gap-2">
                <span className="text-[12px] font-semibold text-[hsl(var(--foreground))]">
                  Compliance Agent
                </span>
                <FxMono style={{ fontSize: 10 }}>gpt-4o</FxMono>
              </div>
              <ReasoningBlock
                steps={reasoning}
                streaming={isRunning}
                streamIdx={streamIdx}
              />
              {(!isRunning || streamIdx >= reasoning.length) && finalAnswer ? (
                <div className="mt-2.5 rounded-[14px_14px_14px_4px] border border-[var(--ui-border)] bg-[hsl(var(--card))] px-4 py-3.5">
                  <article
                    className="prose prose-sm max-w-none [&_code]:bg-[hsl(var(--muted))] [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_h2]:text-[15px] [&_h2]:font-bold [&_h2]:mb-2 [&_h2]:mt-1 [&_h3]:text-[13px] [&_h3]:font-bold [&_h3]:mt-3 [&_h3]:mb-1.5 [&_p]:text-[13px] [&_p]:leading-relaxed [&_li]:text-[13px] [&_li]:leading-relaxed [&_table]:w-full [&_table]:border [&_table]:border-[var(--ui-border)] [&_table]:rounded-md [&_table]:my-2 [&_table]:overflow-hidden [&_thead]:bg-[hsl(var(--muted))] [&_th]:p-1.5 [&_th]:text-[11px] [&_th]:font-semibold [&_th]:text-left [&_td]:p-1.5 [&_td]:text-[12px] [&_td]:border-t [&_td]:border-[var(--ui-border)]"
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {finalAnswer}
                    </ReactMarkdown>
                  </article>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <TasksComposer />
      </div>
    </div>
  );
}
