import { UserChatWorkspace } from "@/components/user-chat-workspace";
import { getInbox, getWorkflowRuns } from "@/lib/api";
import type { InboxItem, WorkflowRunSummary } from "@/types/locus";

type ActivityPageProps = {
  searchParams: Promise<{ session?: string; details?: string; tab?: string }>;
};

/**
 * `/activity` is the run workspace: pick a session in the sidebar
 * (`?session=<runId>`) to open its conversation, graph and details. New work
 * starts from the composer on Home. (`/inbox` and `/runs/:id` redirect here.)
 */
export default async function ActivityPage({ searchParams }: ActivityPageProps) {
  const { session, details, tab } = await searchParams;

  let items: InboxItem[] = [];
  let runs: WorkflowRunSummary[] = [];
  let initialLoadError: string | null = null;

  try {
    [items, runs] = await Promise.all([getInbox(), getWorkflowRuns()]);
  } catch (error) {
    initialLoadError = error instanceof Error ? error.message : "Unable to load runs.";
  }

  return (
    <UserChatWorkspace
      initialRuns={runs}
      initialInbox={items}
      initialSelectedRunId={session ?? null}
      initialDetailsOpen={details !== "0"}
      initialTab={tab === "graph" ? "graph" : "chat"}
      initialLoadError={initialLoadError}
    />
  );
}
