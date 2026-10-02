"use client";

import { useState } from "react";
import type { ChangedFile } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = { A: "added", M: "modified", D: "deleted", R: "renamed" };

function statusColor(status: string): string {
  const s = (status || "M")[0].toUpperCase();
  if (s === "A") return "var(--fx-success, #3fb950)";
  if (s === "D") return "var(--fx-danger, #f85149)";
  if (s === "R") return "var(--fx-warning, #d29922)";
  return "var(--foreground)";
}

function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) {
    return <p className="fx-muted p-3 text-xs">No textual diff (binary or no content change).</p>;
  }
  const lines = diff.split("\n");
  return (
    <pre className="overflow-auto p-2 font-mono text-[11px] leading-relaxed">
      {lines.map((line, i) => {
        let bg = "transparent";
        let fg = "var(--foreground)";
        if (line.startsWith("+") && !line.startsWith("+++")) {
          bg = "hsl(140 60% 40% / 0.15)";
          fg = "var(--fx-success, #3fb950)";
        } else if (line.startsWith("-") && !line.startsWith("---")) {
          bg = "hsl(0 70% 50% / 0.15)";
          fg = "var(--fx-danger, #f85149)";
        } else if (line.startsWith("@@")) {
          fg = "var(--fx-accent, #58a6ff)";
        } else if (line.startsWith("diff --git") || line.startsWith("index ") || line.startsWith("+++") || line.startsWith("---")) {
          fg = "var(--fx-muted)";
        }
        return (
          <div key={i} style={{ background: bg, color: fg, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

export function RunChangesPane({ changedFiles }: { changedFiles: ChangedFile[] }) {
  const [selected, setSelected] = useState(0);
  if (!changedFiles || changedFiles.length === 0) {
    return (
      <div className="p-4 text-xs">
        <p className="font-medium text-[var(--foreground)]">No file changes yet.</p>
        <p className="fx-muted mt-1">
          Files touched by the agents appear here once a run makes real changes (Execute mode with a bound working folder).
        </p>
      </div>
    );
  }
  const current = changedFiles[Math.min(selected, changedFiles.length - 1)];
  const totalAdds = changedFiles.reduce((n, f) => n + (f.additions || 0), 0);
  const totalDels = changedFiles.reduce((n, f) => n + (f.deletions || 0), 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-[var(--ui-border)] px-3 py-2 text-xs">
        <span className="font-semibold text-[var(--foreground)]">{changedFiles.length} file{changedFiles.length === 1 ? "" : "s"} changed</span>
        <span className="ml-2 text-[var(--fx-success,#3fb950)]">+{totalAdds}</span>
        <span className="ml-1 text-[var(--fx-danger,#f85149)]">-{totalDels}</span>
      </div>
      <div className="max-h-44 shrink-0 overflow-auto border-b border-[var(--ui-border)]">
        {changedFiles.map((f, i) => (
          <button
            key={f.path}
            type="button"
            onClick={() => setSelected(i)}
            title={f.path}
            className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-[11px] ${
              i === selected ? "bg-[hsl(var(--primary)/0.15)]" : "hover:bg-[var(--fx-nav-hover)]"
            }`}
          >
            <span className="font-mono text-[10px]" style={{ color: statusColor(f.status) }} title={STATUS_LABEL[f.status[0]?.toUpperCase()] || "modified"}>
              {(f.status || "M")[0].toUpperCase()}
            </span>
            <span className="flex-1 truncate font-mono text-[var(--foreground)]">{f.path}</span>
            <span className="text-[var(--fx-success,#3fb950)]">+{f.additions}</span>
            <span className="text-[var(--fx-danger,#f85149)]">-{f.deletions}</span>
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <DiffView diff={current?.diff || ""} />
      </div>
    </div>
  );
}
