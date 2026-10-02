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
export type { ObservabilityRunTrace } from "@/types/locus";

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
const EMPTY_WORKFLOWS: WorkflowDefinition[] = [];
const EMPTY_ARTIFACTS: ArtifactSummary[] = [];
const EMPTY_AGENTS: AgentDefinition[] = [];
const EMPTY_GUARDRAILS: GuardrailRuleSet[] = [];
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

async function safeFetch<T>(path: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const requestHeaders = await getRequestAuthHeaders();
    const res = await fetchWithRetry(
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

    if (!res.ok) {
      setApiConnected(true); // Server reachable, just returned an error
      return fallback;
    }

    setApiConnected(true);
    return (await res.json()) as T;
  } catch {
    setApiConnected(false);
    return fallback;
  }
}

async function strictFetch<T>(path: string, init?: RequestInit): Promise<T> {
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
    throw new Error(`Request failed (${res.status})${details ? `: ${details}` : ""}`);
  }

  return (await res.json()) as T;
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
  return safeFetch<WorkflowDefinition[]>("/workflows/published", EMPTY_WORKFLOWS);
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

export function streamWorkflowRun(
  id: string,
  handlers: {
    onMessage: (event: { id: string; type: string; createdAt: string; payload: Record<string, unknown> }) => void;
    onError?: () => void;
  },
): () => void {
  const controller = new AbortController();
  void (async () => {
    try {
      const requestHeaders = await getRequestAuthHeaders();
      const res = await fetch(`${getApiBase()}/workflow-runs/${encodeURIComponent(id)}/stream`, {
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
        return;
      }

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
          try {
            handlers.onMessage(
              JSON.parse(line.slice(5).trim()) as {
                id: string;
                type: string;
                createdAt: string;
                payload: Record<string, unknown>;
              },
            );
          } catch {
            handlers.onError?.();
          }
        }
      }
    } catch {
      if (!controller.signal.aborted) {
        handlers.onError?.();
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
};

// Fetch-based SSE consumer (fetch can carry identity headers; EventSource cannot).
// Resolves with the server's stream_end reason ("terminal" | "timeout"); throws on
// transport/HTTP failure so callers can fall back to polling.
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
    } else if (eventName === "stream_end") {
      return String((parsed as { reason?: string }).reason ?? "terminal");
    }
    return null;
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return "terminal";
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
  return safeFetch<InboxGroup[]>("/inbox/groups", []);
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
  return safeFetch<UserSettings>("/user/settings", {
    default_working_folder: "",
    preferred_model: "",
    preferred_reasoning_effort: "",
    default_mode: "execute",
  });
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
  return safeFetch<WorkspaceFolders>(`/workspace/folders${suffix}`, {
    root: "/projects",
    path: "",
    exists: false,
    is_git: false,
    folders: [],
  });
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
  const res = await safeFetch<{ servers: McpServer[] }>("/mcp/servers", { servers: [] });
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
  const res = await safeFetch<{ escalations: RunEscalation[] }>(
    `/workflow-runs/${encodeURIComponent(runId)}/escalations`,
    { escalations: [] },
  );
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
  return safeFetch<ArtifactSummary[]>("/artifacts", EMPTY_ARTIFACTS);
}

export async function getArtifact(id: string): Promise<ArtifactDetail | null> {
  return safeFetch<ArtifactDetail | null>(`/artifacts/${id}`, null);
}

// Builder mode endpoints
export async function getWorkflowDefinitions(): Promise<WorkflowDefinition[]> {
  return safeFetch<WorkflowDefinition[]>("/workflow-definitions", EMPTY_WORKFLOWS);
}

export async function getWorkflowDefinitionVersions(id: string): Promise<DefinitionRevisionHistory> {
  return strictFetch<DefinitionRevisionHistory>(`/workflow-definitions/${id}/versions`);
}

export async function getWorkflowDefinition(id: string): Promise<WorkflowDefinition | null> {
  // single-definition fetch includes graph_json (the list endpoint excludes it)
  return safeFetch<WorkflowDefinition | null>(`/workflow-definitions/${id}`, null);
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
  return safeFetch<AgentDefinition[]>("/agent-definitions", EMPTY_AGENTS);
}

export async function getAgentDefinition(id: string): Promise<AgentDefinition | null> {
  return safeFetch<AgentDefinition | null>(`/agent-definitions/${id}`, null);
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
  return safeFetch(`/node-definitions${suffix}`, [
    { type_key: "locus/trigger", title: "Trigger", description: "Workflow trigger/intake node", category: "Core", color: "#6ca0ff" },
    { type_key: "locus/agent", title: "Agent", description: "Delegates to a selected agent definition", category: "Agent", color: "#1f7f53" },
    { type_key: "locus/prompt", title: "Prompt", description: "Compose reusable system prompt instructions and pass them to agent nodes", category: "Agent", color: "#5f4bb6" },
    { type_key: "locus/tool-call", title: "Tool / API Call", description: "Invokes external API or tool", category: "Integration", color: "#6fd3ff" },
    { type_key: "locus/retrieval", title: "Retrieval", description: "Retrieves ranked context", category: "Knowledge", color: "#8a6717" },
    { type_key: "locus/guardrail", title: "Guardrail", description: "Checks content against guardrail rules", category: "Control", color: "#9f3550" },
    { type_key: "locus/human-review", title: "Human Review", description: "Requires human approval before next step", category: "Control", color: "#8d5c1a" },
    { type_key: "locus/manifold", title: "Manifold", description: "Consolidates multiple inbound flows via AND/OR logic", category: "Logic", color: "#7863d3" },
    { type_key: "locus/router", title: "Router", description: "Makes deterministic routing decisions from rules, thresholds, or keyword classifiers", category: "Logic", color: "#3158a4" },
    { type_key: "locus/iterator", title: "Iterator", description: "Processes lists, batches, and paginated payloads with loop and done branches", category: "Logic", color: "#5670d9" },
    { type_key: "locus/transform", title: "Transform", description: "Deterministically shapes payloads without an LLM or external tool hop", category: "Logic", color: "#1e8a72" },
    { type_key: "locus/event", title: "Event", description: "Publishes or consumes workflow events with structured envelopes and receipts", category: "Integration", color: "#0f8c8c" },
    { type_key: "locus/data-store", title: "Data Store", description: "Creates, reads, updates, appends, or deletes business records inside a scoped data store", category: "Integration", color: "#6e7c2d" },
    { type_key: "locus/error-handler", title: "Error Handler", description: "Normalizes failures and emits fallback payloads and recovery status", category: "Control", color: "#aa5a2f" },
    { type_key: "locus/wait", title: "Wait", description: "Delays, times out, or resumes execution windows with explicit branches", category: "Control", color: "#8c6a13" },
    { type_key: "locus/output", title: "Output", description: "Final output emission", category: "Core", color: "#69a3ff" },
  ]);
}

export async function getGuardrailRulesets(): Promise<GuardrailRuleSet[]> {
  return safeFetch<GuardrailRuleSet[]>("/guardrail-rulesets", EMPTY_GUARDRAILS);
}

export async function getGuardrailRulesetVersions(id: string): Promise<DefinitionRevisionHistory> {
  return strictFetch<DefinitionRevisionHistory>(`/guardrail-rulesets/${id}/versions`);
}

export async function getWorkflowSecurityPolicy(workflowId: string): Promise<SecurityPolicyResponse> {
  return safeFetch<SecurityPolicyResponse>(`/workflows/${workflowId}/security-policy`, {
    immutable_baseline: {} as SecurityPolicyResponse["immutable_baseline"],
    platform_defaults: {} as SecurityPolicyResponse["platform_defaults"],
    workflow_overrides: {},
    agent_overrides: {},
    effective: {} as SecurityPolicyResponse["effective"],
  });
}

export async function getAgentSecurityPolicy(agentId: string): Promise<SecurityPolicyResponse> {
  return safeFetch<SecurityPolicyResponse>(`/agents/${agentId}/security-policy`, {
    immutable_baseline: {} as SecurityPolicyResponse["immutable_baseline"],
    platform_defaults: {} as SecurityPolicyResponse["platform_defaults"],
    workflow_overrides: {},
    agent_overrides: {},
    effective: {} as SecurityPolicyResponse["effective"],
  });
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
  return safeFetch<GraphValidationResponse>(
    "/graph/validate",
    { valid: true, issues: [] },
    {
      method: "POST",
      body: JSON.stringify(withGraphSchemaVersion(payload)),
    },
  );
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
  const response = await safeFetch<UserRuntimeProvidersResponse>("/runtime/user-providers", {
    principal_id: "anonymous",
    providers: [],
  });
  return response.providers ?? [];
}

export async function getUserSkills(): Promise<UserSkillsResponse> {
  return safeFetch<UserSkillsResponse>("/skills/user", {
    principal_id: "anonymous",
    skills: [],
    updated_at: "",
  });
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
  return safeFetch<MemorySessionResponse>(`/memory/${encodeURIComponent(sessionId)}`, {
    session_id: sessionId,
    count: 0,
    entries: [],
  });
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
  const value = await safeFetch<PlatformSettings>("/platform/settings", {
    org_name: "Lattix Locus",
    org_slug: "lattix-locus",
    support_email: "support@lattix.io",
    website: "https://lattix.io",
    console_classification_banner_enabled: true,
    console_classification_banner_text: "Internal • Operational Console",
    console_classification_banner_background_color: "#2e2a28",
    console_classification_banner_text_color: "#e7dcc0",
    default_kickoff_workflow: "Auto-select from intent",
    preferred_review_depth: "Standard",
    idle_timeout: "30 minutes",
    local_only_mode: true,
    mask_secrets_in_events: true,
    require_human_approval: false,
    require_human_approval_for_high_risk_tools: true,
    emergency_read_only_mode: false,
    block_new_runs: false,
    block_graph_runs: false,
    block_tool_calls: false,
    block_retrieval_calls: false,
    require_authenticated_requests: false,
    require_a2a_runtime_headers: false,
    a2a_require_signed_messages: true,
    a2a_replay_protection: true,
    default_guardrail_ruleset_id: null,
    global_blocked_keywords: [],
    tenant_scoped_skills: [],
    collaboration_max_agents: 8,
    max_tool_calls_per_run: 8,
    max_retrieval_items: 8,
    default_runtime_engine: "native",
    default_runtime_strategy: "single",
    default_hybrid_runtime_routing: {
      default: "native",
      orchestration: "native",
      retrieval: "native",
      tooling: "native",
      collaboration: "native",
    },
    allowed_runtime_engines: ["native"],
    allow_runtime_engine_override: false,
    enforce_runtime_engine_allowlist: true,
    enforce_egress_allowlist: false,
    allowed_egress_hosts: [],
    enforce_local_network_only: true,
    allow_local_network_hostnames: ["localhost", ".local"],
    allowed_retrieval_sources: [],
    retrieval_require_local_source_url: true,
    allowed_mcp_server_urls: [],
    mcp_require_local_server: true,
    high_risk_tool_patterns: [],
    enable_foss_guardrail_signals: true,
    foss_guardrail_signal_enforcement: "block_high",
  });
  return writeCachedValue("platform-settings", value, 30000);
}

export async function getOperatorSession(): Promise<OperatorSession> {
  const cached = readCachedValue<OperatorSession>("operator-session");
  if (cached) {
    return cached;
  }
  const value = await safeFetch<OperatorSession>("/auth/session", {
    authenticated: false,
    actor: "anonymous",
    principal_id: "anonymous",
    principal_type: "user",
    display_name: "Anonymous",
    subject: "",
    email: "",
    preferred_username: "",
    auth_mode: "shared-token",
    provider: "",
    roles: [],
    capabilities: {
      can_admin: false,
      can_builder: false,
    },
    allowed_modes: ["user"],
    default_mode: "user",
    oidc: {
      configured: false,
      issuer: "",
      audience: "",
      provider: "",
      validation_error: "",
      browser_flow_configured: false,
      browser_flow_error: "",
    },
  });
  return writeCachedValue("operator-session", value, 15000);
}

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
  const value = await safeFetch<PlatformVersionStatus>("/platform/version", {
    current_version: "0.0.0",
    latest_version: "0.0.0",
    update_available: false,
    status: "unknown",
    install_mode: "wheel",
    update_command: "lattix update",
    release_notes_url: "",
    checked_at: new Date().toISOString(),
    source: "",
    summary: "Version metadata is unavailable right now.",
  });
  return writeCachedValue("platform-version", value, 30000);
}

export async function getPlatformHealthDetails(): Promise<PlatformHealthDetails | null> {
  const cached = readCachedValue<PlatformHealthDetails>("platform-health-details");
  if (cached) {
    return cached;
  }

  const value = await safeFetch<PlatformHealthDetails | null>("/healthz/details", null);
  if (!value) {
    return null;
  }

  return writeCachedValue("platform-health-details", value, 30000);
}

export async function getPlatformSecurityPolicy(): Promise<SecurityPolicyResponse> {
  return safeFetch<SecurityPolicyResponse>("/platform/security-policy", {
    immutable_baseline: {
      enforce_capability_filter: true,
      enforce_policy_gate: true,
      fail_closed_policy_decisions: true,
      enforce_signed_a2a_messages: true,
      enforce_a2a_replay_protection: true,
      require_readonly_rootfs_for_sandbox: true,
      require_non_root_sandbox_user: true,
      require_egress_mediation_when_network_enabled: true,
      allow_filter_chain_reordering: false,
      allow_custom_policy_code: false,
    },
    platform_defaults: {
      classification: "internal",
      guardrail_ruleset_id: null,
      blocked_keywords: [],
      allowed_egress_hosts: [],
      allowed_retrieval_sources: [],
      allowed_mcp_server_urls: [],
      allowed_runtime_engines: ["native"],
      allowed_memory_scopes: ["run", "session", "user", "tenant", "agent", "workflow", "global"],
      max_tool_calls_per_run: 8,
      max_retrieval_items: 8,
      max_collaboration_agents: 8,
      require_human_approval: false,
      require_human_approval_for_high_risk_tools: true,
      allow_runtime_override: false,
      enable_platform_signals: true,
      platform_signal_enforcement: "block_high",
    },
    workflow_overrides: {},
    agent_overrides: {},
    effective: {
      classification: "internal",
      guardrail_ruleset_id: null,
      blocked_keywords: [],
      allowed_egress_hosts: [],
      allowed_retrieval_sources: [],
      allowed_mcp_server_urls: [],
      allowed_runtime_engines: ["native"],
      allowed_memory_scopes: ["run", "session", "user", "tenant", "agent", "workflow", "global"],
      max_tool_calls_per_run: 8,
      max_retrieval_items: 8,
      max_collaboration_agents: 8,
      require_human_approval: false,
      require_human_approval_for_high_risk_tools: true,
      allow_runtime_override: false,
      enable_platform_signals: true,
      platform_signal_enforcement: "block_high",
    },
    backend_enforced_controls: [],
    configurable_controls: [],
  });
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
  return safeFetch<AgentTemplate[]>("/templates/agents", []);
}

export async function getTemplateCatalog(): Promise<TemplateCatalogItem[]> {
  return safeFetch<TemplateCatalogItem[]>("/templates/catalog", []);
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
  return safeFetch<PlaybookDefinition[]>("/playbooks", []);
}

export async function getPlaybook(id: string): Promise<PlaybookDefinition | null> {
  return safeFetch<PlaybookDefinition | null>(`/playbooks/${id}`, null);
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
  return safeFetch<ObservabilityRunTrace | null>(`/observability/runs/${runId}/trace`, null);
}

export async function getObservabilityDashboard(): Promise<ObservabilityDashboardResponse> {
  // Strict (throws on failure) so the page surfaces a real outage as an error
  // instead of silently rendering all-zeros. A genuinely empty platform still
  // returns 200 with total_runs: 0, which the page shows legitimately.
  return strictFetch<ObservabilityDashboardResponse>("/observability/dashboard");
}

export async function getAuditEvents(limit = 200): Promise<{ count: number; events: AuditEvent[] }> {
  const bounded = Math.max(1, Math.min(1000, Math.trunc(limit)));
  return safeFetch<{ count: number; events: AuditEvent[] }>(`/audit/events?limit=${bounded}`, {
    count: 0,
    events: [],
  });
}

export async function getAtfAlignmentReport(): Promise<AtfAlignmentReport> {
  return safeFetch<AtfAlignmentReport>("/audit/atf-alignment-report", {
    generated_at: new Date().toISOString(),
    framework: "CSA Agentic Trust Framework",
    coverage_percent: 0,
    maturity_estimate: "intern",
    pillars: {
      identity: { status: "partial", controls: {}, gaps: [] },
      behavior_monitoring: { status: "partial", controls: {}, gaps: [] },
      data_governance: { status: "partial", controls: {}, gaps: [] },
      segmentation: { status: "partial", controls: {}, gaps: [] },
      incident_response: { status: "partial", controls: {}, gaps: [] },
    },
    evidence: {
      audit_window_hours: 24,
      audit_event_count_24h: 0,
      audit_allowed_24h: 0,
      audit_blocked_24h: 0,
      audit_error_24h: 0,
      total_audit_events: 0,
      run_count_total: 0,
    },
  });
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
  return safeFetch<CollaborationSession | null>(`/collab/sessions/${encodeURIComponent(sessionId)}`, null);
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
  return safeFetch<IntegrationDefinition[]>("/integrations", []);
}

export async function getIntegrationStarterTemplates(): Promise<IntegrationStarterTemplate[]> {
  return safeFetch<IntegrationStarterTemplate[]>("/integrations/starters", []);
}

export async function getMcpConnections(): Promise<MCPConnectionDefinition[]> {
  return safeFetch<MCPConnectionDefinition[]>("/integrations/mcp", []);
}

export async function getMcpStarterTemplates(): Promise<MCPStarterTemplate[]> {
  return safeFetch<MCPStarterTemplate[]>("/integrations/mcp/starters", []);
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
