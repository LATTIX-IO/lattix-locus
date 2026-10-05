"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { LoadState, SectionHeader, SettingsGroup } from "@/components/settings/settings-kit";
import {
  getKnowledgeVectorStores,
  getMemoryLayers,
  getPlatformHealthDetails,
  type KnowledgeVectorStore,
  type MemoryLayer,
} from "@/lib/api";
import { useIsDesktopShell } from "@/lib/desktop-shell";
import type { PlatformHealthDetails } from "@/types/locus";

/** How each memory layer is turned on. The backend reads this at startup; it
 * is not a setting the UI can change, so the text says what to set. */
const LAYER_SETUP: Record<string, string> = {
  short_term: "Short-term memory uses a process-local session cache. Redis is optional for shared or multi-worker deployments.",
  long_term:
    "Needs Postgres with the pgvector extension and an embedding model: set POSTGRES_DSN and LOCUS_MEMORY_ENABLE_LONG_TERM=true for the Locus backend, then restart Locus.",
  world_graph: "Uses PostgreSQL from LOCUS_WORLD_GRAPH_DSN or POSTGRES_DSN. Restart Locus after configuring the connection.",
  knowledge: "Runs on long-term memory: set that up first, then knowledge collections can index and search documents.",
};

export type MemorySnapshot = {
  layers: MemoryLayer[];
  vectorStores: KnowledgeVectorStore[];
  health: PlatformHealthDetails | null;
};

/** Whether long-term memory (and so knowledge indexing) is usable, and why not. */
export function longTermMemoryState(snapshot: Pick<MemorySnapshot, "layers" | "health">): { ready: boolean; reason: string } {
  const layer = snapshot.layers.find((item) => item.id === "long_term");
  const ready = Boolean(layer?.enabled && layer.healthy);
  if (ready) {
    return { ready, reason: "" };
  }
  const reason = snapshot.health?.long_term_memory_reason?.trim();
  if (!layer?.enabled) {
    return { ready, reason: reason || "Long-term memory is not turned on for this Locus backend." };
  }
  return { ready, reason: reason || "Long-term memory is turned on but its database does not answer." };
}

export function useMemorySnapshot(): { snapshot: MemorySnapshot | null; error: string | null; reload: () => void } {
  const [snapshot, setSnapshot] = useState<MemorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getMemoryLayers(), getKnowledgeVectorStores(), getPlatformHealthDetails()])
      .then(([layers, vectorStores, health]) => {
        if (cancelled) return;
        setSnapshot({ layers, vectorStores, health });
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load the memory configuration.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  return { snapshot, error, reload: () => setToken((value) => value + 1) };
}

function LayerBadge({ layer }: { layer: MemoryLayer }) {
  const variant = !layer.enabled ? "secondary" : layer.healthy ? "success" : "warning";
  return (
    <Badge variant={variant}>
      <span aria-hidden="true">{!layer.enabled ? "○" : layer.healthy ? "●" : "◐"}</span>
      {!layer.enabled ? "Off" : layer.healthy ? "On" : "Degraded"}
    </Badge>
  );
}

/**
 * Settings → Memory & knowledge: how memory and knowledge are configured, as
 * the backend reports it (layers on/off, embedding model, vector stores,
 * retention). The content (collections, documents) lives in Library →
 * Knowledge and a run's memory under Memory.
 */
export function MemorySection() {
  const isDesktop = useIsDesktopShell();
  const { snapshot, error, reload } = useMemorySnapshot();
  const setupText = (layerId: string) => {
    if (!isDesktop) return LAYER_SETUP[layerId] ?? "See the backend configuration.";
    if (layerId === "short_term") return "Short-term memory is on by default and uses a process-local session cache.";
    if (layerId === "long_term") return "Long-term memory is on by default and uses the local embedded store.";
    if (layerId === "world_graph") return "The local PostgreSQL graph turns on when its managed database is ready.";
    return "The desktop app manages this memory layer.";
  };

  const header = (
    <SectionHeader
      title="Memory & knowledge"
      description="Where the agents keep what they learn, and what backs your knowledge collections."
      actions={
        <Button asChild variant="secondary" size="sm">
          <Link href="/library/knowledge">Open knowledge</Link>
        </Button>
      }
    />
  );

  if (!snapshot) {
    return (
      <div className="flex flex-col gap-4">
        {header}
        <LoadState loading={!error} error={error} onRetry={reload} />
      </div>
    );
  }

  const longTerm = longTermMemoryState(snapshot);
  const longTermLayer = snapshot.layers.find((layer) => layer.id === "long_term");
  const embeddingModel = String(longTermLayer?.stats?.embedding_model ?? "") || snapshot.vectorStores.find((store) => store.kind === "builtin")?.embedding_model || "";
  const vectorSearch = longTermLayer?.stats?.vector_search === true;

  return (
    <div className="flex flex-col gap-4">
      {header}

      {!longTerm.ready ? (
        <div role="alert" className="flex flex-col gap-2 rounded-[12px] border border-warning/50 bg-warning/10 px-3 py-2.5 text-[13px]">
          <p className="font-medium">Long-term memory is unavailable</p>
          <p>{longTerm.reason} Agents still keep a run&apos;s short-term context, but nothing is remembered across runs and knowledge collections cannot index or search documents.</p>
          <p className="text-xs text-muted-foreground">Set up: {setupText("long_term")}</p>
          <div>
            <Button size="sm" variant="secondary" onClick={reload}>
              Check again
            </Button>
          </div>
        </div>
      ) : null}

      <SettingsGroup title="Memory layers" description="On or off as the backend runs them. The backend reads this at startup.">
        {snapshot.layers.length === 0 ? (
          <p className="text-[13px] text-muted-foreground">The backend reports no memory layers.</p>
        ) : (
          <ul className="grid gap-2 lg:grid-cols-2" aria-label="Memory layers">
            {snapshot.layers.map((layer) => (
              <li key={layer.id} className="rounded-[10px] border border-border px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[13px] font-medium">{layer.name}</span>
                  <LayerBadge layer={layer} />
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {layer.backend} · {layer.scope}
                </p>
                {!layer.enabled || !layer.healthy ? (
                  <p className="mt-1 text-xs">Set up: {setupText(layer.id)}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </SettingsGroup>

      <SettingsGroup title="Embeddings and vector store" description="What turns documents and memories into searchable vectors.">
        <dl className="grid gap-2 text-[13px] sm:grid-cols-2">
          <div className="rounded-[10px] border border-border px-3 py-2">
            <dt className="text-xs text-muted-foreground">Embedding model</dt>
            <dd className="font-medium">{embeddingModel || "None configured"}</dd>
          </div>
          <div className="rounded-[10px] border border-border px-3 py-2">
            <dt className="text-xs text-muted-foreground">Semantic (vector) search</dt>
            <dd className="font-medium">{vectorSearch ? "On" : "Off"}</dd>
          </div>
        </dl>
        <ul className="flex flex-col gap-2" aria-label="Vector stores">
          {snapshot.vectorStores.map((store) => (
            <li key={store.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-border px-3 py-2 text-[13px]">
              <span>
                <span className="font-medium">{store.name}</span>
                {store.note ? <span className="block text-xs text-muted-foreground">{store.note}</span> : null}
              </span>
              <Badge variant={store.ready ? "success" : "secondary"}>
                <span aria-hidden="true">{store.ready ? "●" : "○"}</span>
                {store.ready ? "Ready" : "Unavailable"}
              </Badge>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">
          External vector stores are added as connections of type vector in{" "}
          <Link href="/library/connections" className="underline underline-offset-2">
            Library → Connectors
          </Link>
          .
        </p>
      </SettingsGroup>

      <SettingsGroup title="Retention" description="How long memory is kept.">
        <p className="text-[13px] text-muted-foreground">
          Long-term memories decay by the backend&apos;s memory settings (LOCUS_MEMORY_DECAY_ENABLED, LOCUS_MEMORY_DECAY_HALF_LIFE_DAYS); they are not editable here yet. Captured trace content has its own retention in{" "}
          <Link href="/settings?section=observability" className="underline underline-offset-2">
            Observability
          </Link>
          . Which sources retrieval may read is set in{" "}
          <Link href="/settings?section=policies" className="underline underline-offset-2">
            Policies &amp; autonomy
          </Link>
          .
        </p>
      </SettingsGroup>
    </div>
  );
}
