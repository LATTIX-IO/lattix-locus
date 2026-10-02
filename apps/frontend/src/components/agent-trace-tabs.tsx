"use client";

import { useState } from "react";

type AgentTrace = {
  agent: string;
  reasoningSummary: string;
  actions: string[];
  output: string;
};

type Props = {
  traces: AgentTrace[];
};

export function AgentTraceTabs({ traces }: Props) {
  const [active, setActive] = useState(traces[0]?.agent ?? "");

  if (traces.length === 0) {
    return (
      <div className="fx-panel rounded-[1.35rem] p-4 shadow-[0_16px_40px_rgba(15,23,42,0.05)]">
        <p className="fx-muted text-sm">No agent trace entries yet.</p>
      </div>
    );
  }

  const selected = traces.find((t) => t.agent === active) ?? traces[0];

  if (!selected) return null;

  return (
    <div className="fx-panel rounded-[1.45rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Trace review</p>
          <h3 className="mt-2 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Agent traces</h3>
        </div>
        <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">{traces.length} agent{traces.length === 1 ? "" : "s"}</div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.84)] p-1.5">
        {traces.map((trace) => (
          <button
            key={trace.agent}
            onClick={() => setActive(trace.agent)}
            className={`rounded-full px-3 py-1.5 text-xs font-medium ${
              trace.agent === selected.agent
                ? "fx-nav-active"
                : "border border-transparent text-[var(--foreground)] hover:border-[var(--fx-border)] hover:bg-[var(--fx-nav-hover)]"
            }`}
          >
            {trace.agent}
          </button>
        ))}
      </div>

      <div className="space-y-3 text-sm">
        <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
          <p className="fx-muted mb-1 text-[0.72rem] font-medium">Reasoning summary</p>
          <p className="text-[var(--foreground)]">{selected.reasoningSummary}</p>
        </div>

        <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
          <p className="fx-muted mb-1 text-[0.72rem] font-medium">Actions</p>
          <ul className="list-disc space-y-1 pl-5 text-[var(--foreground)]">
            {selected.actions.map((a, i) => (
              <li key={`${selected.agent}-${i}`}>{a}</li>
            ))}
          </ul>
        </div>

        <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-4">
          <p className="fx-muted mb-1 text-[0.72rem] font-medium">Output</p>
          <p className="text-[var(--foreground)]">{selected.output}</p>
        </div>
      </div>
    </div>
  );
}
