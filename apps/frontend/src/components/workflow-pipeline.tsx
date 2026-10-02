"use client";

import { useMemo, useState } from "react";
import {
  FX_STATUS,
  FxKicker,
  FxMono,
  FxPanel,
  FxSectionHeader,
  FxStatusBadge,
  type FxStatus,
} from "@/components/fx-ui";
import type { WorkflowDefinition } from "@/types/frontier";

type Step = {
  id: string;
  label: string;
  status: FxStatus;
  agent: string;
  model: string;
  time: string;
};

const FALLBACK_STEPS: Step[] = [
  { id: "s1", label: "Ingest Request", status: "complete", agent: "Ingest Agent", model: "gpt-4o-mini", time: "0.8s" },
  { id: "s2", label: "Classify Assets", status: "complete", agent: "Classification Agent", model: "claude-sonnet", time: "2.4s" },
  { id: "s3", label: "Encrypt Payload", status: "complete", agent: "Encryption Agent", model: "local-pqe", time: "0.3s" },
  { id: "s4", label: "Provision Room", status: "running", agent: "Provision Agent", model: "gpt-4o", time: "—" },
  { id: "s5", label: "Verify Access", status: "pending", agent: "Verify Agent", model: "gpt-4o", time: "—" },
  { id: "s6", label: "Notify Owners", status: "pending", agent: "Notify Agent", model: "gpt-4o-mini", time: "—" },
  { id: "s7", label: "Audit Log", status: "pending", agent: "Audit Agent", model: "gpt-4o-mini", time: "—" },
];

export function WorkflowPipelineDetail({
  workflow,
  backHref = "/workflows/start",
}: {
  workflow: WorkflowDefinition;
  backHref?: string;
}) {
  const steps = FALLBACK_STEPS;
  const [selectedId, setSelectedId] = useState<string>("s4");
  const selected = useMemo(
    () => steps.find((s) => s.id === selectedId) ?? steps[0],
    [steps, selectedId],
  );
  const completed = steps.filter((s) => s.status === "complete").length;

  return (
    <div className="flex flex-col gap-4">
      <FxSectionHeader
        label={workflow.name}
        index="/02 — Workflow"
        sub={`${completed}/${steps.length} steps complete · v${workflow.version}`}
        action={
          <div className="flex items-center gap-2">
            <a
              href={backHref}
              className="fx-btn-secondary inline-flex items-center px-3 py-1.5 text-[12px] no-underline"
            >
              ← All Workflows
            </a>
            <FxStatusBadge status="running" />
          </div>
        }
      />

      <FxPanel padding={24}>
        <div className="flex items-start gap-0 overflow-x-auto pb-1">
          {steps.map((s, i) => {
            const spec = FX_STATUS[s.status];
            const active = selectedId === s.id;
            const isLast = i === steps.length - 1;
            return (
              <div key={s.id} className="flex flex-none items-start">
                <button
                  type="button"
                  onClick={() => setSelectedId(s.id)}
                  className="flex min-w-[110px] flex-col items-center gap-2 px-1 py-1 text-center"
                >
                  <div
                    className="flex h-10 w-10 items-center justify-center rounded-md border-2 transition-all"
                    style={{
                      borderColor: active ? "hsl(35 95% 52%)" : spec.border,
                      background: active ? "hsl(35 95% 52% / 0.12)" : spec.bg,
                      boxShadow: active ? "0 0 0 3px hsl(35 95% 52% / 0.2)" : "none",
                    }}
                  >
                    {s.status === "complete" ? (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 16 16"
                        fill="none"
                        stroke={spec.text}
                        strokeWidth="2.5"
                      >
                        <path d="M3 8l4 4 6-6" />
                      </svg>
                    ) : s.status === "running" ? (
                      <span
                        className="h-2.5 w-2.5 animate-pulse rounded-full"
                        style={{ background: spec.dot }}
                      />
                    ) : s.status === "failed" ? (
                      <span className="text-[14px]" style={{ color: spec.text }}>
                        ✕
                      </span>
                    ) : (
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ background: "var(--ui-border)" }}
                      />
                    )}
                  </div>
                  <p
                    className="text-[11px] leading-tight"
                    style={{
                      color:
                        s.status === "pending" && !active
                          ? "var(--fx-muted)"
                          : "hsl(var(--foreground))",
                      fontWeight: active ? 600 : 400,
                    }}
                  >
                    {s.label}
                  </p>
                  {s.time !== "—" ? (
                    <FxMono style={{ fontSize: 9 }}>{s.time}</FxMono>
                  ) : null}
                </button>
                {!isLast ? (
                  <div
                    className="mt-5 h-0.5 w-8 flex-shrink-0"
                    style={{
                      background: `linear-gradient(to right, ${spec.dot}, var(--ui-border))`,
                    }}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      </FxPanel>

      {selected ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <FxPanel padding={20}>
            <FxKicker>Step Detail</FxKicker>
            {(
              [
                ["Step", selected.label],
                ["Agent", selected.agent],
                ["Model", selected.model],
                ["Status", FX_STATUS[selected.status].label],
                ["Duration", selected.time],
              ] as const
            ).map(([k, v]) => (
              <div
                key={k}
                className="flex justify-between border-b border-[var(--ui-border)] py-2 last:border-b-0"
              >
                <span className="text-[11px] text-[var(--fx-muted)]">{k}</span>
                <span className="text-[12px] font-medium text-[hsl(var(--foreground))]">
                  {v}
                </span>
              </div>
            ))}
          </FxPanel>
          <FxPanel padding={20}>
            <FxKicker>Agent Output</FxKicker>
            <p className="mt-3 text-[12px] leading-relaxed text-[hsl(var(--foreground))]">
              {selected.status === "complete"
                ? "Step completed successfully. Output passed to next agent in the pipeline."
                : selected.status === "running"
                  ? "Agent is processing the payload. Waiting for tool results…"
                  : selected.status === "failed"
                    ? "Step failed. Inspect the agent trace and retry from the previous checkpoint."
                    : "Step not yet started. Waiting for upstream step to complete."}
            </p>
          </FxPanel>
        </div>
      ) : null}
    </div>
  );
}
