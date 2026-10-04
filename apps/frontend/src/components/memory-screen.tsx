"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { getMemoryLayers, getMemorySession, type MemoryLayer, type MemorySessionResponse } from "@/lib/api";
import type { WorkflowRunSummary } from "@/types/locus";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function formatStat(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
  if (typeof value === "string" || typeof value === "boolean") return String(value);
  return "—";
}

function ErrorLine({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px]">
      <span>{message}</span>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

/**
 * What the agents remember: the memory layers as the backend reports them, and
 * the short-term memory of one run. Only real data; no placeholder figures.
 */
export function MemoryScreen({ initialRuns, initialError = null }: { initialRuns: WorkflowRunSummary[]; initialError?: string | null }) {
  const [layers, setLayers] = useState<MemoryLayer[] | null>(null);
  const [layersError, setLayersError] = useState<string | null>(null);
  const [layersToken, setLayersToken] = useState(0);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(initialRuns[0]?.id ?? null);
  const [session, setSession] = useState<MemorySessionResponse | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [sessionToken, setSessionToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getMemoryLayers()
      .then((next) => {
        if (!cancelled) {
          setLayers(next);
          setLayersError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLayersError(reason instanceof Error ? reason.message : "Could not load memory layers.");
      });
    return () => {
      cancelled = true;
    };
  }, [layersToken]);

  useEffect(() => {
    if (!selectedSessionId) return;
    let cancelled = false;
    getMemorySession(selectedSessionId)
      .then((next) => {
        if (!cancelled) {
          setSession(next);
          setSessionError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setSession(null);
          setSessionError(reason instanceof Error ? reason.message : "Could not load this run's memory.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedSessionId, sessionToken]);

  const entries = session && session.session_id === selectedSessionId ? session.entries : [];
  const sessionLoading = Boolean(selectedSessionId) && !sessionError && session?.session_id !== selectedSessionId;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <h1 className="text-xl font-semibold tracking-tight">Memory</h1>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Memory layers</CardTitle>
            <CardDescription>Where memory is kept, as the backend reports it.</CardDescription>
          </div>
        </CardHeader>
        {layersError ? (
          <ErrorLine message={`Could not load memory layers: ${layersError}`} onRetry={() => setLayersToken((value) => value + 1)} />
        ) : !layers ? (
          <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>
        ) : layers.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">No memory layers are configured.</p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2" aria-label="Memory layers">
            {layers.map((layer) => (
              <li key={layer.id} className="rounded-[10px] border border-border px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-medium">{layer.name}</span>
                  <Badge variant={!layer.enabled ? "secondary" : layer.healthy ? "success" : "warning"}>
                    <span aria-hidden="true">{!layer.enabled ? "○" : layer.healthy ? "●" : "◐"}</span>
                    {!layer.enabled ? "Off" : layer.healthy ? "Healthy" : "Degraded"}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {layer.backend} · {layer.scope} scope
                </p>
                {Object.keys(layer.stats ?? {}).length ? (
                  <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
                    {Object.entries(layer.stats).map(([key, value]) => (
                      <div key={key} className="contents">
                        <dt className="text-muted-foreground">{key.replace(/_/g, " ")}</dt>
                        <dd className="text-right tabular-nums">{formatStat(value)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Run memory</CardTitle>
            <CardDescription>The short-term context one run built up.</CardDescription>
          </div>
          {initialRuns.length > 0 ? (
            <div className="flex items-center gap-2">
              <Label htmlFor="memory-run">Run</Label>
              <select
                id="memory-run"
                className="fx-field h-8 max-w-64 px-2 text-[12px]"
                value={selectedSessionId ?? ""}
                onChange={(event) => {
                  setSelectedSessionId(event.target.value || null);
                  setSessionError(null);
                }}
              >
                {initialRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.title.slice(0, 60)}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
        </CardHeader>
        {initialError ? (
          <p role="alert" className="text-[13px] text-destructive">Could not load runs: {initialError}</p>
        ) : !selectedSessionId ? (
          <p className="text-[13px] text-muted-foreground">No runs yet. Memory appears here once a run starts.</p>
        ) : sessionError ? (
          <ErrorLine message={`Could not load this run's memory: ${sessionError}`} onRetry={() => setSessionToken((value) => value + 1)} />
        ) : sessionLoading ? (
          <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>
        ) : entries.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">This run has no memory entries.</p>
        ) : (
          <ul className="divide-y divide-border rounded-[10px] border border-border" aria-label="Run memory entries">
            {entries.map((entry) => (
              <li key={entry.id} className="px-3 py-2">
                <div className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-mono font-semibold">{entry.node_id || entry.id}</span>
                  <span className="tabular-nums text-muted-foreground">
                    {formatBytes(entry.content?.length ?? 0)} · {entry.at ? new Date(entry.at).toLocaleString() : "—"}
                  </span>
                </div>
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={entry.content}>
                  {entry.content}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
