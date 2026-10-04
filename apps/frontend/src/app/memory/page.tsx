import { MemoryScreen } from "@/components/memory-screen";
import { getWorkflowRuns } from "@/lib/api";
import type { WorkflowRunSummary } from "@/types/locus";

export default async function MemoryPage() {
  let runs: WorkflowRunSummary[] = [];
  let error: string | null = null;
  try {
    runs = await getWorkflowRuns();
  } catch (reason) {
    error = reason instanceof Error ? reason.message : "Unable to load runs.";
  }
  return <MemoryScreen initialRuns={runs} initialError={error} />;
}
