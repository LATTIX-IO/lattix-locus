import type { RunKind, WorkflowRunKind } from "@/types/frontier";

// The console uses "chat"/"task" for user-initiated runs; the inbox groups them with "individual".
export function normalizeRunKind(kind: RunKind | WorkflowRunKind | undefined): RunKind {
  if (!kind || kind === "chat" || kind === "task") return "individual";
  return kind;
}
