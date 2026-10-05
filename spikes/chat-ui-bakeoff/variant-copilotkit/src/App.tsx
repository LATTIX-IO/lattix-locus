/**
 * Variant B: CopilotKit v2 API (@copilotkit/react-core/v2 provider, CopilotChat, hooks) with a
 * self-managed AG-UI agent, no @copilotkit/runtime.
 * Sections marked ADAPTER-GLUE / HITL-GLUE / GENUI-GLUE / THEME-GLUE are counted in the report.
 */
import { CopilotChat, CopilotKitProvider, useAgent, useInterrupt, useRenderTool } from "@copilotkit/react-core/v2";
import "@copilotkit/react-core/v2/styles.css";
import { useMemo, useState, type ReactNode } from "react";
import { z } from "zod";
import {
  ApprovalCard,
  DeclarativeView,
  PlanPanel,
  RunErrorNotice,
  RunSummaryCard,
  Shell,
  ToolCallCard,
  parseRunSummary,
  parseSpec,
  scenarioFromUrl,
  type ToolStatus,
} from "@bakeoff/shared";
import { MockAgUiAgent } from "./agent";
import "./copilotkit-theme.css";

const AGENT_ID = "default";
const anyArgs = z.record(z.string(), z.unknown());

// ---- THEME-GLUE start
// Locus components inside the chat need their own token scope (see copilotkit-theme.css).
const Scoped = ({ children }: { children: ReactNode }) => <div className="locus-scope">{children}</div>;
// ---- THEME-GLUE end

// ---- ADAPTER-GLUE start
const statusOf = (s: string, result: unknown): ToolStatus =>
  s === "complete" ? "complete" : s === "executing" ? (result === undefined ? "awaiting-approval" : "running") : "streaming";
type ToolProps = { name: string; toolCallId: string; parameters: unknown; status: string; result: string | undefined };
const argsText = (p: unknown) => (p && Object.keys(p as object).length ? JSON.stringify(p) : undefined);

function GenericTool(p: ToolProps) {
  return <Scoped><ToolCallCard name={p.name} argsText={argsText(p.parameters)} status={statusOf(p.status, p.result)} result={p.result} /></Scoped>;
}
// ---- ADAPTER-GLUE end

function ToolRenderers() {
  useRenderTool({ name: "*", render: (p: ToolProps) => <GenericTool {...p} /> }, []);
  // Tier 1: typed card, validated.
  useRenderTool(
    {
      name: "summarize_run",
      parameters: anyArgs,
      render: (p) => {
        const s = p.status === "complete" ? parseRunSummary(p.result) : null;
        return s ? <Scoped><RunSummaryCard summary={s} /></Scoped> : <GenericTool {...(p as ToolProps)} />;
      },
    },
    [],
  );
  // ---- GENUI-GLUE start
  // Tier 2: no allowlist renderer for plain specs in CopilotKit (its A2UI renderer needs the A2UI
  // wire format), so the spec goes through the shared DeclarativeView walker.
  useRenderTool(
    {
      name: "render_card",
      parameters: anyArgs,
      render: (p) => {
        const spec = p.status === "complete" ? parseSpec(p.result) : null;
        return spec ? <Scoped><DeclarativeView spec={spec} /></Scoped> : <GenericTool {...(p as ToolProps)} />;
      },
    },
    [],
  );
  // ---- GENUI-GLUE end
  return null;
}

// ---- HITL-GLUE start
function InterruptHandler() {
  useInterrupt({
    agentId: AGENT_ID,
    render: ({ interrupt, resolve, cancel }) => (
      <Scoped><ApprovalCard
        request={{
          interruptId: interrupt?.id ?? "unknown",
          message: interrupt?.message ?? "Approve this action?",
          action: String((interrupt?.metadata as { action?: string } | undefined)?.action ?? "action"),
          toolCallId: interrupt?.toolCallId,
        }}
        onDecision={async (r) => {
          await (r.approved ? resolve({ approved: true, proof: r.proof }) : cancel()).catch(() => {});
        }}
      /></Scoped>
    ),
  });
  return null;
}
// ---- HITL-GLUE end

function StateAside() {
  const { agent } = useAgent({ agentId: AGENT_ID });
  return <PlanPanel state={agent.state} />;
}

export function App() {
  const { scenario, pace } = scenarioFromUrl();
  // ---- ADAPTER-GLUE start
  const agent = useMemo(() => new MockAgUiAgent(scenario, pace), [scenario, pace]);
  // cast: CopilotKit pins @ag-ui/client 1.0.1 exactly; ours is 1.0.2 (nominal private-field mismatch)
  const agents = useMemo(() => ({ [AGENT_ID]: agent as never }), [agent]);
  const [runError, setRunError] = useState<{ message: string; code?: string } | null>(null);
  // ---- ADAPTER-GLUE end
  return (
    <CopilotKitProvider
      selfManagedAgents={agents}
      enableInspector={false}
      showIntelligenceIndicator={false}
      // ---- ADAPTER-GLUE start (RUN_ERROR is not rendered in the thread)
      onError={({ error, code }) => setRunError({ message: error.message, code: String(code) })}
      // ---- ADAPTER-GLUE end
    >
      <ToolRenderers />
      <InterruptHandler />
      <Shell
        variant="CopilotKit v2"
        aside={<StateAside />}
        // ---- THEME-GLUE start (CopilotKit's own dark variants key on a `.dark` ancestor)
        onTheme={(dark) => document.documentElement.classList.toggle("dark", dark)}
        // ---- THEME-GLUE end
        chat={
          <div className="flex min-h-0 flex-1 flex-col">
            {/* ---- UI-COMPOSITION start */}
            <CopilotChat agentId={AGENT_ID} className="min-h-0 flex-1" />
            {/* ---- UI-COMPOSITION end */}
            {/* ---- ADAPTER-GLUE start */}
            {runError ? (
              <div className="px-4">
                <RunErrorNotice message={runError.message} code={runError.code} />
              </div>
            ) : null}
            {/* ---- ADAPTER-GLUE end */}
          </div>
        }
      />
    </CopilotKitProvider>
  );
}
