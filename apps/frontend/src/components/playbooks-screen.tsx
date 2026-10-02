"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  FX_ACCENT,
  FX_STATUS,
  FxKicker,
  FxMono,
  FxPanel,
  FxSectionHeader,
  FxStatusBadge,
  type FxStatus,
} from "@/components/fx-ui";
import { getPlaybooks } from "@/lib/api";
import type { PlaybookDefinition } from "@/types/frontier";

type DerivedPlaybook = {
  id: string;
  name: string;
  description: string;
  category: string;
  status: FxStatus;
  phases: number;
  workflows: number;
  totalAgents: number;
  activeAgents: number;
  currentPhase: number;
  progress: number;
  est: string;
};

const FALLBACK_PHASES = [
  { id: "ph1", name: "Discovery", status: "complete" as FxStatus, workflows: 3, agents: 12, duration: "2h 14m", progress: 100 },
  { id: "ph2", name: "Analysis", status: "running" as FxStatus, workflows: 5, agents: 24, duration: "—", progress: 62 },
  { id: "ph3", name: "Remediation", status: "pending" as FxStatus, workflows: 4, agents: 8, duration: "—", progress: 0 },
  { id: "ph4", name: "Validation", status: "pending" as FxStatus, workflows: 2, agents: 3, duration: "—", progress: 0 },
  { id: "ph5", name: "Reporting", status: "pending" as FxStatus, workflows: 1, agents: 1, duration: "—", progress: 0 },
];

const FALLBACK_FEED = [
  { t: "14:23:41", agent: "Agent-14", msg: "Completed PII scan — email marketing archive (2,847 records flagged)" },
  { t: "14:23:38", agent: "Agent-7", msg: "Completed PII scan — marketing database" },
  { t: "14:23:35", agent: "Agent-22", msg: "Started cross-reference with GDPR article 17 — user deletion requests" },
  { t: "14:23:31", agent: "Agent-12", msg: "Started cross-reference with GDPR article 17 — analytics events" },
  { t: "14:23:28", agent: "Agent-3", msg: "Flagged: potential violation in analytics events table" },
];

function derivePlaybook(p: PlaybookDefinition, idx: number): DerivedPlaybook {
  const seed = (p.id?.length ?? 0) + idx;
  const phases = 4 + (seed % 3);
  const workflows = 6 + (seed % 8);
  const totalAgents = 18 + (seed % 32);
  const status: FxStatus = p.status === "archived" ? "paused" : idx === 0 ? "running" : "active";
  const isLive = status === "running";
  return {
    id: p.id,
    name: p.name,
    description: p.description ?? "",
    category: p.category,
    status,
    phases,
    workflows,
    totalAgents,
    activeAgents: isLive ? Math.max(2, Math.floor(totalAgents / 2)) : 0,
    currentPhase: isLive ? 2 : status === "active" ? 1 : 0,
    progress: isLive ? 38 : status === "active" ? 12 : 0,
    est: isLive ? "23m remaining" : "—",
  };
}

export function PlaybooksScreen({ initialPlaybooks }: { initialPlaybooks: PlaybookDefinition[] }) {
  const [playbooks, setPlaybooks] = useState<PlaybookDefinition[]>(initialPlaybooks);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [feed, setFeed] = useState(FALLBACK_FEED);

  useEffect(() => {
    let cancelled = false;
    void getPlaybooks()
      .then((p) => {
        if (cancelled) return;
        setPlaybooks(p);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const derived = useMemo(
    () => playbooks.map((p, i) => derivePlaybook(p, i)),
    [playbooks],
  );

  const active = useMemo(
    () => derived.find((p) => p.id === activeId) ?? null,
    [derived, activeId],
  );

  const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!active || active.status !== "running") return;
    const tid = setInterval(() => {
      setFeed((prev) => {
        const messages = [
          "Completed analysis step",
          "Flagged potential violation — routing to queue",
          "Started cross-reference task",
          "Memory checkpoint written",
          "Policy evaluation complete — no violations",
        ];
        const next = {
          t: new Date().toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: false,
          }),
          agent: `Agent-${Math.floor(Math.random() * 24) + 1}`,
          msg: messages[Math.floor(Math.random() * messages.length)],
        };
        return [next, ...prev.slice(0, 12)];
      });
    }, 2800);
    return () => clearInterval(tid);
  }, [active]);

  if (active) {
    const isLive = active.status === "running";
    return (
      <div className="flex flex-col gap-4">
        <div
          className="rounded-lg p-5 text-white"
          style={{ background: "hsl(220 14% 12%)" }}
        >
          <div className="mb-3 flex items-start justify-between">
            <div>
              <p
                className="font-mono text-[10px] font-bold uppercase tracking-[0.14em]"
                style={{ color: "hsl(220 9% 60%)" }}
              >
                /03 — Playbook War Room
              </p>
              <h1 className="mt-1 text-[20px] font-bold">{active.name}</h1>
              {active.description ? (
                <p className="mt-1 text-[12px]" style={{ color: "hsl(220 9% 70%)" }}>
                  {active.description}
                </p>
              ) : null}
            </div>
            <button
              type="button"
              onClick={() => setActiveId(null)}
              className="inline-flex items-center rounded-md border px-3 py-1.5 text-[11px] font-medium"
              style={{
                borderColor: "hsl(220 9% 40%)",
                color: "hsl(220 9% 80%)",
                background: "transparent",
              }}
            >
              ← All Playbooks
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              { label: "Total Agents", value: active.totalAgents },
              {
                label: "Active Now",
                value: active.activeAgents,
                accent: FX_ACCENT.success,
              },
              {
                label: "Current Phase",
                value: `${active.currentPhase}/${active.phases}`,
              },
              { label: "Est. Remaining", value: active.est },
            ].map((s) => (
              <div
                key={s.label}
                className="rounded-md p-3"
                style={{ background: "hsl(220 14% 18%)" }}
              >
                <p
                  className="font-mono text-[9px] font-bold uppercase tracking-[0.12em]"
                  style={{ color: "hsl(220 9% 56%)" }}
                >
                  {s.label}
                </p>
                <p
                  className="mt-1.5 text-[22px] font-bold leading-none"
                  style={{ color: s.accent ?? "white" }}
                >
                  {s.value}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex flex-col gap-2">
            <FxKicker>Execution Phases</FxKicker>
            {FALLBACK_PHASES.map((ph) => {
              const spec = FX_STATUS[ph.status];
              return (
                <FxPanel key={ph.id} padding={16}>
                  <div className="grid items-center gap-3 lg:grid-cols-[minmax(0,1fr)_auto]">
                    <div>
                      <div className="mb-2 flex items-center gap-2.5">
                        <span
                          className="h-2 w-2 flex-shrink-0 rounded-full"
                          style={{ background: spec.dot }}
                        />
                        <span className="text-[13px] font-semibold text-[hsl(var(--foreground))]">
                          {ph.name}
                        </span>
                        <FxStatusBadge status={ph.status} />
                      </div>
                      <div className="h-1.5 rounded-full bg-[hsl(var(--muted))]">
                        <div
                          className="h-full rounded-full transition-[width] duration-500"
                          style={{
                            width: `${ph.progress}%`,
                            background: spec.dot,
                          }}
                        />
                      </div>
                      <div className="mt-2 flex gap-4">
                        <span className="text-[10px] text-[var(--fx-muted)]">
                          {ph.workflows} workflows
                        </span>
                        <span className="text-[10px] text-[var(--fx-muted)]">
                          {ph.agents} agents
                        </span>
                        {ph.duration !== "—" ? (
                          <FxMono style={{ fontSize: 10 }}>{ph.duration}</FxMono>
                        ) : null}
                      </div>
                    </div>
                    <FxMono
                      style={{
                        fontSize: 18,
                        fontWeight: 700,
                        color: "hsl(var(--foreground))",
                      }}
                    >
                      {ph.progress}%
                    </FxMono>
                  </div>
                </FxPanel>
              );
            })}
          </div>

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <FxKicker>Live Agent Feed</FxKicker>
              {isLive ? (
                <span
                  className="flex items-center gap-1.5 text-[10px]"
                  style={{ color: FX_ACCENT.success }}
                >
                  <span
                    className="h-1.5 w-1.5 animate-pulse rounded-full"
                    style={{ background: FX_ACCENT.success }}
                  />
                  Live
                </span>
              ) : null}
            </div>
            <FxPanel className="flex-1">
              <div ref={feedRef} className="max-h-[420px] overflow-y-auto py-2">
                {feed.map((f, i) => (
                  <div
                    key={i}
                    className="px-3.5 py-2"
                    style={{
                      borderBottom: "1px solid var(--ui-border)",
                      opacity: i === 0 ? 1 : Math.max(0.4, 1 - i * 0.07),
                    }}
                  >
                    <div className="mb-0.5 flex items-center gap-2">
                      <FxMono style={{ fontSize: 9 }}>{f.t}</FxMono>
                      <FxMono
                        style={{ fontSize: 9, fontWeight: 700, color: FX_ACCENT.info }}
                      >
                        {f.agent}
                      </FxMono>
                    </div>
                    <p className="text-[11px] leading-snug text-[hsl(var(--foreground))]">
                      {f.msg}
                    </p>
                  </div>
                ))}
              </div>
            </FxPanel>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <FxSectionHeader
        label="Playbooks"
        index="/03 — Work"
        sub="Collections of workflows — phased multi-agent operations"
      />
      {derived.length === 0 ? (
        <FxPanel padding={24}>
          <p className="text-center text-[13px] text-[var(--fx-muted)]">
            No playbooks available yet. Build one in the Builder.
          </p>
        </FxPanel>
      ) : (
        <div className="flex flex-col gap-3">
          {derived.map((pb) => (
            <FxPanel
              key={pb.id}
              padding={20}
              className="cursor-pointer transition-shadow hover:shadow-md"
              style={{}}
            >
              <button
                type="button"
                onClick={() => setActiveId(pb.id)}
                className="block w-full text-left"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-[14px] font-semibold text-[hsl(var(--foreground))]">
                      {pb.name}
                    </p>
                    {pb.description ? (
                      <p className="mt-1 text-[11px] text-[var(--fx-muted)]">
                        {pb.description}
                      </p>
                    ) : null}
                    <div className="mt-2 flex flex-wrap gap-3">
                      <span className="text-[11px] text-[var(--fx-muted)]">
                        {pb.phases} phases
                      </span>
                      <span className="text-[11px] text-[var(--fx-muted)]">
                        {pb.workflows} workflows
                      </span>
                      <span className="text-[11px] text-[var(--fx-muted)]">
                        {pb.totalAgents} agents
                      </span>
                      <span className="text-[11px] uppercase tracking-[0.06em] text-[var(--fx-muted)]">
                        {pb.category.replace(/_/g, " ")}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-2">
                    <FxStatusBadge status={pb.status} />
                  </div>
                </div>
                <div className="flex items-center gap-2.5">
                  <div className="h-1.5 flex-1 rounded-full bg-[hsl(var(--muted))]">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${pb.progress}%`,
                        background:
                          pb.status === "complete"
                            ? FX_ACCENT.success
                            : FX_ACCENT.primary,
                      }}
                    />
                  </div>
                  <FxMono
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "hsl(var(--foreground))",
                    }}
                  >
                    {pb.progress}%
                  </FxMono>
                </div>
                {pb.status === "running" || pb.status === "active" ? (
                  <p className="mt-2 text-[11px] text-[var(--fx-muted)]">
                    Phase {pb.currentPhase}/{pb.phases} · {pb.est}
                  </p>
                ) : null}
              </button>
            </FxPanel>
          ))}
        </div>
      )}
    </div>
  );
}
