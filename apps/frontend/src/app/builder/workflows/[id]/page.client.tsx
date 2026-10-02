"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { getAgentDefinitions, publishWorkflowDefinition, saveWorkflowDefinition } from "@/lib/api";
import { SecurityScopeEditor } from "@/components/security-scope-editor";
import type { GraphLink, GraphNode } from "@/components/reactflow-canvas";
import type { AgentDefinition, GeneratedCodeArtifact, SecurityScopeConfig } from "@/types/frontier";

const StudioFullCanvas = dynamic(
  () => import("@/components/studio-full-canvas").then((m) => m.StudioFullCanvas),
  { loading: () => <div className="flex min-h-[40vh] items-center justify-center"><span className="text-xs" style={{ color: "var(--fx-muted)" }}>Loading canvas...</span></div> },
);

type Props = {
  workflowId: string;
  workflowName: string;
  initialGraph?: {
    nodes?: GraphNode[];
    links?: GraphLink[];
  };
  initialSecurity?: SecurityScopeConfig;
  initialGeneratedArtifacts?: GeneratedCodeArtifact[];
};

export function WorkflowStudioClient({ workflowId, workflowName, initialGraph, initialSecurity, initialGeneratedArtifacts }: Props) {
  const router = useRouter();
  const [securityConfig, setSecurityConfig] = useState<SecurityScopeConfig>(initialSecurity ?? {});
  const [agentDefinitions, setAgentDefinitions] = useState<AgentDefinition[]>([]);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const defaultNodes: GraphNode[] = [
    { id: "trigger", title: "Trigger", type: "trigger", x: 70, y: 90 },
    { id: "agent", title: "Agent", type: "agent", x: 330, y: 90 },
    { id: "retrieval", title: "Retrieval", type: "retrieval", x: 610, y: 90 },
    { id: "guardrail", title: "Guardrail", type: "guardrail", x: 860, y: 90 },
    { id: "output", title: "Output", type: "output", x: 1110, y: 90 },
  ];
  const defaultLinks: GraphLink[] = [
    { from: "trigger", to: "agent" },
    { from: "agent", to: "retrieval" },
    { from: "retrieval", to: "guardrail" },
    { from: "guardrail", to: "output" },
  ];

  const initialNodes = initialGraph?.nodes?.length ? initialGraph.nodes : defaultNodes;
  const initialLinks = initialGraph?.links?.length ? initialGraph.links : defaultLinks;
  const agentIdOptions = useMemo(() => agentDefinitions.map((agent) => agent.id), [agentDefinitions]);
  const openAgentBuilder = (agentId: string) => {
    router.push(`/builder/agents/${encodeURIComponent(agentId)}?returnTo=${encodeURIComponent(`/builder/workflows/${workflowId}`)}`);
  };
  const selectedAgentId = typeof selectedNode?.config?.agent_id === "string" ? selectedNode.config.agent_id : "";
  const selectedAgentDefinition = useMemo(
    () => agentDefinitions.find((agent) => agent.id === selectedAgentId) ?? null,
    [agentDefinitions, selectedAgentId],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadAgents() {
      const definitions = await getAgentDefinitions();
      if (!cancelled) {
        setAgentDefinitions(definitions);
      }
    }

    void loadAgents();
    const handleFocus = () => {
      void loadAgents();
    };
    window.addEventListener("focus", handleFocus);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", handleFocus);
    };
  }, []);

  const nodeInspector = (
    <div className="space-y-4">
      <SecurityScopeEditor
        entityType="workflow"
        entityId={workflowId}
        entityName={workflowName}
        value={securityConfig}
        onChange={setSecurityConfig}
        onSave={async () => {
          await saveWorkflowDefinition({
            id: workflowId,
            name: workflowName,
            security_config: securityConfig,
          });
        }}
      />

      <section className="space-y-2 border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3">
        <h3 className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Selected Node</h3>
        {selectedNode ? (
          <>
            <div>
              <p className="text-sm font-semibold text-[var(--foreground)]">{selectedNode.title}</p>
              <p className="text-[0.72rem] text-[var(--fx-muted)]">{selectedNode.type}</p>
            </div>
            {selectedNode.type === "agent" || selectedNode.type === "frontier/agent" ? (
              <div className="space-y-2">
                <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-surface)] p-2 text-[0.78rem]">
                  <p className="text-[0.68rem] uppercase tracking-[0.1em] text-[var(--fx-muted)]">Bound Agent</p>
                  <p className="mt-1 font-medium text-[var(--foreground)]">{selectedAgentDefinition?.name ?? (selectedAgentId || "No agent selected")}</p>
                  {selectedAgentDefinition ? <p className="mt-1 text-[0.72rem] text-[var(--fx-muted)]">v{selectedAgentDefinition.version} · {selectedAgentDefinition.status}</p> : null}
                </div>
                {selectedAgentId ? (
                  <button
                    type="button"
                    onClick={() => openAgentBuilder(selectedAgentId)}
                    className="fx-btn-secondary w-full px-3 py-2 text-xs font-medium"
                  >
                    Edit Agent In Builder
                  </button>
                ) : (
                  <p className="text-[0.72rem] text-[var(--fx-muted)]">Assign a saved agent to this node using the agent_id field on the node, then edit it here.</p>
                )}
              </div>
            ) : (
              <p className="text-[0.78rem] text-[var(--fx-muted)]">Select an agent node to jump into the underlying agent builder while staying inside this workflow authoring flow.</p>
            )}
          </>
        ) : (
          <p className="text-[0.78rem] text-[var(--fx-muted)]">Click a node on the canvas to inspect it here.</p>
        )}
      </section>
    </div>
  );

  return (
    <StudioFullCanvas
      entityType="workflow"
      entityId={workflowId}
      entityName={workflowName}
      builderMode="standard"
      description="Compose multiple agents, tools, retrieval, and controls into one outcome-focused workflow. This graph is the execution contract persisted by the orchestration runtime."
      initialNodes={initialNodes}
      initialLinks={initialLinks}
      initialGeneratedArtifacts={initialGeneratedArtifacts}
      rightSidebarSlot={nodeInspector}
      externalWidgetOptionOverrides={{ agent: { agent_id: agentIdOptions } }}
      onNodeSelected={setSelectedNode}
      onEditAgent={openAgentBuilder}
      onSave={async (graph) => {
        await saveWorkflowDefinition({
          id: workflowId,
          name: workflowName,
          graph_json: graph,
          security_config: securityConfig,
        });
      }}
      onPublish={async () => {
        await publishWorkflowDefinition(workflowId);
      }}
    />
  );
}
