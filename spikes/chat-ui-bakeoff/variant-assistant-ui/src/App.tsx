/**
 * Variant A: assistant-ui primitives + @assistant-ui/react-ag-ui, no Node runtime.
 * Sections marked ADAPTER-GLUE / HITL-GLUE / GENUI-GLUE / THEME-GLUE are counted in the report.
 */
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePartPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { useAgUiInterrupts, useAgUiRuntime, useAgUiState, useAgUiSubmitInterruptResponses } from "@assistant-ui/react-ag-ui";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { useMemo } from "react";
import {
  ALLOWLIST,
  ApprovalCard,
  BlockedComponent,
  Button,
  PlanPanel,
  RunSummaryCard,
  Shell,
  ToolCallCard,
  parseRunSummary,
  parseSpec,
  scenarioFromUrl,
  type ToolStatus,
} from "@bakeoff/shared";
import { MockAgUiAgent } from "./agent";

// ---- ADAPTER-GLUE start
function statusOf(p: ToolCallMessagePartProps): ToolStatus {
  if (p.status.type === "running") return p.argsText && p.result === undefined ? "running" : "streaming";
  if (p.status.type === "requires-action") return "awaiting-approval";
  if (p.isError || p.status.type === "incomplete") return p.result === undefined ? "awaiting-approval" : "error";
  return "complete";
}
const resultText = (r: unknown) => (r === undefined ? undefined : typeof r === "string" ? r : JSON.stringify(r));
// ---- ADAPTER-GLUE end

function GenericTool(p: ToolCallMessagePartProps) {
  return <ToolCallCard name={p.toolName} argsText={p.argsText} status={statusOf(p)} result={resultText(p.result)} />;
}

// Tier 1: typed card, validated; falls back to the generic card if the result does not validate.
function SummaryTool(p: ToolCallMessagePartProps) {
  const summary = parseRunSummary(p.result);
  return summary ? <RunSummaryCard summary={summary} /> : <GenericTool {...p} />;
}

// ---- GENUI-GLUE start
// Tier 2: assistant-ui's own allowlist renderer (MessagePrimitive.GenerativeUI), fed our registry.
function DeclarativeTool(p: ToolCallMessagePartProps) {
  const spec = parseSpec(p.result);
  if (!spec) return <GenericTool {...p} />;
  return (
    <div data-tier="2">
      <MessagePrimitive.GenerativeUI spec={spec as never} components={ALLOWLIST as never} Fallback={BlockedComponent} />
    </div>
  );
}
// ---- GENUI-GLUE end

// ---- HITL-GLUE start
function ApprovalTool(p: ToolCallMessagePartProps) {
  const interrupts = useAgUiInterrupts();
  const submit = useAgUiSubmitInterruptResponses();
  const pending = interrupts.find((i) => i.toolCallId === p.toolCallId);
  if (!pending) return <GenericTool {...p} />;
  return (
    <ToolCallCard name={p.toolName} argsText={p.argsText} status="awaiting-approval">
      <ApprovalCard
        request={{ interruptId: pending.id, message: pending.message ?? "Approve this action?", action: p.toolName, toolCallId: p.toolCallId, argsText: p.argsText }}
        onDecision={(r) =>
          // The promise rejects when the resumed run fails; the thread already renders that error.
          submit([{ interruptId: pending.id, status: r.approved ? "resolved" : "cancelled", payload: { approved: r.approved, proof: r.proof } }]).catch(() => {})
        }
      />
    </ToolCallCard>
  );
}
// ---- HITL-GLUE end

// ---- UI-COMPOSITION start
const TOOLS = { by_name: { summarize_run: SummaryTool, render_card: DeclarativeTool, send_email: ApprovalTool }, Fallback: GenericTool };

// Ablation switch for the bench (?md=0): plain text instead of markdown, to isolate parse cost.
const PLAIN_TEXT = new URLSearchParams(globalThis.location?.search ?? "").get("md") === "0"; // bench-only

function MarkdownText() {
  if (PLAIN_TEXT) return <p className="whitespace-pre-wrap"><MessagePartPrimitive.Text /></p>; // bench-only
  return <MarkdownTextPrimitive smooth={false} className="prose-sm [&_li]:ml-4 [&_ul]:list-disc [&_h2]:text-base [&_h2]:font-semibold [&_p]:my-2" />;
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="my-3 flex justify-end">
      <div className="max-w-[80%] rounded-[var(--radius)] bg-primary px-3 py-2 text-primary-foreground">
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="my-3 max-w-[90%]">
      <MessagePrimitive.Parts components={{ Text: MarkdownText, tools: TOOLS }} />
      <MessagePrimitive.Error>
        <ErrorPrimitive.Root role="alert" className="my-2 rounded-[var(--radius)] border border-destructive bg-card p-3 text-sm text-destructive" data-run-error>
          <strong>Run failed.</strong> <ErrorPrimitive.Message />
        </ErrorPrimitive.Root>
      </MessagePrimitive.Error>
    </MessagePrimitive.Root>
  );
}

function Thread() {
  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto px-4" aria-label="Conversation" role="log">
        <ThreadPrimitive.Empty>
          <p className="mt-8 text-center text-muted-foreground">Send any message to start the mock run.</p>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
      </ThreadPrimitive.Viewport>
      <ComposerPrimitive.Root className="flex gap-2 border-t border-border p-3">
        <ComposerPrimitive.Input
          aria-label="Message"
          placeholder="Ask Locus…"
          className="min-h-9 flex-1 resize-none rounded-md border border-input bg-card px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring"
        />
        <ComposerPrimitive.Send asChild>
          <Button>Send</Button>
        </ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}
// ---- UI-COMPOSITION end

function StateAside() {
  return <PlanPanel state={useAgUiState()} />;
}

export function App() {
  const { scenario, pace } = scenarioFromUrl();
  // ---- ADAPTER-GLUE start
  const agent = useMemo(() => new MockAgUiAgent(scenario, pace), [scenario, pace]);
  // Cast: the adapter's types come from its own @ag-ui/client 0.0.59, ours is 1.0.2.
  const runtime = useAgUiRuntime({ agent: agent as never });
  // ---- ADAPTER-GLUE end
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Shell variant="assistant-ui" chat={<Thread />} aside={<StateAside />} />
    </AssistantRuntimeProvider>
  );
}
