"use client";

import dynamic from "next/dynamic";
import { publishAgentDefinition, saveAgentDefinition } from "@/lib/api";
import type { GraphLink, GraphNode } from "@/components/reactflow-canvas";

const StudioFullCanvas = dynamic(
  () => import("@/components/studio-full-canvas").then((m) => m.StudioFullCanvas),
  { loading: () => <div className="flex min-h-[40vh] items-center justify-center"><span className="text-xs" style={{ color: "var(--fx-muted)" }}>Loading canvas...</span></div> },
);

type Props = {
  agentId: string;
  agentName: string;
  initialGraph?: {
    nodes?: GraphNode[];
    links?: GraphLink[];
  };
  returnHref?: string;
};

export function AgentStudioClient({ agentId, agentName, initialGraph, returnHref }: Props) {
  const defaultNodes: GraphNode[] = [
    { id: "trigger", title: "Trigger", type: "trigger", x: 70, y: 90 },
    { id: "agent", title: "Agent", type: "agent", x: 330, y: 90 },
    { id: "tool-call", title: "Tool / API Call", type: "tool-call", x: 610, y: 90 },
    { id: "output", title: "Output", type: "output", x: 860, y: 90 },
  ];
  const defaultLinks: GraphLink[] = [
    { from: "trigger", to: "agent" },
    { from: "agent", to: "tool-call" },
    { from: "tool-call", to: "output" },
  ];

  const initialNodes = initialGraph?.nodes?.length ? initialGraph.nodes : defaultNodes;
  const initialLinks = initialGraph?.links?.length ? initialGraph.links : defaultLinks;

  return (
    <StudioFullCanvas
      entityType="agent"
      entityId={agentId}
      entityName={agentName}
      builderMode="standard"
      description="Model the smallest reusable specialist unit here. Agents hold their own prompt, tools, memory, and handoff behavior, then plug into workflows and playbooks."
      initialNodes={initialNodes}
      initialLinks={initialLinks}
      returnAction={returnHref ? { label: "Save & Return", href: returnHref } : undefined}
      onSave={async (graph) => {
        await saveAgentDefinition({
          id: agentId,
          name: agentName,
          config_json: {
            schema_version: "frontier-agent-definition/1.0",
            source_agent_id: agentId,
            meta: {
              name: agentName,
            },
            graph_json: graph,
          },
        });
      }}
      onPublish={async () => {
        await publishAgentDefinition(agentId);
      }}
    />
  );
}
