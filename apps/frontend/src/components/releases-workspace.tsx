"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import {
  activateAgentDefinition,
  activateGuardrailRuleset,
  activateWorkflowDefinition,
  getAgentDefinitionVersions,
  getGuardrailRulesetVersions,
  getWorkflowDefinitionVersions,
  rollbackAgentDefinition,
  rollbackGuardrailRuleset,
  rollbackWorkflowDefinition,
} from "@/lib/api";
import { useToast } from "@/components/toast";
import type {
  AgentDefinition,
  DefinitionRevisionSummary,
  GuardrailRuleSet,
  WorkflowDefinition,
} from "@/types/frontier";

type ReleaseEntityType = "workflow" | "agent" | "guardrail";

type ReleaseItem = {
  id: string;
  name: string;
  version: number;
  status: string;
  published_revision_id?: string | null;
  active_revision_id?: string | null;
  published_at?: string | null;
  active_at?: string | null;
};

type Props = {
  workflows: WorkflowDefinition[];
  agents: AgentDefinition[];
  guardrails: GuardrailRuleSet[];
};

const versionLoaders = {
  workflow: getWorkflowDefinitionVersions,
  agent: getAgentDefinitionVersions,
  guardrail: getGuardrailRulesetVersions,
} as const;

const activationActions = {
  workflow: activateWorkflowDefinition,
  agent: activateAgentDefinition,
  guardrail: activateGuardrailRuleset,
} as const;

const rollbackActions = {
  workflow: rollbackWorkflowDefinition,
  agent: rollbackAgentDefinition,
  guardrail: rollbackGuardrailRuleset,
} as const;

const entityLabels: Record<ReleaseEntityType, string> = {
  workflow: "Workflow",
  agent: "Agent",
  guardrail: "Guardrail",
};

const openHrefForEntity = {
  workflow: (id: string) => `/builder/workflows/${encodeURIComponent(id)}`,
  agent: (id: string) => `/builder/agents/${encodeURIComponent(id)}`,
  guardrail: (id: string) => `/builder/guardrails/${encodeURIComponent(id)}`,
} as const;

function entityKey(entityType: ReleaseEntityType, entityId: string): string {
  return `${entityType}:${entityId}`;
}

function sortReleaseItems<T extends ReleaseItem>(items: T[]): T[] {
  return [...items].sort((left, right) => left.name.localeCompare(right.name));
}

function resolvePublishedRevisionIdAtRevision(
  revisions: DefinitionRevisionSummary[],
  revisionId: string,
): string | null {
  const orderedRevisions = [...revisions].sort((left, right) => left.revision - right.revision);
  const targetRevision = orderedRevisions.find((revision) => revision.id === revisionId);
  if (!targetRevision) {
    return null;
  }

  let publishedRevisionId: string | null = null;
  for (const revision of orderedRevisions) {
    if (revision.revision > targetRevision.revision) {
      break;
    }
    if (revision.status === "published") {
      publishedRevisionId = revision.id;
    }
  }

  return publishedRevisionId;
}

function determinePublishedRevisionAfterRollback(
  revisions: DefinitionRevisionSummary[],
  restoredRevisionId: string,
  status: string,
): string | null {
  if (revisions.length > 0) {
    return resolvePublishedRevisionIdAtRevision(revisions, restoredRevisionId);
  }

  return status === "published" ? restoredRevisionId : null;
}

function StatusBadge({ label, tone }: { label: string; tone: "default" | "success" | "info" }) {
  const className = tone === "success"
    ? "border-[color-mix(in_srgb,var(--fx-success)_42%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-success)_14%,transparent)]"
    : tone === "info"
      ? "border-[color-mix(in_srgb,var(--fx-primary)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_12%,transparent)]"
      : "border-[var(--ui-border)] bg-[hsl(var(--card))]";

  return (
    <span className={`rounded-full border px-2.5 py-1 text-[0.72rem] font-medium tracking-[0.01em] text-[var(--foreground)] ${className}`}>
      {label}
    </span>
  );
}

function ReleaseSection({
  entityType,
  items,
}: {
  entityType: ReleaseEntityType;
  items: ReleaseItem[];
}) {
  const router = useRouter();
  const { addToast } = useToast();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [versionsByKey, setVersionsByKey] = useState<Record<string, DefinitionRevisionSummary[]>>({});
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);
  const [itemOverridesByKey, setItemOverridesByKey] = useState<Record<string, Partial<ReleaseItem>>>({});

  function updateLocalItem(key: string, update: Partial<ReleaseItem>) {
    setItemOverridesByKey((current) => ({
      ...current,
      [key]: {
        ...current[key],
        ...update,
      },
    }));
  }

  async function loadVersions(item: ReleaseItem, options?: { toggle?: boolean }) {
    const key = entityKey(entityType, item.id);
    setLoadingKey(key);
    try {
      const response = await versionLoaders[entityType](item.id);
      setVersionsByKey((current) => ({ ...current, [key]: response.versions ?? [] }));
      if (options?.toggle === false) {
        setExpandedKey(key);
      } else {
        setExpandedKey((current) => (current === key ? null : key));
      }
    } catch (error) {
      addToast("error", error instanceof Error ? error.message : `Unable to load ${entityLabels[entityType].toLowerCase()} revisions.`);
    } finally {
      setLoadingKey(null);
    }
  }

  async function activateRevision(item: ReleaseItem, revisionId?: string) {
    const key = entityKey(entityType, item.id);
    const targetRevisionId = revisionId ?? item.published_revision_id ?? null;
    setActionKey(`${key}:activate:${targetRevisionId ?? "published"}`);
    let shouldRefresh = false;
    try {
      const response = await activationActions[entityType](item.id, revisionId ? { revision_id: revisionId } : {});
      updateLocalItem(key, {
        active_revision_id: response.active_revision.id,
      });
      addToast("success", `${entityLabels[entityType]} runtime revision activated.`);
      await loadVersions(item, { toggle: false });
      shouldRefresh = true;
    } catch (error) {
      addToast("error", error instanceof Error ? error.message : `Unable to activate ${entityLabels[entityType].toLowerCase()} revision.`);
    } finally {
      setActionKey(null);
      if (shouldRefresh) {
        router.refresh();
      }
    }
  }

  async function rollbackRevision(item: ReleaseItem, revisionId: string) {
    const key = entityKey(entityType, item.id);
    setActionKey(`${key}:rollback:${revisionId}`);
    let shouldRefresh = false;
    try {
      const response = await rollbackActions[entityType](item.id, { revision_id: revisionId });
      updateLocalItem(key, {
        version: response.version,
        status: response.status,
        published_revision_id: determinePublishedRevisionAfterRollback(
          versionsByKey[key] ?? [],
          response.restored_from.id,
          response.status,
        ),
      });
      addToast("success", `${entityLabels[entityType]} restored from the selected revision.`);
      await loadVersions(item, { toggle: false });
      shouldRefresh = true;
    } catch (error) {
      addToast("error", error instanceof Error ? error.message : `Unable to restore ${entityLabels[entityType].toLowerCase()} revision.`);
    } finally {
      setActionKey(null);
      if (shouldRefresh) {
        router.refresh();
      }
    }
  }

  const title = entityType === "workflow" ? "Workflow Definitions" : entityType === "agent" ? "Agent Definitions" : "Guardrail Rulesets";
  const emptyLabel = entityType === "guardrail" ? "No guardrail rulesets available." : `No ${entityType} definitions available.`;

  return (
    <div className="fx-panel rounded-[1.6rem] p-4 shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Release lane</p>
          <h2 className="mt-2 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">{title}</h2>
        </div>
        <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">{items.length} item{items.length === 1 ? "" : "s"}</div>
      </div>
      {items.length === 0 ? (
        <p className="text-sm text-[var(--fx-muted)]">{emptyLabel}</p>
      ) : (
        <div className="space-y-3">
          {items.map((item) => {
            const key = entityKey(entityType, item.id);
            const versions = versionsByKey[key] ?? [];
            const currentItem = itemOverridesByKey[key] ? { ...item, ...itemOverridesByKey[key] } : item;
            const expanded = expandedKey === key;
            const publishedRevisionId = currentItem.published_revision_id ?? null;
            const activeRevisionId = currentItem.active_revision_id ?? null;
            const canActivatePublished = Boolean(publishedRevisionId);

            return (
              <article key={item.id} className="rounded-[1.25rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.42)]">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-semibold text-[var(--foreground)]">{currentItem.name}</p>
                      <StatusBadge label={`v${currentItem.version}`} tone="default" />
                      <StatusBadge label={currentItem.status} tone="info" />
                      {activeRevisionId ? <StatusBadge label="Runtime active" tone="success" /> : null}
                    </div>
                    <p className="mt-2 font-mono text-[11px] text-[var(--fx-muted)]">{item.id}</p>
                    <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-[var(--fx-muted)]">
                      <span>published: {publishedRevisionId ? publishedRevisionId.slice(0, 8) : "none"}</span>
                      <span>active: {activeRevisionId ? activeRevisionId.slice(0, 8) : "none"}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Link className="fx-btn-secondary px-2.5 py-1 text-xs font-medium" href={openHrefForEntity[entityType](item.id)}>
                      Open
                    </Link>
                    <button
                      type="button"
                      className="fx-btn-secondary px-2.5 py-1 text-xs font-medium"
                      onClick={() => void loadVersions(item)}
                      disabled={loadingKey === key}
                    >
                      {loadingKey === key ? "Loading..." : expanded ? "Hide revisions" : "Show revisions"}
                    </button>
                    <button
                      type="button"
                      className="fx-btn-primary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                      onClick={() => void activateRevision(item)}
                      disabled={!canActivatePublished || actionKey === `${key}:activate:${publishedRevisionId ?? "published"}`}
                      title={canActivatePublished ? "Activate the currently published revision for runtime." : "Publish this item before promoting it to runtime."}
                    >
                      {actionKey === `${key}:activate:${publishedRevisionId ?? "published"}` ? "Promoting..." : "Promote"}
                    </button>
                  </div>
                </div>

                {expanded ? (
                  <div className="mt-3 overflow-hidden rounded-[1rem] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)]">
                    <table className="w-full text-xs">
                      <thead className="fx-table-head">
                        <tr>
                          <th className="px-3 py-2 text-left">Revision</th>
                          <th className="px-3 py-2 text-left">Action</th>
                          <th className="px-3 py-2 text-left">Version</th>
                          <th className="px-3 py-2 text-left">Status</th>
                          <th className="px-3 py-2 text-left">Created</th>
                          <th className="px-3 py-2 text-right">Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {versions.length === 0 ? (
                          <tr className="border-t border-[var(--fx-border)]">
                            <td colSpan={6} className="px-3 py-4 text-[var(--fx-muted)]">No revisions available yet.</td>
                          </tr>
                        ) : (
                          versions.map((revision) => {
                            const activateActionKey = `${key}:activate:${revision.id}`;
                            const rollbackActionKey = `${key}:rollback:${revision.id}`;
                            const isRuntimeActive = activeRevisionId === revision.id;
                            const isPublished = publishedRevisionId === revision.id;

                            return (
                              <tr key={revision.id} className="border-t border-[var(--fx-border)]">
                                <td className="px-3 py-2 font-mono text-[var(--foreground)]">r{revision.revision}</td>
                                <td className="px-3 py-2 text-[var(--foreground)]">{revision.action}</td>
                                <td className="px-3 py-2 text-[var(--foreground)]">v{revision.version}</td>
                                <td className="px-3 py-2">
                                  <div className="flex flex-wrap gap-1">
                                    <StatusBadge label={revision.status} tone="default" />
                                    {isPublished ? <StatusBadge label="Published" tone="info" /> : null}
                                    {isRuntimeActive ? <StatusBadge label="Active" tone="success" /> : null}
                                  </div>
                                </td>
                                <td className="px-3 py-2 text-[var(--fx-muted)]">{new Date(revision.created_at).toLocaleString()}</td>
                                <td className="px-3 py-2">
                                  <div className="flex justify-end gap-2">
                                    <button
                                      type="button"
                                      className="fx-btn-secondary px-2 py-1 text-[11px] disabled:opacity-60"
                                      onClick={() => void activateRevision(item, revision.id)}
                                      disabled={revision.status !== "published" || actionKey === activateActionKey}
                                    >
                                      {actionKey === activateActionKey ? "Activating..." : "Activate"}
                                    </button>
                                    <button
                                      type="button"
                                      className="fx-btn-secondary px-2 py-1 text-[11px] disabled:opacity-60"
                                      onClick={() => void rollbackRevision(item, revision.id)}
                                      disabled={actionKey === rollbackActionKey}
                                    >
                                      {actionKey === rollbackActionKey ? "Restoring..." : "Restore"}
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            );
                          })
                        )}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ReleasesWorkspace({ workflows, agents, guardrails }: Props) {
  const sections = useMemo(
    () => [
      { entityType: "workflow" as const, items: sortReleaseItems(workflows) },
      { entityType: "agent" as const, items: sortReleaseItems(agents) },
      { entityType: "guardrail" as const, items: sortReleaseItems(guardrails) },
    ],
    [agents, guardrails, workflows],
  );

  return (
    <section className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-4 rounded-[1.7rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_97%,hsl(var(--background))_3%)] px-5 py-4 shadow-[0_22px_56px_rgba(15,23,42,0.06)]">
        <div className="max-w-2xl">
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Builder workspace</p>
          <h1 className="mt-2 text-[1.5rem] font-semibold tracking-[-0.03em] text-[var(--foreground)]">Versions & Releases</h1>
          <p className="mt-2 text-sm leading-6 text-[var(--fx-muted)]">Promote published revisions to runtime, inspect revision history, and restore prior versions when needed without leaving the release surface.</p>
        </div>
        <div className="grid min-w-[220px] gap-2 sm:grid-cols-3">
          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
            <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Workflows</p>
            <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{workflows.length}</p>
          </div>
          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
            <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Agents</p>
            <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{agents.length}</p>
          </div>
          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
            <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Guardrails</p>
            <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{guardrails.length}</p>
          </div>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-3">
        {sections.map((section) => (
          <ReleaseSection key={section.entityType} entityType={section.entityType} items={section.items} />
        ))}
      </div>
    </section>
  );
}
