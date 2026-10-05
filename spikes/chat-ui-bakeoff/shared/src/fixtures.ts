/**
 * Deterministic AG-UI 1.0 fixture streams for the chat-UI bake-off.
 *
 * Library-agnostic: events are plain wire objects (`{ type: "TEXT_MESSAGE_CONTENT", ... }`),
 * exactly what `ag-ui-langgraph` would put on the SSE stream. Each variant wraps them in its own
 * `@ag-ui/client` AbstractAgent, so no network and no backend are involved.
 *
 * Scenarios (selected with `?scenario=`):
 *   run   - streamed text (~2,000 tokens), state snapshot + deltas, 3 tool calls with results
 *           (generic, tier-1 typed card, tier-2 declarative spec), then a tool-approval interrupt.
 *           Resuming after an approval runs the approved tool through the "gateway" and ends in
 *           RUN_ERROR; resuming after a denial finishes cleanly.
 *   long  - MESSAGES_SNAPSHOT with 200 messages containing 50 tool calls.
 */

export type WireEvent = { type: string } & Record<string, unknown>;

export type WireMessage =
  | { id: string; role: "user"; content: string }
  | {
      id: string;
      role: "assistant";
      content?: string;
      toolCalls?: { id: string; type: "function"; function: { name: string; arguments: string } }[];
    }
  | { id: string; role: "tool"; content: string; toolCallId: string };

export type ResumeEntryLike = { interruptId: string; status: "resolved" | "cancelled"; payload?: unknown };

export type Scenario = "run" | "long";

export const STREAM_TOKENS = 2000;
/** Last token of the streamed answer; the bench waits for it to be visible. */
export const STREAM_SENTINEL = "EOS-2000";
/** Text of the last message of the long thread; the bench waits for it to be visible. */
export const LONG_SENTINEL = "LONG-END-200";
export const INTERRUPT_ID = "int-approve-send-email";
export const APPROVAL_TOOL_CALL_ID = "tc-send-email";
export const ERROR_CODE = "GATEWAY_UPSTREAM_TIMEOUT";

const WORDS = (
  "gateway policy evidence goal commitment memory column agent operator desktop approval scope " +
  "classification runtime trace span budget token model tool result card schema allowlist render " +
  "stream event delta snapshot interrupt resume confirm native shell webview sandbox iframe plan " +
  "step verify audit row decision deny allow refuse write read local first durable retry idempotent"
).split(" ");

/** Small deterministic PRNG (LCG) so every run of a scenario is byte-identical. */
function lcg(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

/** ~2,000 whitespace-delimited tokens of markdown, one AG-UI delta per token. */
export function streamTokens(count = STREAM_TOKENS): string[] {
  const rnd = lcg(42);
  const out: string[] = ["## Weekly operator report\n\n"];
  let sentence = 0;
  for (let i = 1; i < count - 1; i++) {
    const w = WORDS[Math.floor(rnd() * WORDS.length)]!;
    let tok = (sentence === 0 ? w[0]!.toUpperCase() + w.slice(1) : w) + " ";
    sentence++;
    if (sentence > 10 && rnd() < 0.18) {
      tok = w + ". ";
      sentence = 0;
      if (rnd() < 0.15) tok = w + ".\n\n";
      if (rnd() < 0.04) tok = w + ".\n\n- **" + WORDS[i % WORDS.length] + "**: listed item\n- second item\n\n";
    }
    // One fenced code block, as agents emit them; exercises the libraries' code highlighting paths.
    if (i === 1500) tok = "\n\n```ts\nconst approved = await confirmAction(request);\n```\n\n";
    out.push(tok);
  }
  out.push(STREAM_SENTINEL);
  return out;
}

function toolCall(
  id: string,
  name: string,
  args: Record<string, unknown>,
  result: unknown | undefined,
  parentMessageId: string,
): WireEvent[] {
  const json = JSON.stringify(args);
  // Stream the arguments in three chunks, like a model would.
  const a = Math.floor(json.length / 3);
  const ev: WireEvent[] = [
    { type: "TOOL_CALL_START", toolCallId: id, toolCallName: name, parentMessageId },
    { type: "TOOL_CALL_ARGS", toolCallId: id, delta: json.slice(0, a) },
    { type: "TOOL_CALL_ARGS", toolCallId: id, delta: json.slice(a, 2 * a) },
    { type: "TOOL_CALL_ARGS", toolCallId: id, delta: json.slice(2 * a) },
    { type: "TOOL_CALL_END", toolCallId: id },
  ];
  if (result !== undefined) {
    ev.push({
      type: "TOOL_CALL_RESULT",
      messageId: `${id}-result`,
      toolCallId: id,
      role: "tool",
      content: typeof result === "string" ? result : JSON.stringify(result),
    });
  }
  return ev;
}

export const RUN_SUMMARY_RESULT = {
  kind: "locus.run_summary.v1",
  title: "Weekly report run",
  status: "succeeded",
  durationMs: 41250,
  metrics: [
    { label: "Tool calls", value: 3 },
    { label: "Tokens", value: 2000 },
    { label: "Policy denials", value: 0 },
  ],
};

/** Tier 2: declarative spec, rendered only through the component allowlist. */
export const DECLARATIVE_SPEC = {
  root: {
    component: "Card",
    props: { title: "Inbox triage" },
    children: [
      { component: "Text", props: { tone: "muted" }, children: ["12 messages classified, 2 need you."] },
      {
        component: "Stack",
        children: [
          { component: "Badge", props: { variant: "warning" }, children: ["2 waiting"] },
          { component: "Badge", props: { variant: "success" }, children: ["10 filed"] },
        ],
      },
      {
        component: "Table",
        props: {
          columns: ["From", "Subject", "Action"],
          rows: [
            ["finance@example.com", "Invoice 4411", "Needs approval"],
            ["ops@example.com", "Rotation schedule", "Filed"],
          ],
        },
      },
      // Not on the allowlist: must render as a blocked placeholder, never as markup.
      { component: "Script", props: { src: "https://evil.example/x.js" } },
    ],
  },
};

export function runScript(threadId: string, runId: string): WireEvent[] {
  const msgId = `${runId}-a1`;
  const ev: WireEvent[] = [
    { type: "RUN_STARTED", threadId, runId },
    {
      type: "STATE_SNAPSHOT",
      snapshot: {
        plan: [
          { id: "s1", title: "Search the workspace", status: "pending" },
          { id: "s2", title: "Summarise the run", status: "pending" },
          { id: "s3", title: "Triage the inbox", status: "pending" },
          { id: "s4", title: "Send the report", status: "pending" },
        ],
      },
    },
    { type: "STEP_STARTED", stepName: "report" },
    { type: "TEXT_MESSAGE_START", messageId: msgId, role: "assistant" },
  ];
  for (const t of streamTokens()) ev.push({ type: "TEXT_MESSAGE_CONTENT", messageId: msgId, delta: t });
  ev.push({ type: "TEXT_MESSAGE_END", messageId: msgId });

  ev.push(
    ...toolCall("tc-search", "search_files", { query: "weekly report", limit: 5 }, {
      matches: ["reports/2026-w40.md", "reports/2026-w39.md"],
    }, msgId),
    { type: "STATE_DELTA", delta: [{ op: "replace", path: "/plan/0/status", value: "done" }] },
    ...toolCall("tc-summary", "summarize_run", { runId: "run-7f3" }, RUN_SUMMARY_RESULT, msgId),
    { type: "STATE_DELTA", delta: [{ op: "replace", path: "/plan/1/status", value: "done" }] },
    ...toolCall("tc-triage", "render_card", { surface: "inbox" }, DECLARATIVE_SPEC, msgId),
    { type: "STATE_DELTA", delta: [{ op: "replace", path: "/plan/2/status", value: "done" }] },
    // The approval: the tool call is streamed, the gateway refuses to run it until the human
    // confirms, and the run finishes with an AG-UI 1.0 interrupt outcome.
    ...toolCall(APPROVAL_TOOL_CALL_ID, "send_email", {
      to: "team@example.com",
      subject: "Weekly operator report",
    }, undefined, msgId),
    { type: "STEP_FINISHED", stepName: "report" },
    {
      type: "RUN_FINISHED",
      threadId,
      runId,
      outcome: {
        type: "interrupt",
        interrupts: [
          {
            id: INTERRUPT_ID,
            reason: "tool_call",
            toolCallId: APPROVAL_TOOL_CALL_ID,
            message: "Send the weekly report to team@example.com?",
            metadata: { action: "send_email", widening: true, risk: "external_write" },
          },
        ],
      },
    },
  );
  return ev;
}

export function resumeScript(threadId: string, runId: string, resume: ResumeEntryLike[]): WireEvent[] {
  const entry = resume.find((r) => r.interruptId === INTERRUPT_ID);
  const approved =
    entry?.status === "resolved" && (entry.payload as { approved?: boolean } | undefined)?.approved === true;
  const msgId = `${runId}-a2`;
  const ev: WireEvent[] = [{ type: "RUN_STARTED", threadId, runId }];
  if (!approved) {
    ev.push(
      { type: "TEXT_MESSAGE_START", messageId: msgId, role: "assistant" },
      { type: "TEXT_MESSAGE_CONTENT", messageId: msgId, delta: "Cancelled in the confirmation dialog. Nothing was sent." },
      { type: "TEXT_MESSAGE_END", messageId: msgId },
      { type: "STATE_DELTA", delta: [{ op: "replace", path: "/plan/3/status", value: "cancelled" }] },
      { type: "RUN_FINISHED", threadId, runId, outcome: { type: "success" } },
    );
    return ev;
  }
  ev.push(
    { type: "STATE_DELTA", delta: [{ op: "replace", path: "/plan/3/status", value: "running" }] },
    { type: "TEXT_MESSAGE_START", messageId: msgId, role: "assistant" },
    { type: "TEXT_MESSAGE_CONTENT", messageId: msgId, delta: "Approved. Sending through the gateway…" },
    { type: "TEXT_MESSAGE_END", messageId: msgId },
    { type: "RUN_ERROR", message: "Gateway: upstream mail relay timed out (simulated).", code: ERROR_CODE },
  );
  return ev;
}

export function longThreadMessages(): WireMessage[] {
  const msgs: WireMessage[] = [];
  for (let t = 0; t < 50; t++) {
    const tc = `lt-tc-${t}`;
    msgs.push({ id: `lt-u-${t}`, role: "user", content: `Step ${t + 1}: check item ${t + 1} of the backlog.` });
    msgs.push({
      id: `lt-a-${t}`,
      role: "assistant",
      content: "",
      toolCalls: [{ id: tc, type: "function", function: { name: "search_files", arguments: JSON.stringify({ query: `item ${t + 1}` }) } }],
    });
    msgs.push({ id: `lt-t-${t}`, role: "tool", toolCallId: tc, content: JSON.stringify({ matches: [`notes/item-${t + 1}.md`] }) });
    msgs.push({
      id: `lt-b-${t}`,
      role: "assistant",
      content: t === 49 ? `Done. ${LONG_SENTINEL}` : `Item ${t + 1} is **filed**; evidence attached to the run trace.`,
    });
  }
  return msgs;
}

export function longScript(threadId: string, runId: string): WireEvent[] {
  return [
    { type: "RUN_STARTED", threadId, runId },
    { type: "MESSAGES_SNAPSHOT", messages: longThreadMessages() },
    { type: "RUN_FINISHED", threadId, runId, outcome: { type: "success" } },
  ];
}

export function scriptFor(
  scenario: Scenario,
  input: { threadId: string; runId: string; resume?: ResumeEntryLike[] | undefined },
): WireEvent[] {
  if (scenario === "long") return longScript(input.threadId, input.runId);
  if (input.resume && input.resume.length > 0) return resumeScript(input.threadId, input.runId, input.resume);
  return runScript(input.threadId, input.runId);
}

/**
 * Plays a script one event per macrotask (pace 0) or with a fixed delay. MessageChannel avoids the
 * 4 ms setTimeout clamp so pace 0 measures render throughput, not timer granularity.
 */
export function playScript(
  events: WireEvent[],
  onEvent: (e: WireEvent) => void,
  onDone: () => void,
  paceMs = 0,
): () => void {
  let i = 0;
  let cancelled = false;
  const channel = new MessageChannel();
  const step = () => {
    if (cancelled) return;
    if (i >= events.length) {
      channel.port1.close();
      onDone();
      return;
    }
    onEvent(events[i++]!);
    schedule();
  };
  const schedule = () => {
    if (paceMs > 0) setTimeout(step, paceMs);
    else channel.port2.postMessage(0);
  };
  channel.port1.onmessage = step;
  schedule();
  return () => {
    cancelled = true;
    channel.port1.close();
  };
}

export function scenarioFromUrl(): { scenario: Scenario; pace: number } {
  const p = new URLSearchParams(globalThis.location?.search ?? "");
  const scenario = p.get("scenario") === "long" ? "long" : "run";
  const pace = Number(p.get("pace") ?? "4");
  return { scenario, pace: Number.isFinite(pace) && pace >= 0 ? pace : 4 };
}
