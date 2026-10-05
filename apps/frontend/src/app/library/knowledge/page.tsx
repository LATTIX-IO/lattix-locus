"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  addKnowledgeDocument,
  createKnowledgeCollection,
  deleteKnowledgeCollection,
  getKnowledgeCollections,
  getKnowledgeVectorStores,
  getMemoryLayers,
  getPlatformHealthDetails,
  searchKnowledgeCollection,
  type KnowledgeCollection,
  type KnowledgeSearchResult,
  type KnowledgeVectorStore,
  type MemoryLayer,
} from "@/lib/api";
import { longTermMemoryState } from "@/components/settings/memory-section";
import type { PlatformHealthDetails } from "@/types/locus";

const MEMORY_SETTINGS_HREF = "/settings?section=memory";

export default function KnowledgePage() {
  const [collections, setCollections] = useState<KnowledgeCollection[]>([]);
  const [layers, setLayers] = useState<MemoryLayer[]>([]);
  const [health, setHealth] = useState<PlatformHealthDetails | null>(null);
  const [vectorStores, setVectorStores] = useState<KnowledgeVectorStore[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newVectorStore, setNewVectorStore] = useState("platform");
  const [docName, setDocName] = useState("");
  const [docText, setDocText] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchResult[]>([]);

  const refresh = useCallback(async () => {
    try {
      // No silent fallbacks: a failed read is an error with Retry, never an
      // empty list that reads as "nothing configured".
      const [cols, lyrs, stores, details] = await Promise.all([
        getKnowledgeCollections(),
        getMemoryLayers(),
        getKnowledgeVectorStores(),
        getPlatformHealthDetails(),
      ]);
      setCollections(cols);
      setLayers(lyrs);
      setVectorStores(stores);
      setHealth(details);
      setSelected((current) => current ?? cols[0]?.id ?? null);
      setError(null);
      setLoaded(true);
    } catch (err) {
      setError(err instanceof Error ? `Unable to load knowledge: ${err.message}` : "Unable to load knowledge.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const active = collections.find((c) => c.id === selected) ?? null;
  const storeFor = (id: string) => vectorStores.find((s) => s.id === id) ?? null;
  const longTerm = longTermMemoryState({ layers, health });
  // Indexing and search need the collection's store to be ready (the platform
  // store runs on long-term memory); the backend refuses them otherwise.
  const activeStore = active ? storeFor(active.vector_store_id) : null;
  const activeReady = Boolean(activeStore?.ready);

  async function addCollection() {
    if (!newName.trim()) {
      setNotice("Collection name is required.");
      return;
    }
    setBusy(true);
    try {
      const created = await createKnowledgeCollection(
        newName.trim(),
        newDescription.trim(),
        newVectorStore,
      );
      setNewName("");
      setNewDescription("");
      setSelected(created.id);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Unable to create collection.");
    } finally {
      setBusy(false);
    }
  }

  async function ingestDocument() {
    if (!active || !docText.trim()) {
      setNotice("Select a collection and paste document text first.");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const result = await addKnowledgeDocument(active.id, docName.trim() || "document", docText);
      setNotice(`Indexed ${result.chunks_indexed} chunk(s) from "${docName || "document"}".`);
      setDocName("");
      setDocText("");
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Unable to ingest document.");
    } finally {
      setBusy(false);
    }
  }

  async function runSearch() {
    if (!active || !query.trim()) {
      return;
    }
    setBusy(true);
    setNotice(null);
    setResults([]);
    try {
      const response = await searchKnowledgeCollection(active.id, query.trim());
      setResults(response.results);
      if (response.results.length === 0) {
        setNotice(response.reason ? `No results — ${response.reason}.` : "No matching chunks found.");
      }
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">Knowledge</h1>
        <p className="fx-muted">
          Document collections the agents can search and cite. How memory and the vector store are configured lives in{" "}
          <Link className="underline" href={MEMORY_SETTINGS_HREF}>
            Settings → Memory &amp; knowledge
          </Link>
          .
        </p>
      </header>

      {error ? (
        <div role="alert" className="fx-panel flex flex-wrap items-center justify-between gap-2 border-[hsl(var(--state-critical)/0.4)] p-3 text-sm">
          <span>{error}</span>
          <Button variant="secondary" size="sm" onClick={() => void refresh()}>
            Retry
          </Button>
        </div>
      ) : null}

      {loaded && !longTerm.ready ? (
        <div role="status" className="fx-panel flex flex-wrap items-center justify-between gap-3 border-[hsl(var(--state-warning)/0.5)] p-3 text-sm">
          <div className="min-w-0">
            <p className="font-medium">Long-term memory is not set up, so documents cannot be indexed or searched yet.</p>
            <p className="fx-muted mt-0.5 text-xs">{longTerm.reason} You can still create collections now.</p>
          </div>
          <Button asChild size="sm">
            <Link href={MEMORY_SETTINGS_HREF}>Set up</Link>
          </Button>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[320px_1fr]">
        <aside className="space-y-3">
          <article className="fx-panel p-3">
            <h2 className="mb-2 text-sm font-semibold">Collections</h2>
            <ul className="space-y-1 text-sm">
              {collections.map((collection) => (
                <li key={collection.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(collection.id)}
                    className={`w-full rounded border px-2 py-1.5 text-left ${
                      collection.id === selected
                        ? "border-[hsl(var(--accent)/0.5)] bg-[hsl(var(--accent)/0.1)]"
                        : "border-[var(--fx-border)] bg-[var(--fx-surface-elevated)]"
                    }`}
                  >
                    <span className="font-medium text-[var(--foreground)]">{collection.name}</span>
                    <span className="fx-muted block text-xs">
                      {collection.document_count} doc(s) · {collection.chunk_count} chunk(s)
                    </span>
                  </button>
                </li>
              ))}
              {collections.length === 0 ? (
                <li className="fx-muted text-xs">{loaded ? "No collections yet. Create one below." : "Loading…"}</li>
              ) : null}
            </ul>
          </article>

          <article className="fx-panel space-y-2 p-3 text-xs">
            <h2 className="text-sm font-semibold">New collection</h2>
            <input
              className="fx-field h-8 w-full px-2"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Collection name"
            />
            <input
              className="fx-field h-8 w-full px-2"
              value={newDescription}
              onChange={(e) => setNewDescription(e.target.value)}
              placeholder="Description (optional)"
            />
            <label className="fx-muted block">Vector store</label>
            <select
              className="fx-field h-8 w-full px-2"
              value={newVectorStore}
              onChange={(e) => setNewVectorStore(e.target.value)}
            >
              {vectorStores.map((vs) => (
                <option key={vs.id} value={vs.id} disabled={!vs.ready}>
                  {vs.name}
                  {vs.ready ? "" : " — unavailable"}
                </option>
              ))}
            </select>
            {storeFor(newVectorStore) && !storeFor(newVectorStore)!.ready ? (
              <p className="text-[11px] text-[hsl(var(--state-warning))]">
                {storeFor(newVectorStore)!.note}{" "}
                <Link className="underline" href={MEMORY_SETTINGS_HREF}>
                  Set up
                </Link>
              </p>
            ) : null}
            <button
              type="button"
              disabled={busy}
              onClick={() => void addCollection()}
              className="fx-btn-primary w-full px-3 py-1.5 font-medium disabled:opacity-60"
            >
              Create collection
            </button>
          </article>
        </aside>

        <div className="space-y-4">
          {active ? (
            <>
              <article className="fx-panel p-3 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">{active.name}</h2>
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await deleteKnowledgeCollection(active.id);
                        setSelected(null);
                        await refresh();
                      } catch {
                        setNotice("Unable to delete collection.");
                      }
                    }}
                    className="fx-btn-secondary px-2 py-1 text-[11px]"
                  >
                    Delete collection
                  </button>
                </div>
                <p className="fx-muted mt-1">{active.description || "No description."}</p>
                <p className="fx-muted mt-1">
                  Vector store:{" "}
                  <span className="text-[var(--foreground)]">
                    {storeFor(active.vector_store_id)?.name ?? active.vector_store_id}
                  </span>
                  {storeFor(active.vector_store_id)?.embedding_model
                    ? ` · ${storeFor(active.vector_store_id)!.embedding_model}`
                    : ""}
                </p>
              </article>

              {!activeReady ? (
                <p role="status" className="fx-panel p-3 text-xs">
                  {activeStore?.note || "This collection's vector store is not available."}{" "}
                  <Link className="underline" href={MEMORY_SETTINGS_HREF}>
                    Set up
                  </Link>
                </p>
              ) : null}

              <article className="fx-panel space-y-2 p-3 text-xs">
                <h2 className="text-sm font-semibold">Add document</h2>
                <input
                  className="fx-field h-8 w-full px-2"
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="Document name"
                />
                <textarea
                  className="fx-field min-h-32 w-full p-2"
                  value={docText}
                  onChange={(e) => setDocText(e.target.value)}
                  placeholder="Paste document text — it will be chunked and embedded."
                />
                <button
                  type="button"
                  disabled={busy || !activeReady}
                  onClick={() => void ingestDocument()}
                  className="fx-btn-primary px-3 py-1.5 font-medium disabled:opacity-60"
                >
                  Index document
                </button>
              </article>

              <article className="fx-panel space-y-2 p-3 text-xs">
                <h2 className="text-sm font-semibold">Test retrieval</h2>
                <div className="flex gap-2">
                  <input
                    className="fx-field h-8 flex-1 px-2"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && activeReady) void runSearch();
                    }}
                    placeholder="Search query..."
                  />
                  <button
                    type="button"
                    disabled={busy || !activeReady}
                    onClick={() => void runSearch()}
                    className="fx-btn-secondary px-3 py-1.5 font-medium disabled:opacity-60"
                  >
                    Search
                  </button>
                </div>
                {results.length > 0 ? (
                  <ul className="space-y-2">
                    {results.map((result, index) => (
                      <li key={index} className="rounded border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                        <div className="mb-1 flex items-center justify-between gap-2 text-[10px] uppercase tracking-wide fx-muted">
                          <span>{result.document_name || "document"} · chunk {result.chunk_index}</span>
                          {typeof result.score === "number" ? <span>score {result.score.toFixed(3)}</span> : null}
                        </div>
                        <p className="whitespace-pre-wrap text-[var(--foreground)]">{result.content}</p>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>
            </>
          ) : (
            <article className="fx-panel p-4 text-sm fx-muted">
              Select or create a collection to add documents and test retrieval.
            </article>
          )}
        </div>
      </div>

      {notice ? <p role="status" className="fx-muted text-xs">{notice}</p> : null}
    </section>
  );
}
