import { describe, expect, it } from "vitest";
import { getOutcomeReviewGate, type OutcomeReviewSnapshot } from "@/lib/run-outcome-review";

const REVIEWABLE: OutcomeReviewSnapshot = {
  status: "Needs Review",
  approvals: { required: true, pending: true, artifact_id: "art-1", version: 2 },
  artifacts: [{ id: "art-1", version: 2, status: "Needs Review" }],
};

describe("outcome review lifecycle", () => {
  it("reviews only the exact pending artifact revision", () => {
    expect(getOutcomeReviewGate(REVIEWABLE)).toEqual({ canDecide: true, artifactId: "art-1", version: 2 });
  });
  it.each(["Blocked", "Failed", "Done", "Archived", "Running", "Canceled"])("never approves status %s", (status) => {
    expect(getOutcomeReviewGate({ ...REVIEWABLE, status }).canDecide).toBe(false);
  });
  it("ignores a forged approval pending flag on guardrail-blocked output", () => {
    expect(getOutcomeReviewGate({ ...REVIEWABLE, status: "Blocked" })).toEqual({ canDecide: false, reason: "blocked" });
  });
  it.each([
    { required: false, pending: true, artifact_id: "art-1", version: 2 },
    { required: true, pending: false, artifact_id: "art-1", version: 2 },
    { required: true, pending: true, artifact_id: "art-2", version: 2 },
    { required: true, pending: true, artifact_id: "art-1", version: 3 },
    { required: true, pending: true, artifact_id: "", version: 2 },
  ])("refuses incomplete or stale reviews %j", (approvals) => {
    expect(getOutcomeReviewGate({ ...REVIEWABLE, approvals }).canDecide).toBe(false);
  });
  it("refuses already-approved artifact data", () => {
    expect(getOutcomeReviewGate({ ...REVIEWABLE, artifacts: [{ id: "art-1", version: 2, status: "Approved" }] }).canDecide).toBe(false);
  });
  it("fails closed while the run detail is unavailable", () => {
    expect(getOutcomeReviewGate(null)).toEqual({ canDecide: false, reason: "not-pending" });
  });
});
