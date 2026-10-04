import { randomUUID } from "node:crypto";
import Link from "next/link";
import { TypedDeleteButton } from "@/components/typed-delete-button";
import { ImportExportControls } from "@/components/import-export-controls";
import { BuilderLibraryActions } from "@/components/builder-library-actions";
import { BuilderLibraryStatusBadges } from "@/components/builder-library-status-badges";
import { getWorkflowDefinitions } from "@/lib/api";

type BuilderWorkflowsPageProps = {
  searchParams?: Promise<{ view?: string }>;
};

export default async function BuilderWorkflowsPage({ searchParams }: BuilderWorkflowsPageProps) {
  const workflows = await getWorkflowDefinitions();
  const newWorkflowId = randomUUID();
  const resolvedSearchParams = await searchParams;
  const view = resolvedSearchParams?.view === "archived" ? "archived" : "library";
  const workflowCounts = {
    draft: workflows.filter((workflow) => workflow.status === "draft").length,
    published: workflows.filter((workflow) => workflow.status === "published").length,
    archived: workflows.filter((workflow) => workflow.status === "archived").length,
  };
  const visibleWorkflows = workflows.filter((workflow) =>
    view === "archived" ? workflow.status === "archived" : workflow.status !== "archived",
  );

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Workflow Studio</h1>
          <p className="fx-muted">Workflows are tasks for one or more agents to execute end-to-end.</p>
          <BuilderLibraryStatusBadges
            counts={[
              { label: "Draft", count: workflowCounts.draft },
              { label: "Published", count: workflowCounts.published },
              { label: "Archived", count: workflowCounts.archived },
            ]}
          />
        </div>
        <div className="flex items-center gap-3">
          <ImportExportControls kind="workflow-definitions" />
          <Link className="fx-btn-primary px-3 py-2 text-sm font-medium" href={`/library/workflows/${newWorkflowId}`}>
            New Workflow
          </Link>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/library/workflows"
          className={view === "library" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Library
        </Link>
        <Link
          href="/library/workflows?view=archived"
          className={view === "archived" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Archived
        </Link>
        <p className="fx-muted ml-auto text-xs uppercase tracking-[0.12em]">{visibleWorkflows.length} shown</p>
      </div>

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Workflow</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Version</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {visibleWorkflows.length === 0 ? (
              <tr className="border-t border-[var(--fx-border)]">
                <td colSpan={5} className="fx-muted px-3 py-6 text-center">
                  {view === "archived" ? "No archived workflows." : "No workflows available."}
                </td>
              </tr>
            ) : (
              visibleWorkflows.map((workflow) => (
                <tr key={workflow.id} className="border-t border-[var(--fx-border)]">
                  <td className="px-3 py-2 text-[var(--foreground)]">{workflow.name}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{workflow.status}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">v{workflow.version}</td>
                  <td className="fx-muted px-3 py-2">{workflow.description}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <div className="flex flex-nowrap items-center justify-end gap-2">
                      <ImportExportControls kind="workflow-definitions" id={workflow.id} compact />
                      <BuilderLibraryActions
                        entityType="workflow"
                        entityId={workflow.id}
                        entityName={workflow.name}
                        openHref={`/library/workflows/${workflow.id}`}
                        status={workflow.status}
                      />
                      <TypedDeleteButton itemType="workflow" itemId={workflow.id} itemName={workflow.name} />
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
