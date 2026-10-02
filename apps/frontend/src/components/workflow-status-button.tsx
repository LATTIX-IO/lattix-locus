"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { archiveWorkflowDefinition, publishWorkflowDefinition } from "@/lib/api";

type Props = {
  workflowId: string;
  status: "draft" | "published" | "archived";
};

export function WorkflowStatusButton({ workflowId, status }: Props) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runAction() {
    setBusy(true);
    setError(null);
    try {
      if (status === "published") {
        await archiveWorkflowDefinition(workflowId);
      } else {
        await publishWorkflowDefinition(workflowId);
      }
      router.refresh();
    } catch (err) {
      // Surface the reason (e.g. graph validation failure) instead of silently
      // doing nothing — a failed publish must tell the user why.
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      window.alert(`${status === "published" ? "Archive" : "Publish"} failed:\n\n${message}`);
    } finally {
      setBusy(false);
    }
  }

  const isArchive = status === "published";

  return (
    <span className="inline-flex items-center gap-1">
      <button
        onClick={runAction}
        disabled={busy}
        className={(isArchive ? "fx-btn-secondary" : "fx-btn-primary") + " px-2.5 py-1 text-xs font-medium disabled:opacity-60"}
      >
        {busy ? (isArchive ? "Archiving..." : "Publishing...") : (isArchive ? "Archive" : "Publish")}
      </button>
      {error ? (
        <span
          role="alert"
          title={error}
          className="max-w-[10rem] truncate text-[10px] text-[var(--fx-danger,#f87171)]"
        >
          {error}
        </span>
      ) : null}
    </span>
  );
}
