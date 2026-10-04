import {
  AgentDefinition,
  AgentTemplate,
  ArtifactDetail,
  ArtifactSummary,
  AtfAlignmentReport,
  AuditEvent,
  CollaborationSession,
  DefinitionRevisionHistory,
  DefinitionRevisionSummary,
  GuardrailRuleSet,
  InboxItem,
  IntegrationDefinition,
  MCPConnectionDefinition,
  MCPConnectionValidationResponse,
  MCPStarterTemplate,
  IntegrationOAuthConnectResponse,
  IntegrationOAuthStatus,
  IntegrationStarterTemplate,
  ObservabilityRunTrace,
  OperatorSession,
  PlatformHealthDetails,
  PlaybookDefinition,
  PlatformVersionStatus,
  PlatformSettings,
  SecurityPolicyResponse,
  TemplateCatalogItem,
  WorkflowDefinition,
  WorkflowRunEvent,
  RunKind,
  WorkflowRunKind,
  WorkflowRunSummary,
} from "@/types/locus";
import { RunStreamInterruptedError } from "@/lib/run-stream";
import {
  confirmViaDesktopShell,
  DesktopConfirmationCancelledError,
  DesktopConfirmationError,
  getDesktopInvoke,
  isShellProofRefusal,
  matchShellAction,
  needsConfirmationUpfront,
  requestBodyObject,
} from "@/lib/desktop-confirmation";
export type { ObservabilityRunTrace } from "@/types/locus";
export {
  CONFIRMATION_CANCELLED_MESSAGE,
  DesktopConfirmationCancelledError,
  DesktopConfirmationError,
} from "@/lib/desktop-confirmation";

/* ------------------------------------------------------------------ */
/*  Configuration helpers                                              */
/* ------------------------------------------------------------------ */

function getApiBase(): string {
  if (typeof window === "undefined") {
    return process.env.API_BASE_URL_INTERNAL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }

  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";
}

function getRequestIdentityHeaders(): Record<string, string> {
  const actor = (process.env.NEXT_PUBLIC_LOCUS_ACTOR ?? "").trim();
  const headers: Record<string, string> = {};
  if (actor) {
    headers["x-locus-actor"] = actor;
  }
  return headers;
}

async function getRequestAuthHeaders(): Promise<Record<string, string>> {
  const headers = getRequestIdentityHeaders();

  if (typeof window !== "undefined") {
    return headers;
  }

  try {
    const nextHeadersModule = await import("next/headers");
    const requestHeaders = await nextHeadersModule.headers();
    const cookieHeader = requestHeaders.get("cookie")?.trim();
    const authorizationHeader = requestHeaders.get("authorization")?.trim();

    if (cookieHeader) {
      headers.cookie = cookieHeader;
    }
    if (authorizationHeader) {
      headers.authorization = authorizationHeader;
    }
  } catch {
    // Outside a Next request context there may be no incoming headers to forward.
  }

  return headers;
}

/* ------------------------------------------------------------------ */
/*  Retry with exponential backoff                                     */
/* ------------------------------------------------------------------ */

async function fetchWithRetry(
  url: string,
  init: RequestInit | undefined,
  retries = 2,
  baseDelayMs = 500,
): Promise<Response> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, init);
      // Retry on 502/503/504 (transient server errors)
      if (res.status >= 502 && res.status <= 504 && attempt < retries) {
        await delay(baseDelayMs * 2 ** attempt);
        continue;
      }
      return res;
    } catch (err) {
      lastError = err;
      if (attempt < retries) {
        await delay(baseDelayMs * 2 ** attempt);
      }
    }
  }
  throw lastError;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ------------------------------------------------------------------ */
/*  API connectivity tracking (client-side only)                       */
/* ------------------------------------------------------------------ */

type ApiStatusListener = (connected: boolean) => void;
const apiStatusListeners = new Set<ApiStatusListener>();
let lastApiConnected = true;

export function onApiStatusChange(listener: ApiStatusListener): () => void {
  apiStatusListeners.add(listener);
  return () => { apiStatusListeners.delete(listener); };
}

function setApiConnected(connected: boolean) {
  if (connected !== lastApiConnected) {
    lastApiConnected = connected;
    apiStatusListeners.forEach((fn) => fn(connected));
  }
}

type Json = Record<string, unknown> | unknown[];

type CacheEntry<T> = {
  value: T;
  expiresAt: number;
};

const responseCache = new Map<string, CacheEntry<unknown>>();
export const PLATFORM_SETTINGS_UPDATED_EVENT = "locus:platform-settings-updated";
export const WORKFLOW_RUN_UPDATED_EVENT = "locus:workflow-run-updated";

function readCachedValue<T>(cacheKey: string): T | null {
  const cached = responseCache.get(cacheKey);
  if (!cached) {
    return null;
  }
  if (cached.expiresAt <= Date.now()) {
    responseCache.delete(cacheKey);
    return null;
  }
  return cached.value as T;
}

function writeCachedValue<T>(cacheKey: string, value: T, ttlMs: number): T {
  responseCache.set(cacheKey, { value, expiresAt: Date.now() + ttlMs });
  return value;
}

function deleteCachedValue(cacheKey: string): void {
  responseCache.delete(cacheKey);
}

function invalidateWorkflowRunCaches(id: string): void {
  deleteCachedValue(`workflow-run:${id}`);
  deleteCachedValue(`workflow-run-events:${id}`);
  deleteCachedValue("inbox");
  for (const key of Array.from(responseCache.keys())) {
    if (key.startsWith("workflow-runs:")) {
      deleteCachedValue(key);
    }
  }
}

function invalidateOperatorSessionCache(): void {
  deleteCachedValue("operator-session");
}

function publishWorkflowRunUpdate(run: WorkflowRunSummary): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent<WorkflowRunSummary>(WORKFLOW_RUN_UPDATED_EVENT, { detail: run }));
}

function isJsonRecord(value: Json): value is Record<string, unknown> {
  return !Array.isArray(value) && typeof value === "object" && value !== null;
}

function publishPlatformSettingsUpdate(settings: PlatformSettings): void {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent<PlatformSettings>(PLATFORM_SETTINGS_UPDATED_EVENT, { detail: settings }));
}

const LOCUS_GRAPH_SCHEMA_VERSION = "locus-graph/1.0";

export type GraphCanvasPayload = {
  schema_version?: string;
  nodes: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
  links: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  input?: Record<string, unknown>;
};

function withGraphSchemaVersion(payload: GraphCanvasPayload): GraphCanvasPayload {
  return {
    ...payload,
    schema_version: payload.schema_version ?? LOCUS_GRAPH_SCHEMA_VERSION,
  };
}

export type RuntimeProvider = {
  provider: string;
  configured: boolean;
  model: string;
  mode: "live" | "not_configured";
};

export type RuntimeFrameworkAdapterProbe = {
  engine: string;
  available: boolean;
  missing_modules: string[];
};

export type RuntimeProvidersResponse = {
  providers: RuntimeProvider[];
  framework_adapters?: Record<string, RuntimeFrameworkAdapterProbe>;
};

export type UserRuntimeProviderConfig = {
  provider: string;
  configured: boolean;
  model: string;
  available_models?: string[];
  base_url: string;
  api_key_masked: string;
  preferred?: boolean;
  created_at?: string;
  updated_at: string;
  source: "user" | "environment";
};

export type UserRuntimeProvidersResponse = {
  principal_id: string;
  providers: UserRuntimeProviderConfig[];
};

export type UserSkillsResponse = {
  principal_id: string;
  skills: string[];
  updated_at?: string;
};

export type RuntimeEngineName = "native" | "langgraph" | "langchain" | "semantic-kernel" | "autogen";

export type RuntimeStrategyName = "single" | "hybrid";

export type RuntimeHybridRole = "default" | "orchestration" | "retrieval" | "tooling" | "collaboration";

export type RuntimeHybridRouting = Partial<Record<RuntimeHybridRole, RuntimeEngineName>>;

export type PlatformRuntimePolicySettings = Pick<
  PlatformSettings,
  | "default_runtime_engine"
  | "default_runtime_strategy"
  | "default_hybrid_runtime_routing"
  | "allowed_runtime_engines"
  | "allow_runtime_engine_override"
  | "enforce_runtime_engine_allowlist"
>;

export type GraphValidationResponse = {
  valid: boolean;
  issues: Array<{ code: string; message: string; path: string }>;
};

export type GraphRunResponse = {
  run_id: string;
  status: "completed" | "failed";
  execution_order: string[];
  node_results: Record<string, Record<string, unknown>>;
  events: Array<{
    id: string;
    node_id: string;
    type: "node_started" | "node_completed" | "node_failed";
    title: string;
    summary: string;
    created_at: string;
  }>;
  validation: GraphValidationResponse;
  runtime?: {
    requested_engine?: string;
    selected_engine?: string;
    executed_engine?: string;
    mode?: string;
    strategy?: RuntimeStrategyName | string;
    allow_override?: boolean;
    allowed_engines?: string[];
    node_mapping?: Record<string, string>;
    adapter_probe?: {
      engine?: string;
      available?: boolean;
      missing_modules?: string[];
    };
    hybrid_routing?: RuntimeHybridRouting;
    hybrid_effective_routing?: RuntimeHybridRouting;
    hybrid_role_modes?: Partial<Record<RuntimeHybridRole, string>>;
    hybrid_resolution_notes?: string[];
    node_dispatches?: Array<{
      node_id: string;
      node_title?: string;
      role?: string;
      requested_engine?: string;
      executed_engine?: string;
      mode?: string;
    }>;
  };
};

export type MemorySessionResponse = {
  session_id: string;
  count: number;
  entries: Array<{ id: string; at: string; node_id: string; content: string }>;
};

export type IntegrationTestResponse = {
  ok: boolean;
  id: string;
  status: string;
  message: string;
  diagnostics?: {
    checks?: Record<string, boolean>;
    masked?: {
      base_url?: string;
      secret_ref?: string;
    };
    warnings?: string[];
  };
};

export type ObservabilityDashboardResponse = {
  summary: {
    total_runs: number;
    failed_or_blocked_runs: number;
    token_estimate: number;
    cost_estimate_usd: number;
    average_latency_ms: number;
  };
  runs: ObservabilityRunTrace[];
};

export type RunParticipants = {
  user: { label: string; active: boolean };
  agents: Array<{ id: string; name: string; active: boolean }>;
};

export type ChangedFile = {
  path: string;
  status: string;
  additions: number;
  deletions: number;
  diff: string;
};

export type WorkflowRunDetail = {
  title?: string;
  title_source?: "system" | "generated" | "user";
  artifacts: ArtifactSummary[];
  status: string;
  participants?: RunParticipants;
  changed_files?: ChangedFile[];
  working_folder?: string;
  runtime?: {
    provider?: string;
    model?: string;
    mode?: string;
    source?: string;
    state?: string;
  };
  graph?: {
    nodes: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
    links: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  };
  agent_traces?: Array<{
    agent: string;
    reasoningSummary: string;
    actions: string[];
    output: string;
  }>;
  approvals?: {
    required?: boolean;
    pending?: boolean;
    artifact_id?: string;
    version?: number;
    scope?: string;
  };
  cognitive?: {
    assembly?: {
      assembly_id?: string;
      consensus_policy?: string;
      inference_mode?: string;
      columns?: string[];
    };
    commitment?: {
      decision?: string;
      confidence?: number;
      supporting_columns?: string[];
      dissenting_columns?: string[];
      blockers?: string[];
      next_actions?: string[];
      evidence_refs?: string[];
      rationale?: string;
      status?: string;
    };
    states?: Record<
      string,
      {
        column_id?: string;
        assembly_id?: string;
        belief_set?: Record<string, unknown>;
        evidence_refs?: string[];
        confidence?: number;
        last_updated?: string;
      }
    >;
    messages?: Array<{
      message_type?: string;
      column_id?: string;
      assembly_id?: string;
      confidence?: number;
      evidence_refs?: string[];
    }>;
  };
};

async function strictFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // Inside the desktop shell, a capability-widening call is confirmed in a
  // native dialog and sent by the shell (LOCUS-357); elsewhere it is a plain
  // request. Narrowing calls never match a shell action.
  const shellAction = matchShellAction(init?.method ?? "GET", path);
  const invoke = shellAction ? getDesktopInvoke() : null;
  if (shellAction && invoke) {
    const body = requestBodyObject(init?.body);
    if (needsConfirmationUpfront(shellAction, body)) {
      const confirmed = await confirmViaDesktopShell<T>(invoke, shellAction, path, body);
      setApiConnected(true);
      return confirmed;
    }
  }

  let res: Response;
  try {
    const requestHeaders = await getRequestAuthHeaders();
    res = await fetchWithRetry(
      `${getApiBase()}${path}`,
      {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...requestHeaders,
          ...(init?.headers ?? {}),
        },
        cache: "no-store",
        credentials: "include",
      },
    );
  } catch (error) {
    setApiConnected(false);
    throw error;
  }

  setApiConnected(true);

  if (!res.ok) {
    let details = "";
    try {
      details = await res.text();
    } catch {
      details = "";
    }
    // The backend decides from stored state that this change widens (for
    // example a settings save that adds an egress host): confirm it in the
    // shell's dialog. Not a silent retry: the human sees the dialog, and a
    // cancel raises DesktopConfirmationCancelledError.
    if (shellAction && invoke && isShellProofRefusal(res.status, details)) {
      return confirmViaDesktopShell<T>(invoke, shellAction, path, requestBodyObject(init?.body));
    }
    throw new Error(`Request failed (${res.status})${details ? `: ${details}` : ""}`);
  }

  return (await res.json()) as T;
}

/** A read where "not found" is a normal answer: 404 resolves to null, every
 * other failure throws (no silent fallbacks, FRONTEND.md). */
async function strictFetchOrNull<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    return await strictFetch<T>(path, init);
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("Request failed (404)")) {
      return null;
    }
    throw error;
  }
}

// Accept both run-kind vocabularies (WorkflowRunKind and the inbox RunKind) so
// "individual" / "agent" runs are not silently rewritten to "workflow".
const KNOWN_RUN_KINDS: ReadonlyArray<WorkflowRunKind | RunKind> = ["workflow", "chat", "playbook", "task", "individual", "agent"];

function normalizeWorkflowRunSummary(run: WorkflowRunSummary): WorkflowRunSummary {
  const candidate = typeof run.kind === "string" ? run.kind.toLowerCase() : "";
  const kind: WorkflowRunKind | RunKind = (KNOWN_RUN_KINDS as readonly string[]).includes(candidate)
    ? candidate as WorkflowRunKind | RunKind
    : "workflow";
  return {
    ...run,
    kind,
  };
}

export async function getPublishedWorkflows(): Promise<WorkflowDefinition[]> {
  return strictFetch<WorkflowDefinition[]>("/workflows/published");
}

export async function createWorkflowRun(
  payload: Json,
  options?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<{ id: string; status: string }> {
  const timeoutMs = options?.timeoutMs ?? 120000;
  const timeoutController = new AbortController();
  const timeoutHandle = setTimeout(() => {
    timeoutController.abort(new Error("Run creation is taking longer than expected"));
  }, timeoutMs);

  const abortForwarder = () => timeoutController.abort(options?.signal?.reason);
  options?.signal?.addEventListener("abort", abortForwarder, { once: true });

  try {
    const requestHeaders = await getRequestAuthHeaders();
    const res = await fetch(`${getApiBase()}/workflow-runs`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...requestHeaders,
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      credentials: "include",
      signal: timeoutController.signal,
    });

    if (!res.ok) {
      setApiConnected(true);
      let details = "";
      try {
        details = await res.text();
      } catch {
        details = "";
      }
      throw new Error(`Failed to create run (${res.status})${details ? `: ${details}` : ""}`);
    }

    setApiConnected(true);
    return (await res.json()) as { id: string; status: string };
  } catch (error) {
    setApiConnected(false);
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Run creation timed out after ${Math.round(timeoutMs / 1000)}s. The model/backend may still be processing; please retry in a moment.`);
    }
    if (error instanceof Error && /taking longer than expected/i.test(error.message)) {
      throw new Error(`Run creation timed out after ${Math.round(timeoutMs / 1000)}s. The model/backend may still be processing; please retry in a moment.`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutHandle);
    options?.signal?.removeEventListener("abort", abortForwarder);
  }
}

export async function getWorkflowRuns(status?: string): Promise<WorkflowRunSummary[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
  const cacheKey = `workflow-runs:${status ?? "all"}`;
  const cached = readCachedValue<WorkflowRunSummary[]>(cacheKey);
  if (cached) {
    return cached;
  }
  const response = await strictFetch<WorkflowRunSummary[]>(`/workflow-runs${suffix}`);
  const value = response.map((run) => normalizeWorkflowRunSummary(run));
  return writeCachedValue(cacheKey, value, 5000);
}

export async function getWorkflowRun(_id: string): Promise<WorkflowRunDetail> {
  const cacheKey = `workflow-run:${_id}`;
  const cached = readCachedValue<WorkflowRunDetail>(cacheKey);
  if (cached) {
    return cached;
  }
  const value = await strictFetch<WorkflowRunDetail>(`/workflow-runs/${_id}`);
  return writeCachedValue(cacheKey, value, 1500);
}

export async function updateWorkflowRunTitle(id: string, title: string): Promise<WorkflowRunSummary> {
  const updated = normalizeWorkflowRunSummary(
    await strictFetch<WorkflowRunSummary>(`/workflow-runs/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  );

  invalidateWorkflowRunCaches(id);

  publishWorkflowRunUpdate(updated);
  return updated;
}

export async function getWorkflowRunEvents(id: string): Promise<WorkflowRunEvent[]> {
  const cacheKey = `workflow-run-events:${id}`;
  const cached = readCachedValue<WorkflowRunEvent[]>(cacheKey);
  if (cached) {
    return cached;
  }
  const value = await strictFetch<WorkflowRunEvent[]>(`/workflow-runs/${id}/events`);
  return writeCachedValue(cacheKey, value, 1500);
}

export type RunStreamItem = { id: string; type: string; createdAt: string; payload: Record<string, unknown> };

/** Payload of the terminal `end` lifecycle frame. `terminal` reports whether the
 * run's status is terminal — `end` alone only means the stream is finished. */
export type RunStreamEnd = { run_id?: string; reason: string; status: string; terminal: boolean };

/** Why a run stream stopped without an `end` frame. Never means completion:
 * "timeout" = server rotated the connection, "dropped" = closed without a
 * lifecycle frame, "error" = HTTP/transport failure. */
export type RunStreamDisconnect = { reason: "timeout" | "dropped" | "error"; after?: string };

/**
 * Fetch-based consumer for `/workflow-runs/{id}/stream` (delta stream).
 * Exactly one of `onEnd` / `onDisconnect` fires per connection (unless aborted).
 * `onError` is kept for HTTP/transport failures and fires alongside
 * `onDisconnect({ reason: "error" })`.
 */
export function streamWorkflowRun(
  id: string,
  handlers: {
    onMessage: (event: RunStreamItem) => void;
    onError?: () => void;
    onOpen?: () => void;
    onEnd?: (end: RunStreamEnd) => void;
    onDisconnect?: (info: RunStreamDisconnect) => void;
  },
  options: { after?: string } = {},
): () => void {
  const controller = new AbortController();
  const suffix = options.after ? `?after=${encodeURIComponent(options.after)}` : "";
  void (async () => {
    try {
      const requestHeaders = await getRequestAuthHeaders();
      const res = await fetch(`${getApiBase()}/workflow-runs/${encodeURIComponent(id)}/stream${suffix}`, {
        method: "GET",
        headers: {
          ...requestHeaders,
        },
        cache: "no-store",
        credentials: "include",
        signal: controller.signal,
      });
      if (!res.ok || !res.body) {
        handlers.onError?.();
        handlers.onDisconnect?.({ reason: "error" });
        return;
      }
      handlers.onOpen?.();

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame
            .split("\n")
            .find((candidate) => candidate.startsWith("data:"));
          if (!line) {
            continue;
          }
          let item: RunStreamItem;
          try {
            item = JSON.parse(line.slice(5).trim()) as RunStreamItem;
          } catch {
            continue; // A malformed frame is not a disconnect; skip it.
          }
          if (item.type === "end") {
            handlers.onEnd?.(item.payload as unknown as RunStreamEnd);
            return;
          }
          if (item.type === "stream_closed") {
            const after = typeof item.payload?.after === "string" ? item.payload.after : undefined;
            handlers.onDisconnect?.({ reason: "timeout", after: after || undefined });
            return;
          }
          handlers.onMessage(item);
        }
      }
      // Closed without `end`/`stream_closed`: a drop, never a completion.
      if (!controller.signal.aborted) {
        handlers.onDisconnect?.({ reason: "dropped" });
      }
    } catch {
      if (!controller.signal.aborted) {
        handlers.onError?.();
        handlers.onDisconnect?.({ reason: "error" });
      }
    }
  })();
  return () => {
    controller.abort();
  };
}

// Live variants throw on failure instead of falling back to mock data, so pollers
// never overwrite real run state with placeholders.
export async function getWorkflowRunLive(id: string): Promise<WorkflowRunDetail> {
  return strictFetch<WorkflowRunDetail>(`/workflow-runs/${id}`);
}

export async function getWorkflowRunEventsLive(
  id: string,
  afterEventId?: string,
): Promise<WorkflowRunEvent[]> {
  const suffix = afterEventId ? `?after=${encodeURIComponent(afterEventId)}` : "";
  return strictFetch<WorkflowRunEvent[]>(`/workflow-runs/${id}/events${suffix}`);
}

export type KnowledgeCollection = {
  id: string;
  name: string;
  description: string;
  created_at: string;
  document_count: number;
  chunk_count: number;
  vector_store_id: string;
};

export type KnowledgeSearchResult = {
  content: string;
  document_name: string;
  chunk_index: number;
  score: number | null;
};

export type MemoryLayer = {
  id: string;
  name: string;
  backend: string;
  scope: string;
  enabled: boolean;
  healthy: boolean;
  stats: Record<string, unknown>;
};

export type KnowledgeVectorStore = {
  id: string;
  name: string;
  kind: "builtin" | "integration";
  ready: boolean;
  status: string;
  embedding_model: string;
  note: string;
};

export async function getKnowledgeCollections(): Promise<KnowledgeCollection[]> {
  return strictFetch<KnowledgeCollection[]>("/knowledge/collections");
}

export async function getMemoryLayers(): Promise<MemoryLayer[]> {
  const data = await strictFetch<{ layers: MemoryLayer[] }>("/knowledge/memory-layers");
  return data.layers;
}

export async function getKnowledgeVectorStores(): Promise<KnowledgeVectorStore[]> {
  const data = await strictFetch<{ vector_stores: KnowledgeVectorStore[] }>(
    "/knowledge/vector-stores",
  );
  return data.vector_stores;
}

export async function createKnowledgeCollection(
  name: string,
  description: string,
  vectorStoreId?: string,
): Promise<KnowledgeCollection> {
  return strictFetch("/knowledge/collections", {
    method: "POST",
    body: JSON.stringify({ name, description, vector_store_id: vectorStoreId }),
  });
}

export async function deleteKnowledgeCollection(id: string): Promise<{ ok: boolean }> {
  return strictFetch(`/knowledge/collections/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function addKnowledgeDocument(
  collectionId: string,
  name: string,
  text: string,
): Promise<{ ok: boolean; document_id: string; chunks_indexed: number; collection: KnowledgeCollection }> {
  return strictFetch(`/knowledge/collections/${encodeURIComponent(collectionId)}/documents`, {
    method: "POST",
    body: JSON.stringify({ name, text }),
  });
}

export async function searchKnowledgeCollection(
  collectionId: string,
  query: string,
  topK = 5,
): Promise<{ query: string; results: KnowledgeSearchResult[]; reason?: string }> {
  return strictFetch(`/knowledge/collections/${encodeURIComponent(collectionId)}/search`, {
    method: "POST",
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export type SkillDefinition = {
  id: string;
  name: string;
  description: string;
  content: string;
  status: "enabled" | "disabled";
  tags: string[];
  source: "bundled" | "custom";
  auto_inject: boolean;
  version: number;
  updated_at: string;
  usage_count: number;
  last_used_at: string;
  tier: "tier1" | "tier2" | "tier3";
  maturity: "draft" | "incubating" | "validated" | "standard";
  owner: string;
  dependencies: string[];
  eval_rubric: string;
  eval_dataset: { prompt: string; expectation: string }[];
  last_eval: {
    score: number;
    passed: boolean;
    summary: string;
    case_count: number;
    ran_at: string;
    model: string;
  } | null;
  import_source: string;
  quarantine_status: "none" | "pending" | "cleared" | "blocked";
  security_scan: SkillSecurityScan | null;
};

export type SkillSecurityFinding = {
  code: string;
  message: string;
  severity: string;
  source: string;
  stage: string;
};

export type SkillSecurityScan = {
  cleared: boolean;
  static_passed: boolean;
  dry_run_passed: boolean;
  dry_run_mode: string;
  summary: string;
  ran_at: string;
  findings: SkillSecurityFinding[];
};

export async function importSkill(payload: {
  url?: string;
  content?: string;
  name?: string;
  description?: string;
}): Promise<SkillDefinition> {
  return strictFetch<SkillDefinition>("/skills/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function scanSkill(
  id: string,
): Promise<{ skill_id: string; quarantine_status: string; security_scan: SkillSecurityScan }> {
  return strictFetch(`/skills/${encodeURIComponent(id)}/scan`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export type SkillEvalRunResult = {
  skill_id: string;
  score: number;
  passed: boolean;
  mode: string;
  maturity: string;
  cases: { prompt: string; score: number; reason: string }[];
};

export async function runSkillEval(
  id: string,
  payload?: { model?: string },
): Promise<SkillEvalRunResult> {
  return strictFetch<SkillEvalRunResult>(`/skills/${encodeURIComponent(id)}/eval`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

export async function promoteSkill(id: string): Promise<SkillDefinition> {
  return strictFetch<SkillDefinition>(`/skills/${encodeURIComponent(id)}/promote`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export type WorkflowTrigger = {
  id: string;
  token_fingerprint: string;
  label: string;
  created_at: string;
};

export async function getWorkflowTriggers(workflowId: string): Promise<WorkflowTrigger[]> {
  return strictFetch<WorkflowTrigger[]>(
    `/workflow-definitions/${encodeURIComponent(workflowId)}/triggers`,
  );
}

export async function createWorkflowTrigger(
  workflowId: string,
  label: string,
): Promise<{ ok: boolean; token: string; webhook_url: string; label: string }> {
  return strictFetch(`/workflow-definitions/${encodeURIComponent(workflowId)}/triggers`, {
    method: "POST",
    body: JSON.stringify({ label }),
  });
}

export async function revokeWorkflowTrigger(token: string): Promise<{ ok: boolean }> {
  return strictFetch(`/triggers/${encodeURIComponent(token)}`, { method: "DELETE" });
}

export type WorkflowSchedule = {
  id: string;
  workflow_id: string;
  label: string;
  cron: string;
  enabled: boolean;
  created_at: string;
  last_fired_minute: string;
};

export async function getWorkflowSchedules(workflowId: string): Promise<WorkflowSchedule[]> {
  return strictFetch<WorkflowSchedule[]>(
    `/workflow-definitions/${encodeURIComponent(workflowId)}/schedules`,
  );
}

export async function createWorkflowSchedule(
  workflowId: string,
  cron: string,
  label: string,
): Promise<WorkflowSchedule> {
  return strictFetch(`/workflow-definitions/${encodeURIComponent(workflowId)}/schedules`, {
    method: "POST",
    body: JSON.stringify({ cron, label }),
  });
}

export async function toggleWorkflowSchedule(
  scheduleId: string,
  enabled: boolean,
): Promise<WorkflowSchedule> {
  return strictFetch(`/schedules/${encodeURIComponent(scheduleId)}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function deleteWorkflowSchedule(scheduleId: string): Promise<{ ok: boolean }> {
  return strictFetch(`/schedules/${encodeURIComponent(scheduleId)}`, { method: "DELETE" });
}

export type SkillTestResult = {
  skill_id: string;
  model: string;
  provider: string;
  mode: string;
  reason?: string;
  output: string;
};

export async function testSkill(
  id: string,
  payload: { prompt: string; model?: string },
): Promise<SkillTestResult> {
  return strictFetch<SkillTestResult>(`/skills/${encodeURIComponent(id)}/test`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getSkills(): Promise<SkillDefinition[]> {
  return strictFetch<SkillDefinition[]>("/skills");
}

export async function saveSkill(payload: Partial<SkillDefinition>): Promise<SkillDefinition> {
  return strictFetch<SkillDefinition>("/skills", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteSkill(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export type IntegrationCatalogEntry = {
  catalog_id: string;
  name: string;
  type: string;
  auth_type: string;
  base_url: string;
  publisher: string;
  capabilities: string[];
  egress_allowlist: string[];
  metadata_json: Record<string, unknown>;
  installed: boolean;
};

export async function getIntegrationCatalog(): Promise<IntegrationCatalogEntry[]> {
  return strictFetch<IntegrationCatalogEntry[]>("/integrations/catalog");
}

export async function installCatalogIntegration(
  catalogId: string,
): Promise<{ ok: boolean; id: string; already_installed: boolean }> {
  return strictFetch(`/integrations/catalog/${encodeURIComponent(catalogId)}/install`, {
    method: "POST",
  });
}

export type LocalModelPullState = {
  status: "downloading" | "ready" | "error" | string;
  detail?: string;
  progress_percent?: number;
};

export type LocalModelCatalogItem = {
  id: string;
  label: string;
  family: string;
  size_gb: number;
  min_ram_gb: number;
  notes: string;
  installed: boolean;
  reference: string;
  pull?: LocalModelPullState | null;
};

export type ModelsOverview = {
  providers: {
    openai: { configured: boolean; default_model: string };
    nim: {
      configured: boolean;
      base_url: string;
      default_model: string;
      reference_example: string;
    };
    ollama: {
      available: boolean;
      base_url: string;
      default_model: string;
      installed_models: { id: string; size_bytes: number; modified_at: string }[];
    };
  };
  external: {
    id: string;
    label: string;
    configured: boolean;
    base_url: string;
    default_model: string;
    reference_example: string;
    key_required: boolean;
  }[];
  catalog: LocalModelCatalogItem[];
};

export async function getModelsOverview(): Promise<ModelsOverview> {
  return strictFetch<ModelsOverview>("/models/overview");
}

export type ProviderModelsResponse = {
  provider: string;
  configured: boolean;
  models: string[];
  reason?: string;
};

export async function getProviderModels(providerId: string): Promise<ProviderModelsResponse> {
  return strictFetch<ProviderModelsResponse>(
    `/models/providers/${encodeURIComponent(providerId)}/models`,
  );
}

export async function pullLocalModel(
  model: string,
): Promise<{ ok: boolean; model: string; pull: LocalModelPullState }> {
  return strictFetch("/models/local/pull", {
    method: "POST",
    body: JSON.stringify({ model }),
  });
}

export type RunStatusFrame = {
  status: string;
  progress_label?: string;
  approval_pending?: boolean;
};

export type RunStreamOptions = {
  afterEventId?: string;
  signal: AbortSignal;
  onEvent?: (event: WorkflowRunEvent) => void;
  onStatus?: (status: RunStatusFrame) => void;
  onOpen?: () => void;
};

// Fetch-based SSE consumer (fetch can carry identity headers; EventSource cannot).
// Resolves "terminal" only on the server's `end` frame and "timeout" on a
// non-terminal `stream_closed` rotation (reconnect with the cursor). Throws on
// HTTP/transport failure, and RunStreamInterruptedError when the stream closes
// without either frame — a drop must never be read as completion.
export async function streamWorkflowRunEvents(
  id: string,
  options: RunStreamOptions,
): Promise<string> {
  const suffix = options.afterEventId ? `?after=${encodeURIComponent(options.afterEventId)}` : "";
  const res = await fetch(`${getApiBase()}/workflow-runs/${id}/events/stream${suffix}`, {
    headers: {
      Accept: "text/event-stream",
      ...getRequestIdentityHeaders(),
    },
    cache: "no-store",
    signal: options.signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Run event stream failed (${res.status})`);
  }
  setApiConnected(true);
  options.onOpen?.();

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const handleFrame = (frame: string): string | null => {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trim());
      }
    }
    if (dataLines.length === 0) {
      return null;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(dataLines.join("\n"));
    } catch {
      return null;
    }
    if (eventName === "run_event") {
      options.onEvent?.(parsed as WorkflowRunEvent);
    } else if (eventName === "run_status") {
      options.onStatus?.(parsed as RunStatusFrame);
    } else if (eventName === "end") {
      return "terminal";
    } else if (eventName === "stream_closed") {
      return "timeout";
    }
    return null;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      throw new RunStreamInterruptedError();
    }
    buffer += decoder.decode(value, { stream: true });
    let separator = buffer.indexOf("\n\n");
    while (separator >= 0) {
      const frame = buffer.slice(0, separator);
      buffer = buffer.slice(separator + 2);
      const endReason = handleFrame(frame);
      if (endReason !== null) {
        return endReason;
      }
      separator = buffer.indexOf("\n\n");
    }
  }
}

export async function archiveWorkflowRun(id: string): Promise<{ ok: boolean }> {
  const result = await strictFetch<{ ok: boolean }>(`/workflow-runs/${id}/archive`, { method: "POST" });
  invalidateWorkflowRunCaches(id);
  return result;
}

export async function createArtifactVersion(
  id: string,
  payload: Json,
): Promise<{ ok: boolean; artifactId: string }> {
  return strictFetch(`/artifacts/${id}/versions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function submitApproval(payload: Json): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>("/approvals", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getInbox(): Promise<InboxItem[]> {
  const cached = readCachedValue<InboxItem[]>("inbox");
  if (cached) {
    return cached;
  }
  const value = await strictFetch<InboxItem[]>("/inbox");
  return writeCachedValue("inbox", value, 5000);
}

export type InboxGroup = {
  id: string;
  name: string;
  created_at: string;
  run_ids: string[];
};

export async function getInboxGroups(): Promise<InboxGroup[]> {
  return strictFetch<InboxGroup[]>("/inbox/groups");
}

export async function createInboxGroup(name: string): Promise<InboxGroup> {
  return strictFetch<InboxGroup>("/inbox/groups", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function updateInboxGroup(
  id: string,
  payload: { name?: string; add_run_id?: string; remove_run_id?: string },
): Promise<InboxGroup> {
  return strictFetch<InboxGroup>(`/inbox/groups/${encodeURIComponent(id)}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteInboxGroup(id: string): Promise<{ ok: boolean }> {
  return strictFetch(`/inbox/groups/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function renameWorkflowRun(id: string, title: string): Promise<{ id: string; title: string }> {
  return strictFetch(`/workflow-runs/${encodeURIComponent(id)}/rename`, {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

export async function sendRunMessage(
  runId: string,
  message: string,
  options?: Record<string, unknown>,
): Promise<{ ok: boolean; run_id: string }> {
  return strictFetch(`/workflow-runs/${encodeURIComponent(runId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ message, ...(options ?? {}) }),
  });
}

// --- Composer capabilities (working folder, model/reasoning, mode, MCP/skills) ---

export type ComposerOptions = {
  workspace?: { repo_path: string; allow_outside?: "ask" | "deny" | "allow" };
  model?: string;
  reasoning_effort?: "" | "low" | "medium" | "high";
  mode?: "chat" | "plan" | "execute";
  mcp_server_ids?: string[];
  skill_ids?: string[];
};

export type UserSettings = {
  default_working_folder: string;
  preferred_model: string;
  preferred_reasoning_effort: "" | "low" | "medium" | "high";
  default_mode: "chat" | "plan" | "execute";
};

export async function getUserSettings(): Promise<UserSettings> {
  return strictFetch<UserSettings>("/user/settings");
}

export async function saveUserSettings(payload: Partial<UserSettings>): Promise<UserSettings> {
  return strictFetch<UserSettings>("/user/settings", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export type WorkspaceFolders = {
  root: string;
  path: string;
  exists: boolean;
  is_git: boolean;
  folders: { name: string; path: string }[];
};

export async function getWorkspaceFolders(path?: string): Promise<WorkspaceFolders> {
  const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
  return strictFetch<WorkspaceFolders>(`/workspace/folders${suffix}`);
}

export type McpServer = {
  id: string;
  slug: string;
  name: string;
  configured: boolean;
  transport: string;
  base_url: string;
};

export async function getMcpServers(): Promise<McpServer[]> {
  const res = await strictFetch<{ servers: McpServer[] }>("/mcp/servers");
  return res.servers ?? [];
}

export type RunEscalation = {
  id: string;
  path: string;
  workspace_root: string;
  policy: string;
  status: string;
};

export async function getRunEscalations(runId: string): Promise<RunEscalation[]> {
  const res = await strictFetch<{ escalations: RunEscalation[] }>(`/workflow-runs/${encodeURIComponent(runId)}/escalations`);
  return res.escalations ?? [];
}

export async function approveRunEscalation(
  runId: string,
  escalationId: string,
): Promise<{ ok: boolean }> {
  return strictFetch(
    `/workflow-runs/${encodeURIComponent(runId)}/escalations/${encodeURIComponent(escalationId)}/approve`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

export async function getArtifacts(): Promise<ArtifactSummary[]> {
  return strictFetch<ArtifactSummary[]>("/artifacts");
}

export async function getArtifact(id: string): Promise<ArtifactDetail | null> {
  return strictFetchOrNull<ArtifactDetail>(`/artifacts/${id}`);
}

// Builder mode endpoints
export async function getWorkflowDefinitions(): Promise<WorkflowDefinition[]> {
  return strictFetch<WorkflowDefinition[]>("/workflow-definitions");
}

export async function getWorkflowDefinitionVersions(id: string): Promise<DefinitionRevisionHistory> {
  return strictFetch<DefinitionRevisionHistory>(`/workflow-definitions/${id}/versions`);
}

export async function getWorkflowDefinition(id: string): Promise<WorkflowDefinition | null> {
  // single-definition fetch includes graph_json (the list endpoint excludes it)
  return strictFetchOrNull<WorkflowDefinition>(`/workflow-definitions/${id}`);
}

export async function saveWorkflowDefinition(payload: Json): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>("/workflow-definitions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function publishWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/workflow-definitions/${id}/publish`, { method: "POST" });
}

export async function unpublishWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/workflow-definitions/${id}/unpublish`, { method: "POST" });
}

export async function archiveWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/workflow-definitions/${id}/archive`, { method: "POST" });
}

export async function deleteWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/workflow-definitions/${id}`, { method: "DELETE" });
}

export async function rollbackWorkflowDefinition(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number },
): Promise<{ ok: boolean; id: string; version: number; status: string; restored_from: DefinitionRevisionSummary; revision: DefinitionRevisionSummary }> {
  return strictFetch(`/workflow-definitions/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function activateWorkflowDefinition(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number } = {},
): Promise<{ ok: boolean; id: string; active_revision: DefinitionRevisionSummary; activation_revision: DefinitionRevisionSummary }> {
  return strictFetch(`/workflow-definitions/${id}/activate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getAgentDefinitions(): Promise<AgentDefinition[]> {
  return strictFetch<AgentDefinition[]>("/agent-definitions");
}

export async function getAgentDefinition(id: string): Promise<AgentDefinition | null> {
  return strictFetchOrNull<AgentDefinition>(`/agent-definitions/${id}`);
}

export async function getAgentDefinitionVersions(id: string): Promise<DefinitionRevisionHistory> {
  return strictFetch<DefinitionRevisionHistory>(`/agent-definitions/${id}/versions`);
}

export async function saveAgentDefinition(payload: Json): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>("/agent-definitions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function publishAgentDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/agent-definitions/${id}/publish`, { method: "POST" });
}

export async function unpublishAgentDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/agent-definitions/${id}/unpublish`, { method: "POST" });
}

export async function archiveAgentDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/agent-definitions/${id}/archive`, { method: "POST" });
}

export async function deleteAgentDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/agent-definitions/${id}`, { method: "DELETE" });
}

export async function rollbackAgentDefinition(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number },
): Promise<{ ok: boolean; id: string; version: number; status: string; restored_from: DefinitionRevisionSummary; revision: DefinitionRevisionSummary }> {
  return strictFetch(`/agent-definitions/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function activateAgentDefinition(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number } = {},
): Promise<{ ok: boolean; id: string; active_revision: DefinitionRevisionSummary; activation_revision: DefinitionRevisionSummary }> {
  return strictFetch(`/agent-definitions/${id}/activate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export type NodeFieldSpec = {
  name: string;
  label: string;
  field_type: "text" | "textarea" | "number" | "slider" | "bool" | "dropdown" | "secret" | "code";
  description?: string;
  required?: boolean;
  advanced?: boolean;
  default?: unknown;
  options?: string[];
  placeholder?: string;
  min?: number | null;
  max?: number | null;
  step?: number | null;
  options_source?: string;
};

export type NodeDefinitionResponse = {
  type_key: string;
  title?: string;
  description: string;
  category?: string;
  color?: string;
  inputs?: NodeFieldSpec[];
};

export async function getNodeDefinitions(options?: { includeInternal?: boolean }): Promise<NodeDefinitionResponse[]> {
  const suffix = options?.includeInternal ? "?include_internal=true" : "";
  return strictFetch<NodeDefinitionResponse[]>(`/node-definitions${suffix}`);
}

export async function getGuardrailRulesets(): Promise<GuardrailRuleSet[]> {
  return strictFetch<GuardrailRuleSet[]>("/guardrail-rulesets");
}

export async function getGuardrailRulesetVersions(id: string): Promise<DefinitionRevisionHistory> {
  return strictFetch<DefinitionRevisionHistory>(`/guardrail-rulesets/${id}/versions`);
}

export async function getWorkflowSecurityPolicy(workflowId: string): Promise<SecurityPolicyResponse> {
  return strictFetch<SecurityPolicyResponse>(`/workflows/${workflowId}/security-policy`);
}

export async function getAgentSecurityPolicy(agentId: string): Promise<SecurityPolicyResponse> {
  return strictFetch<SecurityPolicyResponse>(`/agents/${agentId}/security-policy`);
}

export async function saveGuardrailRuleset(payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>("/guardrail-rulesets", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function publishGuardrailRuleset(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/guardrail-rulesets/${id}/publish`, { method: "POST" });
}

export async function deleteGuardrailRuleset(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/guardrail-rulesets/${id}`, { method: "DELETE" });
}

export async function deleteNodeDefinition(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/node-definitions/${id}`, { method: "DELETE" });
}

export async function rollbackGuardrailRuleset(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number },
): Promise<{ ok: boolean; id: string; version: number; status: string; restored_from: DefinitionRevisionSummary; revision: DefinitionRevisionSummary }> {
  return strictFetch(`/guardrail-rulesets/${id}/rollback`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function activateGuardrailRuleset(
  id: string,
  payload: { revision_id?: string; revision?: number; version?: number } = {},
): Promise<{ ok: boolean; id: string; active_revision: DefinitionRevisionSummary; activation_revision: DefinitionRevisionSummary }> {
  return strictFetch(`/guardrail-rulesets/${id}/activate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function validateGraph(payload: GraphCanvasPayload): Promise<GraphValidationResponse> {
  return strictFetch<GraphValidationResponse>("/graph/validate", {
      method: "POST",
      body: JSON.stringify(withGraphSchemaVersion(payload)),
    });
}

export async function runGraph(payload: GraphCanvasPayload): Promise<GraphRunResponse> {
  return strictFetch<GraphRunResponse>("/graph/runs", {
    method: "POST",
    body: JSON.stringify(withGraphSchemaVersion(payload)),
  });
}

export async function getRuntimeProviders(): Promise<RuntimeProvidersResponse> {
  return strictFetch<RuntimeProvidersResponse>("/runtime/providers");
}

export async function getUserRuntimeProviders(): Promise<UserRuntimeProviderConfig[]> {
  const response = await strictFetch<UserRuntimeProvidersResponse>("/runtime/user-providers");
  return response.providers ?? [];
}

export async function getUserSkills(): Promise<UserSkillsResponse> {
  return strictFetch<UserSkillsResponse>("/skills/user");
}

export async function saveUserSkills(payload: { skills: string[] }): Promise<UserSkillsResponse> {
  return strictFetch<UserSkillsResponse>("/skills/user", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function saveUserRuntimeProvider(
  provider: string,
  payload: { api_key?: string; model?: string; available_models?: string[]; base_url?: string; preferred?: boolean },
): Promise<UserRuntimeProviderConfig> {
  return strictFetch<UserRuntimeProviderConfig>(
    `/runtime/user-providers/${encodeURIComponent(provider)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
  );
}

export async function deleteUserRuntimeProvider(provider: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/runtime/user-providers/${encodeURIComponent(provider)}`, {
    method: "DELETE",
  });
}

export async function getMemorySession(sessionId: string): Promise<MemorySessionResponse> {
  return strictFetch<MemorySessionResponse>(`/memory/${encodeURIComponent(sessionId)}`);
}

export async function clearMemorySession(sessionId: string): Promise<{ ok: boolean; session_id: string }> {
  return strictFetch<{ ok: boolean; session_id: string }>(
    `/memory/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

export async function getPlatformSettings(): Promise<PlatformSettings> {
  const cached = readCachedValue<PlatformSettings>("platform-settings");
  if (cached) {
    return cached;
  }
  const value = await strictFetch<PlatformSettings>("/platform/settings");
  return writeCachedValue("platform-settings", value, 30000);
}

export async function getOperatorSession(): Promise<OperatorSession> {
  const cached = readCachedValue<OperatorSession>("operator-session");
  if (cached) {
    return cached;
  }
  let value: OperatorSession;
  try {
    value = await strictFetch<OperatorSession>("/auth/session");
  } catch (error) {
    // 401 is an answer ("not signed in"), not a failure. Anything else
    // (backend down, 5xx) throws so the shell can say so instead of
    // pretending the operator signed out.
    if (error instanceof Error && error.message.startsWith("Request failed (401)")) {
      return ANONYMOUS_SESSION;
    }
    throw error;
  }
  return writeCachedValue("operator-session", value, 15000);
}

const ANONYMOUS_SESSION: OperatorSession = {
  authenticated: false,
  actor: "anonymous",
  principal_id: "anonymous",
  principal_type: "user",
  display_name: "Anonymous",
  subject: "",
  auth_mode: "shared-token",
  roles: [],
  capabilities: { can_admin: false, can_builder: false },
  allowed_modes: ["user"],
  default_mode: "user",
  oidc: { configured: false, issuer: "", audience: "", provider: "" },
};

export async function loginWithLocalPassword(payload: {
  username: string;
  password: string;
}): Promise<{ ok: boolean; authenticated: boolean; provider: string; mode: string }> {
  const response = await strictFetch<{ ok: boolean; authenticated: boolean; provider: string; mode: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  invalidateOperatorSessionCache();
  return response;
}

export async function registerWithLocalPassword(payload: {
  username: string;
  email: string;
  display_name: string;
  password: string;
}): Promise<{ ok: boolean; authenticated: boolean; provider: string; mode: string; created: boolean }> {
  const response = await strictFetch<{ ok: boolean; authenticated: boolean; provider: string; mode: string; created: boolean }>("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  invalidateOperatorSessionCache();
  return response;
}

export async function logoutOperator(): Promise<{ ok: boolean }> {
  const response = await strictFetch<{ ok: boolean }>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
  invalidateOperatorSessionCache();
  return response;
}

export async function getPlatformVersionStatus(): Promise<PlatformVersionStatus> {
  const cached = readCachedValue<PlatformVersionStatus>("platform-version");
  if (cached) {
    return cached;
  }
  const value = await strictFetch<PlatformVersionStatus>("/platform/version");
  return writeCachedValue("platform-version", value, 30000);
}

export async function getPlatformHealthDetails(): Promise<PlatformHealthDetails | null> {
  const cached = readCachedValue<PlatformHealthDetails>("platform-health-details");
  if (cached) {
    return cached;
  }

  const value = await strictFetchOrNull<PlatformHealthDetails>("/healthz/details");
  if (!value) {
    return null;
  }

  return writeCachedValue("platform-health-details", value, 30000);
}

export async function getPlatformSecurityPolicy(): Promise<SecurityPolicyResponse> {
  return strictFetch<SecurityPolicyResponse>("/platform/security-policy");
}

export async function savePlatformSettings(payload: Json): Promise<{ ok: boolean }> {
  const result = await strictFetch<{ ok: boolean }>("/platform/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });

  if (isJsonRecord(payload)) {
    const cached = readCachedValue<PlatformSettings>("platform-settings");
    const nextSettings = writeCachedValue(
      "platform-settings",
      {
        ...(cached ?? {}),
        ...payload,
      } as PlatformSettings,
      30000,
    );
    publishPlatformSettingsUpdate(nextSettings);
  } else {
    deleteCachedValue("platform-settings");
  }

  return result;
}

export async function getAgentTemplates(): Promise<AgentTemplate[]> {
  return strictFetch<AgentTemplate[]>("/templates/agents");
}

export async function getTemplateCatalog(): Promise<TemplateCatalogItem[]> {
  return strictFetch<TemplateCatalogItem[]>("/templates/catalog");
}

export async function instantiateAgentTemplate(templateId: string, payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>(`/templates/agents/${templateId}/instantiate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function instantiateWorkflowTemplate(workflowId: string, payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>(`/templates/workflows/${workflowId}/instantiate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getPlaybooks(): Promise<PlaybookDefinition[]> {
  return strictFetch<PlaybookDefinition[]>("/playbooks");
}

export async function getPlaybook(id: string): Promise<PlaybookDefinition | null> {
  return strictFetchOrNull<PlaybookDefinition>(`/playbooks/${id}`);
}

export async function savePlaybook(payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>("/playbooks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function publishPlaybook(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/playbooks/${id}/publish`, { method: "POST" });
}

export async function unpublishPlaybook(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/playbooks/${id}/unpublish`, { method: "POST" });
}

export async function archivePlaybook(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/playbooks/${id}/archive`, { method: "POST" });
}

export async function instantiatePlaybook(playbookId: string, payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>(`/playbooks/${playbookId}/instantiate`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/* ------------------------------------------------------------------ */
/*  Export / import — agents, workflows, playbooks (JSON or YAML)       */
/* ------------------------------------------------------------------ */

export type ExportKind = "agent-definitions" | "workflow-definitions" | "playbooks" | "bundle";
export type ExportFormat = "json" | "yaml";

/** Download a definition (or the full bundle) as a JSON/YAML file. */
export async function downloadDefinitionExport(
  kind: ExportKind,
  id: string | null,
  format: ExportFormat = "json",
): Promise<void> {
  const base = getApiBase();
  const url =
    kind === "bundle"
      ? `${base}/bundle/export?format=${format}`
      : `${base}/${kind}/${id}/export?format=${format}`;
  const res = await fetchWithRetry(url, {
    credentials: "include",
    headers: { ...getRequestIdentityHeaders() },
  });
  if (!res.ok) throw new Error(`Export failed (${res.status})`);
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match?.[1] ?? `lattix-${kind}.${format}`;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

/** Read a JSON/YAML file and apply it to the platform. */
export async function importDefinitionFile(
  kind: ExportKind,
  file: File,
): Promise<{ ok: boolean; id?: string; errors?: string[]; [k: string]: unknown }> {
  const content = await file.text();
  const format: "json" | "yaml" | "auto" = /\.ya?ml$/i.test(file.name)
    ? "yaml"
    : /\.json$/i.test(file.name)
      ? "json"
      : "auto";
  const path = kind === "bundle" ? "/bundle/import" : `/${kind}/import`;
  const res = await fetchWithRetry(`${getApiBase()}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...getRequestIdentityHeaders() },
    body: JSON.stringify({ format, content }),
  });
  if (!res.ok) {
    let detail = `Import failed (${res.status})`;
    try {
      const data = await res.json();
      const raw = (data as { detail?: unknown })?.detail;
      if (typeof raw === "string") {
        detail = raw;
      } else if (raw && typeof raw === "object" && typeof (raw as { message?: unknown }).message === "string") {
        // Structured validation errors (e.g. missing agent/workflow references).
        detail = (raw as { message: string }).message;
      } else if (Array.isArray((data as { errors?: unknown })?.errors)) {
        detail = ((data as { errors: string[] }).errors).join("; ");
      }
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getObservabilityRunTrace(runId: string): Promise<ObservabilityRunTrace | null> {
  return strictFetchOrNull<ObservabilityRunTrace>(`/observability/runs/${runId}/trace`);
}

export async function getObservabilityDashboard(): Promise<ObservabilityDashboardResponse> {
  // Strict (throws on failure) so the page surfaces a real outage as an error
  // instead of silently rendering all-zeros. A genuinely empty platform still
  // returns 200 with total_runs: 0, which the page shows legitimately.
  return strictFetch<ObservabilityDashboardResponse>("/observability/dashboard");
}

export async function getAuditEvents(limit = 200): Promise<{ count: number; events: AuditEvent[] }> {
  const bounded = Math.max(1, Math.min(1000, Math.trunc(limit)));
  return strictFetch<{ count: number; events: AuditEvent[] }>(`/audit/events?limit=${bounded}`);
}

export async function getAtfAlignmentReport(): Promise<AtfAlignmentReport> {
  return strictFetch<AtfAlignmentReport>("/audit/atf-alignment-report");
}

export async function joinCollaborationSession(payload: {
  entity_type: "agent" | "workflow" | "playbook";
  entity_id: string;
  user_id?: string;
  principal_id?: string;
  principal_type?: "user" | "agent" | "service" | "npe";
  auth_subject?: string;
  display_name: string;
  role?: "owner" | "editor" | "viewer";
}): Promise<{ ok: boolean; session: CollaborationSession; participant: { user_id: string; principal_id?: string; principal_type?: "user" | "agent" | "service" | "npe"; auth_subject?: string | null; display_name: string; role: "owner" | "editor" | "viewer" } }> {
  return strictFetch("/collab/sessions/join", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getCollaborationSession(sessionId: string): Promise<CollaborationSession | null> {
  return strictFetchOrNull<CollaborationSession>(`/collab/sessions/${encodeURIComponent(sessionId)}`);
}

export async function syncCollaborationSession(
  sessionId: string,
  payload: {
    user_id?: string;
    principal_id?: string;
    base_version?: number;
    graph_json?: GraphCanvasPayload;
    force?: boolean;
  },
): Promise<{ ok: boolean; conflict?: boolean; message?: string; version: number; graph_json: GraphCanvasPayload; updated_at: string }> {
  return strictFetch(`/collab/sessions/${encodeURIComponent(sessionId)}/sync`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateCollaborationPermissions(
  sessionId: string,
  payload: {
    actor_user_id?: string;
    actor_principal_id?: string;
    target_user_id?: string;
    target_principal_id?: string;
    role: "owner" | "editor" | "viewer";
  },
): Promise<{ ok: boolean; session: CollaborationSession }> {
  return strictFetch(`/collab/sessions/${encodeURIComponent(sessionId)}/permissions`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getIntegrations(): Promise<IntegrationDefinition[]> {
  return strictFetch<IntegrationDefinition[]>("/integrations");
}

export async function getIntegrationStarterTemplates(): Promise<IntegrationStarterTemplate[]> {
  return strictFetch<IntegrationStarterTemplate[]>("/integrations/starters");
}

export async function getMcpConnections(): Promise<MCPConnectionDefinition[]> {
  return strictFetch<MCPConnectionDefinition[]>("/integrations/mcp");
}

export async function getMcpStarterTemplates(): Promise<MCPStarterTemplate[]> {
  return strictFetch<MCPStarterTemplate[]>("/integrations/mcp/starters");
}

export async function saveMcpConnection(payload: Json): Promise<{ ok: boolean; id: string; status: MCPConnectionDefinition["status"] }> {
  return strictFetch<{ ok: boolean; id: string; status: MCPConnectionDefinition["status"] }>("/integrations/mcp", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function validateMcpConnection(id: string): Promise<MCPConnectionValidationResponse> {
  return strictFetch<MCPConnectionValidationResponse>(`/integrations/mcp/${id}/validate`, {
    method: "POST",
  });
}

export async function approveMcpConnection(id: string): Promise<{ ok: boolean; id: string; status: MCPConnectionDefinition["status"] }> {
  return strictFetch<{ ok: boolean; id: string; status: MCPConnectionDefinition["status"] }>(`/integrations/mcp/${id}/approve`, {
    method: "POST",
  });
}

export async function saveIntegration(payload: Json): Promise<{ ok: boolean; id: string }> {
  return strictFetch<{ ok: boolean; id: string }>("/integrations", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function testIntegration(id: string): Promise<IntegrationTestResponse> {
  return strictFetch<IntegrationTestResponse>(`/integrations/${id}/test`, { method: "POST" });
}

export async function deleteIntegration(id: string): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>(`/integrations/${id}`, { method: "DELETE" });
}

export async function getIntegrationOAuthStatus(id: string): Promise<IntegrationOAuthStatus> {
  return strictFetch<IntegrationOAuthStatus>(`/integrations/${id}/oauth/status`);
}

export async function connectIntegrationOAuth(
  id: string,
  payload?: { return_to?: string },
): Promise<IntegrationOAuthConnectResponse> {
  return strictFetch<IntegrationOAuthConnectResponse>(`/integrations/${id}/oauth/connect`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

export async function refreshIntegrationOAuth(
  id: string,
): Promise<{ ok: boolean; status: IntegrationOAuthStatus }> {
  return strictFetch<{ ok: boolean; status: IntegrationOAuthStatus }>(`/integrations/${id}/oauth/refresh`, {
    method: "POST",
  });
}

export async function disconnectIntegrationOAuth(
  id: string,
): Promise<{ ok: boolean; status: IntegrationOAuthStatus }> {
  return strictFetch<{ ok: boolean; status: IntegrationOAuthStatus }>(`/integrations/${id}/oauth/disconnect`, {
    method: "POST",
  });
}

/* ------------------------------------------------------------------ */
/*  Unified Settings (LOCUS-353)                                       */
/* ------------------------------------------------------------------ */

/** Store a provider API key in the OS keychain. Widening: the desktop shell
 * confirms it (models.provider.key.set). The key is never returned. */
export async function setProviderKey(providerId: string, apiKey: string): Promise<Record<string, unknown>> {
  return strictFetch(`/models/providers/${encodeURIComponent(providerId)}/key`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export async function clearProviderKey(providerId: string): Promise<Record<string, unknown>> {
  return strictFetch(`/models/providers/${encodeURIComponent(providerId)}/key`, { method: "DELETE" });
}

export type ComputerUseStatus = {
  mode: string;
  panicked: boolean;
  panic_source: string;
  inflight_actions: number;
  installed?: boolean;
};

export async function getComputerUseStatus(): Promise<ComputerUseStatus> {
  return strictFetch<ComputerUseStatus>("/computer-use/status");
}

/** Stop every computer-use action now (narrowing; never needs a confirmation). */
export async function triggerComputerUsePanic(): Promise<Record<string, unknown>> {
  return strictFetch("/computer-use/panic", { method: "POST", body: JSON.stringify({}) });
}

/** Clear the panic stop. Widening: confirmed in the desktop shell (computer_use.reset). */
export async function resetComputerUse(): Promise<ComputerUseStatus> {
  return strictFetch<ComputerUseStatus>("/computer-use/reset", { method: "POST", body: JSON.stringify({}) });
}

export const BROWSER_TIERS = ["strict", "assisted", "trusted", "open"] as const;
export type BrowserTier = (typeof BROWSER_TIERS)[number];

export type UserBrowserTierSettings = {
  tier: BrowserTier;
  effective_tier: BrowserTier;
  allowlisted_sites: string[];
  granted_sites: string[];
  consent: { tier: string; recorded_at_iso?: string; risk_acknowledged?: string } | null;
  tier_risks?: Partial<Record<BrowserTier, string>>;
};

export type BrowserTierChange = {
  tier: BrowserTier;
  allowlisted_sites: string[];
  granted_sites: string[];
};

export async function getUserBrowserTier(): Promise<UserBrowserTierSettings> {
  return strictFetch<UserBrowserTierSettings>("/user-browser/tier");
}

/** Mirrors TierStore.update: a higher tier than the effective one, or any new
 * site, widens. Moving down with no new sites narrows. The backend decides. */
export function isBrowserTierWidening(current: UserBrowserTierSettings, next: BrowserTierChange): boolean {
  if (next.tier === "strict") {
    return false;
  }
  const rank = (tier: string) => BROWSER_TIERS.indexOf(tier as BrowserTier);
  const isSubset = (items: string[], of: string[]) => items.every((item) => of.includes(item));
  return (
    rank(next.tier) > rank(current.effective_tier ?? current.tier) ||
    !isSubset(next.allowlisted_sites, current.allowlisted_sites) ||
    !isSubset(next.granted_sites, current.granted_sites)
  );
}

const TIER_CONFIRMATION_REFUSAL = "needs confirmation in the Locus desktop app";

async function confirmBrowserTierInShell(next: BrowserTierChange): Promise<UserBrowserTierSettings> {
  const invoke = getDesktopInvoke();
  if (!invoke) {
    throw new DesktopConfirmationError("the desktop shell is not available");
  }
  let relayed: unknown;
  try {
    // Tauri passes command arguments in camelCase (confirm_browser_tier, LOCUS-350).
    relayed = await invoke("confirm_browser_tier", {
      tier: next.tier,
      allowlistedSites: next.allowlisted_sites,
      grantedSites: next.granted_sites,
    });
  } catch (error) {
    const reason = typeof error === "string" ? error : error instanceof Error ? error.message : String(error);
    if (reason === "cancelled") {
      throw new DesktopConfirmationCancelledError();
    }
    throw new DesktopConfirmationError(reason);
  }
  const text = typeof relayed === "string" ? relayed : "";
  setApiConnected(true);
  return (text.trim() ? JSON.parse(text) : {}) as UserBrowserTierSettings;
}

/**
 * Change the user-browser tier (LOCUS-350). Widening goes through the desktop
 * shell's native dialog (`confirm_browser_tier`), which signs and sends the
 * request itself; the webview never sends a widening change as a plain request
 * on the desktop. On the web profile widening needs `acknowledgeRisk` (the UI
 * shows the risk text first). Narrowing is a plain request.
 */
export async function setUserBrowserTier(
  current: UserBrowserTierSettings,
  next: BrowserTierChange,
  options: { acknowledgeRisk?: boolean } = {},
): Promise<UserBrowserTierSettings> {
  const widening = isBrowserTierWidening(current, next);
  const desktop = getDesktopInvoke() !== null;
  if (widening && desktop) {
    return confirmBrowserTierInShell(next);
  }
  if (widening && !options.acknowledgeRisk) {
    throw new Error("Widening the browser tier needs you to acknowledge its risk first.");
  }
  try {
    return await strictFetch<UserBrowserTierSettings>("/user-browser/tier", {
      method: "PUT",
      body: JSON.stringify({ ...next, ...(widening ? { acknowledge_risk: true } : {}) }),
    });
  } catch (error) {
    // The backend is the authority: if it still sees a widening (stale state),
    // confirm it in the shell instead of retrying silently.
    if (desktop && error instanceof Error && error.message.includes(TIER_CONFIRMATION_REFUSAL)) {
      return confirmBrowserTierInShell(next);
    }
    throw error;
  }
}

export type SystemUpdateStatus = {
  active_runs?: number;
  may_install?: boolean;
  loop?: { lock_owner?: string | null; held?: boolean } | null;
  handshake?: Record<string, unknown>;
};

export async function getSystemUpdateStatus(): Promise<SystemUpdateStatus> {
  return strictFetch<SystemUpdateStatus>("/system/update/status");
}

export type TelemetrySummary = {
  since_ns: number;
  until_ns: number;
  runs: number;
  empty?: boolean;
  [key: string]: unknown;
};

export async function getTelemetrySummary(windowHours = 24): Promise<TelemetrySummary> {
  return strictFetch<TelemetrySummary>(`/telemetry/summary?window_hours=${encodeURIComponent(String(windowHours))}`);
}

/* ------------------------------------------------------------------ */
/*  Self-improvement loop (LOCUS-338/349) and its Linear intake        */
/* ------------------------------------------------------------------ */

export type LoopStatus = {
  enabled: boolean;
  disabled_reason: string;
  /** True when LOCUS_LOOP_DISABLED pins the loop off (the UI cannot clear it). */
  disabled_by_environment: boolean;
  runs_today: number;
  max_runs_per_day: number;
  active_run: Record<string, unknown> | null;
  last_run: { run_id?: string; issue?: string; outcome?: string; finished_at?: string } | null;
  open_prs: unknown[];
  autostart: { enabled: boolean; repo_path: string };
  /** Whether LINEAR_API_KEY resolves (env or OS keychain). Never the value. */
  linear: { api_key_configured: boolean };
};

export async function getLoopStatus(): Promise<LoopStatus> {
  return strictFetch<LoopStatus>("/loop/status");
}

/** Clear the file kill switch. Widening: confirmed in the desktop shell (loop.enable). */
export async function enableLoop(): Promise<LoopStatus> {
  return strictFetch<LoopStatus>("/loop/enable", { method: "POST", body: JSON.stringify({}) });
}

/** Set the file kill switch (the loop stops before its next step). */
export async function disableLoop(): Promise<LoopStatus> {
  return strictFetch<LoopStatus>("/loop/disable", { method: "POST", body: JSON.stringify({}) });
}

/** Start the loop with the desktop app on this checkout (needs WORKFLOW.md).
 * Widening: confirmed in the desktop shell (loop.autostart.enable). */
export async function enableLoopAutostart(repoPath: string): Promise<LoopStatus> {
  return strictFetch<LoopStatus>("/loop/autostart", { method: "POST", body: JSON.stringify({ repo_path: repoPath }) });
}

export async function disableLoopAutostart(): Promise<LoopStatus> {
  return strictFetch<LoopStatus>("/loop/autostart", { method: "DELETE" });
}
