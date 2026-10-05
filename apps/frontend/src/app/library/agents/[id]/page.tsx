import { getAgentDefinition, getAgentDefinitions } from "@/lib/api";
import { AgentStudioClient } from "./page.client";

function resolveReturnHref(returnTo: string | string[] | undefined): string | undefined {
  const candidate = Array.isArray(returnTo) ? returnTo[0] : returnTo;
  if (!candidate) {
    return undefined;
  }
  if (!candidate.startsWith("/") || candidate.startsWith("//") || /^[a-zA-Z][a-zA-Z\d+.-]*:/.test(candidate)) {
    return undefined;
  }
  return candidate;
}

export default async function AgentStudioPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ returnTo?: string | string[] }>;
}) {
  const { id: agentId } = await params;
  const resolvedSearchParams = searchParams ? await searchParams : undefined;
  const returnHref = resolveReturnHref(resolvedSearchParams?.returnTo);
  const agents = await getAgentDefinitions();
  const listedAgent = agents.find((agent) => agent.id === agentId);
  const selectedAgent = await getAgentDefinition(agentId);
  const agentName = selectedAgent?.name || listedAgent?.name || "Agent";
  const initialGraph = selectedAgent?.config_json?.graph_json;

  return <AgentStudioClient agentId={agentId} agentName={agentName} initialGraph={initialGraph} returnHref={returnHref} />;
}
