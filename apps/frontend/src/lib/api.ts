import {
  AgentDefinition,
  AgentTemplate,
  ArtifactDetail,
  ArtifactSummary,
  AtfAlignmentReport,
  AuditEvent,
  CollaborationSession,
  GuardrailRuleSet,
  InboxItem,
  IntegrationDefinition,
  ObservabilityRunTrace,
  OperatorSession,
  PlaybookDefinition,
  PlatformVersionStatus,
  PlatformSettings,
  SecurityPolicyResponse,
  TemplateCatalogItem,
  WorkflowDefinition,
  WorkflowRunEvent,
  WorkflowRunSummary,
} from "@/types/frontier";
export type { ObservabilityRunTrace } from "@/types/frontier";
import {
  mockAgentDefinitions,
  mockArtifacts,
  mockEvents,
  mockGuardrailRulesets,
  mockInbox,
  mockPublishedWorkflows,
  mockRuns,
  mockWorkflowDefinitions,
} from "@/lib/mock-data";

/* ------------------------------------------------------------------ */
/*  Configuration helpers                                              */
/* ------------------------------------------------------------------ */

function getApiBase(): string {
  if (typeof window === "undefined") {
    return process.env.API_BASE_URL_INTERNAL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }

  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api";
}

function isMockDataEnabled(): boolean {
  const flag = process.env.NEXT_PUBLIC_ENABLE_MOCK_DATA ?? "";
  return flag === "1" || flag.toLowerCase() === "true";
}

function getRequestIdentityHeaders(): Record<string, string> {
  const actor = (process.env.NEXT_PUBLIC_FRONTIER_ACTOR ?? "").trim();
  const headers: Record<string, string> = {};
  if (actor) {
    headers["x-frontier-actor"] = actor;
  }
  return headers;
}

/**
 * Server-side only: forward the incoming request's cookies (including the
 * `frontier_operator_session` casdoor session) to the backend, so server
 * components authenticate as the logged-in user instead of anonymously.
 * Without this, server-side calls hit the backend with no session → 401 →
 * the UI silently falls back to mock data. Dynamically imports `next/headers`
 * so the client bundle is unaffected.
 */
async function getServerAuthHeaders(): Promise<Record<string, string>> {
  if (typeof window !== "undefined") return {};
  try {
    const { headers } = await import("next/headers");
    const incoming = await headers();
    const cookie = incoming.get("cookie");
    return cookie ? { cookie } : {};
  } catch {
    return {};
  }
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

const FRONTIER_GRAPH_SCHEMA_VERSION = "frontier-graph/1.0";

export type GraphCanvasPayload = {
  schema_version?: string;
  nodes: Array<{ id: string; title: string; type: string; x: number; y: number; config?: Record<string, unknown> }>;
  links: Array<{ from: string; to: string; from_port?: string; to_port?: string }>;
  input?: Record<string, unknown>;
};

function withGraphSchemaVersion(payload: GraphCanvasPayload): GraphCanvasPayload {
  return {
    ...payload,
    schema_version: payload.schema_version ?? FRONTIER_GRAPH_SCHEMA_VERSION,
  };
}

export type RuntimeProvider = {
  provider: string;
  configured: boolean;
  model: string;
  mode: "live" | "simulated";
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
  artifacts: ArtifactSummary[];
  status: string;
  participants?: RunParticipants;
  changed_files?: ChangedFile[];
  working_folder?: string;
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
    const res = await fetchWithRetry(
      `${getApiBase()}${path}`,
      {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...getRequestIdentityHeaders(),
          ...(await getServerAuthHeaders()),
          ...(init?.headers ?? {}),
        },
        cache: "no-store",
      },
    );

    if (!res.ok) {
      setApiConnected(true); // Server reachable, just returned an error
      if (isMockDataEnabled()) return fallback;
      return fallback;
    }

    setApiConnected(true);
    return (await res.json()) as T;
  } catch {
    setApiConnected(false);
    if (!isMockDataEnabled()) {
      // Return fallback even when mock is disabled so pages don't crash,
      // but the connectivity listener will trigger a UI warning.
      return fallback;
    }
    return fallback;
  }
}

async function strictFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetchWithRetry(
    `${getApiBase()}${path}`,
    {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...getRequestIdentityHeaders(),
        ...(await getServerAuthHeaders()),
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    },
  );

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

export async function getPublishedWorkflows(): Promise<WorkflowDefinition[]> {
  return safeFetch<WorkflowDefinition[]>("/workflows/published", mockPublishedWorkflows);
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
    const res = await fetch(`${getApiBase()}/workflow-runs`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...getRequestIdentityHeaders(),
      },
      body: JSON.stringify(payload),
      cache: "no-store",
      signal: timeoutController.signal,
    });

    if (!res.ok) {
      let details = "";
      try {
        details = await res.text();
      } catch {
        details = "";
      }
      throw new Error(`Failed to create run (${res.status})${details ? `: ${details}` : ""}`);
    }

    return (await res.json()) as { id: string; status: string };
  } catch (error) {
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
  return safeFetch<WorkflowRunSummary[]>(`/workflow-runs${suffix}`, mockRuns);
}

export async function getWorkflowRun(_id: string): Promise<WorkflowRunDetail> {
  return safeFetch<WorkflowRunDetail>(`/workflow-runs/${_id}`, {
    artifacts: mockArtifacts,
    status: "Running",
    graph: { nodes: [], links: [] },
    agent_traces: [],
    approvals: { required: false, pending: false },
  });
}

export async function getWorkflowRunEvents(id: string): Promise<WorkflowRunEvent[]> {
  return safeFetch<WorkflowRunEvent[]>(`/workflow-runs/${id}/events`, mockEvents);
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
  return safeFetch<{ ok: boolean }>(`/workflow-runs/${id}/archive`, { ok: true }, { method: "POST" });
}

export async function createArtifactVersion(
  id: string,
  payload: Json,
): Promise<{ ok: boolean; artifactId: string }> {
  return safeFetch(`/artifacts/${id}/versions`, { ok: true, artifactId: id }, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function submitApproval(payload: Json): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>("/approvals", { ok: true }, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getInbox(): Promise<InboxItem[]> {
  return safeFetch<InboxItem[]>("/inbox", mockInbox);
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
  return safeFetch<ArtifactSummary[]>("/artifacts", mockArtifacts);
}

export async function getArtifact(id: string): Promise<ArtifactDetail | null> {
  return safeFetch<ArtifactDetail | null>(`/artifacts/${id}`, null);
}

// Builder mode endpoints
export async function getWorkflowDefinitions(): Promise<WorkflowDefinition[]> {
  return safeFetch<WorkflowDefinition[]>("/workflow-definitions", mockWorkflowDefinitions);
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

export async function archiveWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/workflow-definitions/${id}/archive`, { ok: true }, { method: "POST" });
}

export async function deleteWorkflowDefinition(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/workflow-definitions/${id}`, { ok: true }, { method: "DELETE" });
}

export async function getAgentDefinitions(): Promise<AgentDefinition[]> {
  return safeFetch<AgentDefinition[]>("/agent-definitions", mockAgentDefinitions);
}

export async function getAgentDefinition(id: string): Promise<AgentDefinition | null> {
  return safeFetch<AgentDefinition | null>(`/agent-definitions/${id}`, null);
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

export async function deleteAgentDefinition(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/agent-definitions/${id}`, { ok: true }, { method: "DELETE" });
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
    { type_key: "frontier/trigger", title: "Trigger", description: "Workflow trigger/intake node", category: "Core", color: "#6ca0ff" },
    { type_key: "frontier/agent", title: "Agent", description: "Delegates to a selected agent definition", category: "Agent", color: "#1f7f53" },
    { type_key: "frontier/prompt", title: "Prompt", description: "Compose reusable system prompt instructions and pass them to agent nodes", category: "Agent", color: "#5f4bb6" },
    { type_key: "frontier/tool-call", title: "Tool / API Call", description: "Invokes external API or tool", category: "Integration", color: "#6fd3ff" },
    { type_key: "frontier/retrieval", title: "Retrieval", description: "Retrieves ranked context", category: "Knowledge", color: "#8a6717" },
    { type_key: "frontier/guardrail", title: "Guardrail", description: "Checks content against guardrail rules", category: "Control", color: "#9f3550" },
    { type_key: "frontier/human-review", title: "Human Review", description: "Requires human approval before next step", category: "Control", color: "#8d5c1a" },
    { type_key: "frontier/manifold", title: "Manifold", description: "Consolidates multiple inbound flows via AND/OR logic", category: "Logic", color: "#7863d3" },
    { type_key: "frontier/output", title: "Output", description: "Final output emission", category: "Core", color: "#69a3ff" },
  ]);
}

export async function getGuardrailRulesets(): Promise<GuardrailRuleSet[]> {
  return safeFetch<GuardrailRuleSet[]>("/guardrail-rulesets", mockGuardrailRulesets);
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

export async function saveGuardrailRuleset(payload: Json): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>("/guardrail-rulesets", { ok: true }, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function publishGuardrailRuleset(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/guardrail-rulesets/${id}/publish`, { ok: true }, { method: "POST" });
}

export async function deleteGuardrailRuleset(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/guardrail-rulesets/${id}`, { ok: true }, { method: "DELETE" });
}

export async function deleteNodeDefinition(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/node-definitions/${id}`, { ok: true }, { method: "DELETE" });
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
  return safeFetch<GraphRunResponse>(
    "/graph/runs",
    {
      run_id: "local-fallback-run",
      status: "completed",
      execution_order: payload.nodes.map((node) => node.id),
      node_results: {},
      events: [],
      validation: { valid: true, issues: [] },
      runtime: {
        requested_engine: "native",
        selected_engine: "native",
        executed_engine: "native",
        mode: "native",
        allow_override: false,
        allowed_engines: ["native"],
      },
    },
    {
      method: "POST",
      body: JSON.stringify(withGraphSchemaVersion(payload)),
    },
  );
}

export async function getRuntimeProviders(): Promise<RuntimeProvidersResponse> {
  return safeFetch<RuntimeProvidersResponse>("/runtime/providers", {
    providers: [
      {
        provider: "openai",
        configured: false,
        model: "gpt-5.2",
        mode: "simulated",
      },
    ],
    framework_adapters: {
      langgraph: { engine: "langgraph", available: false, missing_modules: ["langgraph", "langchain_openai"] },
      langchain: { engine: "langchain", available: false, missing_modules: ["langchain_core", "langchain_openai"] },
      "semantic-kernel": { engine: "semantic-kernel", available: false, missing_modules: ["semantic_kernel"] },
      autogen: { engine: "autogen", available: false, missing_modules: ["autogen_agentchat|autogen"] },
    },
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
  return safeFetch<{ ok: boolean; session_id: string }>(
    `/memory/${encodeURIComponent(sessionId)}`,
    { ok: true, session_id: sessionId },
    { method: "DELETE" },
  );
}

export async function getPlatformSettings(): Promise<PlatformSettings> {
  return safeFetch<PlatformSettings>("/platform/settings", {
    org_name: "Lattix xFrontier",
    org_slug: "lattix-frontier",
    support_email: "support@lattix.io",
    website: "https://lattix.io",
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
}

export async function getOperatorSession(): Promise<OperatorSession> {
  return safeFetch<OperatorSession>("/auth/session", {
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
    },
  });
}

export async function loginWithLocalPassword(payload: {
  username: string;
  password: string;
}): Promise<{ ok: boolean; authenticated: boolean; provider: string; mode: string }> {
  return strictFetch<{ ok: boolean; authenticated: boolean; provider: string; mode: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function registerWithLocalPassword(payload: {
  username: string;
  email: string;
  display_name: string;
  password: string;
}): Promise<{ ok: boolean; authenticated: boolean; provider: string; mode: string; created: boolean }> {
  return strictFetch<{ ok: boolean; authenticated: boolean; provider: string; mode: string; created: boolean }>("/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function logoutOperator(): Promise<{ ok: boolean }> {
  return strictFetch<{ ok: boolean }>("/auth/logout", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function getPlatformVersionStatus(): Promise<PlatformVersionStatus> {
  return safeFetch<PlatformVersionStatus>("/platform/version", {
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
  return safeFetch<{ ok: boolean }>("/platform/settings", { ok: true }, {
    method: "POST",
    body: JSON.stringify(payload),
  });
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
  entity_type: "agent" | "workflow";
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

export async function saveIntegration(payload: Json): Promise<{ ok: boolean; id: string }> {
  return safeFetch<{ ok: boolean; id: string }>("/integrations", { ok: true, id: "" }, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function testIntegration(id: string): Promise<IntegrationTestResponse> {
  return safeFetch<IntegrationTestResponse>(
    `/integrations/${id}/test`,
    { ok: false, id, status: "error", message: "Unable to test integration", diagnostics: { warnings: ["Backend unavailable"] } },
    { method: "POST" },
  );
}

export async function deleteIntegration(id: string): Promise<{ ok: boolean }> {
  return safeFetch<{ ok: boolean }>(`/integrations/${id}`, { ok: true }, { method: "DELETE" });
}

export async function oauthAuthorize(integrationId: string): Promise<{ authorize_url: string; state: string }> {
  return strictFetch(`/integrations/${encodeURIComponent(integrationId)}/oauth/authorize`);
}

export async function oauthDisconnect(integrationId: string): Promise<{ ok: boolean; id: string }> {
  return strictFetch(`/integrations/${encodeURIComponent(integrationId)}/oauth/disconnect`, {
    method: "POST",
  });
}
