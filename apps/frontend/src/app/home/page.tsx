import { CommandCenter } from "@/components/command-center";
import { getInbox, getWorkflowRuns } from "@/lib/api";

export default async function HomePage() {
  const [runs, inbox] = await Promise.all([getWorkflowRuns(), getInbox()]);
  return <CommandCenter initialRuns={runs} initialInbox={inbox} />;
}
