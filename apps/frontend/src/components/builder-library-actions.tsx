"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  archiveAgentDefinition,
  archivePlaybook,
  archiveWorkflowDefinition,
  publishAgentDefinition,
  publishPlaybook,
  publishWorkflowDefinition,
  unpublishAgentDefinition,
  unpublishPlaybook,
  unpublishWorkflowDefinition,
} from "@/lib/api";

type EntityType = "workflow" | "agent" | "playbook";
type LifecycleStatus = "draft" | "published" | "archived";
type LifecycleAction = "publish" | "unpublish" | "archive";

type Props = {
  entityType: EntityType;
  entityId: string;
  entityName: string;
  openHref: string;
  status: LifecycleStatus;
};

const lifecycleRequests: Record<EntityType, Record<LifecycleAction, (id: string) => Promise<{ ok: boolean }>>> = {
  workflow: {
    publish: publishWorkflowDefinition,
    unpublish: unpublishWorkflowDefinition,
    archive: archiveWorkflowDefinition,
  },
  agent: {
    publish: publishAgentDefinition,
    unpublish: unpublishAgentDefinition,
    archive: archiveAgentDefinition,
  },
  playbook: {
    publish: publishPlaybook,
    unpublish: unpublishPlaybook,
    archive: archivePlaybook,
  },
};

const busyLabels: Record<LifecycleAction, string> = {
  publish: "Publishing...",
  unpublish: "Unpublishing...",
  archive: "Archiving...",
};

export function BuilderLibraryActions({ entityType, entityId, entityName, openHref, status }: Props) {
  const router = useRouter();
  const [busyAction, setBusyAction] = useState<LifecycleAction | null>(null);

  const primaryAction: LifecycleAction = status === "published" ? "unpublish" : "publish";
  const primaryLabel = primaryAction === "publish" ? "Publish" : "Unpublish";

  async function runAction(action: LifecycleAction) {
    setBusyAction(action);
    try {
      await lifecycleRequests[entityType][action](entityId);
      router.refresh();
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <div className="flex justify-end gap-2">
      <Link className="fx-btn-primary px-2.5 py-1 text-xs font-medium" href={openHref}>
        Open
      </Link>
      <button
        type="button"
        onClick={() => void runAction(primaryAction)}
        disabled={busyAction !== null}
        aria-label={`${primaryLabel} ${entityName}`}
        className={(primaryAction === "publish" ? "fx-btn-primary" : "fx-btn-secondary") + " px-2.5 py-1 text-xs font-medium disabled:opacity-60"}
      >
        {busyAction === primaryAction ? busyLabels[primaryAction] : primaryLabel}
      </button>
      <button
        type="button"
        onClick={() => void runAction("archive")}
        disabled={busyAction !== null || status === "archived"}
        aria-label={`Archive ${entityName}`}
        className="fx-btn-secondary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
      >
        {busyAction === "archive" ? busyLabels.archive : "Archive"}
      </button>
    </div>
  );
}