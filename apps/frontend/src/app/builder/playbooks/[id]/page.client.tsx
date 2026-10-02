"use client";

import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { GraphLink, GraphNode } from "@/components/reactflow-canvas";
import { getPlaybook, getWorkflowDefinitions, savePlaybook } from "@/lib/api";
import type { PlaybookDefinition, WorkflowDefinition } from "@/types/frontier";

const StudioFullCanvas = dynamic(
  () => import("@/components/studio-full-canvas").then((module) => module.StudioFullCanvas),
  { loading: () => <div className="flex min-h-[40vh] items-center justify-center"><span className="text-xs" style={{ color: "var(--fx-muted)" }}>Loading canvas...</span></div> },
);

type Props = {
  playbookId: string;
  initialPlaybook: PlaybookDefinition | null;
  isNew: boolean;
};

const defaultNodes: GraphNode[] = [
  { id: "trigger", title: "Trigger", type: "trigger", x: 70, y: 90 },
  { id: "workflow-primary", title: "Workflow", type: "workflow", x: 380, y: 90, config: { handoff_mode: "blocking" } },
  { id: "manifold", title: "Manifold", type: "manifold", x: 720, y: 90, config: { logic_mode: "OR", min_required: 1 } },
  { id: "output", title: "Output", type: "output", x: 1030, y: 90 },
];

const defaultLinks: GraphLink[] = [
  { from: "trigger", to: "workflow-primary" },
  { from: "workflow-primary", to: "manifold" },
  { from: "manifold", to: "output" },
];

function createPlaybookId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `playbook-${Date.now()}`;
}

function resolveInitialValue(initialPlaybook: PlaybookDefinition | null, key: keyof PlaybookDefinition) {
  return initialPlaybook?.[key];
}

export function PlaybookStudioClient({ playbookId, initialPlaybook, isNew }: Props) {
  const router = useRouter();
  const [workflowDefinitions, setWorkflowDefinitions] = useState<WorkflowDefinition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState((resolveInitialValue(initialPlaybook, "name") as string | undefined) ?? "New Playbook");
  const [description, setDescription] = useState(
    (resolveInitialValue(initialPlaybook, "description") as string | undefined) ?? "Coordinate multi-step operating motions across multiple workflows.",
  );
  const [category, setCategory] = useState<PlaybookDefinition["category"]>(
    (resolveInitialValue(initialPlaybook, "category") as PlaybookDefinition["category"] | undefined) ?? "operations",
  );
  const [status, setStatus] = useState<PlaybookDefinition["status"]>(
    (resolveInitialValue(initialPlaybook, "status") as PlaybookDefinition["status"] | undefined) ?? "draft",
  );
  const [persistedPlaybook, setPersistedPlaybook] = useState<PlaybookDefinition | null>(initialPlaybook);

  useEffect(() => {
    let cancelled = false;

    async function loadWorkflowDefinitions() {
      try {
        const nextWorkflowDefinitions = await getWorkflowDefinitions();
        if (!cancelled) {
          setWorkflowDefinitions(nextWorkflowDefinitions);
          setError(null);
        }
      } catch {
        if (!cancelled) {
          setError("Unable to load workflow definitions.");
        }
      }
    }

    void loadWorkflowDefinitions();
    return () => {
      cancelled = true;
    };
  }, []);

  const activeNodes = persistedPlaybook?.graph_json?.nodes ?? defaultNodes;
  const activeLinks = persistedPlaybook?.graph_json?.links ?? defaultLinks;
  const workflowIdOptions = useMemo(() => workflowDefinitions.map((workflow) => workflow.id), [workflowDefinitions]);
  const canvasEntityId = isNew ? "playbook-draft" : playbookId;

  async function handleSave(graph: { nodes: GraphNode[]; links: GraphLink[] }) {
    setError(null);
    try {
      const requestedId = isNew ? createPlaybookId() : playbookId;
      const saved = await savePlaybook({
        id: requestedId,
        name,
        description,
        category,
        status,
        graph_json: graph,
      });

      const persistedId = saved.id || requestedId;
      setPersistedPlaybook({
        id: persistedId,
        name,
        description,
        category,
        status,
        metadata_json: persistedPlaybook?.metadata_json ?? {},
        graph_json: graph,
      });

      try {
        const detail = await getPlaybook(persistedId);
        if (detail) {
          setPersistedPlaybook(detail);
        }
      } catch {
        // Preserve the successful save state even if the immediate readback fails.
      }

      if (isNew) {
        router.replace(`/builder/playbooks/${persistedId}`);
      }
    } catch {
      setError("Unable to save this playbook.");
      throw new Error("Unable to save this playbook.");
    }
  }

  return (
    <StudioFullCanvas
      key={persistedPlaybook?.id ?? canvasEntityId}
      entityType="playbook"
      entityId={persistedPlaybook?.id ?? canvasEntityId}
      entityName={name}
      builderMode="standard"
      description="Coordinate workflow nodes, branching logic, approvals, and output sinks inside a playbook graph."
      initialNodes={activeNodes}
      initialLinks={activeLinks}
      externalWidgetOptionOverrides={{ workflow: { workflow_id: workflowIdOptions } }}
      rightSidebarSlot={
        <section className="space-y-3">
          {error ? (
            <div className="border border-[#6b1f2a] bg-[#2f1a21] p-3 text-sm text-[#ffb8c4]">
              {error}
            </div>
          ) : null}
          <h3 className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Playbook Settings</h3>
          <label className="block space-y-1 text-xs text-[var(--fx-muted)]">
            <span>Name</span>
            <input aria-label="Name" value={name} onChange={(event) => setName(event.target.value)} className="fx-field w-full px-2 py-1.5 text-sm" />
          </label>
          <label className="block space-y-1 text-xs text-[var(--fx-muted)]">
            <span>Description</span>
            <textarea aria-label="Description" value={description} onChange={(event) => setDescription(event.target.value)} className="fx-field min-h-24 w-full px-2 py-1.5 text-sm" />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block space-y-1 text-xs text-[var(--fx-muted)]">
              <span>Category</span>
              <select value={category} onChange={(event) => setCategory(event.target.value as PlaybookDefinition["category"])} className="fx-field w-full px-2 py-1.5 text-sm">
                <option value="operations">operations</option>
                <option value="security">security</option>
                <option value="support">support</option>
                <option value="go_to_market">go_to_market</option>
                <option value="other">other</option>
              </select>
            </label>
            <label className="block space-y-1 text-xs text-[var(--fx-muted)]">
              <span>Status</span>
              <select value={status} onChange={(event) => setStatus(event.target.value as PlaybookDefinition["status"])} className="fx-field w-full px-2 py-1.5 text-sm">
                <option value="draft">draft</option>
                <option value="published">published</option>
                <option value="archived">archived</option>
              </select>
            </label>
          </div>
        </section>
      }
      onSave={handleSave}
    />
  );
}