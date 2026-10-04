"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowCanvas, type GraphLink, type GraphNode } from "@/components/reactflow-canvas";
import { normalizeNodeTypeForSchema, resolveNodePortAlias } from "@/lib/locus-node-schema";
import type { IntegrationDefinition } from "@/types/locus";
import {
  clearMemorySession,
  getCollaborationSession,
  getGuardrailRulesets,
  getIntegrations,
  getMcpConnections,
  getMemorySession,
  getModelsOverview,
  getNodeDefinitions,
  getObservabilityDashboard,
  getObservabilityRunTrace,
  getPlatformSettings,
  getRuntimeProviders,
  getUserSkills,
  joinCollaborationSession,
  runGraph,
  syncCollaborationSession,
  type GraphRunResponse,
  type ObservabilityDashboardResponse,
  type ObservabilityRunTrace,
  type RuntimeProvider,
  validateGraph,
} from "@/lib/api";

type StudioEntityType = "agent" | "workflow" | "playbook";

type CanvasApi = {
  addNode: (node: { type: string; title?: string; x?: number; y?: number; config?: Record<string, unknown> }) => void;
  autoLayout: (options?: { fitView?: boolean }) => void;
  replaceGraph: (graph: { nodes: GraphNode[]; links: GraphLink[] }, options?: { fitView?: boolean }) => void;
  clear: () => void;
  serialize: () => { nodes: GraphNode[]; links: GraphLink[] };
};

type Props = {
  entityType: StudioEntityType;
  entityId: string;
  entityName: string;
  builderMode?: "standard" | "internal";
  description: string;
  initialNodes: GraphNode[];
  initialLinks: GraphLink[];
  rightSidebarSlot?: React.ReactNode;
  externalWidgetOptionOverrides?: Record<string, Record<string, string[]>>;
  onSave: (payload: { nodes: GraphNode[]; links: GraphLink[] }) => Promise<void>;
  onPublish?: (() => Promise<void>) | undefined;
  onNodeSelected?: (node: GraphNode | null) => void;
  onEditAgent?: (agentId: string) => void;
  onCanvasReady?: (api: CanvasApi) => void;
  returnAction?: { label: string; href: string };
};

function generateCollaborationUserId(entityType: StudioEntityType, entityId: string): string {
  const prefix = `${entityType}-${entityId}-`;
  const cryptoApi = globalThis.crypto;

  if (cryptoApi?.randomUUID) {
    return `${prefix}${cryptoApi.randomUUID().replace(/-/g, "").slice(0, 7)}`;
  }

  if (cryptoApi?.getRandomValues) {
    const bytes = new Uint8Array(4);
    cryptoApi.getRandomValues(bytes);
    const suffix = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("").slice(0, 7);
    return `${prefix}${suffix}`;
  }

  return `${prefix}${Date.now().toString(36)}`;
}

function readLocalStorage(key: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const storage = window.localStorage;
  if (!storage || typeof storage.getItem !== "function") {
    return null;
  }

  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocalStorage(key: string, value: string): void {
  if (typeof window === "undefined") {
    return;
  }

  const storage = window.localStorage;
  if (!storage || typeof storage.setItem !== "function") {
    return;
  }

  try {
    storage.setItem(key, value);
  } catch {
    // Ignore storage write failures and continue with in-memory state.
  }
}

function mergeUniqueOptions(...groups: Array<string[] | undefined>): string[] {
  const seen = new Set<string>();
  const merged: string[] = [];

  for (const group of groups) {
    for (const entry of group ?? []) {
      const normalized = String(entry ?? "").trim();
      if (!normalized || seen.has(normalized)) {
        continue;
      }
      seen.add(normalized);
      merged.push(normalized);
    }
  }

  return merged;
}

function toMatchTokens(values: string[] | undefined): Set<string> {
  const tokens = new Set<string>();

  for (const value of values ?? []) {
    const normalized = String(value ?? "").trim().toLowerCase();
    if (!normalized) {
      continue;
    }

    for (const candidate of [normalized, normalized.replace(/^\/+/, "")]) {
      if (!candidate) {
        continue;
      }
      tokens.add(candidate);
      for (const part of candidate.split(/[^a-z0-9]+/)) {
        if (part.length >= 2) {
          tokens.add(part);
        }
      }
    }
  }

  return tokens;
}

function rankIntegrationSkillMatches(
  integrations: IntegrationDefinition[],
  scopedSkills: string[],
): string[] {
  const skillTokens = toMatchTokens(scopedSkills);

  return integrations
    .filter((integration) => integration.status !== "archived")
    .map((integration) => {
      const capabilityTokens = toMatchTokens(integration.capabilities);
      let score = 0;
      let matched = false;

      for (const token of skillTokens) {
        if (capabilityTokens.has(token)) {
          matched = true;
          score += token.includes("/") ? 6 : 2;
        }
      }

      if (matched && integration.status === "configured") {
        score += 1;
      }

      return {
        id: integration.id,
        name: integration.name,
        score,
      };
    })
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score || left.name.localeCompare(right.name) || left.id.localeCompare(right.id))
    .map((item) => item.id);
}

const defaultToolIdOptions = [
  "tool/unspecified",
  "tool/search",
  "tool/http",
  "tool/retrieval",
  "tool/code",
  "tool/sql",
  "tool/file",
  "tool/email",
  "tool/slack",
  "tool/mcp",
];

export function StudioFullCanvas({
  entityType,
  entityId,
  entityName,
  builderMode = "standard",
  description,
  initialNodes,
  initialLinks,
  rightSidebarSlot,
  externalWidgetOptionOverrides,
  onSave,
  onPublish,
  onNodeSelected,
  onEditAgent,
  onCanvasReady,
  returnAction,
}: Props) {
  const router = useRouter();
  const canvasApiRef = useRef<{
    addNode: (node: { type: string; title?: string; x?: number; y?: number; config?: Record<string, unknown> }) => void;
    autoLayout: (options?: { fitView?: boolean }) => void;
    replaceGraph: (graph: { nodes: GraphNode[]; links: GraphLink[] }, options?: { fitView?: boolean }) => void;
    clear: () => void;
    serialize: () => { nodes: GraphNode[]; links: GraphLink[] };
  } | null>(null);
  const suppressCollabSyncRef = useRef(false);
  const collabVersionRef = useRef(0);
  const localGraphDirtyRef = useRef(false);
  const [extraNodeDefinitions, setExtraNodeDefinitions] = useState<
    Array<{ key: `locus/${string}`; title: string; color?: string; description?: string }>
  >([]);
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; links: GraphLink[] }>({
    nodes: initialNodes,
    links: initialLinks,
  });
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "published" | "error">("idle");
  const [validateState, setValidateState] = useState<"idle" | "validating" | "valid" | "invalid" | "error">("idle");
  const [runState, setRunState] = useState<"idle" | "running" | "completed" | "failed" | "error">("idle");
  const [validationIssues, setValidationIssues] = useState<Array<{ code: string; message: string; path: string }>>([]);
  const [runResult, setRunResult] = useState<GraphRunResponse | null>(null);
  const [providerStatus, setProviderStatus] = useState<RuntimeProvider | null>(null);
  const [runtimeLoadError, setRuntimeLoadError] = useState<string | null>(null);
  const [catalogLoadError, setCatalogLoadError] = useState<string | null>(null);
  const [memoryError, setMemoryError] = useState<string | null>(null);
  const [runtimeModel, setRuntimeModel] = useState("gpt-5.2");
  const [runtimeTemperature, setRuntimeTemperature] = useState("0.2");
  const [sessionId, setSessionId] = useState(`${entityType}:${entityId}`);
  const [useMemory, setUseMemory] = useState(true);
  const [memoryCount, setMemoryCount] = useState(0);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [widgetOptionOverrides, setWidgetOptionOverrides] = useState<Record<string, Record<string, string[]>>>({});
  const [edgeType, setEdgeType] = useState<"smoothstep" | "step" | "straight" | "default" | "simplebezier">("default");
  const [edgeAnimated, setEdgeAnimated] = useState(true);
  const [collabSessionId, setCollabSessionId] = useState("");
  const [collabRole, setCollabRole] = useState<"owner" | "editor" | "viewer">("editor");
  const [collabParticipants, setCollabParticipants] = useState<Array<{ user_id: string; display_name: string; role: "owner" | "editor" | "viewer"; last_seen_at: string }>>([]);
  const [collabVersion, setCollabVersion] = useState(0);
  const [collabSyncState, setCollabSyncState] = useState<"idle" | "syncing" | "ok" | "conflict" | "error">("idle");
  const [observabilityDashboard, setObservabilityDashboard] = useState<ObservabilityDashboardResponse | null>(null);
  const [selectedTrace, setSelectedTrace] = useState<ObservabilityRunTrace | null>(null);
  const [collabUserId, setCollabUserId] = useState("local-user");
  const [runtimePanelCollapsed, setRuntimePanelCollapsed] = useState(false);
  const [headerPanelCollapsed, setHeaderPanelCollapsed] = useState(false);
  const isInternalBuilderMode = builderMode === "internal";

  const isReadOnly = collabRole === "viewer";

  const handleGraphChange = useCallback((nextGraph: { nodes: GraphNode[]; links: GraphLink[] }) => {
    if (suppressCollabSyncRef.current) {
      setGraph(nextGraph);
      suppressCollabSyncRef.current = false;
      return;
    }

    localGraphDirtyRef.current = true;
    setGraph((previous) => {
      const previousConfigByNodeId = new Map(previous.nodes.map((node) => [node.id, node.config ?? {}]));
      return {
        ...nextGraph,
        nodes: nextGraph.nodes.map((node) => ({
          ...node,
          config: node.config && Object.keys(node.config).length > 0 ? node.config : previousConfigByNodeId.get(node.id) ?? {},
        })),
      };
    });
  }, []);

  const summary = useMemo(() => {
    const typeCount = graph.nodes.reduce<Record<string, number>>((acc, node) => {
      acc[node.type] = (acc[node.type] ?? 0) + 1;
      return acc;
    }, {});
    return {
      nodes: graph.nodes.length,
      edges: graph.links.length,
      topTypes: Object.entries(typeCount)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 3),
    };
  }, [graph.links.length, graph.nodes]);

  const runOutputPreview = useMemo(() => {
    if (!runResult) {
      return null;
    }

    const orderedNodeIds = [...runResult.execution_order].reverse();
    for (const nodeId of orderedNodeIds) {
      const result = runResult.node_results[nodeId];
      if (!result || typeof result !== "object") {
        continue;
      }

      const directResponse = result.response;
      if (typeof directResponse === "string" && directResponse.trim()) {
        return directResponse.trim();
      }

      const published = result.published as { payload?: unknown } | undefined;
      const payload = published?.payload;
      if (payload && typeof payload === "object") {
        const maybeResponse = (payload as { response?: unknown }).response;
        if (typeof maybeResponse === "string" && maybeResponse.trim()) {
          return maybeResponse.trim();
        }
      }
    }

    return null;
  }, [runResult]);

  const wiringIssues = useMemo(() => {
    const issues: Array<{ code: string; message: string }> = [];
    const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));

    const incomingTo = (nodeId: string, expectedPorts: string[]) => {
      const targetNode = nodeById.get(nodeId);
      if (!targetNode) {
        return 0;
      }

      return graph.links.filter((link) => {
        if (link.to !== nodeId) {
          return false;
        }
        const rawPort = link.to_port ?? "in";
        const normalized = resolveNodePortAlias(targetNode.type, "input", rawPort) ?? rawPort;
        return expectedPorts.includes(normalized);
      }).length;
    };

    for (const node of graph.nodes) {
      const normalizedType = normalizeNodeTypeForSchema(node.type);
      const cfg = (node.config ?? {}) as Record<string, unknown>;

      if (normalizedType === "agent") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "AGENT_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        const hasPromptEdge = incomingTo(node.id, ["prompt"]) > 0;
        const hasInlinePrompt = String(cfg.system_prompt ?? "").trim().length > 0;
        if (!hasPromptEdge && !hasInlinePrompt) {
          issues.push({ code: "AGENT_PROMPT_REQUIRED", message: `${node.title}: connect 'prompt' or set inline system_prompt.` });
        }
      }

      if (normalizedType === "retrieval") {
        if (incomingTo(node.id, ["query"]) === 0) {
          issues.push({ code: "RETRIEVAL_QUERY_INPUT_REQUIRED", message: `${node.title}: connect query input to 'query'.` });
        }
      }

      if (normalizedType === "router") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "ROUTER_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["candidate"]) === 0) {
          issues.push({ code: "ROUTER_CANDIDATE_INPUT_REQUIRED", message: `${node.title}: connect candidate input to 'candidate'.` });
        }
      }

      if (normalizedType === "transform") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "TRANSFORM_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["source"]) === 0) {
          issues.push({ code: "TRANSFORM_SOURCE_INPUT_REQUIRED", message: `${node.title}: connect source input to 'source'.` });
        }
      }

      if (normalizedType === "iterator") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "ITERATOR_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["items"]) === 0) {
          issues.push({ code: "ITERATOR_ITEMS_INPUT_REQUIRED", message: `${node.title}: connect items input to 'items'.` });
        }
      }

      if (normalizedType === "event") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "EVENT_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["payload"]) === 0) {
          issues.push({ code: "EVENT_PAYLOAD_INPUT_REQUIRED", message: `${node.title}: connect payload input to 'payload'.` });
        }
      }

      if (normalizedType === "data-store") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "DATA_STORE_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["record"]) === 0) {
          issues.push({ code: "DATA_STORE_RECORD_INPUT_REQUIRED", message: `${node.title}: connect record input to 'record'.` });
        }
      }

      if (normalizedType === "tool-call") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "TOOL_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["request"]) === 0) {
          issues.push({ code: "TOOL_REQUEST_INPUT_REQUIRED", message: `${node.title}: connect request input to 'request'.` });
        }
      }

      if (normalizedType === "output") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "OUTPUT_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["result"]) === 0) {
          issues.push({ code: "OUTPUT_RESULT_INPUT_REQUIRED", message: `${node.title}: connect payload input to 'result'.` });
        }
      }

      if (normalizedType === "error-handler") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "ERROR_HANDLER_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
        if (incomingTo(node.id, ["error"]) === 0) {
          issues.push({ code: "ERROR_HANDLER_INPUT_REQUIRED", message: `${node.title}: connect error input to 'error'.` });
        }
      }

      if (normalizedType === "wait") {
        if (incomingTo(node.id, ["in"]) === 0) {
          issues.push({ code: "WAIT_FLOW_INPUT_REQUIRED", message: `${node.title}: connect flow input to 'in'.` });
        }
      }
    }

    return issues;
  }, [graph.links, graph.nodes]);

  useEffect(() => {
    let cancelled = false;

    async function loadNodeDefinitions() {
      let nodeDefinitions: Awaited<ReturnType<typeof getNodeDefinitions>>;
      try {
        nodeDefinitions = await getNodeDefinitions({ includeInternal: isInternalBuilderMode });
      } catch (error) {
        if (!cancelled) setCatalogLoadError(error instanceof Error ? `Node catalog: ${error.message}` : "Node catalog unavailable.");
        return;
      }
      if (cancelled) {
        return;
      }

      const allNodeDefinitions = nodeDefinitions
        .filter((node) => node.type_key.startsWith("locus/"))
        .map((node) => ({
          key: node.type_key as `locus/${string}`,
          title: node.title ?? node.type_key.replace("locus/", ""),
          color: node.color,
          description: node.description,
        }));

      setExtraNodeDefinitions(allNodeDefinitions);
    }

    void loadNodeDefinitions();

    return () => {
      cancelled = true;
    };
  }, [isInternalBuilderMode]);

  useEffect(() => {
    const storageKey = `locus:collab:${entityType}:${entityId}:user`;
    const existing = readLocalStorage(storageKey);
    if (existing) {
      setCollabUserId(existing);
      return;
    }

    const generated = generateCollaborationUserId(entityType, entityId);
    writeLocalStorage(storageKey, generated);
    setCollabUserId(generated);
  }, [entityId, entityType]);

  useEffect(() => {
    let cancelled = false;

    async function bootstrapCollaboration() {
      try {
        const joined = await joinCollaborationSession({
          entity_type: entityType,
          entity_id: entityId,
          user_id: collabUserId,
          display_name: `Local ${collabUserId.slice(-6)}`,
        });

        if (cancelled) {
          return;
        }

        setCollabSessionId(joined.session.id);
        setCollabRole(joined.participant.role);
        setCollabParticipants(joined.session.participants);
        setCollabVersion(joined.session.version);
        collabVersionRef.current = joined.session.version;
        setCollabSyncState("ok");
      } catch {
        if (!cancelled) {
          setCollabSyncState("error");
        }
      }
    }

    if (collabUserId) {
      void bootstrapCollaboration();
    }

    return () => {
      cancelled = true;
    };
  }, [collabUserId, entityId, entityType]);

  useEffect(() => {
    if (!collabSessionId) {
      return;
    }

    const interval = setInterval(() => {
      void (async () => {
        try {
          if (!collabSessionId) {
            return;
          }

          const session = await getCollaborationSession(collabSessionId);
          if (!session) {
            return;
          }
          setCollabParticipants(session.participants);
          const self = session.participants.find((participant) => participant.user_id === collabUserId);
          setCollabRole(self?.role ?? collabRole);
          setCollabVersion(session.version);

          const shouldPullRemote = session.version > collabVersionRef.current && !localGraphDirtyRef.current;
          if (shouldPullRemote) {
            collabVersionRef.current = session.version;
            suppressCollabSyncRef.current = true;
            canvasApiRef.current?.replaceGraph(
              {
                nodes: session.graph_json.nodes ?? [],
                links: session.graph_json.links ?? [],
              },
              { fitView: false },
            );
            setGraph({ nodes: session.graph_json.nodes ?? [], links: session.graph_json.links ?? [] });
            setCollabSyncState("ok");
          }
        } catch {
          setCollabSyncState("error");
        }
      })();
    }, 2500);

    return () => {
      clearInterval(interval);
    };
  }, [collabRole, collabSessionId, collabUserId, entityId, entityType]);

  useEffect(() => {
    if (!collabSessionId || isReadOnly || !localGraphDirtyRef.current) {
      return;
    }

    const timeout = setTimeout(() => {
      void (async () => {
        setCollabSyncState("syncing");
        try {
          const synced = await syncCollaborationSession(collabSessionId, {
            user_id: collabUserId,
            base_version: collabVersionRef.current,
            graph_json: {
              nodes: graph.nodes,
              links: graph.links,
            },
          });

          collabVersionRef.current = synced.version;
          setCollabVersion(synced.version);
          setCollabSyncState("ok");
          localGraphDirtyRef.current = false;
        } catch (error) {
          if (error instanceof Error && error.message.includes("409")) {
            setCollabSyncState("conflict");
            return;
          }
          setCollabSyncState("error");
        }
      })();
    }, 700);

    return () => {
      clearTimeout(timeout);
    };
  }, [collabSessionId, collabUserId, entityId, entityType, graph.links, graph.nodes, isReadOnly]);

  useEffect(() => {
    let cancelled = false;

    async function refreshObservability() {
      try {
        const dashboard = await getObservabilityDashboard();
        if (!cancelled) {
          setObservabilityDashboard(dashboard);
        }
      } catch {
        if (!cancelled) {
          setObservabilityDashboard(null);
        }
      }
    }

    void refreshObservability();

    return () => {
      cancelled = true;
    };
  }, [runResult?.run_id]);

  useEffect(() => {
    let cancelled = false;

    async function loadGuardrailOptions() {
      let rulesets: Awaited<ReturnType<typeof getGuardrailRulesets>>;
      let modelsOverview: Awaited<ReturnType<typeof getModelsOverview>> | null;
      try {
        [rulesets, modelsOverview] = await Promise.all([
          getGuardrailRulesets(),
          // Wrapped so a synchronous throw also degrades to the static model options.
          Promise.resolve().then(() => getModelsOverview()).catch(() => null),
        ]);
      } catch (error) {
        if (!cancelled) setCatalogLoadError(error instanceof Error ? `Guardrail rulesets: ${error.message}` : "Guardrail rulesets unavailable.");
        return;
      }
      if (cancelled) {
        return;
      }

      const publishedRuleSetIds = rulesets
        .filter((item) => item.status === "published")
        .map((item) => item.id);

      // Build the agent-node model dropdown from the models actually configured
      // in the platform: locally-installed Ollama models + the default model of
      // every configured API/external provider. Falls back to the schema's static
      // options when nothing is configured (or the overview is unavailable).
      const modelOptions: string[] = [];
      const addModel = (value: string | undefined | null) => {
        const v = String(value ?? "").trim();
        if (v && !modelOptions.includes(v)) modelOptions.push(v);
      };
      if (modelsOverview) {
        const ollama = modelsOverview.providers?.ollama;
        for (const installed of ollama?.installed_models ?? []) {
          const id = String(installed?.id ?? "").trim();
          if (id) addModel(id.startsWith("ollama/") ? id : `ollama/${id}`);
        }
        for (const item of modelsOverview.catalog ?? []) {
          if (item?.installed) {
            const ref = String(item.reference ?? "").trim();
            if (ref) addModel(ref.startsWith("ollama/") ? ref : `ollama/${ref}`);
          }
        }
        if (modelsOverview.providers?.openai?.configured) addModel(modelsOverview.providers.openai.default_model);
        if (modelsOverview.providers?.nim?.configured) addModel(modelsOverview.providers.nim.default_model);
        for (const ext of modelsOverview.external ?? []) {
          if (ext?.configured) addModel(ext.default_model);
        }
      }

      setWidgetOptionOverrides((current) => ({
        ...current,
        guardrail: {
          ...(current.guardrail ?? {}),
          ruleset_id: publishedRuleSetIds,
        },
        ...(modelOptions.length > 0 ? { agent: { ...(current.agent ?? {}), model: modelOptions } } : {}),
      }));
    }

    void loadGuardrailOptions();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadSkillScopedOptions() {
      try {
        const [platformSettings, userSkillsResponse, integrations, mcpConnections] = await Promise.all([
          getPlatformSettings(),
          getUserSkills(),
          getIntegrations(),
          getMcpConnections(),
        ]);
        if (cancelled) {
          return;
        }

        const skillOptions = mergeUniqueOptions(userSkillsResponse.skills, platformSettings.tenant_scoped_skills);
        const likelyIntegrationIds = rankIntegrationSkillMatches(integrations, skillOptions);
        const approvedMcpConnectionIds = mcpConnections
          .filter((connection) => connection.status === "approved")
          .sort((left, right) => left.name.localeCompare(right.name) || left.id.localeCompare(right.id))
          .map((connection) => connection.id);
        const remainingIntegrationIds = integrations
          .filter((integration) => integration.status !== "archived")
          .map((integration) => integration.id)
          .filter((integrationId) => !likelyIntegrationIds.includes(integrationId))
          .sort((left, right) => left.localeCompare(right));
        setWidgetOptionOverrides((current) => ({
          ...current,
          agent: {
            ...(current.agent ?? {}),
            skills: skillOptions,
          },
          "tool-call": {
            ...(current["tool-call"] ?? {}),
            tool_id: [...likelyIntegrationIds, ...defaultToolIdOptions, ...remainingIntegrationIds],
            mcp_connection_id: approvedMcpConnectionIds,
          },
        }));
      } catch {
        if (cancelled) {
          return;
        }
      }
    }

    void loadSkillScopedOptions();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeProvider() {
      try {
        const response = await getRuntimeProviders();
        if (cancelled) {
          return;
        }
        setRuntimeLoadError(null);
        const openai = response.providers.find((provider) => provider.provider === "openai") ?? null;
        setProviderStatus(openai);
        if (openai?.model) {
          setRuntimeModel(openai.model);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        setProviderStatus(null);
        setRuntimeLoadError(error instanceof Error ? error.message : "Unable to load runtime provider status.");
      }
    }

    void loadRuntimeProvider();

    return () => {
      cancelled = true;
    };
  }, []);

  const mergedWidgetOptionOverrides = useMemo(() => {
    const merged: Record<string, Record<string, string[]>> = {};
    for (const source of [widgetOptionOverrides, externalWidgetOptionOverrides ?? {}]) {
      for (const [nodeType, options] of Object.entries(source)) {
        merged[nodeType] = {
          ...(merged[nodeType] ?? {}),
          ...options,
        };
      }
    }
    return merged;
  }, [externalWidgetOptionOverrides, widgetOptionOverrides]);

  async function handleSaveDraft(): Promise<boolean> {
    setSaveState("saving");
    try {
      await onSave(graph);
      setSaveState("saved");
      return true;
    } catch {
      setSaveState("error");
      return false;
    }
  }

  async function handleSaveAndReturn() {
    const saved = await handleSaveDraft();
    if (saved && returnAction) {
      router.push(returnAction.href);
    }
  }

  async function handlePublish() {
    if (!onPublish) {
      return;
    }
    setPublishState("publishing");
    try {
      // Persist the current canvas FIRST, then publish — otherwise publish
      // snapshots the last *saved* draft and the runtime keeps calling a stale
      // version of the graph (the autosave only writes the collab session, not
      // the workflow definition).
      await onSave(graph);
      await onPublish();
      setPublishState("published");
    } catch {
      setPublishState("error");
    }
  }

  async function handleValidate() {
    setValidateState("validating");
    setValidationIssues([]);
    try {
      const validation = await validateGraph({
        nodes: graph.nodes,
        links: graph.links,
      });
      setValidationIssues(validation.issues);
      setValidateState(validation.valid ? "valid" : "invalid");
    } catch {
      setValidateState("error");
    }
  }

  async function handleRunTest() {
    setRunState("running");
    setRunResult(null);
    try {
      const result = await runGraph({
        nodes: graph.nodes,
        links: graph.links,
        input: {
          message: "Test execution from Studio",
          entityType,
          entityId,
          runtime: {
            provider: "openai",
            model: runtimeModel,
            temperature: Number(runtimeTemperature),
            session_id: sessionId,
            use_memory: useMemory,
          },
        },
      });
      setRunResult(result);
      setRunState(result.status === "completed" ? "completed" : "failed");
      setValidationIssues(result.validation.issues);
      setValidateState(result.validation.valid ? "valid" : "invalid");
      try {
        const trace = await getObservabilityRunTrace(result.run_id);
        setSelectedTrace(trace);
      } catch {
        setSelectedTrace(null);
      }
    } catch {
      setRunState("error");
    }
  }

  async function handleRefreshMemory() {
    setMemoryBusy(true);
    try {
      const memory = await getMemorySession(sessionId);
      setMemoryCount(memory.count);
      setMemoryError(null);
    } catch (error) {
      setMemoryError(error instanceof Error ? error.message : "Could not read session memory.");
    } finally {
      setMemoryBusy(false);
    }
  }

  async function handleClearMemory() {
    setMemoryBusy(true);
    try {
      await clearMemorySession(sessionId);
      setMemoryCount(0);
    } finally {
      setMemoryBusy(false);
    }
  }

  function handleAutoLayout() {
    canvasApiRef.current?.autoLayout({ fitView: true });
  }

  const title = entityType === "agent" ? "Agent Studio" : entityType === "workflow" ? "Workflow Studio" : "Playbook Studio";
  const backHref = entityType === "agent" ? "/library/agents" : entityType === "workflow" ? "/library/workflows" : "/library/playbooks";

  return (
    <section className="-m-5 h-[calc(100vh-var(--fx-content-top,57px))] overflow-hidden md:-m-6">
      <div className="relative h-full w-full">
        <ReactFlowCanvas
          className="h-full border-0"
          nodes={graph.nodes}
          links={graph.links}
          readOnly={isReadOnly}
          extraNodeDefinitions={extraNodeDefinitions}
          widgetOptionOverrides={mergedWidgetOptionOverrides}
          edgeType={edgeType}
          edgeAnimated={edgeAnimated}
          onGraphChange={handleGraphChange}
          onNodeSelected={onNodeSelected}
          onEditAgent={onEditAgent}
          onReady={(api) => {
            canvasApiRef.current = api;
            onCanvasReady?.(api);
          }}
        />

        <div className="pointer-events-none absolute inset-x-3 top-3 flex items-start justify-between gap-3">
          <div className="pointer-events-auto w-full max-w-[30rem] overflow-hidden rounded-[1.6rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] px-4 py-3 text-[var(--foreground)] shadow-[0_24px_60px_rgba(15,23,42,0.1)] backdrop-blur-md xl:max-w-[34rem]">
            <Link href={backHref} className="text-[0.72rem] font-medium fx-muted underline decoration-dotted underline-offset-4">
              Back to Library
            </Link>
            <div className="mt-1 flex items-center justify-between gap-2">
              <div className="min-w-0 text-[1rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">
                <span className="truncate">{title} / {entityName}</span>
              </div>
              <button
                type="button"
                onClick={() => setHeaderPanelCollapsed((current) => !current)}
                className="fx-btn-secondary shrink-0 px-2 py-0.5 text-[10px]"
                aria-label={headerPanelCollapsed ? "Expand Header Details panel" : "Collapse Header Details panel"}
                title={headerPanelCollapsed ? "Expand panel" : "Collapse panel"}
              >
                {headerPanelCollapsed ? "Expand" : "Collapse"}
              </button>
            </div>
            <div className={headerPanelCollapsed ? "mt-0.5 flex items-center gap-1 text-[10px]" : "mt-1"}>
              <div className="text-[11px] font-mono fx-muted">{entityType}_id: {entityId}</div>
              <div className={headerPanelCollapsed ? "flex min-w-0 flex-wrap items-center gap-1 text-[10px]" : "mt-1 flex flex-wrap items-center gap-1 text-[10px]"}>
                <span className="fx-muted">collab:</span>
                <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-0.5 font-mono text-[var(--foreground)]">{collabSessionId ? collabSessionId.slice(0, 8) : "--"}</span>
                <span className="fx-muted">role:</span>
                <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-0.5 font-semibold text-[var(--foreground)]">{collabRole}</span>
                <span className="fx-muted">sync:</span>
                <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-0.5 font-semibold text-[var(--foreground)]">{collabSyncState}</span>
                  {isInternalBuilderMode && (
                    <>
                      <span className="fx-muted">mode:</span>
                      <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-0.5 font-semibold text-[var(--foreground)]">internal</span>
                    </>
                  )}
              </div>
            </div>

            {!headerPanelCollapsed ? (
              <div className="mt-2 border-t border-[var(--fx-border)] pt-2">
                <div className="max-h-[32vh] space-y-2 overflow-y-auto pr-1 lg:max-h-[40vh]">
                  <div>
                    <div className="mb-1 text-[0.72rem] font-medium text-[var(--fx-muted)]">Diagram Summary</div>
                    <p className="text-[9px] leading-5 fx-muted">{description}</p>
                    <dl className="mt-2 grid grid-cols-2 gap-1 text-[9px]">
                      <div className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.92)] p-2">
                        <dt className="text-[8px] fx-muted">Nodes</dt>
                        <dd className="text-xs font-semibold text-[var(--foreground)]">{summary.nodes}</dd>
                      </div>
                      <div className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.92)] p-2">
                        <dt className="text-[8px] fx-muted">Edges</dt>
                        <dd className="text-xs font-semibold text-[var(--foreground)]">{summary.edges}</dd>
                      </div>
                    </dl>
                    <div className="mt-2 grid gap-1 text-[9px] sm:grid-cols-2">
                      {summary.topTypes.map(([type, count]) => (
                        <div key={type} className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.92)] px-2 py-1 text-[0.72rem]">
                          <span className="truncate text-[var(--foreground)]">{type}</span>
                          <span className="ml-2 shrink-0 fx-muted">{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {rightSidebarSlot ? <div>{rightSidebarSlot}</div> : null}
                </div>
              </div>
            ) : null}
          </div>

          <div className="pointer-events-auto flex items-center gap-2 rounded-[1.4rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] px-3 py-2 text-[var(--foreground)] shadow-[0_20px_48px_rgba(15,23,42,0.1)] backdrop-blur-md">
            <span className="hidden text-[0.72rem] font-medium fx-muted lg:inline">Canvas Actions</span>
            <label className="flex items-center gap-1 text-[10px] fx-muted">
              <span>Edge</span>
              <select
                aria-label="Edge style"
                value={edgeType}
                onChange={(event) => setEdgeType(event.target.value as "smoothstep" | "step" | "straight" | "default" | "simplebezier")}
                className="fx-field px-1 py-0.5 text-[10px]"
              >
                <option value="step">Right-angle</option>
                <option value="smoothstep">Flow (smoothstep)</option>
                <option value="default">Bezier</option>
                <option value="simplebezier">Simple Bezier</option>
                <option value="straight">Straight</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-[10px] fx-muted">
              <input
                aria-label="Animate edges"
                type="checkbox"
                checked={edgeAnimated}
                onChange={(event) => setEdgeAnimated(event.target.checked)}
              />
              <span>Animate</span>
            </label>
            <button
              onClick={handleAutoLayout}
              className="fx-btn-secondary px-3 py-1.5 text-xs font-medium"
              disabled={graph.nodes.length === 0 || isReadOnly}
              title={graph.nodes.length === 0 ? "Add at least one node to auto-layout the canvas." : "Rearrange nodes to avoid overlap and improve readability."}
            >
              Auto Layout
            </button>
            <button
              onClick={handleValidate}
              className="fx-btn-secondary px-3 py-1.5 text-xs font-medium"
              disabled={validateState === "validating"}
            >
              {validateState === "validating" ? "Validating..." : "Validate"}
            </button>
            <button
              onClick={handleRunTest}
              className="fx-btn-secondary px-3 py-1.5 text-xs font-medium"
              disabled={runState === "running"}
            >
              {runState === "running" ? "Running..." : "Run Test"}
            </button>
            <button
              onClick={handleSaveDraft}
              className="fx-btn-secondary px-3 py-1.5 text-xs font-medium"
              disabled={saveState === "saving" || isReadOnly}
            >
              {saveState === "saving" ? "Saving..." : "Save Draft"}
            </button>
            {returnAction ? (
              <button
                onClick={handleSaveAndReturn}
                className="fx-btn-secondary px-3 py-1.5 text-xs font-medium"
                disabled={saveState === "saving" || isReadOnly}
              >
                {saveState === "saving" ? "Saving..." : returnAction.label}
              </button>
            ) : null}
            {onPublish ? (
              <button
                onClick={handlePublish}
                className="fx-btn-primary px-3 py-1.5 text-xs font-medium"
                disabled={publishState === "publishing" || isReadOnly}
              >
                {publishState === "publishing" ? "Publishing..." : "Publish"}
              </button>
            ) : null}
          </div>
        </div>

        <div className="pointer-events-none absolute bottom-3 left-3 z-20 rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_94%,hsl(var(--background))_6%)] px-3 py-1.5 text-[0.72rem] font-medium fx-muted shadow-[0_12px_30px_rgba(15,23,42,0.08)] backdrop-blur-md">
          Tip: right-click canvas to add locus nodes
        </div>

        <aside className={`absolute bottom-3 right-3 z-20 rounded-[1.5rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] p-3 text-[var(--foreground)] shadow-[0_24px_60px_rgba(15,23,42,0.12)] backdrop-blur-md ${runtimePanelCollapsed ? "w-auto" : "w-[420px]"}`}>
          <div className="mb-1 flex items-center justify-between">
            <h3 className="text-sm font-semibold tracking-[-0.02em] text-[var(--foreground)]">Validation & Runtime</h3>
            <div className="flex items-center gap-2">
              <div className="text-[10px] fx-muted">
                validate={validateState} run={runState}
              </div>
              <button
                onClick={() => setRuntimePanelCollapsed((current) => !current)}
                className="fx-btn-secondary px-2 py-0.5 text-[10px]"
                aria-label={runtimePanelCollapsed ? "Expand Validation & Runtime panel" : "Collapse Validation & Runtime panel"}
                title={runtimePanelCollapsed ? "Expand panel" : "Collapse panel"}
              >
                {runtimePanelCollapsed ? "Expand" : "Collapse"}
              </button>
            </div>
          </div>

          {runtimePanelCollapsed ? (
            <div className="text-[10px] fx-muted">Panel collapsed to maximize canvas space.</div>
          ) : (
            <>

          {wiringIssues.length > 0 && (
            <div className="mb-2 rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-warning)_58%,var(--fx-border)_42%)] bg-[color-mix(in_srgb,var(--fx-warning)_14%,var(--fx-surface)_86%)] p-2 text-[10px] text-[var(--foreground)]">
              <div className="mb-1 font-semibold text-[var(--fx-warning)]">Wiring checks (pre-validate)</div>
              <ul className="max-h-20 list-disc space-y-0.5 overflow-auto pl-4 text-[var(--foreground)]">
                {wiringIssues.slice(0, 6).map((issue) => (
                  <li key={`${issue.code}-${issue.message}`}>{issue.message}</li>
                ))}
              </ul>
            </div>
          )}

          {validationIssues.length > 0 ? (
            <ul className="max-h-24 overflow-auto rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2 text-[10px]">
              {validationIssues.slice(0, 5).map((issue) => (
                <li key={`${issue.code}-${issue.path}`} className="mb-1 border-b border-[var(--fx-border)] pb-1 last:mb-0 last:border-b-0 last:pb-0">
                  <div className="font-semibold text-[var(--fx-danger)]">{issue.code}</div>
                  <div className="text-[var(--foreground)]">{issue.message}</div>
                  <div className="fx-muted">{issue.path}</div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.9)] px-3 py-2 text-[10px] fx-muted">No validation issues.</div>
          )}

          {runResult && (
            <div className="mt-2 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2 text-[10px]">
              <div className="fx-muted">run_id</div>
              <div className="font-mono text-[var(--foreground)]">{runResult.run_id}</div>
              {isInternalBuilderMode && runResult.runtime && (
                <>
                  <div className="mt-1 fx-muted">runtime engine</div>
                  <div className="font-mono text-[var(--foreground)]">
                    strategy={runResult.runtime.strategy ?? "single"} requested={runResult.runtime.requested_engine ?? "native"} selected={runResult.runtime.selected_engine ?? "native"} executed={runResult.runtime.executed_engine ?? "native"} mode={runResult.runtime.mode ?? "native"}
                  </div>
                  {Array.isArray(runResult.runtime.node_dispatches) && runResult.runtime.node_dispatches.length > 0 && (
                    <>
                      <div className="mt-1 fx-muted">node dispatches</div>
                      <ul className="max-h-20 overflow-auto rounded-[0.85rem] border border-[var(--fx-border)] bg-[var(--fx-input)] p-1.5 text-[9px] text-[var(--fx-input-text)]">
                        {runResult.runtime.node_dispatches.slice(0, 8).map((dispatch) => (
                          <li key={`${dispatch.node_id}:${dispatch.role ?? "default"}`}>
                            {dispatch.node_id} [{dispatch.role ?? "default"}] {dispatch.requested_engine ?? "native"}→{dispatch.executed_engine ?? "native"} ({dispatch.mode ?? "native"})
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
              <div className="mt-1 fx-muted">execution order</div>
              <div className="font-mono text-[var(--foreground)]">{runResult.execution_order.join(" → ") || "(empty)"}</div>
              <div className="mt-1 fx-muted">events</div>
              <div className="text-[var(--foreground)]">{runResult.events.length}</div>
              {runOutputPreview && (
                <>
                  <div className="mt-1 fx-muted">output preview</div>
                  <pre className="max-h-28 overflow-auto whitespace-pre-wrap rounded-[0.85rem] border border-[var(--fx-border)] bg-[var(--fx-input)] p-1.5 text-[var(--fx-input-text)]">
                    {runOutputPreview}
                  </pre>
                </>
              )}
            </div>
          )}

          {isInternalBuilderMode ? (
            <div className="mt-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.9)] p-2 text-[10px]">
              <div className="mb-1 fx-muted">Model Runtime</div>
              <div className="mb-1 text-[var(--foreground)]">
                openai={providerStatus?.configured ? "configured" : "not-configured"} mode={providerStatus?.mode ?? "unknown"}
              </div>
              {runtimeLoadError ? <div className="mb-1 text-[9px] text-[var(--fx-danger)]">{runtimeLoadError}</div> : null}
              {catalogLoadError ? <div role="alert" className="mb-1 text-[9px] text-[var(--fx-danger)]">{catalogLoadError}</div> : null}
              <div className="grid grid-cols-2 gap-1">
                <label className="flex flex-col gap-0.5 fx-muted">
                  <span>model</span>
                  <input
                    aria-label="Runtime model"
                    value={runtimeModel}
                    onChange={(event) => setRuntimeModel(event.target.value)}
                    className="fx-field px-1 py-0.5 text-[10px]"
                  />
                </label>
                <label className="flex flex-col gap-0.5 fx-muted">
                  <span>temperature</span>
                  <input
                    aria-label="Runtime temperature"
                    value={runtimeTemperature}
                    onChange={(event) => setRuntimeTemperature(event.target.value)}
                    className="fx-field px-1 py-0.5 text-[10px]"
                  />
                </label>
                <label className="col-span-2 flex flex-col gap-0.5 fx-muted">
                  <span>session_id</span>
                  <input
                    aria-label="Runtime session id"
                    value={sessionId}
                    onChange={(event) => setSessionId(event.target.value)}
                    className="fx-field px-1 py-0.5 text-[10px]"
                  />
                </label>
              </div>
              <label className="mt-1 flex items-center gap-1 text-[var(--foreground)]">
                <input type="checkbox" checked={useMemory} onChange={(event) => setUseMemory(event.target.checked)} />
                <span>Enable memory context</span>
              </label>
              <div className="mt-1 flex items-center justify-between">
                <span className="fx-muted">memory entries: {memoryCount}</span>
                {memoryError ? <span role="alert" className="text-[var(--fx-danger)]">{memoryError}</span> : null}
                <div className="flex items-center gap-1">
                  <button
                    onClick={handleRefreshMemory}
                    className="fx-btn-secondary px-2 py-0.5 text-[10px]"
                    disabled={memoryBusy}
                  >
                    Refresh
                  </button>
                  <button
                    onClick={handleClearMemory}
                    className="fx-btn-warning px-2 py-0.5 text-[10px]"
                    disabled={memoryBusy}
                  >
                    Clear
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.9)] p-2 text-[10px]">
              <div className="mb-1 fx-muted">Execution Profile</div>
              <div className="text-[var(--foreground)]">
                Runs use platform-managed runtime defaults and internal memory policies.
              </div>
              <div className="mt-1 text-[9px] fx-muted">
                Use validation and test runs here; advanced routing and memory controls stay internal by design.
              </div>
            </div>
          )}

          <div className="mt-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.9)] p-2 text-[10px]">
            <div className="mb-1 flex items-center justify-between">
              <span className="fx-muted">Collaboration</span>
              <span className="font-mono text-[var(--foreground)]">v{collabVersion}</span>
            </div>
            <div className="mb-1 text-[var(--foreground)]">
              user={collabUserId.slice(-10)} role={collabRole} sync={collabSyncState}
            </div>
            <ul className="max-h-20 overflow-auto rounded-[0.85rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-1.5">
              {collabParticipants.length === 0 ? (
                <li className="fx-muted">No active participants.</li>
              ) : (
                collabParticipants.map((participant) => (
                  <li key={participant.user_id} className="mb-1 border-b border-[var(--fx-border)] pb-1 last:mb-0 last:border-b-0 last:pb-0">
                    <div className="text-[var(--foreground)]">{participant.display_name}</div>
                    <div className="fx-muted">{participant.role} · {new Date(participant.last_seen_at).toLocaleTimeString()}</div>
                  </li>
                ))
              )}
            </ul>
          </div>

          <div className="mt-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.9)] p-2 text-[10px]">
            <div className="mb-1 fx-muted">Observability</div>
            {observabilityDashboard ? (
              <>
                <div className="grid grid-cols-2 gap-1 text-[var(--foreground)]">
                  <div className="rounded-[0.85rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.94)] p-2">runs: {observabilityDashboard.summary.total_runs}</div>
                  <div className="rounded-[0.85rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.94)] p-2">tokens: {observabilityDashboard.summary.token_estimate}</div>
                  <div className="rounded-[0.85rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.94)] p-2">cost: ${observabilityDashboard.summary.cost_estimate_usd.toFixed(4)}</div>
                  <div className="rounded-[0.85rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.94)] p-2">latency avg: {observabilityDashboard.summary.average_latency_ms} ms</div>
                </div>
                <div className="mt-1 text-[9px] fx-muted">Recent runs</div>
                <ul className="max-h-20 overflow-auto rounded-[0.85rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-1.5">
                  {observabilityDashboard.runs.map((run) => (
                    <li key={run.run_id} className="mb-1 border-b border-[var(--fx-border)] pb-1 last:mb-0 last:border-b-0 last:pb-0">
                      <button
                        className="w-full text-left"
                        onClick={async () => {
                          try {
                            const trace = await getObservabilityRunTrace(run.run_id);
                            setSelectedTrace(trace);
                          } catch {
                            setSelectedTrace(null);
                          }
                        }}
                      >
                        <div className="font-mono text-[var(--foreground)]">{run.run_id}</div>
                        <div className="fx-muted">{run.status} · {run.token_estimate ?? 0} tok · {run.duration_ms ?? 0} ms</div>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <div className="fx-muted">No observability metrics yet.</div>
            )}

            {selectedTrace && (
              <div className="mt-2 rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
                <div className="font-mono text-[var(--foreground)]">trace {selectedTrace.run_id}</div>
                <div className="fx-muted">status {selectedTrace.status}</div>
                <div className="mt-1 text-[var(--foreground)]">events {selectedTrace.event_count} · nodes {selectedTrace.node_count} · edges {selectedTrace.edge_count}</div>
              </div>
            )}
          </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
