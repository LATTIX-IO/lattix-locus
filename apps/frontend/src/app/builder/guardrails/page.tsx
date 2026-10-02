import Link from "next/link";
import { BuilderLibraryStatusBadges } from "@/components/builder-library-status-badges";
import { TypedDeleteButton } from "@/components/typed-delete-button";
import { getGuardrailRulesets } from "@/lib/api";

type GuardrailsBuilderPageProps = {
  searchParams?: Promise<{ view?: string }>;
};

export default async function GuardrailsBuilderPage({ searchParams }: GuardrailsBuilderPageProps) {
  const rulesets = await getGuardrailRulesets();
  const resolvedSearchParams = await searchParams;
  const view = resolvedSearchParams?.view === "archived" ? "archived" : "library";
  const rulesetCounts = {
    draft: rulesets.filter((ruleset) => ruleset.status === "draft").length,
    published: rulesets.filter((ruleset) => ruleset.status === "published").length,
    archived: rulesets.filter((ruleset) => ruleset.status === "archived").length,
  };
  const visibleRulesets = rulesets.filter((ruleset) =>
    view === "archived" ? ruleset.status === "archived" : ruleset.status !== "archived",
  );

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold">Guardrails Builder</h1>
          <p className="fx-muted">Production-ready preconfigured guardrails plus custom sets for agents and workflows.</p>
          <BuilderLibraryStatusBadges
            counts={[
              { label: "Draft", count: rulesetCounts.draft },
              { label: "Published", count: rulesetCounts.published },
              { label: "Archived", count: rulesetCounts.archived },
            ]}
          />
        </div>
        <Link href="/builder/guardrails/new" className="fx-btn-primary px-3 py-2 text-sm">
          New guardrail set
        </Link>
      </header>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/builder/guardrails"
          className={view === "library" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Library
        </Link>
        <Link
          href="/builder/guardrails?view=archived"
          className={view === "archived" ? "fx-btn-secondary px-3 py-1.5 font-medium" : "fx-btn-ghost px-3 py-1.5 font-medium"}
        >
          Archived
        </Link>
        <p className="fx-muted ml-auto text-xs uppercase tracking-[0.12em]">{visibleRulesets.length} shown</p>
      </div>

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Version</th>
              <th className="px-3 py-2 text-left">Guardrail ID</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {visibleRulesets.length === 0 ? (
              <tr className="border-t border-[var(--fx-border)]">
                <td colSpan={5} className="fx-muted px-3 py-6 text-center">
                  {view === "archived" ? "No archived guardrail sets." : "No guardrail sets available."}
                </td>
              </tr>
            ) : (
              visibleRulesets.map((ruleset) => (
                <tr key={ruleset.id} className="border-t border-[var(--fx-border)]">
                  <td className="px-3 py-2 text-[var(--foreground)]">{ruleset.name}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{ruleset.status}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">v{ruleset.version}</td>
                  <td className="px-3 py-2 font-mono text-xs text-[var(--foreground)]">{ruleset.id}</td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-2">
                      <Link className="fx-btn-secondary px-3 py-1.5 text-xs font-medium" href={`/builder/guardrails/${ruleset.id}`}>
                        Open
                      </Link>
                      <TypedDeleteButton itemType="guardrail" itemId={ruleset.id} itemName={ruleset.name} />
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
