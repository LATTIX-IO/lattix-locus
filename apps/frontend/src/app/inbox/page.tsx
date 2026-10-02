import { InboxWorkspace } from "@/components/inbox-workspace";
import { UserChatWorkspace } from "@/components/user-chat-workspace";
import { getInbox, getWorkflowRuns } from "@/lib/api";
import type { InboxItem, WorkflowRunSummary } from "@/types/frontier";

type InboxPageProps = {
  searchParams: Promise<{ session?: string; details?: string; tab?: string; view?: string }>;
};

/**
 * `/inbox` is the composer-first landing (recent sessions + quick starts).
 * Selecting a session from the user console sidebar (`?session=<runId>`), or
 * requesting the chat workspace explicitly (`?view=chat`), opens the user chat
 * workspace with its graph/details panes.
 */
export default async function InboxPage({ searchParams }: InboxPageProps) {
  const { session, details, tab, view } = await searchParams;

  if (!session && view !== "chat") {
    return <InboxWorkspace />;
  }

  let items: InboxItem[] = [];
  let runs: WorkflowRunSummary[] = [];
  let initialLoadError: string | null = null;

  try {
    [items, runs] = await Promise.all([getInbox(), getWorkflowRuns()]);
  } catch (error) {
    initialLoadError = error instanceof Error ? error.message : "Unable to load inbox sessions.";
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
