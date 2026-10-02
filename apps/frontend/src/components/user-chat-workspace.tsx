"use client";

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MarkdownBlock } from "@/components/markdown-block";
import { ReactFlowCanvas, type GraphLink, type GraphNode } from "@/components/reactflow-canvas";
import { RunArchiveButton } from "@/components/run-archive-button";
import { RunFollowupComposer, type FollowupComposerStatus } from "@/components/run-followup-composer";
import { StatusChip } from "@/components/status-chip";
import { TaskKickoffComposer } from "@/components/task-kickoff-composer";
import {
  getAtfAlignmentReport,
  getWorkflowRun,
  getWorkflowRunEvents,
  streamWorkflowRun,
  submitApproval,
  WORKFLOW_RUN_UPDATED_EVENT,
  type WorkflowRunDetail,
} from "@/lib/api";
import type { AtfAlignmentReport, InboxItem, WorkflowRunEvent, WorkflowRunSummary } from "@/types/frontier";

type UserChatWorkspaceProps = {
  initialRuns: WorkflowRunSummary[];
  initialInbox: InboxItem[];
  initialSelectedRunId: string | null;
  initialDetailsOpen: boolean;
  initialTab: "chat" | "graph";
  initialLoadError?: string | null;
};

type DrawerTab = "overview" | "artifacts" | "approvals" | "guardrails";

const EMPTY_FOLLOWUP_STATUS: FollowupComposerStatus = {
  state: "idle",
  message: null,
  createdRunId: null,
  provider: "",
  model: "",
  source: null,
};

const STOP_WORDS = new Set([
  "about",
  "after",
  "agent",
  "brief",
  "chat",
  "done",
  "draft",
  "email",
  "flow",
  "from",
  "into",
  "need",
  "needs",
  "pack",
  "plan",
  "review",
  "run",
  "step",
  "task",
  "that",
  "this",
  "with",
  "workflow",
]);

const RETRIEVAL_KEYWORDS = /\b(retriev|search|evidence|document|knowledge|context|source|research|kb|memory)\b/i;

function summarizeGraphText(value: string, maxLength = 88): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength - 3)}...` : trimmed;
}

function buildGraphNode(
  id: string,
  title: string,
  type: GraphNode["type"],
  x: number,
  y: number,
  config?: Record<string, unknown>,
): GraphNode {
  return { id, title, type, x, y, config };
}

function inboxByRunId(items: InboxItem[]): Map<string, InboxItem[]> {
  const mapping = new Map<string, InboxItem[]>();
  for (const item of items) {
    const current = mapping.get(item.runId) ?? [];
    current.push(item);
    mapping.set(item.runId, current);
  }
  return mapping;
}

function orderEvents(events: WorkflowRunEvent[]): WorkflowRunEvent[] {
  return [...events].sort((left, right) => {
    const leftValue = Date.parse(left.createdAt);
    const rightValue = Date.parse(right.createdAt);
    if (Number.isNaN(leftValue) || Number.isNaN(rightValue)) {
      return 0;
    }
    return leftValue - rightValue;
  });
}

function isChatEvent(event: WorkflowRunEvent): boolean {
  return event.type === "user_message" || event.type === "agent_message";
}

function extractTopics(runTitle: string, events: WorkflowRunEvent[]): string[] {
  const counts = new Map<string, number>();
  const combined = [runTitle, ...events.map((event) => `${event.title} ${event.summary}`)].join(" ");
  for (const token of combined.toLowerCase().match(/[a-z0-9][a-z0-9-]{2,}/g) ?? []) {
    if (STOP_WORDS.has(token)) {
      continue;
    }
    counts.set(token, (counts.get(token) ?? 0) + 1);
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 5)
    .map(([token]) => token.replace(/-/g, " "));
}

function buildExecutionGraph(
  runId: string,
  runTitle: string,
  run: WorkflowRunDetail | null,
  events: WorkflowRunEvent[],
): { nodes: GraphNode[]; links: GraphLink[] } {
  const orderedEvents = orderEvents(events);
  const topics = extractTopics(runTitle, events);
  const toolEvents = orderedEvents.filter((event) => event.type === "step_started" || event.type === "step_completed");
  const retrievalEvents = toolEvents.filter((event) => RETRIEVAL_KEYWORDS.test(`${event.title} ${event.summary}`));
  const memoryHighlights = orderedEvents
    .filter((event) => event.type === "agent_message" || event.type === "guardrail_result" || event.type === "approval_required")
    .slice(-3)
    .map((event) => summarizeGraphText(event.summary || event.title, 42))
    .filter(Boolean);
  const guardrailEvents = orderedEvents.filter((event) => event.type === "guardrail_result");
  const approvalEvents = orderedEvents.filter((event) => event.type === "approval_required" || event.type === "approval_decision");
  const artifactEvents = orderedEvents.filter((event) => event.type === "artifact_created");
  const chatEvents = orderedEvents.filter((event) => event.type === "user_message" || event.type === "agent_message");
  const artifactCount = run?.artifacts?.length ?? 0;
  const basePrompt = orderedEvents.find((event) => event.type === "user_message")?.summary ?? runTitle;

  const nodes: GraphNode[] = [
    buildGraphNode(`${runId}-trigger`, "Run Trigger", "frontier/trigger", 120, 220, {
      trigger_mode: "manual",
      default_message: summarizeGraphText(runTitle, 60),
      tags: topics,
    }),
    buildGraphNode(`${runId}-prompt`, "Prompt", "frontier/prompt", 430, 220, {
      objective: chatEvents.length > 2 ? "planning" : "general_assistant",
      audience: "operator",
      system_prompt_text: summarizeGraphText(basePrompt, 180),
    }),
    buildGraphNode(`${runId}-agent`, `Agent · ${chatEvents.filter((event) => event.type === "agent_message").length || 1}`, "frontier/agent", 760, 220, {
      role: run?.status === "Failed" ? "reviewer" : "executor",
      agent_id: runId,
      system_prompt: summarizeGraphText(
        orderedEvents.filter((event) => event.type === "agent_message").slice(-1)[0]?.summary ?? runTitle,
        200,
      ),
    }),
    buildGraphNode(`${runId}-memory`, `Memory · ${memoryHighlights.length || chatEvents.length || 1}`, "frontier/memory", 760, 58, {
      action: "read",
      scope: "run",
      session_id: runId,
      dimension_key: memoryHighlights.join(" | ") || `${chatEvents.length} conversation turns`,
    }),
  ];

  const links: GraphLink[] = [
    { from: `${runId}-trigger`, to: `${runId}-prompt`, from_port: "out", to_port: "in" },
    { from: `${runId}-prompt`, to: `${runId}-agent`, from_port: "prompt", to_port: "prompt" },
    { from: `${runId}-prompt`, to: `${runId}-agent`, from_port: "out", to_port: "in" },
    { from: `${runId}-memory`, to: `${runId}-agent`, from_port: "memory", to_port: "memory" },
  ];

  if (topics.length > 0 || retrievalEvents.length > 0) {
    nodes.push(
      buildGraphNode(`${runId}-knowledge`, `Knowledge · ${Math.max(retrievalEvents.length, topics.length)}`, "frontier/retrieval", 760, 382, {
        source_type: retrievalEvents.length > 0 ? "hybrid" : "graph",
        source_id: retrievalEvents.length > 0 ? "history://retrieval" : "history://topics",
        index_name: topics.join(", ") || "session-history",
        top_k: Math.max(1, Math.min(5, retrievalEvents.length || topics.length)),
      }),
    );
    links.push({ from: `${runId}-knowledge`, to: `${runId}-agent`, from_port: "documents", to_port: "retrieval" });
  }

  if (toolEvents.length > 0) {
    nodes.push(
      buildGraphNode(`${runId}-tools`, `Tools · ${toolEvents.length}`, "frontier/tool-call", 1110, 110, {
        tool_id: summarizeGraphText(toolEvents.map((event) => event.title).join(" | "), 120) || "tool/unspecified",
        input_schema: summarizeGraphText(toolEvents.map((event) => event.summary).join(" | "), 200),
        retry_count: toolEvents.length,
      }),
    );
    links.push(
      { from: `${runId}-agent`, to: `${runId}-tools`, from_port: "tool_request", to_port: "request" },
      { from: `${runId}-tools`, to: `${runId}-agent`, from_port: "result", to_port: "tool_result" },
    );
  }

  const needsGuardrailNode = guardrailEvents.length > 0 || run?.status === "Failed";
  if (needsGuardrailNode) {
    nodes.push(
      buildGraphNode(`${runId}-guardrail`, `Guardrails · ${Math.max(1, guardrailEvents.length)}`, "frontier/guardrail", 1110, 332, {
        stage: run?.status === "Failed" ? "tool_output" : "output",
        reject_message: summarizeGraphText(
          guardrailEvents.map((event) => event.summary).join(" | ") || `Run status: ${run?.status ?? "unknown"}`,
          200,
        ),
      }),
    );
    links.push({ from: `${runId}-agent`, to: `${runId}-guardrail`, from_port: "response", to_port: "candidate_output" });
  }

  const needsReviewNode = approvalEvents.length > 0 || Boolean(run?.approvals?.required);
  if (needsReviewNode) {
    nodes.push(
      buildGraphNode(`${runId}-review`, `Review · ${approvalEvents.length || 1}`, "frontier/human-review", 1450, 220, {
        reviewer_group: run?.approvals?.pending ? "ops" : "security",
        required_approvals: run?.approvals?.required ? 1 : 0,
        sla_minutes: 120,
      }),
    );
    links.push(
      needsGuardrailNode
        ? { from: `${runId}-guardrail`, to: `${runId}-review`, from_port: "approved_output", to_port: "candidate" }
        : { from: `${runId}-agent`, to: `${runId}-review`, from_port: "response", to_port: "candidate" },
    );
  }

  nodes.push(
    buildGraphNode(`${runId}-output`, artifactCount > 0 ? `Artifacts · ${artifactCount}` : `Outcome · ${run?.status ?? "Pending"}`, "frontier/output", 1780, 220, {
      destination: artifactCount > 0 ? "artifact_store" : "webhook",
      format: artifactCount > 0 ? "markdown" : "text",
      result: summarizeGraphText(
        artifactEvents.map((event) => event.summary).join(" | ") || (run?.artifacts ?? []).map((artifact) => artifact.name).join(", ") || run?.status || runTitle,
        200,
      ),
    }),
  );

  links.push(
    needsReviewNode
      ? { from: `${runId}-review`, to: `${runId}-output`, from_port: "approved", to_port: "result" }
      : needsGuardrailNode
        ? { from: `${runId}-guardrail`, to: `${runId}-output`, from_port: "approved_output", to_port: "result" }
        : { from: `${runId}-agent`, to: `${runId}-output`, from_port: "response", to_port: "result" },
  );

  return {
    nodes,
    links,
  };
}

function getBackendGraph(run: WorkflowRunDetail | null): { nodes: GraphNode[]; links: GraphLink[] } | null {
  const graph = run?.graph;
  if (!graph) {
    return null;
  }

  const nodes = Array.isArray(graph.nodes)
    ? graph.nodes
        .filter((node): node is GraphNode => Boolean(node && typeof node.id === "string" && typeof node.title === "string" && typeof node.type === "string"))
        .map((node) => ({
          id: node.id,
          title: node.title,
          type: node.type,
          x: typeof node.x === "number" ? node.x : 0,
          y: typeof node.y === "number" ? node.y : 0,
          config: node.config,
        }))
    : [];

  const links = Array.isArray(graph.links)
    ? graph.links
        .filter((link): link is GraphLink => Boolean(link && typeof link.from === "string" && typeof link.to === "string"))
        .map((link) => ({
          from: link.from,
          to: link.to,
          from_port: link.from_port,
          to_port: link.to_port,
        }))
    : [];

  if (nodes.length === 0 || links.length === 0) {
    return null;
  }

  return { nodes, links };
}

function ChatBubble({ event }: { event: WorkflowRunEvent }) {
  const content = String(event.content ?? event.summary ?? "").trim();
  const metadata = event.metadata && typeof event.metadata === "object"
    ? event.metadata as Record<string, unknown>
    : null;
  const agentName = typeof metadata?.selected_agent_name === "string"
    ? metadata.selected_agent_name.trim()
    : typeof event.title === "string" && /\sresponse$/i.test(event.title)
      ? event.title.replace(/\s+response$/i, "").trim()
      : "";
  const agentLabel = agentName || "Assistant";

  if (event.type === "user_message") {
    return (
      <div className="flex justify-end">
        <article className="max-w-[82%] min-w-0 border border-[color-mix(in_srgb,var(--fx-primary)_48%,var(--ui-border))] bg-[var(--fx-primary)] px-4 py-3 text-[0.86rem] leading-6 text-[#211200]">
          <p className="mb-1 text-[0.68rem] font-medium tracking-[0.02em] text-[#5d3b03]">You</p>
          <MarkdownBlock content={content} className="min-w-0 break-words [&_pre]:bg-[color-mix(in_srgb,hsl(var(--background))_22%,transparent)] [&_pre]:text-[#211200]" />
        </article>
      </div>
    );
  }

  if (event.type === "agent_message") {
    return (
      <div className="flex justify-start">
        <article className="max-w-[82%] min-w-0 border border-[var(--ui-border)] bg-[hsl(var(--card)/0.96)] px-4 py-3 text-[0.86rem] leading-6 text-[hsl(var(--foreground))]">
          <p className="mb-1 text-[0.68rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]">{agentLabel}</p>
          <MarkdownBlock content={content} className="min-w-0 break-words" />
        </article>
      </div>
    );
  }

  return (
    <div className="flex justify-center">
      <div className="max-w-[78%] rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_72%,hsl(var(--muted))_28%)] px-3.5 py-2 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)] shadow-[var(--fx-shadow-soft)]">
        <span className="font-medium text-[hsl(var(--foreground))]">{event.title}</span>
        <span className="mx-1.5">·</span>
        <span>{event.summary}</span>
      </div>
    </div>
  );
}

function DetailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-b border-[color-mix(in_srgb,var(--ui-border)_72%,transparent)] pb-4 last:border-b-0 last:pb-0">
      <h3 className="text-[0.74rem] font-medium tracking-[0.04em] text-[var(--fx-muted)]">{title}</h3>
      {children}
    </section>
  );
}

function DetailMetric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-[14px] border border-[color-mix(in_srgb,var(--ui-border)_78%,transparent)] bg-[hsl(var(--card)/0.9)] px-3 py-2.5 shadow-[var(--fx-shadow-soft)]">
      <p className="text-[0.7rem] font-medium tracking-[0.03em] text-[var(--fx-muted)]">{label}</p>
      <div className="mt-1 text-sm font-semibold text-[hsl(var(--foreground))]">{value}</div>
    </div>
  );
}

function DetailMessage({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "muted" }) {
  return (
    <div className={tone === "muted"
      ? "rounded-[14px] bg-[hsl(var(--muted)/0.45)] px-3.5 py-3 text-[0.8rem] leading-6 text-[var(--fx-muted)]"
      : "rounded-[14px] border border-[color-mix(in_srgb,var(--ui-border)_72%,transparent)] bg-[hsl(var(--card)/0.9)] px-3.5 py-3 text-[0.8rem] leading-6 text-[hsl(var(--foreground))] shadow-[var(--fx-shadow-soft)]"}
    >
      {children}
    </div>
  );
}

function DetailList({ children }: { children: ReactNode }) {
  return <div className="overflow-hidden rounded-[14px] border border-[color-mix(in_srgb,var(--ui-border)_78%,transparent)] bg-[hsl(var(--card)/0.9)] shadow-[var(--fx-shadow-soft)]">{children}</div>;
}

function DetailListItem({ title, subtitle, meta }: { title: ReactNode; subtitle?: ReactNode; meta?: ReactNode }) {
  return (
    <article className="border-b border-[color-mix(in_srgb,var(--ui-border)_70%,transparent)] px-3 py-3 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-[hsl(var(--foreground))]">{title}</div>
          {subtitle ? <div className="mt-1 text-[0.78rem] leading-6 text-[var(--fx-muted)]">{subtitle}</div> : null}
        </div>
        {meta ? <div className="shrink-0 text-[0.68rem] font-medium tracking-[0.01em] text-[var(--fx-muted)]">{meta}</div> : null}
      </div>
    </article>
  );
}

export function UserChatWorkspace({
  initialRuns,
  initialInbox,
  initialSelectedRunId,
  initialDetailsOpen,
  initialTab,
  initialLoadError = null,
}: UserChatWorkspaceProps) {
  const router = useRouter();
  const detailsPanelWidth = "clamp(20rem, 26vw, 30rem)";
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialSelectedRunId ?? null);
  const [selectedRun, setSelectedRun] = useState<WorkflowRunDetail | null>(null);
  const [events, setEvents] = useState<WorkflowRunEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(initialDetailsOpen);
  const [activeTab, setActiveTab] = useState<"chat" | "graph">(initialTab);
  const [drawerTab, setDrawerTab] = useState<DrawerTab>("overview");
  const [approvalFeedback, setApprovalFeedback] = useState("");
  const [approvalBusy, setApprovalBusy] = useState<"approved" | "changes_requested" | null>(null);
  const [approvalMessage, setApprovalMessage] = useState<string | null>(null);
  const [followupStatus, setFollowupStatus] = useState<FollowupComposerStatus>(EMPTY_FOLLOWUP_STATUS);
  const [atfReport, setAtfReport] = useState<AtfAlignmentReport | null>(null);
  const [streamedResponse, setStreamedResponse] = useState("");
  const [runLoadError, setRunLoadError] = useState<string | null>(initialLoadError);
  const [runRefreshNonce, setRunRefreshNonce] = useState(0);

  useEffect(() => {
    setSelectedRunId(initialSelectedRunId ?? null);
  }, [initialSelectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      setSelectedRun(null);
      setEvents([]);
      setStreamedResponse("");
      return;
    }

    let active = true;
    setLoading(true);
    setStreamedResponse("");
    setRunLoadError(null);

    void Promise.all([getWorkflowRun(selectedRunId), getWorkflowRunEvents(selectedRunId)])
      .then(([runDetail, runEvents]) => {
        if (!active) {
          return;
        }
        setSelectedRun(runDetail);
        setEvents(runEvents);
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setSelectedRun(null);
        setEvents([]);
        setRunLoadError(error instanceof Error ? error.message : "Unable to load the selected session.");
      })
      .finally(() => {
        if (!active) {
          return;
        }
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [initialRuns, runRefreshNonce, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) {
      return;
    }
    const stopStreaming = streamWorkflowRun(selectedRunId, {
      onMessage: (event) => {
        if (event.type === "delta") {
          const text = typeof event.payload.text === "string" ? event.payload.text : "";
          if (text) {
            setStreamedResponse((current: string) => current + text);
          }
          return;
        }
        if (event.type === "final") {
          const text = typeof event.payload.text === "string" ? event.payload.text : "";
          setStreamedResponse(text);
          void Promise.all([getWorkflowRun(selectedRunId), getWorkflowRunEvents(selectedRunId)]).then(
            ([runDetail, runEvents]) => {
              setSelectedRun(runDetail);
              setEvents(runEvents);
              setRunLoadError(null);
            },
          );
          return;
        }
        if (event.type === "complete") {
          setStreamedResponse("");
        }
      },
      onError: () => {
        // Keep the current snapshot; polling refresh can recover on the next selection.
      },
    });

    return () => {
      stopStreaming();
    };
  }, [initialRuns, runRefreshNonce, selectedRunId]);

  useEffect(() => {
    let active = true;

    void getAtfAlignmentReport()
      .then((report) => {
        if (active) {
          setAtfReport(report);
        }
      })
      .catch(() => {
        if (active) {
          setAtfReport(null);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedRunId || typeof window === "undefined") {
      return;
    }

    const handleRunUpdated = (event: Event) => {
      const detail = (event as CustomEvent<WorkflowRunSummary>).detail;
      if (!detail || detail.id !== selectedRunId) {
        return;
      }
      setSelectedRun((current) => (current ? { ...current, title: detail.title, title_source: detail.title_source } : current));
    };

    window.addEventListener(WORKFLOW_RUN_UPDATED_EVENT, handleRunUpdated as EventListener);
    return () => {
      window.removeEventListener(WORKFLOW_RUN_UPDATED_EVENT, handleRunUpdated as EventListener);
    };
  }, [selectedRunId]);

  useEffect(() => {
    setFollowupStatus(EMPTY_FOLLOWUP_STATUS);
  }, [selectedRunId]);

  const selectedRunSummary = useMemo(
    () => (selectedRunId ? initialRuns.find((run) => run.id === selectedRunId) ?? null : null),
    [initialRuns, selectedRunId],
  );
  const displayedRunTitle = selectedRun?.title?.trim() || selectedRunSummary?.title || "Session";
  const relatedInboxItems = useMemo(() => {
    if (!selectedRunId) {
      return [];
    }
    return inboxByRunId(initialInbox).get(selectedRunId) ?? [];
  }, [initialInbox, selectedRunId]);
  const ordered = useMemo<WorkflowRunEvent[]>(() => {
    const base = orderEvents(events);
    if (!streamedResponse.trim()) {
      return base;
    }
    const lastEvent = base[base.length - 1];
    if (lastEvent?.type === "agent_message" && lastEvent.summary === streamedResponse) {
      return base;
    }
    const streamingEvent: WorkflowRunEvent = {
      id: "streaming-agent-message",
      type: "agent_message",
      title: "Assistant",
      summary: streamedResponse,
      createdAt: new Date().toISOString(),
    };
    return [
      ...base,
      streamingEvent,
    ];
  }, [events, streamedResponse]);
  const transcriptEvents = useMemo(
    () => ordered.filter(isChatEvent),
    [ordered],
  );
  const visibleTimelineEvents = transcriptEvents.length > 0 ? transcriptEvents : ordered;
  const recentContext = useMemo(
    () => ordered.filter(isChatEvent).slice(-6).map((event: WorkflowRunEvent) => `${event.type === "user_message" ? "User" : "Agent"}: ${event.summary}`).join("\n"),
    [ordered],
  );
  const stats = useMemo(() => ({
    chatTurns: ordered.filter(isChatEvent).length,
    systemEvents: ordered.filter((event: WorkflowRunEvent) => !isChatEvent(event)).length,
    blockers: ordered.filter((event: WorkflowRunEvent) => event.type === "error" || /blocked|reject|failed/i.test(`${event.title} ${event.summary}`)).length,
    topics: extractTopics(displayedRunTitle, ordered),
  }), [displayedRunTitle, ordered]);
  const graph = useMemo(() => {
    const backendGraph = getBackendGraph(selectedRun);
    if (backendGraph) {
      return backendGraph;
    }
    return buildExecutionGraph(selectedRunId ?? "session", displayedRunTitle, selectedRun, ordered);
  }, [displayedRunTitle, ordered, selectedRun, selectedRunId]);
  const approvals = selectedRun?.approvals ?? { required: false, pending: false };
  const guardrailEvents = ordered.filter((event: WorkflowRunEvent) => event.type === "guardrail_result");
  const activeRuntimeProvider = followupStatus.provider || (typeof selectedRun?.runtime?.provider === "string" ? selectedRun.runtime.provider : "");
  const activeRuntimeModel = followupStatus.model || (typeof selectedRun?.runtime?.model === "string" ? selectedRun.runtime.model : "");
  const activeRuntimeSource = followupStatus.source || (typeof selectedRun?.runtime?.source === "string" ? selectedRun.runtime.source : null);
  const selectedRunStatus = selectedRunSummary?.status ?? selectedRun?.status ?? (loading ? "Loading" : "Running");
  const selectedRunUpdatedAt = selectedRunSummary?.updatedAt ?? (loading ? "loading" : "just now");
  const selectedRunProgressLabel = selectedRunSummary?.progressLabel ?? (loading ? "Loading session" : selectedRun?.status ?? "Active");
  const actionItems = useMemo(() => {
    const items: string[] = [];
    for (const inboxItem of relatedInboxItems) {
      items.push(inboxItem.reason);
    }
    if (approvals.pending) {
      items.push("Approval decision is still pending.");
    }
    for (const event of guardrailEvents) {
      items.push(event.summary);
    }
    return [...new Set(items)].slice(0, 6);
  }, [approvals.pending, guardrailEvents, relatedInboxItems]);

  useEffect(() => {
    const timeline = timelineRef.current;
    if (!timeline) {
      return;
    }
    timeline.scrollTop = timeline.scrollHeight;
  }, [ordered]);

  async function handleApproval(decision: "approved" | "changes_requested") {
    if (!selectedRunId || !approvals.required) {
      return;
    }
    if (decision === "changes_requested" && !approvalFeedback.trim()) {
      setApprovalMessage("Add feedback before requesting changes.");
      return;
    }

    setApprovalBusy(decision);
    setApprovalMessage(null);
    try {
      await submitApproval({
        run_id: selectedRunId,
        decision,
        artifact_id: approvals.artifact_id ?? selectedRun?.artifacts[0]?.id ?? "",
        version: approvals.version ?? 1,
        feedback: decision === "changes_requested" ? approvalFeedback.trim() : undefined,
      });
      setApprovalMessage(decision === "approved" ? "Approval submitted." : "Change request submitted.");
      router.refresh();
    } catch {
      setApprovalMessage("Unable to submit decision right now.");
    } finally {
      setApprovalBusy(null);
    }
  }

  if (!selectedRunId) {
    return (
      <section className="-m-5 flex min-h-[calc(100vh-130px)] items-center justify-center bg-[linear-gradient(180deg,color-mix(in_srgb,var(--fx-primary)_6%,transparent),transparent_24%)] p-5">
        <div className="w-full max-w-4xl">
          <div className="mx-auto max-w-2xl text-center">
            <p className="text-[0.72rem] font-medium tracking-[0.06em] text-[var(--fx-muted)]">Workspace</p>
            <h1 className="mt-4 text-3xl font-semibold text-[hsl(var(--foreground))]">{initialLoadError ? "Inbox unavailable" : "Start a new chat or task"}</h1>
            <p className="mt-3 text-[0.95rem] leading-7 text-[var(--fx-muted)]">
              {initialLoadError
                ? `The inbox could not be loaded from the backend. ${initialLoadError}`
                : "New sign-ins now land here first. Kick off a fresh task from this blank workspace, or pick an existing session from the sidebar when you need to resume work."}
            </p>
          </div>

          {!initialLoadError ? (
            <div className="mx-auto mt-8 max-w-2xl">
              <TaskKickoffComposer />
              <div className="mt-4 flex justify-center gap-3">
                <Link href="/workflows/start" className="fx-btn-secondary px-4 py-2 text-sm font-medium no-underline">Browse Workflows</Link>
                <Link href="/artifacts" className="fx-btn-secondary px-4 py-2 text-sm font-medium no-underline">View Artifacts</Link>
              </div>
            </div>
          ) : null}
        </div>
      </section>
    );
  }

  return (
    <section
      data-testid="session-workspace"
            className="relative z-0 flex h-[calc(100vh-96px)] min-h-[calc(100vh-96px)] flex-col overflow-hidden rounded-[18px] border border-[var(--ui-border)] bg-[linear-gradient(180deg,color-mix(in_srgb,var(--fx-primary)_6%,white_94%),transparent_24%)] shadow-[var(--fx-shadow-panel)]"
    >
      <div className="relative isolate flex min-h-0 flex-1 flex-col overflow-hidden bg-[color-mix(in_srgb,hsl(var(--background))_62%,hsl(var(--card))_38%)]">
        <div className="border-b border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_84%,transparent)] px-5 py-3 lg:px-6 xl:px-8">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <p className="truncate text-[1rem] font-semibold tracking-[-0.02em] text-[hsl(var(--foreground))]">{displayedRunTitle}</p>
                <StatusChip status={selectedRunStatus} />
                <p className="text-[0.75rem] text-[var(--fx-muted)]">Updated {selectedRunUpdatedAt} · {selectedRunProgressLabel}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] p-1 shadow-[var(--fx-shadow-soft)]">
                <button
                  type="button"
                  onClick={() => setActiveTab("chat")}
                  className={activeTab === "chat" ? "rounded-[10px] bg-[var(--fx-primary)] px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.02em] text-[var(--fx-primary-text)]" : "rounded-[10px] px-3 py-1.5 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]"}
                >
                  Chat
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("graph")}
                  className={activeTab === "graph" ? "rounded-[10px] bg-[var(--fx-primary)] px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.02em] text-[var(--fx-primary-text)]" : "rounded-[10px] px-3 py-1.5 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]"}
                >
                  Execution Graph
                </button>
              </div>
              <RunArchiveButton
                runId={selectedRunId}
                iconOnly
                ariaLabel="Archive session"
                buttonClassName="h-8 w-8 rounded-[10px] border border-[color-mix(in_srgb,var(--fx-danger)_32%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] px-0 text-[var(--fx-danger)] transition hover:bg-[color-mix(in_srgb,var(--fx-danger)_18%,transparent)] disabled:opacity-60"
              />
                <button
                  type="button"
                  aria-label={detailsOpen ? "Hide details" : "Show details"}
                  onClick={() => setDetailsOpen((value) => !value)}
                  className="fx-btn-secondary relative z-10 h-8 w-8 shrink-0 px-0 text-xs"
                >
                  <svg viewBox="0 0 16 16" className="mx-auto h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.4">
                    <path d="M2.5 3.5h11v9h-11z" />
                    <path d="M9.5 3.5v9" />
                  </svg>
              </button>
            </div>
          </div>
        </div>

        <div className="relative flex-1 overflow-hidden">
          <div className="h-full transition-[padding-right] duration-200" style={{ paddingRight: detailsOpen ? detailsPanelWidth : "0rem" }}>
            {activeTab === "chat" ? (
                <div className="flex h-full min-h-0 flex-col">
                <div className="border-b border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_78%,transparent)] px-5 py-2 lg:px-6 xl:px-8">
                  <div className="flex flex-wrap items-center gap-2 text-[0.72rem] text-[var(--fx-muted)]">
                    <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{stats.chatTurns} chat turns</span>
                    <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{stats.systemEvents} system events</span>
                    <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{relatedInboxItems.length} action items</span>
                    {stats.topics.slice(0, 2).map((topic) => (
                      <span key={topic} className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{topic}</span>
                    ))}
                  </div>
                </div>

                {runLoadError ? (
                  <div className="border-b border-[var(--ui-border)] bg-[color-mix(in_srgb,var(--fx-warning)_10%,transparent)] px-5 py-3 text-[0.8rem] text-[hsl(var(--foreground))] lg:px-6 xl:px-8">
                    Unable to refresh this session from the backend. {runLoadError}
                  </div>
                ) : null}

                <div ref={timelineRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5 lg:px-6 xl:px-8 2xl:px-10">
                  {loading ? (
                    <div className="space-y-3">
                      {[0, 1, 2].map((index) => (
                        <div key={index} className="h-20 animate-pulse rounded-[14px] border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.65)]" />
                      ))}
                    </div>
                  ) : visibleTimelineEvents.length === 0 ? (
                    <div className="flex h-full min-h-80 items-center justify-center">
                      <div className="max-w-md text-center">
                        <p className="text-[0.74rem] font-medium tracking-[0.05em] text-[var(--fx-muted)]">No messages yet</p>
                        <p className="mt-3 text-[0.95rem] leading-7 text-[var(--fx-muted)]">This session exists, but the timeline is still empty. Use the chat box below to continue the run.</p>
                      </div>
                    </div>
                  ) : (
                    visibleTimelineEvents.map((event) => <ChatBubble key={event.id} event={event} />)
                  )}
                </div>

                <div
                  data-testid="session-followup-dock"
                  className="mt-auto shrink-0 border-t border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_94%,hsl(var(--background))_6%)] px-5 py-3 backdrop-blur-sm lg:px-6 xl:px-8 2xl:px-10"
                >
                  <RunFollowupComposer
                    runId={selectedRunId}
                    recentContext={recentContext}
                    initialRuntime={{
                      provider: typeof selectedRun?.runtime?.provider === "string" ? selectedRun.runtime.provider : undefined,
                      model: typeof selectedRun?.runtime?.model === "string" ? selectedRun.runtime.model : undefined,
                    }}
                    onStatusChange={(status) => {
                      setFollowupStatus(status);
                      if (status.state === "submitting" || (status.state === "success" && status.createdRunId === selectedRunId)) {
                        setRunRefreshNonce((current) => current + 1);
                      }
                      if (status.state !== "idle") {
                        setDetailsOpen(true);
                      }
                    }}
                  />
                </div>
              </div>
            ) : (
              <div className="flex h-full flex-col px-5 py-5 lg:px-6 xl:px-8 2xl:px-10">
                <div className="mb-3 flex flex-wrap items-center gap-2 text-[0.74rem] text-[var(--fx-muted)]">
                  <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{ordered.filter((event) => event.type === "step_started" || event.type === "step_completed").length} tool calls</span>
                  <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{selectedRun?.artifacts.length ?? 0} artifacts</span>
                  {stats.topics.slice(0, 3).map((topic) => (
                    <span key={topic} className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] px-2.5 py-1 font-medium tracking-[0.02em]">{topic}</span>
                  ))}
                </div>
                <div className="min-h-[420px] flex-1 overflow-hidden rounded-[16px] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.92)] shadow-[var(--fx-shadow-soft)] xl:min-h-[560px]">
                  <ReactFlowCanvas nodes={graph.nodes} links={graph.links} readOnly className="h-full" edgeAnimated />
                </div>
              </div>
            )}
          </div>

          <aside
            data-testid="session-details-rail"
            className="absolute inset-y-0 right-0 z-10 border-l border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] transition-transform duration-200"
            style={{ width: detailsPanelWidth, transform: detailsOpen ? "translateX(0)" : "translateX(100%)", pointerEvents: detailsOpen ? "auto" : "none" }}
          >
            <div className="flex h-full flex-col">
              <div className="border-b border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_84%,transparent)] px-4 py-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-[0.72rem] font-medium tracking-[0.04em] text-[var(--fx-muted)]">Session Details</p>
                    <p className="mt-1 truncate text-[0.98rem] font-semibold tracking-[-0.02em] text-[hsl(var(--foreground))]">{displayedRunTitle}</p>
                    <p className="mt-1 text-[0.75rem] text-[var(--fx-muted)]">{selectedRunStatus} · {selectedRunProgressLabel}</p>
                  </div>
                  <button type="button" onClick={() => setDetailsOpen(false)} className="fx-btn-secondary relative z-10 h-8 w-8 shrink-0 px-0 text-xs">X</button>
                </div>
                <div className="mt-3 inline-flex rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.96)] p-1 shadow-[var(--fx-shadow-soft)]">
                  {(["overview", "artifacts", "approvals", "guardrails"] as DrawerTab[]).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => setDrawerTab(tab)}
                      className={drawerTab === tab ? "rounded-[10px] bg-[var(--fx-primary)] px-3 py-1.5 text-[0.72rem] font-semibold tracking-[0.02em] text-[var(--fx-primary-text)]" : "rounded-[10px] px-3 py-1.5 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]"}
                    >
                      {tab}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex-1 space-y-5 overflow-y-auto px-4 py-4">
                {drawerTab === "overview" ? (
                  <>
                    <DetailSection title="Snapshot">
                      <div className="grid grid-cols-2 gap-2 text-sm">
                        <DetailMetric label="Chat turns" value={stats.chatTurns} />
                        <DetailMetric label="Blockers" value={stats.blockers} />
                        <DetailMetric label="Inbox flags" value={relatedInboxItems.length} />
                        <DetailMetric label="Artifacts" value={selectedRun?.artifacts.length ?? 0} />
                      </div>
                    </DetailSection>

                    <DetailSection title="Follow-up Activity">
                      <div className="space-y-3 text-sm">
                        <div className="grid grid-cols-2 gap-2">
                          <DetailMetric label="Provider" value={activeRuntimeProvider || "Default"} />
                          <DetailMetric label="Model" value={activeRuntimeModel || "Platform default"} />
                        </div>
                        <DetailMessage>
                          <p className="text-[0.72rem] font-medium tracking-[0.04em] text-[var(--fx-muted)]">Status</p>
                          <p className="mt-2 text-[0.8rem] leading-6 text-[hsl(var(--foreground))]">
                            {followupStatus.message
                              ? followupStatus.message
                              : followupStatus.state === "submitting"
                                ? "Sending follow-up…"
                                : "Use the chat footer to choose a model and send the next follow-up."}
                          </p>
                          {followupStatus.createdRunId ? (
                            <p className="mt-2 text-[0.78rem] text-[hsl(var(--foreground))]">
                              Run created: <Link href={`/inbox?session=${encodeURIComponent(followupStatus.createdRunId)}`} className="underline decoration-dotted underline-offset-2">{followupStatus.createdRunId}</Link>
                            </p>
                          ) : null}
                          <p className="mt-2 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]">
                            Tools route through published `@agents` and `/workflows` from the chat footer.
                          </p>
                          {activeRuntimeSource ? (
                            <p className="mt-1 text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]">Runtime source: {activeRuntimeSource}</p>
                          ) : null}
                        </DetailMessage>
                      </div>
                    </DetailSection>

                    <DetailSection title="Action Queue">
                      {actionItems.length === 0 ? (
                        <DetailMessage tone="muted">No open action items for this session.</DetailMessage>
                      ) : (
                        <DetailList>
                          {actionItems.map((item) => (
                            <DetailListItem key={item} title={item} />
                          ))}
                        </DetailList>
                      )}
                    </DetailSection>

                    <DetailSection title="Inbox Context">
                      {relatedInboxItems.length === 0 ? (
                        <DetailMessage tone="muted">No outstanding inbox items tied to this session.</DetailMessage>
                      ) : (
                        <DetailList>
                          {relatedInboxItems.map((item) => (
                            <DetailListItem key={item.id} title={item.artifactType} subtitle={item.reason} meta={item.queue} />
                          ))}
                        </DetailList>
                      )}
                    </DetailSection>

                    <DetailSection title="ATF Posture">
                      {atfReport ? (
                        <div className="space-y-3 text-sm">
                          <div className="grid grid-cols-2 gap-2">
                            <DetailMetric label="Coverage" value={`${Math.round(atfReport.coverage_percent)}%`} />
                            <DetailMetric label="Maturity" value={<span className="capitalize">{atfReport.maturity_estimate}</span>} />
                          </div>
                          <DetailMessage>
                            <p className="text-[0.72rem] font-medium tracking-[0.02em] text-[var(--fx-muted)]">Gaps</p>
                            <div className="mt-2 flex flex-wrap gap-2">
                              {Object.values(atfReport.pillars).flatMap((pillar) => pillar.gaps).slice(0, 4).map((gap) => (
                                <span key={gap} className="rounded-full border border-[var(--ui-border)] px-2.5 py-1 text-[0.68rem] font-medium tracking-[0.01em] text-[hsl(var(--foreground))]">{gap}</span>
                              ))}
                            </div>
                          </DetailMessage>
                        </div>
                      ) : (
                        <DetailMessage tone="muted">ATF posture unavailable.</DetailMessage>
                      )}
                    </DetailSection>
                  </>
                ) : null}

                {drawerTab === "artifacts" ? (
                  <DetailSection title="Artifacts">
                    {(selectedRun?.artifacts ?? []).length === 0 ? (
                      <DetailMessage tone="muted">No artifacts recorded yet.</DetailMessage>
                    ) : (
                      <DetailList>
                        {(selectedRun?.artifacts ?? []).map((artifact) => (
                          <DetailListItem key={artifact.id} title={artifact.name} subtitle={artifact.status} meta={`v${artifact.version}`} />
                        ))}
                      </DetailList>
                    )}
                  </DetailSection>
                ) : null}

                {drawerTab === "approvals" ? (
                  <DetailSection title="Approvals">
                    {approvals.required ? (
                      <div className="space-y-3">
                        <DetailMessage tone="muted">
                          {approvals.pending ? "A human decision is required before the run can continue." : "No pending approval decision right now."}
                        </DetailMessage>
                        <textarea
                          value={approvalFeedback}
                          onChange={(event) => setApprovalFeedback(event.target.value)}
                          placeholder="Leave guidance for the next pass"
                          className="fx-input min-h-28 w-full px-3 py-3 text-sm"
                        />
                        <div className="flex gap-2">
                          <button type="button" onClick={() => void handleApproval("approved")} disabled={approvalBusy !== null} className="fx-btn-success px-3 py-2 text-xs font-medium">
                            {approvalBusy === "approved" ? "Approving…" : "Approve"}
                          </button>
                          <button type="button" onClick={() => void handleApproval("changes_requested")} disabled={approvalBusy !== null} className="fx-btn-warning px-3 py-2 text-xs font-medium">
                            {approvalBusy === "changes_requested" ? "Sending…" : "Request changes"}
                          </button>
                        </div>
                        {approvalMessage ? <p className="text-xs text-[var(--fx-muted)]">{approvalMessage}</p> : null}
                      </div>
                    ) : (
                      <DetailMessage tone="muted">This session does not currently require approval.</DetailMessage>
                    )}
                  </DetailSection>
                ) : null}

                {drawerTab === "guardrails" ? (
                  <DetailSection title="Guardrails">
                    {guardrailEvents.length === 0 ? (
                      <DetailMessage tone="muted">No guardrail findings for this session.</DetailMessage>
                    ) : (
                      <DetailList>
                        {guardrailEvents.map((event) => (
                          <DetailListItem key={event.id} title={event.title} subtitle={event.summary} />
                        ))}
                      </DetailList>
                    )}
                  </DetailSection>
                ) : null}
              </div>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
}
