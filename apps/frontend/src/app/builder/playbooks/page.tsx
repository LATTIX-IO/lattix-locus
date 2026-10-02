import Link from "next/link";
import { BuilderLibraryActions } from "@/components/builder-library-actions";
import { BuilderLibraryStatusBadges } from "@/components/builder-library-status-badges";
import { ImportExportControls } from "@/components/import-export-controls";
import { getPlaybooks } from "@/lib/api";

type BuilderPlaybooksPageProps = {
  searchParams?: Promise<{ view?: string }>;
};

export default async function BuilderPlaybooksPage({ searchParams }: BuilderPlaybooksPageProps) {
  const playbooks = await getPlaybooks();
  const resolvedSearchParams = await searchParams;
  const view = resolvedSearchParams?.view === "archived" ? "archived" : "library";
  const playbookCounts = {
    draft: playbooks.filter((playbook) => playbook.status === "draft").length,
    published: playbooks.filter((playbook) => playbook.status === "published").length,
    archived: playbooks.filter((playbook) => playbook.status === "archived").length,
  };
  const visiblePlaybooks = playbooks.filter((playbook) =>
    view === "archived" ? playbook.status === "archived" : playbook.status !== "archived",
  );

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Playbooks</h1>
          <p className="fx-muted">Playbooks orchestrate multiple workflows into larger operating motions.</p>
          <BuilderLibraryStatusBadges
            counts={[
              { label: "Draft", count: playbookCounts.draft },
              { label: "Published", count: playbookCounts.published },
              { label: "Archived", count: playbookCounts.archived },
            ]}
          />
        </div>
        <div className="flex items-center gap-3">
          <ImportExportControls kind="playbooks" />
          <Link className="fx-btn-secondary px-4 py-2 text-sm font-medium" href="/builder/playbooks/new">
            New Playbook
          </Link>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/builder/playbooks"
          className={view === "library" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Library
        </Link>
        <Link
          href="/builder/playbooks?view=archived"
          className={view === "archived" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Archived
        </Link>
        <p className="fx-muted ml-auto text-xs uppercase tracking-[0.12em]">{visiblePlaybooks.length} shown</p>
      </div>

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Playbook</th>
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {visiblePlaybooks.length === 0 ? (
              <tr className="border-t border-[var(--fx-border)]">
                <td colSpan={5} className="fx-muted px-3 py-6 text-center">
                  {view === "archived" ? "No archived playbooks." : "No playbooks available."}
                </td>
              </tr>
            ) : (
              visiblePlaybooks.map((playbook) => (
                <tr key={playbook.id} className="border-t border-[var(--fx-border)]">
                  <td className="px-3 py-2 text-[var(--foreground)]">{playbook.name}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{playbook.category}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{playbook.status}</td>
                  <td className="fx-muted px-3 py-2">{playbook.description}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <div className="flex flex-nowrap items-center justify-end gap-2">
                      <ImportExportControls kind="playbooks" id={playbook.id} compact />
                      <BuilderLibraryActions
                        entityType="playbook"
                        entityId={playbook.id}
                        entityName={playbook.name}
                        openHref={`/builder/playbooks/${playbook.id}`}
                        status={playbook.status}
                      />
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
