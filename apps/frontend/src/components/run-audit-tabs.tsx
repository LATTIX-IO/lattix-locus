"use client";

import { useMemo, useState } from "react";
import type { WorkflowRunEvent } from "@/types/frontier";

type AgentTrace = {
  agent: string;
  reasoningSummary: string;
  actions: string[];
  output: string;
};

type Props = {
  events: WorkflowRunEvent[];
  traces: AgentTrace[];
};

type Tab = { id: string; label: string; kind: "overview" | "agent" };

export function RunAuditTabs({ events, traces }: Props) {
  const tabs = useMemo<Tab[]>(() => {
    const agentTabs = traces.map((trace) => ({ id: trace.agent, label: trace.agent, kind: "agent" as const }));
    return [{ id: "overview", label: "Overview", kind: "overview" as const }, ...agentTabs];
  }, [traces]);

  const [activeTabId, setActiveTabId] = useState(tabs[0]?.id ?? "overview");

  const activeTab = tabs.find((tab) => tab.id === activeTabId) ?? tabs[0];
  const selectedTrace = traces.find((trace) => trace.agent === activeTab?.id);

  return (
    <div className="fx-panel rounded-[1.45rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Audit workspace</p>
          <h3 className="mt-2 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Run audit</h3>
        </div>
        <div className="flex items-center gap-2 text-[0.72rem] text-[var(--fx-muted)]">
          <span className="fx-pill px-3 py-1.5 font-medium">{events.length} events</span>
          <span className="fx-pill px-3 py-1.5 font-medium">{traces.length} traces</span>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.84)] p-1.5">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTabId(tab.id)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium ${
              tab.id === activeTab?.id
                ? "fx-nav-active"
                : "border border-transparent text-[var(--foreground)] hover:border-[var(--fx-border)] hover:bg-[var(--fx-nav-hover)]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab?.kind === "overview" ? (
        <div className="space-y-3 text-sm">
          <p className="fx-muted text-[0.72rem] font-medium">Conversation & Audit Log</p>
          <div className="max-h-[280px] space-y-2 overflow-auto pr-1">
            {events.map((event) => (
              <article key={event.id} className="rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.36)]">
                <div className="mb-1 flex items-center justify-between">
                  <h4 className="text-xs font-semibold text-[var(--foreground)]">{event.title}</h4>
                  <span className="fx-muted text-[11px]">{event.createdAt}</span>
                </div>
                <p className="text-xs text-[var(--foreground)]">{event.summary}</p>
                <p className="fx-muted mt-2 text-[0.72rem] font-medium">{event.type}</p>
              </article>
            ))}
          </div>
        </div>
      ) : selectedTrace ? (
        <div className="space-y-3 text-sm">
          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
            <p className="fx-muted mb-1 text-[0.72rem] font-medium">Reasoning summary</p>
            <p className="text-[var(--foreground)]">{selectedTrace.reasoningSummary}</p>
          </div>

          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
            <p className="fx-muted mb-1 text-[0.72rem] font-medium">Actions</p>
            <ul className="list-disc space-y-1 pl-5 text-[var(--foreground)]">
              {selectedTrace.actions.map((action, index) => (
                <li key={`${selectedTrace.agent}-${index}`}>{action}</li>
              ))}
            </ul>
          </div>

          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
            <p className="fx-muted mb-1 text-[0.72rem] font-medium">Output</p>
            <p className="text-[var(--foreground)]">{selectedTrace.output}</p>
          </div>
        </div>
      ) : (
        <p className="fx-muted text-sm">No trace details found for this tab.</p>
      )}
    </div>
  );
}
