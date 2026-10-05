import { notFound } from "next/navigation";
import { WorkflowPipelineDetail } from "@/components/workflow-pipeline";
import { getPublishedWorkflows } from "@/lib/api";
import { WorkflowTriggersManager } from "@/components/workflow-triggers-manager";

type Props = {
  params: Promise<{ id: string }>;
};

export default async function WorkflowDetailPage({ params }: Props) {
  const { id } = await params;
  const workflows = await getPublishedWorkflows();
  const workflow = workflows.find((item) => item.id === id);

  if (!workflow) {
    notFound();
  }

  return (
    <section className="space-y-4">
      <WorkflowPipelineDetail workflow={workflow} />
      <WorkflowTriggersManager
        workflowId={workflow.id}
        apiBaseHint={process.env.NEXT_PUBLIC_API_BASE_URL ?? ""}
      />
    </section>
  );
}
