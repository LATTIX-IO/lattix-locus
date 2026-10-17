/** Backend-owned outcome lifecycle: no UI may turn a blocked run into a review. */
export type OutcomeReviewSnapshot = {
  status: string;
  approvals?: {
    required?: boolean;
    pending?: boolean;
    artifact_id?: string;
    version?: number;
  };
  artifacts?: ReadonlyArray<{
    id: string;
    version: number;
    status: string;
  }>;
};

export type OutcomeReviewGate =
  | { canDecide: true; artifactId: string; version: number }
  | { canDecide: false; reason: "blocked" | "not-pending" | "missing-artifact" };

/**
 * Advisory visibility decision only. POST /approvals rechecks the same contract
 * authoritatively, and the desktop shell may require a fresh native proof.
 * Blocked is a terminal guardrail outcome, not a paused human approval.
 */
export function getOutcomeReviewGate(run: OutcomeReviewSnapshot | null | undefined): OutcomeReviewGate {
  if (run?.status?.trim().toLowerCase() === "blocked") return { canDecide: false, reason: "blocked" };
  if (run?.status !== "Needs Review" || run.approvals?.required !== true || run.approvals.pending !== true) {
    return { canDecide: false, reason: "not-pending" };
  }
  const { artifact_id: artifactId, version } = run.approvals;
  if (
    typeof artifactId !== "string" ||
    !artifactId.trim() ||
    typeof version !== "number" ||
    !Number.isSafeInteger(version) ||
    version < 1 ||
    !run.artifacts?.some((artifact) => artifact.id === artifactId && artifact.version === version && artifact.status === "Needs Review")
  ) {
    return { canDecide: false, reason: "missing-artifact" };
  }
  return { canDecide: true, artifactId, version };
}
