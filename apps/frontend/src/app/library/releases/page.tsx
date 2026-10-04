import { ReleasesWorkspace } from "@/components/releases-workspace";
import { getAgentDefinitions, getGuardrailRulesets, getWorkflowDefinitions } from "@/lib/api";

export default async function ReleasesPage() {
  const [workflows, agents, guardrails] = await Promise.all([
    getWorkflowDefinitions(),
    getAgentDefinitions(),
    getGuardrailRulesets(),
  ]);

  return <ReleasesWorkspace workflows={workflows} agents={agents} guardrails={guardrails} />;
}
