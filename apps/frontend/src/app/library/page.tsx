import Link from "next/link";
import { LIBRARY_LINKS } from "@/components/navigation/nav-config";

const DESCRIPTIONS: Record<string, string> = {
  "/library/skills": "Instructions and tools the agents can load.",
  "/library/playbooks": "Reusable plans for recurring work.",
  "/library/workflows": "Multi-step flows of agents and tools.",
  "/library/agents": "Agent definitions, models and scopes.",
  "/library/connections": "MCP servers and integrations.",
  "/library/knowledge": "Document collections for retrieval.",
  "/library/templates": "Starting points for agents and workflows.",
  "/library/guardrails": "Rulesets that check prompts and outputs.",
  "/library/nodes": "Building blocks for workflow graphs.",
  "/library/releases": "Published versions and rollbacks.",
};

/** What the agents can use (doc 05 §8). Each entry opens its own page. */
export default function LibraryPage() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4">
      <h1 className="text-xl font-semibold tracking-tight">Library</h1>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {LIBRARY_LINKS.map((link) => (
          <li key={link.href}>
            <Link
              href={link.href}
              className="fx-panel block h-full p-4 no-underline transition-colors hover:border-[var(--fx-nav-active-border)]"
            >
              <span className="block text-sm font-semibold text-[hsl(var(--foreground))]">{link.label}</span>
              <span className="mt-1 block text-xs leading-5 text-[var(--fx-muted)]">{DESCRIPTIONS[link.href] ?? ""}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
