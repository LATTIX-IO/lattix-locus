"use client";

import { FormEvent, type MouseEvent as ReactMouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";

const ReactMarkdown = dynamic(() => import("react-markdown"), { ssr: false });
import remarkGfm from "remark-gfm";
import { ControlStatusList } from "@/components/control-status";
import { RunGraphView } from "@/components/run-graph-view";
import { RunArchiveButton } from "@/components/run-archive-button";
import { RunFollowupComposer } from "@/components/run-followup-composer";
import { RunChangesPane } from "@/components/run-changes-pane";
import {
  getAtfAlignmentReport,
  getWorkflowRunEventsLive,
  getWorkflowRunLive,
  streamWorkflowRunEvents,
  submitApproval,
  type RunParticipants,
  type WorkflowRunDetail,
} from "@/lib/api";
import type { AtfAlignmentReport, WorkflowRunEvent } from "@/types/locus";

// Deterministic per-agent accent so multi-agent conversations are visually
// distinct. Hashes the agent name to a stable hue.
function agentAccentHue(name: string): number {
  let hash = 0;
  const value = String(name || "agent");
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) % 360;
  }
  return hash;
}

const MAX_VISIBLE_PARTICIPANT_AGENTS = 4;

/** Avatar stack of everyone in this chat: the human plus each agent, with a
 * green (active) / gray (idle) presence dot — modeled on member-stack UIs. */
function ParticipantStack({ participants }: { participants?: RunParticipants }) {
  const agents = participants?.agents ?? [];
  const visible = agents.slice(0, MAX_VISIBLE_PARTICIPANT_AGENTS);
  const overflow = agents.length - visible.length;

  return (
    <div className="flex items-center" aria-label="Chat participants">
      <span
        title={`${participants?.user.label ?? "You"} (human)`}
        className="relative z-10 flex h-6 w-6 items-center justify-center rounded-full border border-[var(--ui-border)] bg-[hsl(var(--muted))] text-[hsl(var(--muted-foreground))]"
      >
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <circle cx="12" cy="8" r="3.4" />
          <path d="M5 19c1.4-3 4-4.5 7-4.5s5.6 1.5 7 4.5" />
        </svg>
        <span className="absolute -bottom-0.5 -left-0.5 h-2 w-2 rounded-full border border-[hsl(var(--background))] bg-[hsl(var(--state-success))]" />
      </span>
      {visible.map((agent, index) => {
        const hue = agentAccentHue(agent.name);
        return (
          <span
            key={agent.id}
            title={`${agent.name} — ${agent.active ? "active" : "idle"}`}
            style={{ background: `hsl(${hue} 68% 45%)`, zIndex: 9 - index }}
            className="relative -ml-1.5 flex h-6 w-6 items-center justify-center rounded-full border border-[hsl(var(--background))] text-[10px] font-bold text-white"
          >
            {(agent.name.trim()[0] || "A").toUpperCase()}
            <span
              className={`absolute -bottom-0.5 -left-0.5 h-2 w-2 rounded-full border border-[hsl(var(--background))] ${
                agent.active ? "bg-[hsl(var(--state-success))]" : "bg-[hsl(var(--muted-foreground))]"
              }`}
            />
          </span>
        );
      })}
      {overflow > 0 ? (
        <span
          title={`${overflow} more agent(s)`}
          className="relative -ml-1.5 flex h-6 w-6 items-center justify-center rounded-full border border-[hsl(var(--background))] bg-[hsl(var(--muted))] text-[9px] font-semibold text-[var(--foreground)]"
        >
          +{overflow}
        </span>
      ) : null}
    </div>
  );
}

type Props = {
  runId: string;
  run: WorkflowRunDetail;
  events: WorkflowRunEvent[];
};

type EventFilter = "all" | "chat" | "system" | "errors";
type RightPanelTab = "graph" | "cognition" | "artifacts" | "approvals" | "guardrails";

function toStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function matchesEventFilter(event: WorkflowRunEvent, filter: EventFilter): boolean {
  if (filter === "all") {
    return true;
  }
  if (filter === "chat") {
    return event.type === "user_message" || event.type === "agent_message";
  }
  if (filter === "errors") {
    return event.type === "error" || /error|failed|blocked|reject/i.test(`${event.title} ${event.summary}`);
  }
  return event.type !== "user_message" && event.type !== "agent_message";
}

function MarkdownBlock({ content, className = "" }: { content: string; className?: string }) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0 text-xs leading-relaxed text-[var(--foreground)]">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-4 text-xs text-[var(--foreground)]">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-4 text-xs text-[var(--foreground)]">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          code: ({ children }) => (
            <code className="rounded bg-[hsl(var(--muted)/0.65)] px-1 py-0.5 font-mono text-[11px] text-[var(--foreground)]">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="mb-2 overflow-auto rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.5)] p-2 text-[11px] text-[var(--foreground)]">{children}</pre>
          ),
          h1: ({ children }) => <h4 className="mb-1 mt-2 text-sm font-semibold text-[var(--foreground)]">{children}</h4>,
          h2: ({ children }) => <h4 className="mb-1 mt-2 text-sm font-semibold text-[var(--foreground)]">{children}</h4>,
          h3: ({ children }) => <h5 className="mb-1 mt-2 text-xs font-semibold text-[var(--foreground)]">{children}</h5>,
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-[var(--ui-border)] pl-2 text-xs text-[hsl(var(--muted-foreground))]">{children}</blockquote>
          ),
          // Wide tables scroll horizontally INSIDE the message (the wrapper is the
          // only thing that scrolls) instead of overflowing the bubble / the page.
          table: ({ children }) => (
            <div className="mb-2 max-w-full overflow-x-auto rounded-md border border-[var(--ui-border)]">
              <table className="w-max min-w-full border-collapse text-[11px]">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-[hsl(var(--muted)/0.5)]">{children}</thead>,
          th: ({ children }) => (
            <th className="border border-[var(--ui-border)] px-2 py-1 text-left align-top font-semibold text-[var(--foreground)]">{children}</th>
          ),
          td: ({ children }) => (
            <td className="border border-[var(--ui-border)] px-2 py-1 align-top text-[var(--foreground)]">{children}</td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function statusColorForRun(status: string): string {
  if (status === "Done") {
    return "hsl(var(--state-success))";
  }
  if (status === "Failed") {
    return "hsl(var(--state-critical))";
  }
  if (status === "Needs Review") {
    return "hsl(var(--state-warning))";
  }
  return "hsl(var(--state-info))";
}

const LIVE_POLL_RUNNING_MS = 3000;
const LIVE_POLL_IDLE_MS = 8000;
// Slow heartbeat for terminal runs so externally-triggered wakes still surface.
const LIVE_POLL_HEARTBEAT_MS = 15000;

export function RunConversationConsole({ runId, run: initialRun, events: initialEvents }: Props) {
  const router = useRouter();
  const timelineRef = useRef<HTMLDivElement | null>(null);

  const [run, setRun] = useState<WorkflowRunDetail>(initialRun);
  const [events, setEvents] = useState<WorkflowRunEvent[]>(initialEvents);
  // Prefer the SSE bridge (one idle connection); drop to interval polling if it fails.
  const [liveTransport, setLiveTransport] = useState<"stream" | "poll">("stream");
  const lastEventIdRef = useRef<string>("");
  const [eventFilter, setEventFilter] = useState<EventFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [rightPanelTab, setRightPanelTab] = useState<RightPanelTab>("graph");
  const [autoScroll, setAutoScroll] = useState(true);
  const [expandAllReasoning, setExpandAllReasoning] = useState(false);
  const [approvalFeedback, setApprovalFeedback] = useState("");
  const [approvalBusy, setApprovalBusy] = useState<"approved" | "changes_requested" | null>(null);
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  // Pinnable side panels (Details + Files) — collapsed by default so the chat
  // stays the primary surface; both pin beside content and are width-resizable.
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [detailsWidth, setDetailsWidth] = useState(460);
  const [filesWidth, setFilesWidth] = useState(440);
  const [atfReport, setAtfReport] = useState<AtfAlignmentReport | null>(null);
  const [atfReportError, setAtfReportError] = useState<string | null>(null);

  // Restore persisted panel widths.
  useEffect(() => {
    const d = Number(window.localStorage.getItem("fx.run.detailsWidth"));
    if (d >= 320 && d <= 900) setDetailsWidth(d);
    const f = Number(window.localStorage.getItem("fx.run.filesWidth"));
    if (f >= 320 && f <= 900) setFilesWidth(f);
  }, []);

  // Drag-to-resize: a left-edge handle grows the panel as the cursor moves left.
  const startResize = useCallback(
    (key: "details" | "files", current: number) => (event: ReactMouseEvent) => {
      event.preventDefault();
      const startX = event.clientX;
      const setter = key === "details" ? setDetailsWidth : setFilesWidth;
      const storageKey = key === "details" ? "fx.run.detailsWidth" : "fx.run.filesWidth";
      const onMove = (e: MouseEvent) => {
        const next = Math.max(320, Math.min(900, current + (startX - e.clientX)));
        setter(next);
      };
      const onUp = (e: MouseEvent) => {
        const next = Math.max(320, Math.min(900, current + (startX - e.clientX)));
        try {
          window.localStorage.setItem(storageKey, String(next));
        } catch {
          /* ignore */
        }
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.style.userSelect = "";
      };
      document.body.style.userSelect = "none";
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    },
    [],
  );
  const changedFiles = Array.isArray(run.changed_files) ? run.changed_files : [];

  const rawGraphNodes = Array.isArray(run.graph?.nodes) ? run.graph.nodes : [];
  const graphNodes = rawGraphNodes
    .filter((node): node is { id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> } => {
      return Boolean(node && String(node.id || "").trim() && String(node.title || "").trim() && String(node.type || "").trim());
    })
    .map((node, index) => {
      const hasValidX = Number.isFinite(node.x);
      const hasValidY = Number.isFinite(node.y);
      return {
        ...node,
        x: hasValidX ? node.x : 80 + index * 280,
        y: hasValidY ? node.y : 140,
      };
    });

  const graphNodeIds = new Set(graphNodes.map((node) => node.id));
  const rawGraphLinks = Array.isArray(run.graph?.links) ? run.graph.links : [];
  const graphLinks = rawGraphLinks.filter((link) => Boolean(link && graphNodeIds.has(link.from) && graphNodeIds.has(link.to)));

  const fallbackGraphNodes = [
    { id: "n-trigger", title: "Trigger", type: "trigger" as const, x: 80, y: 140 },
    { id: "n-default-chat-agent", title: "Default Chat Agent", type: "agent" as const, x: 360, y: 140 },
    { id: "n-output", title: "Output", type: "output" as const, x: 680, y: 140 },
  ];
  const fallbackGraphLinks = [
    { from: "n-trigger", to: "n-default-chat-agent", from_port: "out", to_port: "in" },
    { from: "n-default-chat-agent", to: "n-output", from_port: "out", to_port: "in" },
  ];

  const effectiveGraphNodes = graphNodes.length > 0 ? graphNodes : fallbackGraphNodes;
  const effectiveGraphLinks = graphNodes.length > 0 ? graphLinks : fallbackGraphLinks;

  const agentTraces = run.agent_traces ?? [];
  const tracesByAgent = new Map(agentTraces.map((trace) => [trace.agent, trace]));
  const approvals = run.approvals ?? { required: false, pending: false };
  const approvalArtifactId = approvals.artifact_id ?? run.artifacts[0]?.id ?? "";
  const approvalVersion = approvals.version ?? 1;

  const orderedEvents = useMemo(() => {
    return [...events].sort((a, b) => {
      const left = Date.parse(a.createdAt);
      const right = Date.parse(b.createdAt);
      if (Number.isNaN(left) || Number.isNaN(right)) {
        return 0;
      }
      return left - right;
    });
  }, [events]);

  // Live work-log events (thoughts, iterations, tool calls) grouped by work_id.
  // In-flight groups render as a live "working" panel; completed groups fold
  // into their final message's Activity disclosure.
  const progressByWork = useMemo(() => {
    const map = new Map<string, WorkflowRunEvent[]>();
    for (const event of orderedEvents) {
      const workId = typeof event.metadata?.work_id === "string" ? (event.metadata.work_id as string) : "";
      if (workId && event.metadata?.progress) {
        const group = map.get(workId);
        if (group) group.push(event);
        else map.set(workId, [event]);
      }
    }
    return map;
  }, [orderedEvents]);

  const completedWorkIds = useMemo(() => {
    const done = new Set<string>();
    for (const event of orderedEvents) {
      const workId = typeof event.metadata?.work_id === "string" ? (event.metadata.work_id as string) : "";
      if (workId && event.type === "agent_message" && !event.metadata?.progress) {
        done.add(workId);
      }
    }
    return done;
  }, [orderedEvents]);

  const firstProgressEventId = useMemo(() => {
    const map = new Map<string, string>();
    for (const [workId, group] of progressByWork) {
      if (group.length > 0) {
        map.set(workId, group[0].id);
      }
    }
    return map;
  }, [progressByWork]);

  const hasInFlightWork = useMemo(
    () => Array.from(progressByWork.keys()).some((workId) => !completedWorkIds.has(workId)),
    [progressByWork, completedWorkIds],
  );

  let lastUserIndex = -1;
  let lastAgentIndex = -1;
  orderedEvents.forEach((event, index) => {
    if (event.type === "user_message") {
      lastUserIndex = index;
    }
    if (event.type === "agent_message") {
      lastAgentIndex = index;
    }
  });

  // The live working panel replaces the generic typing placeholder.
  const showTypingPlaceholder =
    run.status === "Running" && lastUserIndex > lastAgentIndex && !hasInFlightWork;

  const filteredEvents = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return orderedEvents.filter((event) => {
      if (!matchesEventFilter(event, eventFilter)) {
        return false;
      }
      if (!query) {
        return true;
      }
      return `${event.type} ${event.title} ${event.summary}`.toLowerCase().includes(query);
    });
  }, [eventFilter, orderedEvents, searchQuery]);

  const chatEventsCount = orderedEvents.filter((event) => event.type === "user_message" || event.type === "agent_message").length;
  const systemEventsCount = orderedEvents.length - chatEventsCount;
  const errorEventsCount = orderedEvents.filter((event) => matchesEventFilter(event, "errors")).length;

  const hasAgentNode = effectiveGraphNodes.some((node) => {
    const normalized = String(node.type || "").replace(/^locus\//, "");
    return normalized === "agent" || normalized.startsWith("agent/");
  });
  const usedAgentStudioAgent = hasAgentNode || agentTraces.length > 0;

  const guardrailEvents = orderedEvents.filter((event) => event.type === "guardrail_result");
  const approvalAttention = Boolean(approvals.required && approvals.pending);
  const alertAttention =
    run.status === "Failed" ||
    run.status === "Blocked" ||
    guardrailEvents.length > 0 ||
    orderedEvents.some((event) => event.type === "error");
  // "action" (operator must do something) outranks "alert" (something went wrong).
  const flyoutAttention: "action" | "alert" | null = approvalAttention
    ? "action"
    : alertAttention
      ? "alert"
      : null;
  const cognitiveSummary = run.cognitive ?? null;
  const cognitiveCommitment = cognitiveSummary?.commitment ?? null;
  const cognitiveAssembly = cognitiveSummary?.assembly ?? null;
  const cognitiveStates = cognitiveSummary?.states ?? {};
  const cognitiveMessages = Array.isArray(cognitiveSummary?.messages) ? cognitiveSummary.messages : [];
  const cognitiveStateEntries = Object.entries(cognitiveStates);

  // Server components re-render with fresh data on router.refresh(); adopt it.
  useEffect(() => {
    setRun(initialRun);
    setEvents(initialEvents);
  }, [initialRun, initialEvents]);

  const refreshLiveState = useCallback(async () => {
    const [nextRun, nextEvents] = await Promise.all([
      getWorkflowRunLive(runId),
      getWorkflowRunEventsLive(runId),
    ]);
    setRun(nextRun);
    setEvents(nextEvents);
  }, [runId]);

  const runIsLive =
    run.status === "Running" || run.status === "Needs Review" || Boolean(run.approvals?.pending);

  useEffect(() => {
    lastEventIdRef.current = events.length > 0 ? events[events.length - 1].id : "";
  }, [events]);

  // Primary live transport: server-sent events. One idle connection instead of
  // request pairs every few seconds; any failure downgrades to polling below.
  useEffect(() => {
    if (!runIsLive || liveTransport !== "stream") {
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    let detailTimer: ReturnType<typeof setTimeout> | null = null;

    const scheduleDetailRefresh = () => {
      if (cancelled || detailTimer) {
        return;
      }
      detailTimer = setTimeout(() => {
        detailTimer = null;
        void getWorkflowRunLive(runId)
          .then((nextRun) => {
            if (!cancelled) {
              setRun(nextRun);
            }
          })
          .catch(() => {
            // Transient; the stream keeps delivering events regardless.
          });
      }, 400);
    };

    void (async () => {
      while (!cancelled) {
        try {
          const endReason = await streamWorkflowRunEvents(runId, {
            afterEventId: lastEventIdRef.current || undefined,
            signal: controller.signal,
            onEvent: (event) => {
              lastEventIdRef.current = event.id;
              setEvents((prev) => (prev.some((item) => item.id === event.id) ? prev : [...prev, event]));
              scheduleDetailRefresh();
            },
            onStatus: () => scheduleDetailRefresh(),
          });
          if (endReason === "terminal") {
            // Let polling close out the final state (it stops once terminal).
            if (!cancelled) {
              setLiveTransport("poll");
            }
            return;
          }
          // "timeout": server rotated the connection; reconnect with the cursor.
        } catch {
          if (!cancelled) {
            setLiveTransport("poll");
          }
          return;
        }
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
      if (detailTimer) {
        clearTimeout(detailTimer);
      }
    };
  }, [liveTransport, runId, runIsLive]);

  // Poll when downgraded from SSE — and keep a slow heartbeat even on terminal
  // runs: this is a chat session that can wake again at any time (follow-ups
  // sent from this tab, another tab, or an automation).
  useEffect(() => {
    if (runIsLive && liveTransport === "stream") {
      return; // the SSE stream owns live updates
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const intervalMs = !runIsLive
      ? LIVE_POLL_HEARTBEAT_MS
      : run.status === "Running"
        ? LIVE_POLL_RUNNING_MS
        : LIVE_POLL_IDLE_MS;

    async function tick() {
      if (cancelled) {
        return;
      }
      if (typeof document === "undefined" || document.visibilityState !== "hidden") {
        try {
          await refreshLiveState();
        } catch {
          // Transient failure — keep the last known state and retry on the next tick.
        }
      }
      if (!cancelled) {
        timer = setTimeout(tick, intervalMs);
      }
    }

    timer = setTimeout(tick, intervalMs);
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [liveTransport, refreshLiveState, run.status, runIsLive]);

  // Same-tab wake: the composer broadcasts after sending into this run. Re-arm
  // the SSE stream and refresh now (plus once more shortly after, to catch the
  // background worker's first write) so the reply streams in without a reload.
  useEffect(() => {
    let followupTimer: ReturnType<typeof setTimeout> | null = null;
    const onRunsChanged = () => {
      setLiveTransport("stream");
      void refreshLiveState().catch(() => {});
      if (followupTimer) {
        clearTimeout(followupTimer);
      }
      followupTimer = setTimeout(() => {
        followupTimer = null;
        void refreshLiveState().catch(() => {});
      }, 1200);
    };
    window.addEventListener("locus:runs-changed", onRunsChanged);
    return () => {
      window.removeEventListener("locus:runs-changed", onRunsChanged);
      if (followupTimer) {
        clearTimeout(followupTimer);
      }
    };
  }, [refreshLiveState]);

  useEffect(() => {
    if (approvals.required && approvals.pending) {
      setRightPanelTab("approvals");
    }
  }, [approvals.pending, approvals.required]);

  useEffect(() => {
    if (cognitiveCommitment && rightPanelTab === "graph") {
      setRightPanelTab("cognition");
    }
  }, [cognitiveCommitment, rightPanelTab]);

  useEffect(() => {
    let active = true;
    setAtfReportError(null);

    void getAtfAlignmentReport()
      .then((report) => {
        if (!active) {
          return;
        }
        setAtfReport(report);
      })
      .catch(() => {
        if (!active) {
          return;
        }
        setAtfReportError("Unable to load ATF posture.");
      });

    return () => {
      active = false;
    };
  }, [runId]);

  useEffect(() => {
    if (!autoScroll) {
      return;
    }
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }
    timeline.scrollTop = timeline.scrollHeight;
  }, [autoScroll, filteredEvents.length, showTypingPlaceholder]);

  async function copyTranscript() {
    const transcript = filteredEvents
      .map((event) => `[${event.createdAt}] ${event.type}: ${event.title}\n${event.summary}`)
      .join("\n\n");

    if (!transcript.trim()) {
      setApprovalMessage("No timeline content available to copy.");
      return;
    }

    if (typeof navigator === "undefined" || !navigator.clipboard) {
      setApprovalMessage("Clipboard access is unavailable in this environment.");
      return;
    }

    try {
      await navigator.clipboard.writeText(transcript);
      setApprovalMessage("Timeline copied to clipboard.");
    } catch {
      setApprovalMessage("Unable to copy timeline on this browser/session.");
    }
  }

  async function handleApprovalDecision(decision: "approved" | "changes_requested") {
    if (!approvals.required) {
      return;
    }

    const feedback = approvalFeedback.trim();
    if (decision === "changes_requested" && !feedback) {
      setApprovalMessage("Please provide feedback before requesting changes.");
      return;
    }

    setApprovalBusy(decision);
    setApprovalMessage(null);

    try {
      await submitApproval({
        run_id: runId,
        decision,
        artifact_id: approvalArtifactId,
        version: approvalVersion,
        feedback: decision === "changes_requested" ? feedback : undefined,
      });
      setApprovalMessage(decision === "approved" ? "Approval submitted." : "Change request submitted.");
      try {
        await refreshLiveState();
      } catch {
        // Fresh state also arrives via the server-component refresh below.
      }
      router.refresh();
    } catch {
      setApprovalMessage("Unable to submit approval decision. Please retry.");
    } finally {
      setApprovalBusy(null);
    }
  }

  const statusColor = statusColorForRun(run.status);
  const atfTopGaps = atfReport
    ? Object.values(atfReport.pillars)
        .flatMap((pillar) => pillar.gaps)
        .filter(Boolean)
        .slice(0, 3)
    : [];

  const kindLabel =
    { individual: "Chat", agent: "Agent", workflow: "Workflow", playbook: "Playbook" }[
      String((run as { kind?: string }).kind ?? "")
    ] ?? "Run";

  return (
    <div className="-m-6 flex h-[calc(100vh-var(--fx-content-top,57px))] flex-col">
      {/* Slim top toolbar — the chat owns the rest of the page */}
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--ui-border)] bg-[hsl(var(--background)/0.9)] px-4 py-2 backdrop-blur">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-0.5 text-[11px] font-medium"
            style={{ color: statusColor }}
          >
            {run.status}
          </span>
          <span className="truncate text-sm font-semibold text-[var(--foreground)]">
            {kindLabel} <span className="fx-muted font-mono text-xs">· {runId.slice(0, 8)}</span>
          </span>
          <ParticipantStack participants={run.participants} />
          {run.status === "Running" ? (
            <span className="fx-muted text-[11px]">live</span>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <input
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search…"
            className="fx-field h-7 w-36 px-2 text-xs"
          />
          <div className="flex items-center gap-1 rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card))] p-0.5">
            {(["all", "chat", "system", "errors"] as EventFilter[]).map((filterOption) => (
              <button
                key={filterOption}
                type="button"
                onClick={() => setEventFilter(filterOption)}
                className={`rounded px-1.5 py-0.5 text-[11px] ${eventFilter === filterOption ? "bg-[hsl(var(--primary)/0.18)] text-[var(--foreground)]" : "text-[hsl(var(--muted-foreground))]"}`}
              >
                {filterOption.charAt(0).toUpperCase() + filterOption.slice(1)}
              </button>
            ))}
          </div>
          <button type="button" onClick={() => setExpandAllReasoning((prev) => !prev)} className="fx-btn-secondary px-2 py-1 text-[11px]">
            {expandAllReasoning ? "Collapse" : "Reasoning"}
          </button>
          <label className="flex items-center gap-1 rounded-md border border-[var(--ui-border)] px-1.5 py-1 text-[11px] text-[hsl(var(--muted-foreground))]">
            <input type="checkbox" checked={autoScroll} onChange={(event) => setAutoScroll(event.target.checked)} />
            Auto
          </label>
          <button type="button" onClick={copyTranscript} className="fx-btn-secondary px-2 py-1 text-[11px]">
            Copy
          </button>
          <RunArchiveButton runId={runId} buttonClassName="fx-btn-secondary px-2 py-1 text-[11px] font-medium" />
          <button
            type="button"
            onClick={() => setFlyoutOpen(true)}
            aria-label="Open run details"
            className="fx-btn-secondary relative px-2 py-1 text-[11px] font-medium"
          >
            Details
            {flyoutAttention ? (
              <span
                data-testid="flyout-attention"
                aria-label={flyoutAttention === "action" ? "Action required" : "Issues detected"}
                className={`absolute -right-1 -top-1 h-2.5 w-2.5 animate-pulse rounded-full ${
                  flyoutAttention === "action"
                    ? "bg-[hsl(var(--state-warning))]"
                    : "bg-[hsl(var(--state-critical))]"
                }`}
              />
            ) : null}
          </button>
          <button
            type="button"
            onClick={() => setFilesOpen((prev) => !prev)}
            aria-label="Toggle files changed"
            className={`fx-btn-secondary px-2 py-1 text-[11px] font-medium ${filesOpen ? "bg-[hsl(var(--primary)/0.18)]" : ""}`}
          >
            Files{changedFiles.length ? ` (${changedFiles.length})` : ""}
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
      <section className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[hsl(var(--muted)/0.18)]">
            <div ref={timelineRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto overflow-x-hidden px-3 pb-2 pt-4">
              {filteredEvents.length === 0 ? (
                <div className="mx-auto w-full max-w-[880px] rounded-xl border border-dashed border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] px-3 py-4 text-xs">
                  <p className="font-medium text-[var(--foreground)]">No events match your current filters.</p>
                  <p className="fx-muted mt-1">Try clearing search or switching from <span className="font-semibold">{eventFilter}</span> to <span className="font-semibold">all</span>.</p>
                </div>
              ) : (
                filteredEvents.map((event) => {
                  const eventWorkId =
                    typeof event.metadata?.work_id === "string" ? (event.metadata.work_id as string) : "";
                  const isProgressEvent = Boolean(event.metadata?.progress) && Boolean(eventWorkId);

                  // Work-log events: fold completed groups into their final
                  // message; render in-flight groups as one live working panel
                  // (anchored at the group's first event). The "system" filter
                  // still lists them raw for debugging.
                  if (isProgressEvent && eventFilter === "all") {
                    if (completedWorkIds.has(eventWorkId)) {
                      return null;
                    }
                    if (firstProgressEventId.get(eventWorkId) !== event.id) {
                      return null;
                    }
                    const steps = progressByWork.get(eventWorkId) ?? [];
                    const latest = steps[steps.length - 1];
                    const workerName = String(event.metadata?.selected_agent_name ?? "Agent");
                    const workerHue = agentAccentHue(workerName);
                    return (
                      <article key={event.id} className="mx-auto w-full max-w-[880px]">
                        <details
                          className="rounded-xl border border-[var(--ui-border)] bg-[hsl(var(--card)/0.85)] px-3 py-2"
                          style={{ borderLeft: `3px solid hsl(${workerHue} 68% 55%)` }}
                        >
                          <summary className="flex cursor-pointer list-none items-center gap-2 text-[11px] text-[hsl(var(--muted-foreground))]">
                            <span
                              aria-hidden
                              className="h-2 w-2 shrink-0 animate-pulse rounded-full"
                              style={{ background: `hsl(${workerHue} 68% 55%)` }}
                            />
                            <span className="shrink-0 font-semibold text-[var(--foreground)]">{workerName}</span>
                            <span className="shrink-0">working…</span>
                            <span className="fx-muted min-w-0 flex-1 truncate">
                              {latest ? `${String(latest.metadata?.phase ?? "step")}: ${latest.summary}` : ""}
                            </span>
                            <span className="fx-muted shrink-0">
                              {steps.length} step{steps.length === 1 ? "" : "s"} ▾
                            </span>
                          </summary>
                          <ol className="mt-2 space-y-1.5 border-t border-[var(--ui-border)] pt-2">
                            {steps.map((step) => (
                              <li key={step.id} className="flex gap-2 text-[11px]">
                                <span className="fx-muted w-20 shrink-0 font-mono text-[10px] uppercase">
                                  {String(step.metadata?.phase ?? step.type)}
                                </span>
                                <span className="min-w-0 flex-1 whitespace-pre-wrap leading-relaxed text-[var(--foreground)]">
                                  {step.summary}
                                </span>
                              </li>
                            ))}
                          </ol>
                        </details>
                      </article>
                    );
                  }

                  const trace = event.type === "agent_message" ? tracesByAgent.get(event.title.replace(/\s+response$/i, "")) : undefined;
                  const eventModelMeta =
                    event.metadata && typeof event.metadata === "object" && typeof event.metadata.model === "object"
                      ? (event.metadata.model as Record<string, unknown>)
                      : null;
                  const eventReasoningMeta =
                    eventModelMeta && typeof eventModelMeta.reasoning === "object"
                      ? (eventModelMeta.reasoning as Record<string, unknown>)
                      : null;
                  const reasoningSummaries =
                    eventReasoningMeta && Array.isArray(eventReasoningMeta.summaries)
                      ? eventReasoningMeta.summaries.map((item) => String(item ?? "").trim()).filter(Boolean)
                      : [];

                  const isUserMessage = event.type === "user_message";
                  const isAgentMessage = event.type === "agent_message";
                  const isChatTurn = isUserMessage || isAgentMessage;

                  if (!isChatTurn) {
                    return (
                      <article key={event.id} className="group mx-auto w-full max-w-[880px] rounded-[1.05rem] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] px-3.5 py-3 shadow-[0_10px_24px_rgba(15,23,42,0.04)]">
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <p className="text-[11px] font-medium tracking-[0.02em] text-[hsl(var(--muted-foreground))]">
                            {event.type.replace(/_/g, " ")}
                          </p>
                          <span className="fx-muted text-[11px] opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">{event.createdAt}</span>
                        </div>
                        <p className="mb-1 text-xs font-medium text-[var(--foreground)]">{event.title}</p>
                        <MarkdownBlock content={event.summary} />
                      </article>
                    );
                  }

                  const agentName =
                    trace?.agent ||
                    String((event.metadata?.selected_agent_name as string | undefined) ?? "") ||
                    "Assistant";
                  const roleLabel = isUserMessage ? "You" : agentName;
                  const containerClass = isUserMessage ? "justify-end" : "justify-start";
                  const agentHue = isAgentMessage ? agentAccentHue(agentName) : null;
                  const parentEventId = isAgentMessage
                    ? (event.metadata?.parent_event_id as string | undefined)
                    : undefined;
                  const isReply = Boolean(parentEventId);
                  const isDeclined = Boolean(event.metadata?.declined);
                  const decisionReason =
                    event.metadata && typeof event.metadata.decision === "object"
                      ? String((event.metadata.decision as Record<string, unknown>).reason ?? "")
                      : "";
                  const isSynthesis = Boolean(event.metadata?.synthesis);
                  const fullText =
                    typeof event.metadata?.full_text === "string"
                      ? (event.metadata.full_text as string)
                      : "";
                  const messageBody = fullText.trim() ? fullText : event.summary;
                  const avatarInitial = (agentName.trim()[0] || "A").toUpperCase();
                  const bubbleClass = isUserMessage
                    ? "border-[hsl(var(--primary)/0.45)] bg-[hsl(var(--primary)/0.16)]"
                    : isSynthesis
                      ? "border-[hsl(var(--accent)/0.5)] bg-[hsl(var(--accent)/0.08)]"
                      : "border-[var(--ui-border)] bg-[hsl(var(--card)/0.98)]";
                  const bubbleStyle =
                    agentHue !== null ? { borderLeft: `3px solid hsl(${agentHue} 68% 55%)` } : undefined;

                  // A threaded reply that declined renders as a compact note.
                  if (isDeclined) {
                    return (
                      <article key={event.id} className="mx-auto w-full max-w-[880px] pl-10">
                        <div className="rounded-lg border border-dashed border-[var(--ui-border)] bg-[hsl(var(--muted)/0.25)] px-3 py-1.5 text-[11px] fx-muted">
                          <span className="font-medium" style={agentHue !== null ? { color: `hsl(${agentHue} 68% 55%)` } : undefined}>
                            {agentName}
                          </span>{" "}
                          declined to respond{decisionReason ? ` — ${decisionReason}` : ""}
                        </div>
                      </article>
                    );
                  }

                  return (
                    <article key={event.id} className={`group mx-auto flex w-full max-w-[880px] ${containerClass} ${isReply ? "pl-10" : ""}`}>
                      <div className={`w-full min-w-0 overflow-hidden rounded-2xl border px-3 py-2.5 shadow-sm ${bubbleClass}`} style={bubbleStyle}>
                        <div className="mb-1 flex items-center justify-between gap-2">
                          <p className="flex items-center gap-1.5 text-[11px] font-semibold text-[hsl(var(--muted-foreground))]">
                            {agentHue !== null ? (
                              <span
                                aria-hidden
                                className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-[hsl(var(--card))]"
                                style={{ background: `hsl(${agentHue} 68% 55%)` }}
                              >
                                {avatarInitial}
                              </span>
                            ) : null}
                            {roleLabel}
                            {isSynthesis ? (
                              <span className="rounded-full border border-[hsl(var(--accent)/0.6)] px-1.5 py-0 text-[9px] uppercase tracking-wide text-[hsl(var(--accent))]">
                                Conclusion
                              </span>
                            ) : isReply ? (
                              <span className="rounded-full border border-[var(--ui-border)] px-1.5 py-0 text-[9px] uppercase tracking-wide fx-muted">
                                ↳ thread
                              </span>
                            ) : null}
                          </p>
                          <span className="fx-muted text-[11px] opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">{event.createdAt}</span>
                        </div>
                        {(() => {
                          const workSteps = eventWorkId ? progressByWork.get(eventWorkId) ?? [] : [];
                          if (workSteps.length === 0) {
                            return null;
                          }
                          const lastStep = workSteps[workSteps.length - 1];
                          return (
                            <details className="mb-1.5 rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.28)] px-2 py-1">
                              <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[10px] text-[hsl(var(--muted-foreground))]">
                                <span className="font-medium">
                                  Worked — {workSteps.length} step{workSteps.length === 1 ? "" : "s"}
                                </span>
                                <span className="fx-muted min-w-0 flex-1 truncate">
                                  {lastStep ? lastStep.summary : ""}
                                </span>
                                <span aria-hidden>▾</span>
                              </summary>
                              <ol className="mt-1.5 space-y-1 border-t border-[var(--ui-border)] pt-1.5">
                                {workSteps.map((step) => (
                                  <li key={step.id} className="flex gap-2 text-[10px]">
                                    <span className="fx-muted w-20 shrink-0 font-mono uppercase">
                                      {String(step.metadata?.phase ?? step.type)}
                                    </span>
                                    <span className="min-w-0 flex-1 whitespace-pre-wrap leading-relaxed text-[var(--foreground)]">
                                      {step.summary}
                                    </span>
                                  </li>
                                ))}
                              </ol>
                            </details>
                          );
                        })()}
                        <MarkdownBlock content={messageBody} className="mt-1 leading-relaxed" />

                        {trace ? (
                          <details open={expandAllReasoning} className="mt-2 rounded-xl border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.32)] px-2 py-1.5">
                            <summary className="cursor-pointer list-none text-[11px] font-medium text-[hsl(var(--muted-foreground))]">Reasoning</summary>

                            <div className="mt-2 space-y-2">
                              <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] p-2">
                                <p className="fx-muted text-[10px] font-medium tracking-[0.02em]">Summary</p>
                                <MarkdownBlock content={trace.reasoningSummary} className="mt-1" />
                              </div>

                              {reasoningSummaries.length > 0 ? (
                                <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] p-2">
                                  <p className="fx-muted text-[10px] font-medium tracking-[0.02em]">Model reasoning highlights (auto)</p>
                                  <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-relaxed text-[var(--foreground)]">
                                    {reasoningSummaries.map((item, index) => (
                                      <li key={`${event.id}-reasoning-${index}`}>{item}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}

                              {trace.actions.length > 0 ? (
                                <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] p-2">
                                  <p className="fx-muted text-[10px] font-medium tracking-[0.02em]">Actions</p>
                                  <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-relaxed text-[var(--foreground)]">
                                    {trace.actions.map((action, index) => (
                                      <li key={`${trace.agent}-${index}`}>{action}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null}

                            </div>
                          </details>
                        ) : null}
                      </div>
                    </article>
                  );
                })
              )}

              {showTypingPlaceholder ? (
                <article className="mx-auto flex w-full max-w-[880px] justify-start">
                  <div className="w-full min-w-0 overflow-hidden rounded-2xl border border-[var(--ui-border)] bg-[hsl(var(--card)/0.98)] px-3 py-2.5 shadow-sm">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <p className="text-[11px] font-semibold text-[hsl(var(--muted-foreground))]">Assistant</p>
                      <span className="fx-muted text-[11px]">streaming</span>
                    </div>
                    <div className="inline-flex items-center gap-1.5 border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.45)] px-2 py-1">
                      <span className="h-1.5 w-1.5 animate-pulse bg-[hsl(var(--muted-foreground))] [animation-delay:0ms]" />
                      <span className="h-1.5 w-1.5 animate-pulse bg-[hsl(var(--muted-foreground))] [animation-delay:120ms]" />
                      <span className="h-1.5 w-1.5 animate-pulse bg-[hsl(var(--muted-foreground))] [animation-delay:240ms]" />
                      <span className="ml-1 text-xs text-[hsl(var(--muted-foreground))]">Thinking…</span>
                    </div>
                  </div>
                </article>
              ) : null}
            </div>

            <div className="px-4 pb-4 pt-1">
              <div className="mx-auto w-full max-w-[880px]">
                <RunFollowupComposer runId={runId} delivery="same-run" />
              </div>
            </div>
          </section>

        {flyoutOpen ? (
          <>
            <div
              onMouseDown={startResize("details", detailsWidth)}
              className="w-1 shrink-0 cursor-col-resize bg-[var(--ui-border)] transition-colors hover:bg-[hsl(var(--primary))]"
              aria-hidden="true"
            />
            <aside
              role="dialog"
              aria-label="Run details"
              style={{ width: detailsWidth }}
              className="flex h-full shrink-0 flex-col gap-3 overflow-y-auto border-l border-[var(--ui-border)] bg-[hsl(var(--background))] p-4"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">Run details</h2>
                <button
                  type="button"
                  onClick={() => setFlyoutOpen(false)}
                  aria-label="Close run details"
                  className="fx-btn-secondary px-2 py-1 text-xs font-medium"
                >
                  Close
                </button>
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs md:grid-cols-4">
                <div className="fx-panel px-3 py-2">
                  <p className="fx-muted">Chat turns</p>
                  <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{chatEventsCount}</p>
                </div>
                <div className="fx-panel px-3 py-2">
                  <p className="fx-muted">System events</p>
                  <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{systemEventsCount}</p>
                </div>
                <div className="fx-panel px-3 py-2">
                  <p className="fx-muted">Errors / blockers</p>
                  <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{errorEventsCount}</p>
                </div>
                <div className="fx-panel px-3 py-2">
                  <p className="fx-muted">Graph mode</p>
                  <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{usedAgentStudioAgent ? "Agent-mediated" : "Fallback view"}</p>
                </div>
              </div>

              <div className="fx-panel p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold text-[var(--foreground)]">ATF posture at execution time</h2>
                  <span className="fx-muted text-[11px]">CSA Agentic Trust Framework</span>
                </div>
                {atfReport ? (
                  <>
                    <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4">
                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] px-2 py-1.5">
                        <p className="fx-muted">Coverage</p>
                        <p className="mt-0.5 text-sm font-semibold text-[var(--foreground)]">{Math.round(atfReport.coverage_percent)}%</p>
                      </div>
                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] px-2 py-1.5">
                        <p className="fx-muted">Maturity</p>
                        <p className="mt-0.5 text-sm font-semibold capitalize text-[var(--foreground)]">{atfReport.maturity_estimate}</p>
                      </div>
                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] px-2 py-1.5">
                        <p className="fx-muted">Blocked (24h)</p>
                        <p className="mt-0.5 text-sm font-semibold text-[var(--foreground)]">{atfReport.evidence.audit_blocked_24h}</p>
                      </div>
                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] px-2 py-1.5">
                        <p className="fx-muted">Errors (24h)</p>
                        <p className="mt-0.5 text-sm font-semibold text-[var(--foreground)]">{atfReport.evidence.audit_error_24h}</p>
                      </div>
                    </div>

                    <div className="mt-2 rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.24)] p-2">
                      <p className="fx-muted text-[11px] uppercase tracking-wide">Top current gaps</p>
                      {atfTopGaps.length > 0 ? (
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs leading-relaxed text-[var(--foreground)]">
                          {atfTopGaps.map((gap, index) => (
                            <li key={`atf-gap-${index}`}>{gap}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-1 text-xs text-[var(--foreground)]">No major gaps currently flagged.</p>
                      )}
                    </div>

                    <div className="mt-2">
                      <p className="fx-muted mb-1 text-[11px] uppercase tracking-wide">Control status</p>
                      <ControlStatusList report={atfReport.control_status} />
                    </div>
                  </>
                ) : (
                  <p className="mt-2 text-xs text-[hsl(var(--muted-foreground))]">{atfReportError ?? "Loading ATF posture..."}</p>
                )}
              </div>

            <div className="fx-panel p-2">
              <div className="mb-2 flex items-center gap-1 rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--card))] p-1 text-xs">
                {([
                  ["graph", "Execution Graph"],
                  ["cognition", "Cognition"],
                  ["artifacts", "Artifacts"],
                  ["approvals", "Approvals"],
                  ["guardrails", "Guardrails"],
                ] as const).map(([tabKey, label]) => (
                  <button
                    key={tabKey}
                    type="button"
                    onClick={() => setRightPanelTab(tabKey)}
                    className={`rounded-full px-2.5 py-1 ${rightPanelTab === tabKey ? "bg-[hsl(var(--primary)/0.18)] text-[var(--foreground)]" : "text-[hsl(var(--muted-foreground))]"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {rightPanelTab === "graph" ? (
                <div>
                  <div className="mb-3 flex items-center justify-between px-2 text-xs">
                    <h2 className="font-semibold">Execution Graph</h2>
                    <span className="fx-muted">
                      {usedAgentStudioAgent ? "Read-only snapshot" : "Default chat agent fallback shown"}
                    </span>
                  </div>
                  <RunGraphView nodes={effectiveGraphNodes} links={effectiveGraphLinks} height={520} />
                </div>
              ) : null}

              {rightPanelTab === "cognition" ? (
                <div className="p-2">
                  <h3 className="mb-2 text-sm font-semibold">Cognitive artifacts</h3>
                  {!cognitiveCommitment ? (
                    <p className="fx-muted text-xs">No cognitive artifacts were captured for this run.</p>
                  ) : (
                    <div className="space-y-2 text-xs">
                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="font-semibold text-[var(--foreground)]">Commitment</p>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[11px] text-[var(--foreground)]">
                              Status: {String(cognitiveCommitment.status || "captured")}
                            </span>
                            <span className="rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[11px] text-[var(--foreground)]">
                              Confidence: {typeof cognitiveCommitment.confidence === "number" ? `${Math.round(cognitiveCommitment.confidence * 100)}%` : "n/a"}
                            </span>
                          </div>
                        </div>
                        <p className="mt-2 text-xs font-medium text-[var(--foreground)]">{String(cognitiveCommitment.decision || "No decision captured")}</p>
                        {String(cognitiveCommitment.rationale || "").trim() ? (
                          <p className="mt-1 text-xs leading-relaxed text-[hsl(var(--muted-foreground))]">{String(cognitiveCommitment.rationale)}</p>
                        ) : null}
                      </div>

                      <div className="grid gap-2 md:grid-cols-2">
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Blockers</p>
                          {toStringList(cognitiveCommitment.blockers).length > 0 ? (
                            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[var(--foreground)]">
                              {toStringList(cognitiveCommitment.blockers).map((item, index) => (
                                <li key={`blocker-${index}`}>{item}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="mt-1 text-[var(--foreground)]">No blockers.</p>
                          )}
                        </div>
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Next actions</p>
                          {toStringList(cognitiveCommitment.next_actions).length > 0 ? (
                            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[var(--foreground)]">
                              {toStringList(cognitiveCommitment.next_actions).map((item, index) => (
                                <li key={`next-action-${index}`}>{item}</li>
                              ))}
                            </ul>
                          ) : (
                            <p className="mt-1 text-[var(--foreground)]">No next actions recorded.</p>
                          )}
                        </div>
                      </div>

                      <div className="grid gap-2 md:grid-cols-2">
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Supporting columns</p>
                          <p className="mt-1 text-[var(--foreground)]">{toStringList(cognitiveCommitment.supporting_columns).join(", ") || "None"}</p>
                        </div>
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Dissenting columns</p>
                          <p className="mt-1 text-[var(--foreground)]">{toStringList(cognitiveCommitment.dissenting_columns).join(", ") || "None"}</p>
                        </div>
                      </div>

                      <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                        <p className="fx-muted text-[10px] uppercase tracking-wide">Assembly</p>
                        <p className="mt-1 text-[var(--foreground)]">
                          {String(cognitiveAssembly?.assembly_id || "unknown")} • {String(cognitiveAssembly?.consensus_policy || "unspecified")} • {String(cognitiveAssembly?.inference_mode || "unspecified")}
                        </p>
                        {toStringList(cognitiveAssembly?.columns).length > 0 ? (
                          <p className="mt-1 text-[hsl(var(--muted-foreground))]">Columns: {toStringList(cognitiveAssembly?.columns).join(", ")}</p>
                        ) : null}
                      </div>

                      {cognitiveStateEntries.length > 0 ? (
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Column states</p>
                          <div className="mt-2 space-y-1.5">
                            {cognitiveStateEntries.map(([key, value]) => (
                              <div key={key} className="flex items-center justify-between gap-2 rounded border border-[var(--ui-border)] px-2 py-1">
                                <span className="font-medium text-[var(--foreground)]">{String(value?.column_id || key)}</span>
                                <span className="text-[hsl(var(--muted-foreground))]">
                                  Confidence: {typeof value?.confidence === "number" ? `${Math.round(value.confidence * 100)}%` : "n/a"}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}

                      {cognitiveMessages.length > 0 ? (
                        <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-2">
                          <p className="fx-muted text-[10px] uppercase tracking-wide">Messages</p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {cognitiveMessages.map((message, index) => (
                              <span key={`cognitive-message-${index}`} className="rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[11px] text-[var(--foreground)]">
                                {String(message.message_type || "message")} · {String(message.column_id || "unknown")}
                              </span>
                            ))}
                          </div>
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>
              ) : null}

              {rightPanelTab === "artifacts" ? (
                <div className="p-2">
                  <h3 className="mb-2 text-sm font-semibold">Artifacts</h3>
                  {run.artifacts.length === 0 ? (
                    <p className="fx-muted text-xs">No artifact was captured for this run.</p>
                  ) : (
                    <ul className="space-y-1.5 text-xs text-[var(--foreground)]">
                      {run.artifacts.map((artifact) => (
                        <li key={artifact.id} className="flex items-center justify-between gap-2 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-2">
                          <div className="min-w-0">
                            <p className="truncate font-medium text-[var(--foreground)]">{artifact.name}</p>
                            <p className="fx-muted truncate">{artifact.status} • v{artifact.version}</p>
                          </div>
                          <Link href={`/artifacts/${artifact.id}`} className="fx-btn-secondary shrink-0 px-2 py-1 text-[11px] font-medium">
                            Open
                          </Link>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}

              {rightPanelTab === "approvals" ? (
                <div className="p-2">
                  <h3 className="mb-2 text-sm font-semibold">Approvals</h3>
                  <div className="mb-2 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3 text-xs">
                    <p className="font-semibold text-[var(--foreground)]">Pending approval target</p>
                    {approvals.required ? (
                      <p className="fx-muted mt-1">
                        Artifact: {approvalArtifactId || "(n/a)"} • Version: v{approvalVersion} • Scope: {approvals.scope ?? "final send/export"}
                      </p>
                    ) : (
                      <p className="fx-muted mt-1">No approval required for this run.</p>
                    )}
                  </div>

                  {approvals.required ? (
                    <div className="space-y-2">
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <button
                          type="button"
                          disabled={approvalBusy !== null}
                          onClick={() => handleApprovalDecision("approved")}
                          className="fx-btn-success px-2 py-1.5 text-xs font-medium disabled:opacity-60"
                        >
                          {approvalBusy === "approved" ? "Submitting..." : "Approve"}
                        </button>
                        <button
                          type="button"
                          disabled={approvalBusy !== null}
                          onClick={() => handleApprovalDecision("changes_requested")}
                          className="fx-btn-secondary px-2 py-1.5 text-xs font-medium disabled:opacity-60"
                        >
                          {approvalBusy === "changes_requested" ? "Submitting..." : "Request edits"}
                        </button>
                      </div>

                      <form
                        onSubmit={(event: FormEvent) => {
                          event.preventDefault();
                          void handleApprovalDecision("changes_requested");
                        }}
                        className="space-y-1.5"
                      >
                        <label htmlFor="approval-feedback" className="fx-muted block text-[11px] font-medium">
                          Feedback / requested edits
                        </label>
                        <textarea
                          id="approval-feedback"
                          name="feedback"
                          value={approvalFeedback}
                          onChange={(event) => setApprovalFeedback(event.target.value)}
                          placeholder="Describe what to change before approval..."
                          className="fx-field min-h-20 w-full p-2 text-xs"
                        />
                      </form>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {rightPanelTab === "guardrails" ? (
                <div className="p-2">
                  <h3 className="mb-2 text-sm font-semibold">Guardrails</h3>
                  {guardrailEvents.length === 0 ? (
                    <p className="fx-muted text-xs">No guardrail findings were emitted for this run.</p>
                  ) : (
                    <ul className="space-y-2">
                      {guardrailEvents.map((event) => (
                        <li key={event.id} className="rounded-[1rem] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] p-3">
                          <p className="text-xs font-semibold text-[var(--foreground)]">{event.title}</p>
                          <p className="fx-muted mt-0.5 text-[11px]">{event.createdAt}</p>
                          <MarkdownBlock content={event.summary} className="mt-1" />
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </div>

            {approvalMessage ? (
              <div className="fx-panel px-3 py-2 text-xs text-[var(--foreground)]">
                {approvalMessage}
              </div>
            ) : null}
            </aside>
          </>
        ) : null}

        {filesOpen ? (
          <>
            <div
              onMouseDown={startResize("files", filesWidth)}
              className="w-1 shrink-0 cursor-col-resize bg-[var(--ui-border)] transition-colors hover:bg-[hsl(var(--primary))]"
              aria-hidden="true"
            />
            <aside
              aria-label="Files changed"
              style={{ width: filesWidth }}
              className="flex h-full shrink-0 flex-col overflow-hidden border-l border-[var(--ui-border)] bg-[hsl(var(--background))]"
            >
              <div className="flex items-center justify-between border-b border-[var(--ui-border)] px-3 py-2">
                <h2 className="text-sm font-semibold">Files changed</h2>
                <button type="button" onClick={() => setFilesOpen(false)} className="fx-btn-secondary px-2 py-0.5 text-xs">
                  Close
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                <RunChangesPane changedFiles={changedFiles} />
              </div>
            </aside>
          </>
        ) : null}
      </div>
    </div>
  );
}
