"use client";

import { useEffect, useMemo, useState } from "react";
import {
  FX_ACCENT,
  FxKicker,
  FxMono,
  FxPanel,
  FxSectionHeader,
  FxStat,
  FxTag,
} from "@/components/fx-ui";
import { getMemorySession, getWorkflowRuns, type MemorySessionResponse } from "@/lib/api";
import type { WorkflowRunSummary } from "@/types/frontier";

const CLUSTERS = [
  "Security Policies",
  "User Preferences",
  "Domain Knowledge",
  "Threat Intel",
  "API Contracts",
  "Historical Runs",
  "ABAC Rules",
  "Agent Profiles",
];

const FALLBACK_LONG_TERM = [
  {
    id: "lt_001",
    cluster: "Security Policies",
    summary: "ABAC rule: data_room access requires data_classification attribute",
    score: 0.97,
    indexed: "2h ago",
  },
  {
    id: "lt_002",
    cluster: "ABAC Rules",
    summary: "External partner tokens scoped to read-only within partner namespace",
    score: 0.94,
    indexed: "2h ago",
  },
  {
    id: "lt_003",
    cluster: "Threat Intel",
    summary: "Known lateral movement pattern: off-hours API access from svc accounts",
    score: 0.89,
    indexed: "1d ago",
  },
  {
    id: "lt_004",
    cluster: "Historical Runs",
    summary: "Q3 audit identified 5 violations — 4 remediated, 1 accepted risk",
    score: 0.85,
    indexed: "3d ago",
  },
  {
    id: "lt_005",
    cluster: "Domain Knowledge",
    summary: "EU data rooms subject to GDPR article 17 right-to-erasure obligations",
    score: 0.82,
    indexed: "1w ago",
  },
  {
    id: "lt_006",
    cluster: "Agent Profiles",
    summary: "Compliance Agent: avg 14,200 tokens per task · 94% completion rate",
    score: 0.78,
    indexed: "12h ago",
  },
];

function formatBytes(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / (1024 * 1024)).toFixed(1)}MB`;
}

function relativeTime(iso: string | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diff = Date.now() - d.getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  return `${days}d ago`;
}

export function MemoryScreen({
  initialRuns,
}: {
  initialRuns: WorkflowRunSummary[];
}) {
  const [runs, setRuns] = useState<WorkflowRunSummary[]>(initialRuns);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(
    initialRuns[0]?.id ?? null,
  );
  const [memorySession, setMemorySession] = useState<MemorySessionResponse | null>(null);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    void getWorkflowRuns()
      .then((r) => {
        if (cancelled) return;
        setRuns(r);
        if (!selectedSessionId && r[0]) setSelectedSessionId(r[0].id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedSessionId) {
      setMemorySession(null);
      return;
    }
    let cancelled = false;
    setMemoryLoading(true);
    void getMemorySession(selectedSessionId)
      .then((res) => {
        if (cancelled) return;
        setMemorySession(res);
      })
      .catch(() => {
        if (cancelled) return;
        setMemorySession(null);
      })
      .finally(() => {
        if (cancelled) return;
        setMemoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId]);

  const longTermEntries = useMemo(
    () =>
      FALLBACK_LONG_TERM.filter(
        (e) =>
          !query ||
          e.summary.toLowerCase().includes(query.toLowerCase()) ||
          e.cluster.toLowerCase().includes(query.toLowerCase()),
      ),
    [query],
  );

  const sessionEntries = memorySession?.entries ?? [];
  const sessionSize = sessionEntries.reduce(
    (acc, e) => acc + (e.content?.length ?? 0),
    0,
  );

  return (
    <div className="flex flex-col gap-5">
      <FxSectionHeader
        label="Memory"
        index="/04 — System"
        sub="Cortical column — short-term session context and long-term persistent knowledge"
        action={
          <span
            className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]"
            style={{
              background: "hsl(142 55% 45% / 0.08)",
              borderColor: "hsl(142 55% 45% / 0.3)",
              color: FX_ACCENT.success,
            }}
          >
            <span
              className="h-1.5 w-1.5 animate-pulse rounded-full"
              style={{ background: FX_ACCENT.success }}
            />
            Memory Active
          </span>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <FxStat
          label="Short-term Entries"
          value={sessionEntries.length}
          sub={`${formatBytes(sessionSize)} · session scope`}
          accent={FX_ACCENT.info}
        />
        <FxStat
          label="Long-term Entries"
          value="1,284"
          sub="48 MB indexed"
          accent={FX_ACCENT.purple}
        />
        <FxStat
          label="Memory Clusters"
          value={CLUSTERS.length}
          sub="Last indexed 2m ago"
          accent={FX_ACCENT.primary}
        />
        <FxStat
          label="Retrieval Latency"
          value="12ms"
          sub="p95 across all reads"
          accent={FX_ACCENT.success}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-2.5 flex items-center justify-between">
            <FxKicker>Short-term Memory</FxKicker>
            {runs.length > 0 ? (
              <select
                className="fx-input px-2 py-1 text-[11px]"
                value={selectedSessionId ?? ""}
                onChange={(e) => setSelectedSessionId(e.target.value || null)}
                aria-label="Select session"
              >
                {runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title.slice(0, 50)}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          <FxPanel>
            <div className="flex items-center justify-between border-b border-[var(--ui-border)] bg-[hsl(var(--muted))] px-3.5 py-2.5">
              <span className="text-[11px] font-medium text-[hsl(var(--foreground))]">
                Session context
              </span>
              <FxMono>
                {selectedSessionId
                  ? selectedSessionId.slice(0, 12)
                  : "no session"}
              </FxMono>
            </div>
            {memoryLoading ? (
              <div className="px-3.5 py-6 text-center text-[12px] text-[var(--fx-muted)]">
                Loading session memory…
              </div>
            ) : sessionEntries.length === 0 ? (
              <div className="px-3.5 py-6 text-center text-[12px] text-[var(--fx-muted)]">
                {selectedSessionId
                  ? "No short-term entries for this session yet."
                  : "Select a session to view its memory."}
              </div>
            ) : (
              sessionEntries.map((entry, i) => (
                <div
                  key={entry.id}
                  className="px-3.5 py-2.5"
                  style={{
                    borderBottom:
                      i < sessionEntries.length - 1
                        ? "1px solid var(--ui-border)"
                        : "none",
                  }}
                >
                  <div className="mb-1 flex items-start justify-between gap-2">
                    <FxMono
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: "hsl(var(--foreground))",
                      }}
                    >
                      {entry.node_id || entry.id}
                    </FxMono>
                    <div className="flex items-center gap-1.5">
                      <FxMono style={{ fontSize: 9 }}>
                        {formatBytes(entry.content?.length ?? 0)}
                      </FxMono>
                      <FxMono style={{ fontSize: 9 }}>
                        {relativeTime(entry.at)}
                      </FxMono>
                    </div>
                  </div>
                  <p
                    className="overflow-hidden text-ellipsis whitespace-nowrap font-mono text-[11px] text-[var(--fx-muted)]"
                    title={entry.content}
                  >
                    {entry.content}
                  </p>
                </div>
              ))
            )}
          </FxPanel>
        </div>

        <div>
          <div className="mb-2.5 flex items-center justify-between">
            <FxKicker>Long-term Memory (Cortical Column)</FxKicker>
          </div>

          <div className="mb-2.5 flex flex-wrap gap-1.5">
            {CLUSTERS.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setQuery(c)}
                className="cursor-pointer rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2.5 py-0.5 text-[10px] font-medium text-[hsl(var(--foreground))] hover:bg-[hsl(var(--muted))]"
              >
                {c}
              </button>
            ))}
          </div>

          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search memory…"
            className="fx-input mb-2.5 h-8 w-full px-2.5 text-[12px]"
            aria-label="Search memory"
          />

          <FxPanel>
            {longTermEntries.length === 0 ? (
              <div className="px-3.5 py-6 text-center text-[12px] text-[var(--fx-muted)]">
                No entries match your search
              </div>
            ) : (
              longTermEntries.map((e, i) => (
                <div
                  key={e.id}
                  className="px-3.5 py-3"
                  style={{
                    borderBottom:
                      i < longTermEntries.length - 1
                        ? "1px solid var(--ui-border)"
                        : "none",
                  }}
                >
                  <div className="mb-1.5 flex items-center justify-between">
                    <FxTag label={e.cluster} color={FX_ACCENT.purple} />
                    <div className="flex items-center gap-2">
                      <span
                        className="font-mono text-[10px] font-semibold"
                        style={{ color: FX_ACCENT.success }}
                      >
                        {Math.round(e.score * 100)}%
                      </span>
                      <FxMono style={{ fontSize: 9 }}>{e.indexed}</FxMono>
                    </div>
                  </div>
                  <p className="text-[12px] leading-snug text-[hsl(var(--foreground))]">
                    {e.summary}
                  </p>
                </div>
              ))
            )}
          </FxPanel>
        </div>
      </div>
    </div>
  );
}
