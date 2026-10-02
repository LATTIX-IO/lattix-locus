import { notFound } from "next/navigation";
import { TasksChatWorkspace } from "@/components/tasks-chat-workspace";
import { getWorkflowRun, getWorkflowRunEvents, getWorkflowRuns } from "@/lib/api";

type Props = {
  params: Promise<{ id: string }>;
};

export default async function TaskChatPage({ params }: Props) {
  const { id } = await params;
  const runs = await getWorkflowRuns();
  const run = runs.find((r) => r.id === id);
  if (!run) {
    notFound();
  }

  const [detail, events] = await Promise.all([
    getWorkflowRun(id).catch(() => null),
    getWorkflowRunEvents(id).catch(() => []),
  ]);

  return (
    <TasksChatWorkspace
      run={run}
      initialDetail={detail}
      initialEvents={events}
    />
  );
}
