"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { getPublishedWorkflows, createWorkflowRun } from "@/lib/api";
import type { WorkflowDefinition } from "@/types/locus";
import { useEffect } from "react";

export default function WorkflowStartPage() {
  const router = useRouter();
  const [workflows, setWorkflows] = useState<WorkflowDefinition[]>([]);
  const [startingId, setStartingId] = useState<string | null>(null);
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getPublishedWorkflows()
      .then(setWorkflows)
      .catch((error: unknown) => setLoadError(error instanceof Error ? error.message : "Unable to load workflows."));
  }, []);

  async function onStart(workflowId: string) {
    setStartingId(workflowId);
    setStartError(null);
    try {
      const result = await createWorkflowRun({ workflow_definition_id: workflowId, session_kind: "workflow" });
      setLastRunId(result.id);
      router.push(`/activity?session=${encodeURIComponent(result.id)}`);
    } catch (error) {
      const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
      setStartError(`Unable to start workflow run.${detail}`);
    } finally {
      setStartingId(null);
    }
  }

  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold">Workflow Catalog</h1>
        <p className="fx-muted">Start a published workflow with guided intake and approval-aware execution.</p>
      </header>

      {loadError ? (
        <p role="alert" className="text-sm text-[var(--fx-danger)]">
          Could not load published workflows: {loadError}
        </p>
      ) : null}

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Workflow</th>
              <th className="px-3 py-2 text-left">Version</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2 text-left">Workflow ID</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {workflows.map((workflow) => (
              <tr key={workflow.id} className="border-t border-[var(--fx-border)]">
                <td className="px-3 py-2 text-[var(--foreground)]">{workflow.name}</td>
                <td className="px-3 py-2 text-[var(--foreground)]">v{workflow.version}</td>
                <td className="fx-muted px-3 py-2">{workflow.description}</td>
                <td className="px-3 py-2 font-mono text-xs text-[var(--foreground)]">{workflow.id}</td>
                <td className="px-3 py-2 text-right">
                  <div className="flex justify-end gap-2">
                    <Link className="fx-btn-secondary px-2.5 py-1 text-xs font-medium" href={`/workflows/${workflow.id}`}>
                      Open
                    </Link>
                    <button
                      onClick={() => onStart(workflow.id)}
                      disabled={startingId === workflow.id}
                      className="fx-btn-primary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                    >
                      {startingId === workflow.id ? "Starting..." : "Start"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {lastRunId ? (
        <div className="fx-panel flex items-center justify-between p-3 text-sm">
          <p className="fx-muted">Workflow run started: <span className="font-mono text-[var(--foreground)]">{lastRunId}</span></p>
          <Link className="fx-btn-secondary px-3 py-1.5 text-xs" href={`/activity?session=${encodeURIComponent(lastRunId)}`}>
            Open run
          </Link>
        </div>
      ) : null}

      {startError ? <p className="text-xs text-[var(--fx-danger)]">{startError}</p> : null}
    </section>
  );
}
