"use client";

import { useEffect, useMemo, useState } from "react";
import { getObservabilityDashboard, getObservabilityRunTrace } from "@/lib/api";
import type { ObservabilityRunTrace } from "@/types/frontier";

type DashboardSummary = {
  total_runs: number;
  failed_or_blocked_runs: number;
  token_estimate: number;
  cost_estimate_usd: number;
  average_latency_ms: number;
  skills_enabled?: number;
  skill_injections_total?: number;
};

type SkillUsage = {
  id: string;
  name: string;
  status: string;
  source: string;
  auto_inject: boolean;
  version: number;
  usage_count: number;
  last_used_at: string;
};

type Severity = "critical" | "high" | "medium" | "low";

function statusSeverity(status: string): Severity {
  const value = status.toLowerCase();
  if (value === "failed" || value === "blocked") {
    return "critical";
  }
  if (value === "needs review") {
    return "high";
  }
  if (value === "running") {
    return "medium";
  }
  return "low";
}

function severityStyles(severity: Severity): { label: string; className: string } {
  if (severity === "critical") {
    return {
      label: "Critical",
      className: "border-[hsl(var(--state-critical)/0.45)] bg-[hsl(var(--state-critical)/0.14)] text-[hsl(var(--state-critical))]",
    };
  }
  if (severity === "high") {
    return {
      label: "High",
      className: "border-[hsl(var(--state-warning)/0.45)] bg-[hsl(var(--state-warning)/0.16)] text-[hsl(var(--state-warning))]",
    };
  }
  if (severity === "medium") {
    return {
      label: "Medium",
      className: "border-[hsl(var(--state-info)/0.42)] bg-[hsl(var(--state-info)/0.15)] text-[hsl(var(--state-info))]",
    };
  }
  return {
    label: "Low",
    className: "border-[hsl(var(--state-success)/0.42)] bg-[hsl(var(--state-success)/0.14)] text-[hsl(var(--state-success))]",
  };
}

function runRiskScore(run: ObservabilityRunTrace): number {
  const severity = statusSeverity(run.status);
  const base = severity === "critical" ? 75 : severity === "high" ? 55 : severity === "medium" ? 35 : 12;
  const latencyRisk = Math.min(20, Math.floor((run.duration_ms ?? 0) / 750));
  const graphComplexity = Math.min(10, Math.floor((run.node_count + run.edge_count) / 8));
  const eventBurst = Math.min(8, Math.floor(run.event_count / 10));
  return Math.min(100, base + latencyRisk + graphComplexity + eventBurst);
}

function formatMs(value: number | undefined): string {
  if (!value || value <= 0) {
    return "—";
  }
  return `${value} ms`;
}

function formatUsd(value: number | undefined): string {
  return `$${Number(value ?? 0).toFixed(4)}`;
}

export default function ObservabilityPage() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [runs, setRuns] = useState<ObservabilityRunTrace[]>([]);
  const [skillUsage, setSkillUsage] = useState<SkillUsage[]>([]);
  const [selectedRun, setSelectedRun] = useState<ObservabilityRunTrace | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "failed" | "blocked" | "needs review" | "running" | "done">("all");
  const [severityFilter, setSeverityFilter] = useState<"all" | Severity>("all");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const dashboard = await getObservabilityDashboard();
        if (cancelled) {
          return;
        }
        setSummary(dashboard.summary);
        setRuns(dashboard.runs);
        setSkillUsage(
          Array.isArray((dashboard as { skills?: SkillUsage[] }).skills)
            ? ((dashboard as { skills?: SkillUsage[] }).skills as SkillUsage[])
            : [],
        );
      } catch {
        if (!cancelled) {
          setError("Unable to load observability dashboard.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedRun && runs.length > 0) {
      setSelectedRun(runs[0]);
    }
  }, [runs, selectedRun]);

  async function selectRun(runId: string) {
    try {
      const trace = await getObservabilityRunTrace(runId);
      if (trace) {
        setSelectedRun(trace);
        return;
      }
      const fallback = runs.find((item) => item.run_id === runId) ?? null;
      setSelectedRun(fallback);
    } catch {
      const fallback = runs.find((item) => item.run_id === runId) ?? null;
      setSelectedRun(fallback);
    }
  }

  const normalizedQuery = query.trim().toLowerCase();

  const filteredRuns = useMemo(() => {
    return runs.filter((run) => {
      const status = run.status.toLowerCase();
      const severity = statusSeverity(run.status);
      if (statusFilter !== "all" && status !== statusFilter) {
        return false;
      }
      if (severityFilter !== "all" && severity !== severityFilter) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      return run.run_id.toLowerCase().includes(normalizedQuery) || status.includes(normalizedQuery);
    });
  }, [runs, normalizedQuery, statusFilter, severityFilter]);

  const sortedAuditQueue = useMemo(() => {
    return [...filteredRuns].sort((a, b) => runRiskScore(b) - runRiskScore(a));
  }, [filteredRuns]);

  const criticalFindings = useMemo(() => {
    return runs.filter((run) => statusSeverity(run.status) === "critical").length;
  }, [runs]);

  const highFindings = useMemo(() => {
    return runs.filter((run) => statusSeverity(run.status) === "high").length;
  }, [runs]);

  const avgRiskScore = useMemo(() => {
    if (runs.length === 0) {
      return 0;
    }
    const total = runs.reduce((sum, run) => sum + runRiskScore(run), 0);
    return Math.round(total / runs.length);
  }, [runs]);

  const stageHotspots = useMemo(() => {
    const stageTotals = new Map<string, { total: number; count: number; max: number }>();
    for (const run of runs) {
      const stages = run.latency_by_stage_ms ?? {};
      for (const [stage, latency] of Object.entries(stages)) {
        const current = stageTotals.get(stage) ?? { total: 0, count: 0, max: 0 };
        current.total += latency;
        current.count += 1;
        current.max = Math.max(current.max, latency);
        stageTotals.set(stage, current);
      }
    }
    return [...stageTotals.entries()]
      .map(([stage, values]) => ({
        stage,
        avg: Math.round(values.total / Math.max(1, values.count)),
        max: values.max,
      }))
      .sort((a, b) => b.avg - a.avg)
      .slice(0, 6);
  }, [runs]);

  const selection = selectedRun ?? sortedAuditQueue[0] ?? null;
  const selectionSeverity = selection ? statusSeverity(selection.status) : "low";
  const selectionRisk = selection ? runRiskScore(selection) : 0;

  return (
    <section className="space-y-4">
      <header className="fx-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold">AI Audit & Observability Console</h1>
            <p className="fx-muted text-sm">
              Prioritize risky runs first, inspect model/agent behavior, and identify latency or cost hotspots before they become incidents.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-1">
              Findings queue: {sortedAuditQueue.length}
            </span>
            <span className="rounded-full border border-[hsl(var(--state-critical)/0.45)] bg-[hsl(var(--state-critical)/0.14)] px-2 py-1 text-[hsl(var(--state-critical))]">
              Critical: {criticalFindings}
            </span>
            <span className="rounded-full border border-[hsl(var(--state-warning)/0.45)] bg-[hsl(var(--state-warning)/0.16)] px-2 py-1 text-[hsl(var(--state-warning))]">
              High: {highFindings}
            </span>
          </div>
        </div>
      </header>

      {loading && <div className="fx-panel p-3 text-sm fx-muted">Loading dashboard...</div>}
      {error && <div className="border border-[#6b1f2a] bg-[#2f1a21] p-3 text-sm text-[#ffb8c4]">{error}</div>}

      {summary && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Runs observed</p>
            <div className="mt-1 text-lg font-semibold">{summary.total_runs}</div>
          </div>
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Failed / Blocked</p>
            <div className="mt-1 text-lg font-semibold">{summary.failed_or_blocked_runs}</div>
          </div>
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Avg risk score</p>
            <div className="mt-1 text-lg font-semibold">{avgRiskScore}/100</div>
          </div>
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Token estimate</p>
            <div className="mt-1 text-lg font-semibold">{summary.token_estimate}</div>
          </div>
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Cost estimate</p>
            <div className="mt-1 text-lg font-semibold">{formatUsd(summary.cost_estimate_usd)}</div>
          </div>
          <div className="fx-panel p-3">
            <p className="fx-muted text-xs uppercase tracking-wide">Average latency</p>
            <div className="mt-1 text-lg font-semibold">{summary.average_latency_ms} ms</div>
          </div>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[1.25fr_1fr]">
        <article className="fx-panel p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search by run id or status…"
              className="fx-field min-w-56 flex-1 px-2 text-xs"
            />
            <select
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}
              className="fx-field px-2 text-xs"
            >
              <option value="all">All statuses</option>
              <option value="failed">Failed</option>
              <option value="blocked">Blocked</option>
              <option value="needs review">Needs review</option>
              <option value="running">Running</option>
              <option value="done">Done</option>
            </select>
            <select
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value as typeof severityFilter)}
              className="fx-field px-2 text-xs"
            >
              <option value="all">All severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>

          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Audit findings queue</h2>
            <span className="fx-muted text-xs">Sorted by risk score</span>
          </div>

          <ul className="space-y-1.5 text-xs">
            {sortedAuditQueue.map((run) => {
              const severity = statusSeverity(run.status);
              const severityBadge = severityStyles(severity);
              const riskScore = runRiskScore(run);
              const selected = selection?.run_id === run.run_id;

              return (
                <li key={run.run_id}>
                  <button
                    className={`w-full border p-2 text-left ${selected ? "border-[hsl(var(--accent)/0.5)] bg-[hsl(var(--accent)/0.1)]" : "border-[var(--fx-border)] bg-[var(--fx-surface-elevated)]"}`}
                    onClick={() => void selectRun(run.run_id)}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-mono text-[11px] text-[var(--foreground)]">{run.run_id}</p>
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityBadge.className}`}>
                        {severityBadge.label}
                      </span>
                    </div>
                    <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 md:grid-cols-4">
                      <span className="fx-muted">status: {run.status}</span>
                      <span className="fx-muted">risk: {riskScore}/100</span>
                      <span className="fx-muted">latency: {formatMs(run.duration_ms)}</span>
                      <span className="fx-muted">events: {run.event_count}</span>
                    </div>
                  </button>
                </li>
              );
            })}
            {sortedAuditQueue.length === 0 && <li className="fx-muted">No findings for the current filters.</li>}
          </ul>
        </article>

        <article className="fx-panel p-4">
          <h2 className="mb-2 text-sm font-semibold">Trace inspector</h2>
          {!selection ? (
            <div className="fx-muted text-sm">Select a run to inspect audit details.</div>
          ) : (
            <div className="space-y-3 text-xs">
              <div className="rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.45)] p-2">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-[var(--foreground)]">{selection.run_id}</span>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${severityStyles(selectionSeverity).className}`}>
                    {severityStyles(selectionSeverity).label}
                  </span>
                </div>
                <p className="fx-muted">Audit risk score: {selectionRisk}/100</p>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Status</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{selection.status}</p>
                </div>
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Duration</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{formatMs(selection.duration_ms)}</p>
                </div>
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Token estimate</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{selection.token_estimate ?? 0}</p>
                </div>
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Cost estimate</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{formatUsd(selection.cost_estimate_usd)}</p>
                </div>
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Graph complexity</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{selection.node_count} nodes / {selection.edge_count} edges</p>
                </div>
                <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                  <p className="fx-muted">Event count</p>
                  <p className="mt-0.5 font-semibold text-[var(--foreground)]">{selection.event_count}</p>
                </div>
              </div>

              <div>
                <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide fx-muted">Latency by stage</h3>
                {selection.latency_by_stage_ms && Object.keys(selection.latency_by_stage_ms).length > 0 ? (
                  <ul className="space-y-1">
                    {Object.entries(selection.latency_by_stage_ms)
                      .sort((a, b) => b[1] - a[1])
                      .map(([stage, ms]) => (
                        <li key={stage} className="flex items-center justify-between border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-2 py-1">
                          <span className="text-[var(--foreground)]">{stage}</span>
                          <span className="font-medium text-[var(--foreground)]">{ms} ms</span>
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p className="fx-muted">No per-stage latency data for this trace.</p>
                )}
              </div>
            </div>
          )}
        </article>
      </div>

      <article className="fx-panel p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Skill usage</h2>
          <span className="fx-muted text-xs">
            {summary?.skills_enabled ?? 0} enabled · {summary?.skill_injections_total ?? 0} total injections
          </span>
        </div>
        {skillUsage.length === 0 ? (
          <p className="fx-muted text-xs">No skill metrics yet — metrics populate as runs execute.</p>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {skillUsage.map((skill) => (
              <div key={skill.id} className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <p className="font-mono font-medium text-[var(--foreground)]">{skill.name}</p>
                  <span className={skill.status === "enabled" ? "text-[hsl(var(--state-success))]" : "fx-muted"}>
                    {skill.status}
                  </span>
                </div>
                <p className="fx-muted mt-1">
                  v{skill.version} · {skill.source} · injected {skill.usage_count}×
                </p>
                <p className="fx-muted">last used: {skill.last_used_at || "never"}</p>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="fx-panel p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Stage hotspot analysis</h2>
          <span className="fx-muted text-xs">Top average latency contributors</span>
        </div>
        {stageHotspots.length === 0 ? (
          <p className="fx-muted text-xs">No stage latency data available yet.</p>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {stageHotspots.map((item) => (
              <div key={item.stage} className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2 text-xs">
                <p className="font-medium text-[var(--foreground)]">{item.stage}</p>
                <p className="fx-muted mt-1">avg {item.avg} ms</p>
                <p className="fx-muted">max {item.max} ms</p>
              </div>
            ))}
          </div>
        )}
      </article>

      <article className="fx-panel p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Audit checklist guidance</h2>
          <span className="fx-muted text-xs">Use this when triaging runs</span>
        </div>
        <ul className="list-disc space-y-1 pl-5 text-xs text-[var(--foreground)]">
          <li>Prioritize runs with <strong>critical/high severity</strong> and risk score above 70.</li>
          <li>Investigate stage hotspots where average latency exceeds platform baseline.</li>
          <li>Review high-event runs for runaway loops, retries, or duplicated orchestration paths.</li>
          <li>Use cost and token deltas to identify expensive agents/tools needing prompt or topology tuning.</li>
        </ul>
      </article>
    </section>
  );
}
