import { randomUUID } from "node:crypto";
import Link from "next/link";
import { TypedDeleteButton } from "@/components/typed-delete-button";
import { ImportExportControls } from "@/components/import-export-controls";
import { BuilderLibraryActions } from "@/components/builder-library-actions";
import { BuilderLibraryStatusBadges } from "@/components/builder-library-status-badges";
import { getAgentDefinitions } from "@/lib/api";

type BuilderAgentsPageProps = {
  searchParams?: Promise<{ view?: string }>;
};

export default async function BuilderAgentsPage({ searchParams }: BuilderAgentsPageProps) {
  const agents = await getAgentDefinitions();
  const newAgentId = randomUUID();
  const resolvedSearchParams = await searchParams;
  const view = resolvedSearchParams?.view === "archived" ? "archived" : "library";
  const agentCounts = {
    draft: agents.filter((agent) => agent.status === "draft").length,
    published: agents.filter((agent) => agent.status === "published").length,
    archived: agents.filter((agent) => agent.status === "archived").length,
  };
  const visibleAgents = agents.filter((agent) =>
    view === "archived" ? agent.status === "archived" : agent.status !== "archived",
  );

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Agent Studio</h1>
          <p className="fx-muted">Agents are individual units of execution used by workflows.</p>
          <BuilderLibraryStatusBadges
            counts={[
              { label: "Draft", count: agentCounts.draft },
              { label: "Published", count: agentCounts.published },
              { label: "Archived", count: agentCounts.archived },
            ]}
          />
        </div>
        <div className="flex items-center gap-3">
          <ImportExportControls kind="agent-definitions" />
          <Link className="fx-btn-primary px-3 py-2 text-sm font-medium" href={`/library/agents/${newAgentId}`}>
            New Agent
          </Link>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/library/agents"
          className={view === "library" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Library
        </Link>
        <Link
          href="/library/agents?view=archived"
          className={view === "archived" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Archived
        </Link>
        <p className="fx-muted ml-auto text-xs uppercase tracking-[0.12em]">{visibleAgents.length} shown</p>
      </div>

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Agent</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Version</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {visibleAgents.length === 0 ? (
              <tr className="border-t border-[var(--fx-border)]">
                <td colSpan={5} className="fx-muted px-3 py-6 text-center">
                  {view === "archived" ? "No archived agents." : "No agents available."}
                </td>
              </tr>
            ) : (
              visibleAgents.map((agent) => (
                <tr key={agent.id} className="border-t border-[var(--fx-border)]">
                  <td className="px-3 py-2 text-[var(--foreground)]">{agent.name}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{agent.type}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{agent.status}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">v{agent.version}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <div className="flex flex-nowrap items-center justify-end gap-2">
                      <ImportExportControls kind="agent-definitions" id={agent.id} compact />
                      <BuilderLibraryActions
                        entityType="agent"
                        entityId={agent.id}
                        entityName={agent.name}
                        openHref={`/library/agents/${agent.id}`}
                        status={agent.status}
                      />
                      <TypedDeleteButton itemType="agent" itemId={agent.id} itemName={agent.name} />
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
