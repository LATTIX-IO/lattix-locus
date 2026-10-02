import { describe, expect, it } from "vitest";

import { isTerminalRunStatus, runStreamReconnectDelayMs } from "@/lib/run-stream";

describe("runStreamReconnectDelayMs", () => {
  it("backs off exponentially and stays bounded", () => {
    expect([0, 1, 2, 3].map(runStreamReconnectDelayMs)).toEqual([1000, 2000, 4000, 8000]);
    expect(runStreamReconnectDelayMs(4)).toBe(15000);
    expect(runStreamReconnectDelayMs(50)).toBe(15000);
    expect(runStreamReconnectDelayMs(-3)).toBe(1000);
  });
});

describe("isTerminalRunStatus", () => {
  it("recognises terminal statuses case-insensitively", () => {
    for (const status of ["Done", "Failed", "Blocked", "Canceled", "cancelled", "Archived"]) {
      expect(isTerminalRunStatus(status)).toBe(true);
    }
  });

  it("treats live or unknown statuses as non-terminal", () => {
    for (const status of ["Running", "Needs Review", "queued", "", null, undefined]) {
      expect(isTerminalRunStatus(status)).toBe(false);
    }
  });
});
