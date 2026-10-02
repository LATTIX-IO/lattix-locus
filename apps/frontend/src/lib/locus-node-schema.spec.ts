import { describe, expect, it } from "vitest";

import { getNodePorts, resolveNodePortAlias } from "@/lib/locus-node-schema";

const NODE_TYPES = [
  "locus/trigger",
  "locus/prompt",
  "locus/goal",
  "locus/evidence",
  "locus/agent",
  "locus/assembly",
  "locus/commitment",
  "locus/workflow",
  "locus/tool-call",
  "locus/retrieval",
  "locus/memory",
  "locus/guardrail",
  "locus/human-review",
  "locus/manifold",
  "locus/router",
  "locus/iterator",
  "locus/transform",
  "locus/event",
  "locus/data-store",
  "locus/error-handler",
  "locus/wait",
  "locus/output",
] as const;

describe("locus-node-schema canonical ports", () => {
  it.each(NODE_TYPES)("%s has control-plane ports", (nodeType) => {
    const ports = getNodePorts(nodeType);
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames.length).toBeGreaterThan(0);
    expect(outputNames.length).toBeGreaterThan(0);
    expect(outputNames).toContain("out");
  });

  it("agent defines expected canonical data ports", () => {
    const ports = getNodePorts("locus/agent");
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames).toEqual(expect.arrayContaining(["in", "prompt", "context", "retrieval", "memory", "tool_result", "guardrail"]));
    expect(outputNames).toEqual(expect.arrayContaining(["out", "response", "retrieval_query", "tool_request", "state_delta", "memory", "guardrail"]));
  });

  it("guardrail defines expected canonical ports", () => {
    const ports = getNodePorts("locus/guardrail");
    const inputNames = ports.inputs.map((item) => item.name);
    const outputNames = ports.outputs.map((item) => item.name);

    expect(inputNames).toEqual(expect.arrayContaining(["in", "candidate_output", "context"]));
    expect(outputNames).toEqual(expect.arrayContaining(["out", "approved_output", "violations", "decision"]));
  });

  it("cognitive MVP nodes define expected canonical ports", () => {
    expect(getNodePorts("locus/goal").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "goal"]));
    expect(getNodePorts("locus/evidence").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "evidence"]));
    expect(getNodePorts("locus/assembly").inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "goal", "evidence"]));
    expect(getNodePorts("locus/assembly").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "synthesis", "commitment", "dissent"]));
    expect(getNodePorts("locus/commitment").inputs.map((item) => item.name)).toEqual(expect.arrayContaining(["in", "commitment"]));
    expect(getNodePorts("locus/commitment").outputs.map((item) => item.name)).toEqual(expect.arrayContaining(["out", "result"]));
  });

  it("router, transform, and error-handler expose deterministic data ports", () => {
    const router = getNodePorts("locus/router");
    const iterator = getNodePorts("locus/iterator");
    const transform = getNodePorts("locus/transform");
    const event = getNodePorts("locus/event");
    const dataStore = getNodePorts("locus/data-store");
    const errorHandler = getNodePorts("locus/error-handler");
    const wait = getNodePorts("locus/wait");

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

describe("locus-node-schema alias resolution", () => {
  it("maps output aliases for agent", () => {
    expect(resolveNodePortAlias("locus/agent", "output", "tool_api")).toBe("tool_request");
    expect(resolveNodePortAlias("locus/agent", "output", "query")).toBe("retrieval_query");
  });

  it("maps input aliases for tool-call and output nodes", () => {
    expect(resolveNodePortAlias("locus/tool-call", "input", "tool_input")).toBe("request");
    expect(resolveNodePortAlias("locus/output", "input", "approved_output")).toBe("result");
    expect(resolveNodePortAlias("locus/output", "input", "payload")).toBe("result");
  });

  it("maps aliases for cognitive MVP nodes", () => {
    expect(resolveNodePortAlias("locus/goal", "output", "belief")).toBe("goal");
    expect(resolveNodePortAlias("locus/evidence", "output", "claims")).toBe("evidence");
    expect(resolveNodePortAlias("locus/assembly", "output", "proposal")).toBe("commitment");
    expect(resolveNodePortAlias("locus/commitment", "input", "proposal")).toBe("commitment");
    expect(resolveNodePortAlias("locus/commitment", "output", "published")).toBe("result");
  });

  it("maps input aliases for router, transform, and error-handler", () => {
    expect(resolveNodePortAlias("locus/router", "input", "payload")).toBe("candidate");
    expect(resolveNodePortAlias("locus/iterator", "input", "payload")).toBe("items");
    expect(resolveNodePortAlias("locus/transform", "input", "payload")).toBe("source");
    expect(resolveNodePortAlias("locus/event", "input", "result")).toBe("payload");
    expect(resolveNodePortAlias("locus/data-store", "input", "payload")).toBe("record");
    expect(resolveNodePortAlias("locus/error-handler", "input", "result")).toBe("error");
    expect(resolveNodePortAlias("locus/wait", "input", "payload")).toBe("resume_payload");
  });

  it("falls back safely when unknown alias is provided", () => {
    expect(resolveNodePortAlias("locus/output", "input", "unknown_port")).toBe("in");
  });
});
