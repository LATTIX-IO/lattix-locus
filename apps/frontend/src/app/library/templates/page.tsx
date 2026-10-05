"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { BuilderLibraryStatusBadges } from "@/components/builder-library-status-badges";
import {
  getTemplateCatalog,
  instantiateAgentTemplate,
  instantiatePlaybook,
  instantiateWorkflowTemplate,
} from "@/lib/api";
import type { TemplateCatalogItem } from "@/types/locus";

type TemplateTypeFilter = "all" | "agent" | "workflow" | "playbook";
type TemplateCatalogView = "library" | "archived";

export default function TemplatesPage() {
  const router = useRouter();
  const [items, setItems] = useState<TemplateCatalogItem[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<TemplateTypeFilter>("all");
  const [view, setView] = useState<TemplateCatalogView>("library");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const catalog = await getTemplateCatalog();
        if (cancelled) {
          return;
        }
        setItems(catalog);
      } catch {
        if (!cancelled) {
          setError("Unable to load template catalog.");
        }
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return items.filter((item) => {
      if (typeFilter !== "all" && item.template_type !== typeFilter) {
        return false;
      }
      if (view === "archived" ? item.status !== "deprecated" : item.status === "deprecated") {
        return false;
      }
      if (!needle) {
        return true;
      }
      return (
        item.name.toLowerCase().includes(needle)
        || item.description.toLowerCase().includes(needle)
        || item.category.toLowerCase().includes(needle)
      );
    });
  }, [items, search, typeFilter, view]);

  const templateCounts = useMemo(() => ({
    active: items.filter((item) => item.status === "active").length,
    deprecated: items.filter((item) => item.status === "deprecated").length,
  }), [items]);

  async function handleInstantiate(item: TemplateCatalogItem) {
    setBusyKey(item.id);
    setError(null);
    try {
      if (item.template_type === "agent") {
        const created = await instantiateAgentTemplate(item.source_id, { name: `${item.name} Instance` });
        router.push(`/library/agents/${created.id}`);
        return;
      }

      if (item.template_type === "workflow") {
        const created = await instantiateWorkflowTemplate(item.source_id, { name: `${item.name} Instance` });
        router.push(`/library/workflows/${created.id}`);
        return;
      }

      const created = await instantiatePlaybook(item.source_id, { name: `${item.name} Instance` });
      router.push(`/library/playbooks/${created.id}`);
    } catch {
      setError(`Failed to instantiate ${item.name}.`);
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Templates</h1>
          <p className="fx-muted">Single catalog of agent, workflow, and playbook templates with active library and archived views.</p>
          <BuilderLibraryStatusBadges
            counts={[
              { label: "Active", count: templateCounts.active },
              { label: "Archived", count: templateCounts.deprecated },
            ]}
          />
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="fx-btn-primary px-3 py-2 text-sm font-medium"
            onClick={() => router.push(`/library/agents/${crypto.randomUUID()}`)}
          >
            New Agent
          </button>
          <button
            type="button"
            className="fx-btn-secondary px-3 py-2 text-sm font-medium"
            onClick={() => router.push(`/library/workflows/${crypto.randomUUID()}`)}
          >
            New Workflow
          </button>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <button
          type="button"
          className={view === "library" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
          onClick={() => setView("library")}
        >
          Library
        </button>
        <button
          type="button"
          className={view === "archived" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
          onClick={() => setView("archived")}
        >
          Archived
        </button>
        <p className="fx-muted ml-auto text-xs uppercase tracking-[0.12em]">{filtered.length} shown</p>
      </div>

      {error && (
        <div className="border border-[#6b1f2a] bg-[#2f1a21] p-3 text-sm text-[#ffb8c4]">
          {error}
        </div>
      )}

      <div className="fx-panel p-3">
        <div className="grid gap-2 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs uppercase tracking-wide fx-muted">
            Search
            <input
              className="fx-input px-3 py-2 text-sm normal-case"
              placeholder="Search by name, category, description..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1 text-xs uppercase tracking-wide fx-muted">
            Template Type
            <select
              className="fx-input px-3 py-2 text-sm normal-case"
              value={typeFilter}
              onChange={(event) => setTypeFilter(event.target.value as TemplateTypeFilter)}
            >
              <option value="all">All</option>
              <option value="agent">Agent templates</option>
              <option value="workflow">Workflow templates</option>
              <option value="playbook">Playbook templates</option>
            </select>
          </label>
        </div>
      </div>

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Template</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Version</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => (
              <tr key={item.id} className="border-t border-[var(--fx-border)]">
                <td className="px-3 py-2 text-[var(--foreground)]">{item.name}</td>
                <td className="px-3 py-2 text-[var(--foreground)]">{item.template_type}</td>
                <td className="px-3 py-2 text-[var(--foreground)]">{item.category}</td>
                <td className="px-3 py-2 text-[var(--foreground)]">{item.status}</td>
                <td className="px-3 py-2 text-[var(--foreground)]">{item.version ? `v${item.version}` : "—"}</td>
                <td className="fx-muted px-3 py-2">{item.description}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    className="fx-btn-primary px-2.5 py-1 text-xs font-medium"
                    disabled={busyKey === item.id || item.status !== "active"}
                    onClick={() => void handleInstantiate(item)}
                  >
                    {busyKey === item.id ? "Creating..." : "Instantiate"}
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td className="fx-muted px-3 py-4" colSpan={7}>
                  {view === "archived" ? "No archived templates match the selected filters." : "No templates match the selected filters."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
