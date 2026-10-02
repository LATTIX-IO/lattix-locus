import { getWorkflowDefinition, getWorkflowDefinitions } from "@/lib/api";
import { WorkflowStudioClient } from "./page.client";

export default async function WorkflowStudioPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: workflowId } = await params;
  const workflows = await getWorkflowDefinitions();
  const listedWorkflow = workflows.find((workflow) => workflow.id === workflowId);
  const selectedWorkflow = await getWorkflowDefinition(workflowId);
  const workflowName = selectedWorkflow?.name || listedWorkflow?.name || "Workflow";

  return (
    <WorkflowStudioClient
      workflowId={workflowId}
      workflowName={workflowName}
      initialGraph={selectedWorkflow?.graph_json}
      initialSecurity={selectedWorkflow?.security_config}
      initialGeneratedArtifacts={selectedWorkflow?.generated_artifacts}
    />
  );
}
