import { describe, expect, it } from "vitest";

import { getNodePorts, resolveNodePortAlias } from "@/lib/frontier-node-schema";

const NODE_TYPES = [
  "frontier/trigger",
  "frontier/prompt",
  "frontier/goal",
  "frontier/evidence",
  "frontier/agent",
  "frontier/assembly",
  "frontier/commitment",
  "frontier/workflow",
  "frontier/tool-call",
  "frontier/retrieval",
  "frontier/memory",
  "frontier/guardrail",
  "frontier/human-review",
  "frontier/manifold",
  "frontier/router",
  "frontier/iterator",
  "frontier/transform",
  "frontier/event",
  "frontier/data-store",
  "frontier/error-handler",
  "frontier/wait",
  "frontier/output",
] as const;

describe("frontier-node-schema canonical ports", () => {
  it.each(NODE_TYPES)("%s has control-plane ports", (nodeType) => {
    const ports = getNodePorts(nodeType);
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames.length).toBeGreaterThan(0);
    expect(outputNames.length).toBeGreaterThan(0);
    expect(outputNames).toContain("out");
  });

  it("agent defines expected canonical data ports", () => {
    const ports = getNodePorts("frontier/agent");
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames).toEqual(expect.arrayContaining(["in", "prompt", "context", "retrieval", "memory", "tool_result", "guardrail"]));
    expect(outputNames).toEqual(expect.arrayContaining(["out", "response", "retrieval_query", "tool_request", "state_delta", "memory", "guardrail"]));
  });

  it("guardrail defines expected canonical ports", () => {
    const ports = getNodePorts("frontier/guardrail");
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames).toEqual(expect.arrayContaining(["in", "candidate_output", "context"]));
    expect(outputNames).toEqual(expect.arrayContaining(["out", "approved_output", "violations", "decision"]));
  });

  it("cognitive MVP nodes define expected canonical ports", () => {
    expect(getNodePorts("frontier/goal").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "goal"]));
    expect(getNodePorts("frontier/evidence").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "evidence"]));
    expect(getNodePorts("frontier/assembly").inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "goal", "evidence"]));
    expect(getNodePorts("frontier/assembly").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "synthesis", "commitment", "dissent"]));
    expect(getNodePorts("frontier/commitment").inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "commitment"]));
    expect(getNodePorts("frontier/commitment").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "result"]));
  });

  it("router, transform, and error-handler expose deterministic data ports", () => {
    const router = getNodePorts("frontier/router");
    const iterator = getNodePorts("frontier/iterator");
    const transform = getNodePorts("frontier/transform");
    const event = getNodePorts("frontier/event");
    const dataStore = getNodePorts("frontier/data-store");
    const errorHandler = getNodePorts("frontier/error-handler");
    const wait = getNodePorts("frontier/wait");

    expect(router.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "candidate", "context"]));
    expect(router.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "match_a", "match_b", "default", "decision", "matched_payload"]));

    expect(iterator.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "items", "context"]));
    expect(iterator.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "loop", "done", "item", "aggregate"]));

    expect(transform.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "source", "context"]));
    expect(transform.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "result"]));

    expect(event.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "payload", "context"]));
    expect(event.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "resume", "idle", "event", "receipt"]));

    expect(dataStore.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "record", "context"]));
    expect(dataStore.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "result", "status"]));

    expect(errorHandler.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "error", "context"]));
    expect(errorHandler.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "handled", "status"]));

    expect(wait.inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "resume_payload"]));
    expect(wait.outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "resume", "timeout", "result"]));
  });
});

describe("frontier-node-schema alias resolution", () => {
  it("maps output aliases for agent", () => {
    expect(resolveNodePortAlias("frontier/agent", "output", "tool_api")).toBe("tool_request");
    expect(resolveNodePortAlias("frontier/agent", "output", "query")).toBe("retrieval_query");
  });

  it("maps input aliases for tool-call and output nodes", () => {
    expect(resolveNodePortAlias("frontier/tool-call", "input", "tool_input")).toBe("request");
    expect(resolveNodePortAlias("frontier/output", "input", "approved_output")).toBe("result");
    expect(resolveNodePortAlias("frontier/output", "input", "payload")).toBe("result");
  });

  it("maps aliases for cognitive MVP nodes", () => {
    expect(resolveNodePortAlias("frontier/goal", "output", "belief")).toBe("goal");
    expect(resolveNodePortAlias("frontier/evidence", "output", "claims")).toBe("evidence");
    expect(resolveNodePortAlias("frontier/assembly", "output", "proposal")).toBe("commitment");
    expect(resolveNodePortAlias("frontier/commitment", "input", "proposal")).toBe("commitment");
    expect(resolveNodePortAlias("frontier/commitment", "output", "published")).toBe("result");
  });

  it("maps input aliases for router, transform, and error-handler", () => {
    expect(resolveNodePortAlias("frontier/router", "input", "payload")).toBe("candidate");
    expect(resolveNodePortAlias("frontier/iterator", "input", "payload")).toBe("items");
    expect(resolveNodePortAlias("frontier/transform", "input", "payload")).toBe("source");
    expect(resolveNodePortAlias("frontier/event", "input", "result")).toBe("payload");
    expect(resolveNodePortAlias("frontier/data-store", "input", "payload")).toBe("record");
    expect(resolveNodePortAlias("frontier/error-handler", "input", "result")).toBe("error");
    expect(resolveNodePortAlias("frontier/wait", "input", "payload")).toBe("resume_payload");
  });

  it("falls back safely when unknown alias is provided", () => {
    expect(resolveNodePortAlias("frontier/output", "input", "unknown_port")).toBe("in");
  });
});
