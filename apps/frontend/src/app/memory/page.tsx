import { MemoryScreen } from "@/components/memory-screen";
import { getWorkflowRuns } from "@/lib/api";

export default async function MemoryPage() {
  const runs = await getWorkflowRuns();
  return <MemoryScreen initialRuns={runs} />;
}
