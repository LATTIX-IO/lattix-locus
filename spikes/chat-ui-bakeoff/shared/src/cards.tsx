/**
 * Locus result surfaces used by both variants:
 *   - ToolCallCard: generic tool call (args, status, result)
 *   - RunSummaryCard: tier 1, a controlled typed card with a validator
 *   - Declarative renderer: tier 2, a JSON spec rendered only from an allowlist
 *   - ApprovalCard: the HITL request, resolved through confirmAction()
 *   - PlanPanel: agent state from STATE_SNAPSHOT / STATE_DELTA
 *   - RunErrorNotice: RUN_ERROR
 */
import { useState, type ReactNode } from "react";
import { confirmAction, type ConfirmResult } from "./confirm";
import { Badge, Button, Panel, cn, type BadgeVariant } from "./ui";

// ---------------------------------------------------------------- generic tool call

export type ToolStatus = "streaming" | "running" | "complete" | "awaiting-approval" | "error";

export function ToolCallCard({
  name,
  argsText,
  status,
  result,
  children,
}: {
  name: string;
  argsText?: string | undefined;
  status: ToolStatus;
  result?: string | undefined;
  children?: ReactNode;
}) {
  const badge: Record<ToolStatus, BadgeVariant> = {
    streaming: "info",
    running: "info",
    complete: "success",
    "awaiting-approval": "warning",
    error: "danger",
  };
  return (
    <div role="group" aria-label={`Tool call ${name}`} className="my-2 rounded-[var(--radius)] border border-border bg-card p-3 text-card-foreground" data-tool={name}>
      <div className="flex items-center justify-between gap-2">
        <code className="text-xs font-semibold">{name}</code>
        <Badge variant={badge[status]}>{status}</Badge>
      </div>
      {children}
      <details className="mt-2 text-xs">
        <summary className="cursor-pointer text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">Arguments and result</summary>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted p-2">{argsText || "(none)"}</pre>
        {result !== undefined ? <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-muted p-2">{result}</pre> : null}
      </details>
    </div>
  );
}

// ---------------------------------------------------------------- tier 1: typed card

export type RunSummary = {
  kind: "locus.run_summary.v1";
  title: string;
  status: "succeeded" | "failed" | "running";
  durationMs: number;
  metrics: { label: string; value: number }[];
};

/** Validates an untrusted tool result into the typed card's props; null means "render generic". */
export function parseRunSummary(raw: unknown): RunSummary | null {
  let v: unknown = raw;
  if (typeof raw === "string") {
    try {
      v = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!v || typeof v !== "object") return null;
  const o = v as Record<string, unknown>;
  if (o.kind !== "locus.run_summary.v1" || typeof o.title !== "string" || typeof o.durationMs !== "number") return null;
  if (o.status !== "succeeded" && o.status !== "failed" && o.status !== "running") return null;
  if (!Array.isArray(o.metrics) || o.metrics.length > 20) return null;
  const metrics = o.metrics.filter(
    (m): m is { label: string; value: number } =>
      !!m && typeof (m as { label?: unknown }).label === "string" && typeof (m as { value?: unknown }).value === "number",
  );
  return { kind: o.kind, title: o.title.slice(0, 120), status: o.status, durationMs: o.durationMs, metrics };
}

export function RunSummaryCard({ summary }: { summary: RunSummary }) {
  const tone: BadgeVariant = summary.status === "succeeded" ? "success" : summary.status === "failed" ? "danger" : "info";
  return (
    <Panel aria-label={`Run summary: ${summary.title}`} className="my-2" title={<span className="flex items-center gap-2">{summary.title} <Badge variant={tone}>{summary.status}</Badge></span>}>
      <dl className="grid grid-cols-3 gap-2 text-sm">
        {summary.metrics.map((m) => (
          <div key={m.label} className="rounded bg-muted p-2">
            <dt className="text-xs text-muted-foreground">{m.label}</dt>
            <dd className="font-semibold tabular-nums">{m.value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">Duration {(summary.durationMs / 1000).toFixed(1)} s</p>
    </Panel>
  );
}

// ---------------------------------------------------------------- tier 2: declarative allowlist

type Primitive = string | number | boolean;
export type SpecNode = string | number | { component: string; props?: Record<string, unknown>; children?: SpecNode[] };
export type DeclarativeSpec = { root: SpecNode | SpecNode[] };

/** The allowlist. Components take explicit props only; nothing is spread onto the DOM. */
export const ALLOWLIST = {
  Card: ({ title, children }: { title?: string; children?: ReactNode }) => (
    <Panel className="my-2" title={typeof title === "string" ? title : undefined}>
      <div className="flex flex-col gap-2">{children}</div>
    </Panel>
  ),
  Text: ({ tone, children }: { tone?: string; children?: ReactNode }) => (
    <p className={cn("text-sm", tone === "muted" && "text-muted-foreground")}>{children}</p>
  ),
  Stack: ({ children }: { children?: ReactNode }) => <div className="flex flex-wrap gap-2">{children}</div>,
  Badge: ({ variant, children }: { variant?: string; children?: ReactNode }) => (
    <Badge variant={(["neutral", "success", "warning", "danger", "info"] as const).find((v) => v === variant) ?? "neutral"}>{children}</Badge>
  ),
  Table: ({ columns, rows }: { columns?: unknown; rows?: unknown }) => {
    const cols = Array.isArray(columns) ? columns.filter((c): c is string => typeof c === "string").slice(0, 8) : [];
    const body = Array.isArray(rows) ? rows.filter(Array.isArray).slice(0, 50) : [];
    return (
      <table className="w-full text-left text-xs">
        <thead>
          <tr>{cols.map((c) => <th key={c} scope="col" className="border-b border-border py-1 pr-2 font-semibold">{c}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((r, i) => (
            <tr key={i}>{(r as unknown[]).slice(0, cols.length).map((cell, j) => <td key={j} className="py-1 pr-2">{String(cell)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    );
  },
} as const;

export type AllowlistName = keyof typeof ALLOWLIST;

export function BlockedComponent({ component }: { component: string; props?: unknown }) {
  return (
    <div role="note" className="rounded border border-dashed border-destructive p-2 text-xs text-destructive">
      Blocked component “{component.slice(0, 40)}” (not on the allowlist)
    </div>
  );
}

const DROP_PROP = /^(on|dangerously|style$|className$|ref$|key$|href$|src$|srcdoc$|action$|formaction$)/i;

function safeProps(raw: unknown): Record<string, Primitive | Primitive[] | Primitive[][]> {
  const out: Record<string, Primitive | Primitive[] | Primitive[][]> = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (DROP_PROP.test(k)) continue;
    if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") out[k] = v;
    else if (Array.isArray(v)) out[k] = v as Primitive[];
  }
  return out;
}

/** Own walker (used where the library has no allowlist renderer). Bounded depth and size. */
export function DeclarativeView({ spec }: { spec: unknown }) {
  let count = 0;
  const walk = (node: unknown, depth: number, key: string): ReactNode => {
    if (++count > 200 || depth > 8) return null;
    if (typeof node === "string" || typeof node === "number") return String(node);
    if (Array.isArray(node)) return node.map((n, i) => walk(n, depth + 1, `${key}.${i}`));
    if (!node || typeof node !== "object") return null;
    const n = node as { component?: unknown; props?: unknown; children?: unknown };
    const name = typeof n.component === "string" ? n.component : "(unnamed)";
    const Comp = (ALLOWLIST as Record<string, (p: Record<string, unknown>) => ReactNode>)[name];
    if (!Comp || !Object.prototype.hasOwnProperty.call(ALLOWLIST, name)) return <BlockedComponent key={key} component={name} />;
    const children = Array.isArray(n.children) ? n.children.map((c, i) => walk(c, depth + 1, `${key}.${i}`)) : undefined;
    return <Comp key={key} {...safeProps(n.props)}>{children}</Comp>;
  };
  const root = (spec as DeclarativeSpec | null)?.root;
  return <div data-tier="2">{walk(root, 0, "r")}</div>;
}

export function parseSpec(raw: unknown): DeclarativeSpec | null {
  let v = raw;
  if (typeof raw === "string") {
    try {
      v = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return v && typeof v === "object" && "root" in (v as object) ? (v as DeclarativeSpec) : null;
}

// ---------------------------------------------------------------- HITL approval

export type ApprovalRequest = { interruptId: string; message: string; action: string; toolCallId?: string | undefined; argsText?: string | undefined };

/**
 * The approval card never resolves the interrupt itself: Review goes to confirmAction() (the
 * native dialog) and only its answer is handed to `onDecision`, which resumes the run.
 */
export function ApprovalCard({ request, onDecision }: { request: ApprovalRequest; onDecision: (r: ConfirmResult) => void | Promise<void> }) {
  const [phase, setPhase] = useState<"pending" | "confirming" | "done">("pending");
  const review = async () => {
    setPhase("confirming");
    const r = await confirmAction({ interruptId: request.interruptId, action: request.action, summary: request.message, toolCallId: request.toolCallId });
    setPhase("done");
    await onDecision(r);
  };
  const deny = async () => {
    setPhase("done");
    await onDecision({ approved: false });
  };
  return (
    <div role="group" aria-label="Approval required" className="my-2 rounded-[var(--radius)] border-2 border-warning bg-card p-3 text-card-foreground" data-approval={request.interruptId}>
      <p className="text-sm font-semibold">Approval required</p>
      <p className="mt-1 text-sm">{request.message}</p>
      {request.argsText ? <pre className="mt-2 whitespace-pre-wrap break-all rounded bg-muted p-2 text-xs">{request.argsText}</pre> : null}
      <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
        {phase === "pending" ? "The run is paused until you decide." : phase === "confirming" ? "Waiting for the desktop confirmation…" : "Decision sent."}
      </p>
      <div className="mt-2 flex gap-2">
        <Button data-testid="approve" disabled={phase !== "pending"} onClick={review}>Review and confirm…</Button>
        <Button data-testid="deny" variant="outline" disabled={phase !== "pending"} onClick={deny}>Deny</Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- state + errors

type PlanStep = { id: string; title: string; status: string };

export function PlanPanel({ state }: { state: unknown }) {
  const plan = (state as { plan?: PlanStep[] } | undefined)?.plan;
  const tone = (s: string): BadgeVariant => (s === "done" ? "success" : s === "running" ? "info" : s === "cancelled" ? "danger" : "neutral");
  return (
    <Panel role="region" aria-label="Agent plan" title="Plan (agent state)">
      {!plan?.length ? (
        <p className="text-xs text-muted-foreground">No state yet.</p>
      ) : (
        <ol className="flex flex-col gap-1 text-sm">
          {plan.map((s) => (
            <li key={s.id} className="flex items-center justify-between gap-2">
              <span>{s.title}</span>
              <Badge variant={tone(s.status)}>{s.status}</Badge>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}

export function RunErrorNotice({ message, code }: { message: string; code?: string | undefined }) {
  return (
    <div role="alert" className="my-2 rounded-[var(--radius)] border border-destructive bg-card p-3 text-sm text-destructive" data-run-error>
      <strong>Run failed.</strong> {message} {code ? <code className="text-xs">({code})</code> : null}
    </div>
  );
}

export function safeArgs(argsText: string | undefined): Record<string, unknown> {
  try {
    return argsText ? (JSON.parse(argsText) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
