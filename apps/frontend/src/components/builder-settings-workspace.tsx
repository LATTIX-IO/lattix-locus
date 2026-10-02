"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { SettingsRailCard } from "@/components/settings-shell";
import { useToast } from "@/components/toast";
import {
  getBuilderSettingsNavBadge,
  getBuilderSettingsSection,
  getVisibleBuilderSettingsSections,
  type BuilderSettingsSectionKey,
} from "@/lib/builder-settings";
import {
  deleteUserRuntimeProvider,
  getOperatorSession,
  getPlatformSecurityPolicy,
  getPlatformSettings,
  getRuntimeProviders,
  getUserRuntimeProviders,
  savePlatformSettings,
  saveUserRuntimeProvider,
  type RuntimeProvider,
  type UserRuntimeProviderConfig,
} from "@/lib/api";
import type { OperatorSession, PlatformSettings, PlatformSignalEnforcement, SecurityPolicyResponse } from "@/types/frontier";

type OverviewCardSectionKey = Exclude<BuilderSettingsSectionKey, "governance">;
type RuntimeProviderKey = "openai" | "anthropic" | "gemini" | "openai-compatible";
type RuntimeProviderCategory = "Hosted APIs" | "Local / open-weight";

type RuntimeProviderPreset = {
  provider: RuntimeProviderKey;
  title: string;
  category: RuntimeProviderCategory;
  blurb: string;
  baseUrl: string;
  models: string[];
  examples: string;
};

type RuntimeProviderDraft = {
  model: string;
  available_models: string[];
  api_key: string;
  preferred: boolean;
  modelMenuOpen: boolean;
};

const defaultLocalNetworkHostnames = ["localhost", ".local"] as const;
const runtimeEngineOptions = ["native", "langgraph", "langchain", "semantic-kernel", "autogen"] as const;
const signalEnforcementOptions: Array<{ value: PlatformSignalEnforcement; label: string }> = [
  { value: "off", label: "Off" },
  { value: "audit", label: "Audit only" },
  { value: "block_high", label: "Block high-risk" },
  { value: "raise_high", label: "Escalate high-risk" },
];

const runtimeProviderPresets: readonly RuntimeProviderPreset[] = [
  {
    provider: "openai",
    title: "OpenAI",
    category: "Hosted APIs",
    blurb: "Managed OpenAI responses and embeddings.",
    baseUrl: "https://api.openai.com/v1",
    models: ["gpt-5.4", "gpt-5.4-mini", "gpt-5.1", "gpt-5.1-mini", "gpt-4.5", "gpt-4.5-preview", "codex-1", "codex-mini-latest"],
    examples: "Latest set includes GPT-5.4, GPT-4.5+, and Codex models.",
  },
  {
    provider: "anthropic",
    title: "Anthropic",
    category: "Hosted APIs",
    blurb: "Claude-hosted inference for higher-context reasoning.",
    baseUrl: "https://api.anthropic.com/v1",
    models: ["claude-opus-4-6", "claude-sonnet-4-6", "claude-opus-4-1", "claude-sonnet-4-1", "claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
    examples: "Latest set includes Claude 4.x, 3.7 Sonnet, and current 3.5/3.x variants.",
  },
  {
    provider: "gemini",
    title: "Gemini",
    category: "Hosted APIs",
    blurb: "Google Gemini APIs for multimodal and search-adjacent flows.",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta",
    models: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b", "gemini-1.0-pro"],
    examples: "Latest set includes Gemini 2.5, 2.0, and current 1.5 variants.",
  },
  {
    provider: "openai-compatible",
    title: "OpenAI-compatible",
    category: "Local / open-weight",
    blurb: "Use the platform-managed local OpenAI-compatible endpoint for open-weight models.",
    baseUrl: "http://localhost:11434/v1",
    models: ["qwen2.5-coder:32b", "qwen2.5-coder:14b", "qwen2.5:72b-instruct", "llama3.3:70b-instruct", "llama3.2:90b-vision-instruct", "mistral-small3.1:24b", "mistral-nemo:12b", "mixtral:8x22b-instruct", "deepseek-r1:70b", "phi-4:14b"],
    examples: "Latest set includes current Qwen, Llama, Mistral, DeepSeek, and Phi open-weight models.",
  },
];

function buildRuntimeProviderDrafts(configs: UserRuntimeProviderConfig[]): Record<RuntimeProviderKey, RuntimeProviderDraft> {
  const preferredProvider = configs.find((config) => config.preferred)?.provider ?? "";
  return runtimeProviderPresets.reduce<Record<RuntimeProviderKey, RuntimeProviderDraft>>((drafts, preset) => {
    const config = configs.find((candidate) => candidate.provider === preset.provider);
    const availableModels = (config?.available_models ?? []).filter((item) => preset.models.includes(item));
    const normalizedAvailableModels = availableModels.length > 0 ? availableModels : [config?.model ?? preset.models[0] ?? ""].filter(Boolean);
    const selectedModel = config?.model ?? normalizedAvailableModels[0] ?? preset.models[0] ?? "";
    drafts[preset.provider] = {
      model: selectedModel,
      available_models: normalizedAvailableModels.includes(selectedModel)
        ? normalizedAvailableModels
        : [selectedModel, ...normalizedAvailableModels],
      api_key: "",
      preferred: preferredProvider === preset.provider,
      modelMenuOpen: false,
    };
    return drafts;
  }, {
    openai: { model: runtimeProviderPresets[0]?.models[0] ?? "", available_models: [runtimeProviderPresets[0]?.models[0] ?? ""].filter(Boolean), api_key: "", preferred: false, modelMenuOpen: false },
    anthropic: { model: runtimeProviderPresets[1]?.models[0] ?? "", available_models: [runtimeProviderPresets[1]?.models[0] ?? ""].filter(Boolean), api_key: "", preferred: false, modelMenuOpen: false },
    gemini: { model: runtimeProviderPresets[2]?.models[0] ?? "", available_models: [runtimeProviderPresets[2]?.models[0] ?? ""].filter(Boolean), api_key: "", preferred: false, modelMenuOpen: false },
    "openai-compatible": { model: runtimeProviderPresets[3]?.models[0] ?? "", available_models: [runtimeProviderPresets[3]?.models[0] ?? ""].filter(Boolean), api_key: "", preferred: false, modelMenuOpen: false },
  });
}

function getRuntimeProviderModelOptions(preset: RuntimeProviderPreset, draft: RuntimeProviderDraft): string[] {
  const normalizedAllowed = draft.available_models.filter((item) => preset.models.includes(item));
  const normalizedCurrent = draft.model.trim();
  const options = normalizedAllowed.length > 0 ? normalizedAllowed : preset.models;
  if (!normalizedCurrent || options.includes(normalizedCurrent)) {
    return options;
  }
  return [normalizedCurrent, ...options];
}

function toggleRuntimeProviderAvailableModel(
  current: RuntimeProviderDraft,
  model: string,
): RuntimeProviderDraft {
  const availableModels = current.available_models.includes(model)
    ? current.available_models.filter((item) => item !== model)
    : [...current.available_models, model];
  const normalizedAvailableModels = availableModels.length > 0 ? availableModels : [current.model];
  const selectedModel = normalizedAvailableModels.includes(current.model) ? current.model : normalizedAvailableModels[0] ?? current.model;
  return {
    ...current,
    model: selectedModel,
    available_models: normalizedAvailableModels,
  };
}

function setRuntimeProviderModelMenuOpen(
  current: Record<RuntimeProviderKey, RuntimeProviderDraft>,
  provider: RuntimeProviderKey,
  open: boolean,
): Record<RuntimeProviderKey, RuntimeProviderDraft> {
  return Object.fromEntries(
    Object.entries(current).map(([key, draft]) => [
      key,
      {
        ...draft,
        modelMenuOpen: key === provider ? open : false,
      },
    ]),
  ) as Record<RuntimeProviderKey, RuntimeProviderDraft>;
}

function toListString(values?: string[]): string {
  return (values ?? []).join(", ");
}

function toLineListString(values?: string[]): string {
  return (values ?? []).join("\n");
}

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function parseSkillList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function normalizeLocalNetworkHostnames(value: PlatformSettings["allow_local_network_hostnames"]): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => item.trim()).filter((item) => item.length > 0);
  }
  return value ? [...defaultLocalNetworkHostnames] : [];
}

function localNetworkHostnamesEnabled(value: PlatformSettings["allow_local_network_hostnames"]): boolean {
  return normalizeLocalNetworkHostnames(value).length > 0;
}

function numberOrFallback(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function normalizeHexColor(value: string, fallback: string): string {
  const trimmed = value.trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed) ? trimmed : fallback;
}

function ToggleField({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-start justify-between gap-3 border border-[var(--fx-border)] bg-[hsl(var(--card)/0.7)] px-3 py-3 text-sm">
      <div className="min-w-0 space-y-1">
        <div className="font-medium text-[var(--foreground)]">{label}</div>
        <p className="fx-muted text-xs leading-5">{description}</p>
      </div>
      <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function ListField({
  label,
  description,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  description: string;
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
}) {
  return (
    <label className="block text-xs">
      <span className="font-medium text-[var(--foreground)]">{label}</span>
      <span className="mt-1 block fx-muted leading-5">{description}</span>
      <textarea
        className="fx-field mt-2 min-h-24 w-full px-3 py-2 text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

type BuilderSettingsWorkspaceProps = {
  view: "overview" | BuilderSettingsSectionKey;
};

export function BuilderSettingsWorkspace({ view }: BuilderSettingsWorkspaceProps) {
  const { addToast } = useToast();
  const [operatorSession, setOperatorSession] = useState<OperatorSession | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [guardrailRulesetId, setGuardrailRulesetId] = useState("");
  const [blockedKeywords, setBlockedKeywords] = useState("");
  const [allowedEgressHosts, setAllowedEgressHosts] = useState("");
  const [allowedRetrievalSources, setAllowedRetrievalSources] = useState("");
  const [allowedMcpServers, setAllowedMcpServers] = useState("");
  const [allowedRuntimeEngines, setAllowedRuntimeEngines] = useState("");
  const [highRiskToolPatterns, setHighRiskToolPatterns] = useState("");
  const [tenantScopedSkills, setTenantScopedSkills] = useState("");
  const [maxToolCalls, setMaxToolCalls] = useState("8");
  const [maxRetrievalItems, setMaxRetrievalItems] = useState("8");
  const [maxCollaborationAgents, setMaxCollaborationAgents] = useState("8");
  const [runtimeProviderStatus, setRuntimeProviderStatus] = useState<RuntimeProvider[]>([]);
  const [userRuntimeProviders, setUserRuntimeProviders] = useState<UserRuntimeProviderConfig[]>([]);
  const [runtimeProviderDrafts, setRuntimeProviderDrafts] = useState<Record<RuntimeProviderKey, RuntimeProviderDraft>>(() => buildRuntimeProviderDrafts([]));
  const [runtimeProviderError, setRuntimeProviderError] = useState<string | null>(null);
  const [providerAction, setProviderAction] = useState<RuntimeProviderKey | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [sessionResponse, settingsResponse, policyResponse] = await Promise.all([getOperatorSession(), getPlatformSettings(), getPlatformSecurityPolicy()]);
        if (cancelled) {
          return;
        }
        setOperatorSession(sessionResponse);
        setSettings(settingsResponse);
        setPolicy(policyResponse);
        setGuardrailRulesetId(settingsResponse.default_guardrail_ruleset_id ?? "");
        setBlockedKeywords(toListString(settingsResponse.global_blocked_keywords));
        setAllowedEgressHosts(toListString(settingsResponse.allowed_egress_hosts));
        setAllowedRetrievalSources(toListString(settingsResponse.allowed_retrieval_sources));
        setAllowedMcpServers(toListString(settingsResponse.allowed_mcp_server_urls));
        setAllowedRuntimeEngines(toListString(settingsResponse.allowed_runtime_engines));
        setHighRiskToolPatterns(toListString(settingsResponse.high_risk_tool_patterns));
        setTenantScopedSkills(toLineListString(settingsResponse.tenant_scoped_skills));
        setMaxToolCalls(String(settingsResponse.max_tool_calls_per_run ?? 8));
        setMaxRetrievalItems(String(settingsResponse.max_retrieval_items ?? 8));
        setMaxCollaborationAgents(String(settingsResponse.collaboration_max_agents ?? 8));
        setLoadError(null);
        setLoaded(true);
      } catch {
        if (!cancelled) {
          setLoadError("Could not load builder security settings.");
          addToast("error", "Could not load builder security settings.");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [addToast]);

  useEffect(() => {
    if (view !== "runtime") {
      return;
    }

    let cancelled = false;

    async function loadRuntimeProviderData() {
      try {
        const [providersResponse, userProviderResponse] = await Promise.all([getRuntimeProviders(), getUserRuntimeProviders()]);
        if (cancelled) {
          return;
        }
        setRuntimeProviderStatus(providersResponse.providers ?? []);
        setUserRuntimeProviders(userProviderResponse);
        setRuntimeProviderDrafts(buildRuntimeProviderDrafts(userProviderResponse));
        setRuntimeProviderError(null);
      } catch {
        if (!cancelled) {
          setRuntimeProviderStatus([]);
          setUserRuntimeProviders([]);
          setRuntimeProviderDrafts(buildRuntimeProviderDrafts([]));
          setRuntimeProviderError("Could not load inference backends.");
        }
      }
    }

    void loadRuntimeProviderData();
    return () => {
      cancelled = true;
    };
  }, [view]);

  const effectiveSummary = useMemo(() => {
    if (!policy) {
      return [] as string[];
    }
    return [
      `Classification: ${policy.effective.classification}`,
      `Tool calls capped at ${policy.effective.max_tool_calls_per_run} per run`,
      `Retrieval capped at ${policy.effective.max_retrieval_items} items`,
      `Runtime engines: ${policy.effective.allowed_runtime_engines.join(", ") || "none"}`,
      `Platform signals: ${policy.effective.enable_platform_signals ? policy.effective.platform_signal_enforcement : "off"}`,
    ];
  }, [policy]);

  const visibleSections = useMemo(() => getVisibleBuilderSettingsSections(operatorSession), [operatorSession]);

  const visibleSectionKeys = useMemo(() => new Set(visibleSections.map((section) => section.key)), [visibleSections]);

  const runtimeProviderStatusMap = useMemo(
    () => new Map(runtimeProviderStatus.map((provider) => [provider.provider, provider])),
    [runtimeProviderStatus],
  );

  const userRuntimeProviderMap = useMemo(
    () => new Map(userRuntimeProviders.map((provider) => [provider.provider, provider])),
    [userRuntimeProviders],
  );

  const overviewCards = useMemo(() => {
    const runtimeCount = settings?.allowed_runtime_engines?.length ?? 0;
    const egressCount = settings?.allowed_egress_hosts?.length ?? 0;
    const blockedCount = settings?.global_blocked_keywords?.length ?? 0;

    return {
      guardrails: `${blockedCount} blocked keyword${blockedCount === 1 ? "" : "s"}`,
      network: `${egressCount} egress destination${egressCount === 1 ? "" : "s"}`,
      runtime: `${runtimeCount} engine${runtimeCount === 1 ? "" : "s"} allowed`,
    } satisfies Record<OverviewCardSectionKey, string>;
  }, [settings]);

  const overviewSignals = useMemo(() => {
    if (!settings) {
      return [] as Array<{ label: string; value: string }>;
    }
    return [
      {
        label: "Auth posture",
        value: settings.require_authenticated_requests ? "Authenticated" : "Anonymous access allowed",
      },
      {
        label: "Approval path",
        value: settings.require_human_approval ? "Every run gated" : "Selective review",
      },
      {
        label: "Network stance",
        value: settings.enforce_local_network_only ? "Local only" : settings.enforce_egress_allowlist ? "Allowlist enforced" : "Open egress",
      },
      {
        label: "Runtime choice",
        value: settings.allow_runtime_engine_override ? "Builders may narrow engine choice" : "Platform default fixed",
      },
    ];
  }, [settings]);

  const overviewAttentionItems = useMemo(() => {
    if (!settings) {
      return [] as string[];
    }

    const items: string[] = [];
    if (settings.emergency_read_only_mode) {
      items.push("Emergency read-only mode is active, so write operations stay blocked until governance clears it.");
    }
    if (settings.block_new_runs || settings.block_graph_runs || settings.block_tool_calls || settings.block_retrieval_calls) {
      const blockedSurfaces = [
        settings.block_new_runs ? "new runs" : "",
        settings.block_graph_runs ? "graph runs" : "",
        settings.block_tool_calls ? "tool calls" : "",
        settings.block_retrieval_calls ? "retrieval calls" : "",
      ].filter(Boolean);
      items.push(`Execution controls are partially blocked: ${blockedSurfaces.join(", ")}.`);
    }
    if (!settings.require_authenticated_requests) {
      items.push("Authenticated-request enforcement is off, which lowers the baseline for admin and orchestration surfaces.");
    }
    if (!settings.enforce_egress_allowlist) {
      items.push("Egress allowlist enforcement is off, so builders can point connectors and tools at a wider network surface.");
    }
    if (!settings.enable_foss_guardrail_signals) {
      items.push("Platform guardrail signals are disabled, so prompt injection and exfiltration detections will not influence execution.");
    }
    if (!items.length) {
      items.push("No elevated platform-wide issues are visible. The current posture is authenticated, constrained, and builder-tunable inside the backend envelope.");
    }

    return items.slice(0, 4);
  }, [settings]);

  async function refreshRuntimeProviders() {
    const [providersResponse, userProviderResponse] = await Promise.all([getRuntimeProviders(), getUserRuntimeProviders()]);
    setRuntimeProviderStatus(providersResponse.providers ?? []);
    setUserRuntimeProviders(userProviderResponse);
    setRuntimeProviderDrafts(buildRuntimeProviderDrafts(userProviderResponse));
    setRuntimeProviderError(null);
  }

  function updateRuntimeProviderDraft(
    provider: RuntimeProviderKey,
    patch: Partial<RuntimeProviderDraft>,
  ) {
    setRuntimeProviderDrafts((current) => ({
      ...current,
      [provider]: {
        ...current[provider],
        ...patch,
      },
    }));
  }

  function setPreferredRuntimeProvider(provider: RuntimeProviderKey, preferred: boolean) {
    setRuntimeProviderDrafts((current) => {
      const next = { ...current };
      runtimeProviderPresets.forEach((preset) => {
        next[preset.provider] = {
          ...current[preset.provider],
          preferred: preferred ? preset.provider === provider : preset.provider === provider ? false : current[preset.provider].preferred,
        };
      });
      return next;
    });
  }

  async function handleRuntimeProviderSave(provider: RuntimeProviderKey) {
    const preset = runtimeProviderPresets.find((candidate) => candidate.provider === provider);
    const draft = runtimeProviderDrafts[provider];
    const model = draft.model.trim();
    const apiKey = draft.api_key.trim();

    if (!preset || !model) {
      addToast("error", "Model is required before saving an inference backend.");
      return;
    }

    setProviderAction(provider);
    try {
      await saveUserRuntimeProvider(provider, {
        model,
        available_models: draft.available_models,
        base_url: preset.baseUrl,
        api_key: apiKey || undefined,
        preferred: draft.preferred,
      });
      await refreshRuntimeProviders();
      addToast("success", "Inference backend saved.");
    } catch (error) {
      addToast("error", error instanceof Error ? error.message : "Could not save the inference backend.");
    } finally {
      setProviderAction(null);
    }
  }

  async function handleRuntimeProviderDelete(provider: RuntimeProviderKey) {
    setProviderAction(provider);
    try {
      await deleteUserRuntimeProvider(provider);
      await refreshRuntimeProviders();
      addToast("success", "Inference backend removed.");
    } catch (error) {
      addToast("error", error instanceof Error ? error.message : "Could not remove the inference backend.");
    } finally {
      setProviderAction(null);
    }
  }

  async function handleSave() {
    if (!settings) {
      return;
    }

    setSaving(true);
    try {
      const payload = {
        default_guardrail_ruleset_id: guardrailRulesetId.trim() || null,
        global_blocked_keywords: parseList(blockedKeywords),
        tenant_scoped_skills: parseSkillList(tenantScopedSkills),
        allowed_egress_hosts: parseList(allowedEgressHosts),
        allowed_retrieval_sources: parseList(allowedRetrievalSources),
        allowed_mcp_server_urls: parseList(allowedMcpServers),
        allowed_runtime_engines: parseList(allowedRuntimeEngines),
        high_risk_tool_patterns: parseList(highRiskToolPatterns),
        max_tool_calls_per_run: numberOrFallback(maxToolCalls, settings.max_tool_calls_per_run ?? 8),
        max_retrieval_items: numberOrFallback(maxRetrievalItems, settings.max_retrieval_items ?? 8),
        collaboration_max_agents: numberOrFallback(maxCollaborationAgents, settings.collaboration_max_agents ?? 8),
        emergency_read_only_mode: Boolean(settings.emergency_read_only_mode),
        block_new_runs: Boolean(settings.block_new_runs),
        block_graph_runs: Boolean(settings.block_graph_runs),
        block_tool_calls: Boolean(settings.block_tool_calls),
        block_retrieval_calls: Boolean(settings.block_retrieval_calls),
        require_human_approval: Boolean(settings.require_human_approval),
        require_human_approval_for_high_risk_tools: Boolean(settings.require_human_approval_for_high_risk_tools ?? true),
        enforce_egress_allowlist: Boolean(settings.enforce_egress_allowlist),
        enforce_local_network_only: Boolean(settings.enforce_local_network_only),
        allow_local_network_hostnames: normalizeLocalNetworkHostnames(settings.allow_local_network_hostnames),
        retrieval_require_local_source_url: Boolean(settings.retrieval_require_local_source_url),
        mcp_require_local_server: Boolean(settings.mcp_require_local_server),
        default_runtime_engine: settings.default_runtime_engine ?? "native",
        allow_runtime_engine_override: Boolean(settings.allow_runtime_engine_override),
        require_authenticated_requests: Boolean(settings.require_authenticated_requests),
        console_classification_banner_enabled: Boolean(settings.console_classification_banner_enabled ?? true),
        console_classification_banner_text: (settings.console_classification_banner_text ?? "Internal • Operational Console").trim() || "Internal • Operational Console",
        console_classification_banner_background_color: normalizeHexColor(settings.console_classification_banner_background_color ?? "#2e2a28", "#2e2a28"),
        console_classification_banner_text_color: normalizeHexColor(settings.console_classification_banner_text_color ?? "#e7dcc0", "#e7dcc0"),
        require_a2a_runtime_headers: Boolean(settings.require_a2a_runtime_headers),
        a2a_require_signed_messages: Boolean(settings.a2a_require_signed_messages ?? true),
        a2a_replay_protection: Boolean(settings.a2a_replay_protection ?? true),
        enable_foss_guardrail_signals: Boolean(settings.enable_foss_guardrail_signals ?? true),
        foss_guardrail_signal_enforcement: settings.foss_guardrail_signal_enforcement ?? "block_high",
      };

      await savePlatformSettings(payload);
      const [settingsResponse, policyResponse] = await Promise.all([getPlatformSettings(), getPlatformSecurityPolicy()]);
      setSettings(settingsResponse);
      setPolicy(policyResponse);
      addToast("success", "Builder security settings saved.");
    } catch (error) {
      addToast(
        "error",
        error instanceof Error
          ? error.message
          : "Could not save builder security settings. Check guardrail references and try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (view === "overview") {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <section className="space-y-4">
            <article className="fx-panel rounded-[1.6rem] p-5 shadow-[0_24px_60px_rgba(15,23,42,0.06)]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="max-w-2xl">
                  <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Overview</p>
                  <h2 className="mt-2 text-[1.12rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Builder operating picture</h2>
                  <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">Use this page to understand the platform posture before you dive into a specific control surface. It should answer what is constrained, what is risky, and where a builder needs to go next.</p>
                </div>
                <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--foreground)]">
                  {visibleSections.length} sections available
                </div>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {overviewSignals.map((item) => (
                  <div key={item.label} className="rounded-[1.15rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-4 py-3.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.45)]">
                    <p className="text-[0.72rem] font-medium tracking-[0.01em] text-[var(--fx-muted)]">{item.label}</p>
                    <p className="mt-2 text-sm leading-6 text-[var(--foreground)]">{item.value}</p>
                  </div>
                ))}
              </div>
            </article>

            <article className="fx-panel rounded-[1.6rem] p-5 shadow-[0_24px_60px_rgba(15,23,42,0.06)]">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Routes</p>
                  <h2 className="mt-2 text-[1.12rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Control surfaces</h2>
                  <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">Each route below owns a distinct part of the builder envelope and surfaces the live status you are inheriting right now.</p>
                </div>
                <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Choose a domain</div>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-2">
                {visibleSections.map((section) => (
                  <Link
                    key={section.key}
                    href={section.href}
                    className="rounded-[1.35rem] border border-[var(--ui-border)] bg-[hsl(var(--card)/0.8)] p-4 transition hover:-translate-y-0.5 hover:border-[color-mix(in_srgb,var(--fx-primary)_24%,var(--ui-border))] hover:bg-[color-mix(in_srgb,var(--fx-primary)_6%,hsl(var(--card)))] hover:shadow-[0_22px_44px_rgba(15,23,42,0.08)]"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-[0.72rem] font-medium tracking-[0.01em] text-[var(--fx-muted)]">{section.navLabel}</p>
                        <h3 className="mt-2 text-sm font-semibold text-[var(--foreground)]">{section.title}</h3>
                        <p className="mt-2 text-sm leading-6 text-[var(--fx-muted)]">{section.summary}</p>
                      </div>
                      <span className="fx-pill shrink-0 px-2.5 py-1 text-[0.72rem] font-medium text-[var(--foreground)]">
                        {getBuilderSettingsNavBadge(section.key, settings, policy) ?? "Open"}
                      </span>
                    </div>

                    <div className="mt-4 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card))] px-3 py-3 text-xs text-[var(--foreground)] shadow-[inset_0_1px_0_rgba(255,255,255,0.4)]">
                      <p className="font-medium text-[var(--foreground)]">
                        {section.key !== "governance"
                          ? overviewCards[section.key as OverviewCardSectionKey]
                          : settings?.emergency_read_only_mode
                            ? "Emergency controls engaged"
                            : "Approval gates and kill switches"}
                      </p>
                      <p className="mt-2 leading-5 text-[var(--fx-muted)]">{section.detail}</p>
                    </div>
                  </Link>
                ))}
              </div>
            </article>

            <article className="border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] p-4">
              <h2 className="text-sm font-semibold text-[var(--foreground)]">Attention queue</h2>
              <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">These are the platform-wide conditions a builder should notice before changing anything downstream.</p>

              <div className="mt-4 space-y-3">
                {overviewAttentionItems.map((item) => (
                  <div key={item} className="rounded-[1.1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-4 py-3 text-sm leading-6 text-[var(--foreground)]">
                    {item}
                  </div>
                ))}
              </div>
            </article>

            {loadError ? (
              <article className="border border-[color-mix(in_srgb,var(--fx-danger)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] p-4 text-sm text-[var(--foreground)]">
                {loadError}
              </article>
            ) : null}
          </section>

          <aside className="space-y-4">
            <SettingsRailCard title="Current envelope" description="The highest-level defaults lower scopes inherit before narrowing them.">
              <ul className="space-y-2 text-xs text-[var(--foreground)]">
                {effectiveSummary.map((item) => (
                  <li key={item} className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-2.5">{item}</li>
                ))}
              </ul>
            </SettingsRailCard>

            <SettingsRailCard title="Builder heuristics" description="What this overview page is meant to answer before you move into an editing screen.">
              <ul className="space-y-2 text-xs text-[var(--foreground)]">
                {[
                  "What is platform-wide and inherited everywhere?",
                  "Which controls are currently risky or elevated?",
                  "Which builder settings route owns the change I need?",
                ].map((item) => (
                    <li key={item} className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-2.5">{item}</li>
                ))}
              </ul>
            </SettingsRailCard>

            <SettingsRailCard title="Server-owned rails" description="Controls that remain backend-enforced even when builders tune the envelope.">
              <ul className="space-y-2 text-xs text-[var(--foreground)]">
                {(policy?.backend_enforced_controls ?? []).map((item) => (
                  <li key={item} className="rounded-[0.95rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-2.5 break-words">{item.replace(/_/g, " ")}</li>
                ))}
              </ul>
            </SettingsRailCard>
          </aside>
        </div>
      </div>
    );
  }

  if (!visibleSectionKeys.has(view)) {
    return (
      <article className="border border-[color-mix(in_srgb,var(--fx-warning)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-warning)_8%,transparent)] p-4 text-sm text-[var(--foreground)]">
        This settings domain requires elevated builder privileges.
      </article>
    );
  }

  const section = getBuilderSettingsSection(view);

  return (
    <div className="space-y-4">
      <section className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-4 rounded-[1.6rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] px-5 py-4 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
          <div className="max-w-3xl">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] fx-muted">Builder / {section.navLabel}</p>
            <h2 className="mt-2 text-[1.35rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">{section.title}</h2>
            <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">{section.detail}</p>
          </div>
          <button className="fx-btn-primary px-3 py-2 text-sm" disabled={!loaded || saving || !settings} onClick={handleSave}>
            {saving ? "Saving..." : "Save changes"}
          </button>
        </div>

        {loadError ? (
          <article className="border border-[color-mix(in_srgb,var(--fx-danger)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] p-4 text-sm text-[var(--foreground)]">
            {loadError}
          </article>
        ) : null}

        {view === "guardrails" ? (
          <article className="fx-panel p-3">
            <h3 className="text-sm font-semibold">Guardrails and approvals</h3>
            <p className="fx-muted text-xs leading-5">Set the default safety envelope that workflows and agents may only tighten.</p>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <label className="block text-xs">
                <span className="font-medium text-[var(--foreground)]">Published guardrail ruleset</span>
                <span className="mt-1 block fx-muted">Optional published ruleset ID applied as the default baseline.</span>
                <input className="fx-field mt-2 w-full px-3 py-2 text-sm" value={guardrailRulesetId} onChange={(e) => setGuardrailRulesetId(e.target.value)} />
              </label>
              <label className="block text-xs">
                <span className="font-medium text-[var(--foreground)]">Platform signal enforcement</span>
                <span className="mt-1 block fx-muted">Controls how FOSS guardrail signals affect execution.</span>
                <select
                  className="fx-field mt-2 w-full px-3 py-2 text-sm"
                  value={settings?.foss_guardrail_signal_enforcement ?? "block_high"}
                  onChange={(e) => setSettings((current) => (current ? { ...current, foss_guardrail_signal_enforcement: e.target.value as PlatformSignalEnforcement } : current))}
                >
                  {signalEnforcementOptions.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <ToggleField
                label="Require approval for every run"
                description="Escalates every run into a human review lane before completion or export."
                checked={Boolean(settings?.require_human_approval)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_human_approval: next } : current))}
              />
              <ToggleField
                label="Require approval for high-risk tools"
                description="Keeps sensitive tools behind a reviewer, even when the rest of the run can proceed automatically."
                checked={Boolean(settings?.require_human_approval_for_high_risk_tools ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_human_approval_for_high_risk_tools: next } : current))}
              />
              <ToggleField
                label="Enable platform signals"
                description="Runs prompt injection, exfiltration, and related signal checks as part of the baseline guardrail path."
                checked={Boolean(settings?.enable_foss_guardrail_signals ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, enable_foss_guardrail_signals: next } : current))}
              />
              <ToggleField
                label="Require authenticated requests"
                description="Blocks anonymous writes to admin and orchestration routes unless a caller identity is supplied."
                checked={Boolean(settings?.require_authenticated_requests)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_authenticated_requests: next } : current))}
              />
            </div>

            <div className="mt-3 grid gap-3 lg:grid-cols-2">
              <ListField
                label="Blocked keywords"
                description="Global prompt keywords blocked before workflow execution begins. Comma-separated."
                value={blockedKeywords}
                onChange={setBlockedKeywords}
                placeholder="credential dump, exfiltrate, ssn"
              />
              <ListField
                label="High-risk tool patterns"
                description="Tool names or patterns that should stay on the sharp end of approval gates. Comma-separated."
                value={highRiskToolPatterns}
                onChange={setHighRiskToolPatterns}
                placeholder="shell.exec, file.delete, terraform.apply"
              />
            </div>
          </article>
        ) : null}

        {view === "network" ? (
          <article className="fx-panel p-3">
            <h3 className="text-sm font-semibold">Network and retrieval boundaries</h3>
            <p className="fx-muted text-xs leading-5">Shape the maximum egress and data-source surface area available to workflows and agents.</p>
            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <ToggleField
                label="Enforce egress allowlist"
                description="Only destinations in the allowlist may be contacted when network egress is enabled."
                checked={Boolean(settings?.enforce_egress_allowlist)}
                onChange={(next) => setSettings((current) => (current ? { ...current, enforce_egress_allowlist: next } : current))}
              />
              <ToggleField
                label="Local-network only mode"
                description="Restricts runtime calls to local or approved private-network destinations."
                checked={Boolean(settings?.enforce_local_network_only)}
                onChange={(next) => setSettings((current) => (current ? { ...current, enforce_local_network_only: next } : current))}
              />
              <ToggleField
                label="Allow local hostnames"
                description="Permits localhost-style hostnames when local-network mode is active."
                checked={localNetworkHostnamesEnabled(settings?.allow_local_network_hostnames)}
                onChange={(next) => setSettings((current) => (current ? {
                  ...current,
                  allow_local_network_hostnames: next
                    ? normalizeLocalNetworkHostnames(current.allow_local_network_hostnames).length > 0
                      ? normalizeLocalNetworkHostnames(current.allow_local_network_hostnames)
                      : [...defaultLocalNetworkHostnames]
                    : [],
                } : current))}
              />
              <ToggleField
                label="Require local retrieval sources"
                description="Prevents retrieval connectors from pulling from remote sources outside approved local URLs."
                checked={Boolean(settings?.retrieval_require_local_source_url)}
                onChange={(next) => setSettings((current) => (current ? { ...current, retrieval_require_local_source_url: next } : current))}
              />
              <ToggleField
                label="Require local MCP servers"
                description="Restricts MCP connections to local or explicitly approved servers."
                checked={Boolean(settings?.mcp_require_local_server)}
                onChange={(next) => setSettings((current) => (current ? { ...current, mcp_require_local_server: next } : current))}
              />
              <ToggleField
                label="Require A2A runtime headers"
                description="Requires agent-to-agent requests to include runtime identity headers for enforcement and tracing."
                checked={Boolean(settings?.require_a2a_runtime_headers)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_a2a_runtime_headers: next } : current))}
              />
              <ToggleField
                label="Require signed A2A messages"
                description="Maintains signed message validation on agent-to-agent payloads."
                checked={Boolean(settings?.a2a_require_signed_messages ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, a2a_require_signed_messages: next } : current))}
              />
              <ToggleField
                label="Enable A2A replay protection"
                description="Protects collaborative calls against message replay."
                checked={Boolean(settings?.a2a_replay_protection ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, a2a_replay_protection: next } : current))}
              />
            </div>

            <div className="mt-3 grid gap-3 lg:grid-cols-3">
              <ListField
                label="Allowed egress hosts"
                description="Explicit destinations builders may reach when allowlist enforcement is on."
                value={allowedEgressHosts}
                onChange={setAllowedEgressHosts}
                placeholder="api.openai.com, localhost, graph.microsoft.com"
              />
              <ListField
                label="Allowed retrieval sources"
                description="Knowledge sources that retrieval nodes can query by default."
                value={allowedRetrievalSources}
                onChange={setAllowedRetrievalSources}
                placeholder="kb://default, file://docs, sqlite:///memory.db"
              />
              <ListField
                label="Allowed MCP servers"
                description="Server URLs available to MCP-enabled agents and workflows."
                value={allowedMcpServers}
                onChange={setAllowedMcpServers}
                placeholder="http://localhost:8787, https://mcp.notion.com/mcp"
              />
            </div>
          </article>
        ) : null}

        {view === "runtime" ? (
          <div className="space-y-4">
            <article className="fx-panel p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold">Inference backends</h3>
                  <p className="fx-muted text-xs leading-5">Save hosted API credentials or point the platform at a local OpenAI-compatible model endpoint.</p>
                </div>
                <div className="rounded-full border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-1.5 text-[0.72rem] font-medium text-[var(--foreground)]">
                  {userRuntimeProviders.length} backend{userRuntimeProviders.length === 1 ? "" : "s"} saved
                </div>
              </div>

              {runtimeProviderError ? (
                <div className="mt-3 border border-[color-mix(in_srgb,var(--fx-danger)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] px-3 py-3 text-sm text-[var(--foreground)]">
                  {runtimeProviderError}
                </div>
              ) : null}

              {(["Hosted APIs", "Local / open-weight"] as const).map((category) => (
                <div key={category} className="mt-4 space-y-3">
                  <div className="text-[0.72rem] font-medium tracking-[0.02em] fx-muted">{category}</div>
                  <div className="grid gap-3 xl:grid-cols-2">
                    {runtimeProviderPresets
                      .filter((preset) => preset.category === category)
                      .map((preset) => {
                        const draft = runtimeProviderDrafts[preset.provider];
                        const savedConfig = userRuntimeProviderMap.get(preset.provider);
                        const status = runtimeProviderStatusMap.get(preset.provider);
                        const busy = providerAction === preset.provider;
                        const modelOptions = getRuntimeProviderModelOptions(preset, draft);
                        const statusLabel = savedConfig
                          ? savedConfig.preferred
                            ? "Preferred"
                            : "Saved"
                          : status?.configured
                            ? "Environment"
                            : "Not configured";

                        return (
                          <section key={preset.provider} className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.74)] p-3">
                            <div className="flex flex-wrap items-start justify-between gap-3">
                              <div>
                                <h4 className="text-sm font-semibold text-[var(--foreground)]">{preset.title}</h4>
                                <p className="mt-1 text-xs leading-5 text-[var(--fx-muted)]">{preset.blurb}</p>
                              </div>
                              <span className="rounded-full border border-[var(--fx-border)] bg-[hsl(var(--card))] px-2.5 py-1 text-[0.68rem] font-medium tracking-[0.01em] text-[var(--foreground)]">
                                {statusLabel}
                              </span>
                            </div>

                            <div className="mt-3 grid gap-3 lg:grid-cols-2">
                              <label className="block text-xs">
                                <span className="font-medium text-[var(--foreground)]">Default model</span>
                                <select
                                  className="fx-field mt-2 w-full px-3 py-2 text-sm"
                                  value={draft.model}
                                  onChange={(e) => updateRuntimeProviderDraft(preset.provider, { model: e.target.value })}
                                >
                                  {modelOptions.map((model) => (
                                    <option key={model} value={model}>{model}</option>
                                  ))}
                                </select>
                              </label>
                              <div className="text-xs">
                                <p className="font-medium text-[var(--foreground)]">Allowed models</p>
                                <div className="relative mt-2">
                                  <button
                                    type="button"
                                    className="fx-field flex w-full items-center justify-between px-3 py-2 text-left text-sm"
                                    onClick={() =>
                                      setRuntimeProviderDrafts((current) =>
                                        setRuntimeProviderModelMenuOpen(current, preset.provider, !current[preset.provider].modelMenuOpen),
                                      )
                                    }
                                  >
                                    <span className="truncate">{draft.available_models.length} model{draft.available_models.length === 1 ? "" : "s"} selected</span>
                                    <svg viewBox="0 0 16 16" className={`h-3.5 w-3.5 transition-transform ${draft.modelMenuOpen ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.6">
                                      <path d="M3.5 6 8 10.5 12.5 6" strokeLinecap="square" strokeLinejoin="miter" />
                                    </svg>
                                  </button>
                                  {draft.modelMenuOpen ? (
                                    <div className="absolute left-0 right-0 z-20 mt-2 max-h-72 overflow-auto rounded-[1rem] border border-[var(--ui-border)] bg-[hsl(var(--card))] p-2 shadow-xl">
                                      <div className="grid gap-2">
                                        {preset.models.map((model) => (
                                          <label key={model} className="flex items-center gap-2 rounded-[0.75rem] px-2 py-1.5 text-[var(--foreground)] hover:bg-[var(--fx-nav-hover)]">
                                            <input
                                              type="checkbox"
                                              aria-label={model}
                                              checked={draft.available_models.includes(model)}
                                              onChange={() => updateRuntimeProviderDraft(preset.provider, toggleRuntimeProviderAvailableModel(draft, model))}
                                            />
                                            <span className="text-xs leading-5">{model}</span>
                                          </label>
                                        ))}
                                      </div>
                                    </div>
                                  ) : null}
                                </div>
                              </div>
                              <label className="block text-xs lg:col-span-2">
                                <span className="font-medium text-[var(--foreground)]">API key</span>
                                <input
                                  type="password"
                                  className="fx-field mt-2 w-full px-3 py-2 text-sm"
                                  value={draft.api_key}
                                  onChange={(e) => updateRuntimeProviderDraft(preset.provider, { api_key: e.target.value })}
                                  autoComplete="new-password"
                                  autoCapitalize="off"
                                  autoCorrect="off"
                                  spellCheck={false}
                                  placeholder={savedConfig?.api_key_masked ? "Leave blank to keep the current key" : "Paste a provider key"}
                                />
                                <span className="mt-2 block leading-5 text-[var(--fx-muted)]">
                                  Keys are submitted once, stored encrypted server-side, and only returned to the UI as masked values.
                                </span>
                              </label>
                            </div>

                            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                              <label className="flex items-center gap-2 text-xs text-[var(--foreground)]">
                                <input
                                  type="checkbox"
                                  checked={draft.preferred}
                                  onChange={(e) => setPreferredRuntimeProvider(preset.provider, e.target.checked)}
                                />
                                Set as preferred backend
                              </label>
                              <div className="text-xs text-[var(--fx-muted)]">
                                {savedConfig?.api_key_masked ? `Stored key ${savedConfig.api_key_masked}` : preset.examples}
                              </div>
                            </div>

                            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--fx-border)] pt-3">
                              <div className="text-xs text-[var(--fx-muted)]">
                                {savedConfig?.updated_at ? `Updated ${new Date(savedConfig.updated_at).toLocaleString()}` : "Save once to make this backend available in runtime selection."}
                              </div>
                              <div className="flex flex-wrap gap-2">
                                {savedConfig ? (
                                  <button
                                    type="button"
                                    className="fx-btn-secondary px-3 py-2 text-sm"
                                    disabled={busy}
                                    onClick={() => handleRuntimeProviderDelete(preset.provider)}
                                  >
                                    Remove
                                  </button>
                                ) : null}
                                <button
                                  type="button"
                                  className="fx-btn-primary px-3 py-2 text-sm"
                                  disabled={busy}
                                  onClick={() => handleRuntimeProviderSave(preset.provider)}
                                >
                                  {busy ? "Saving..." : "Save backend"}
                                </button>
                              </div>
                            </div>
                          </section>
                        );
                      })}
                  </div>
                </div>
              ))}
            </article>

            <article className="fx-panel p-3">
              <h3 className="text-sm font-semibold">Runtime ceilings</h3>
              <p className="fx-muted text-xs leading-5">Set platform-wide orchestration limits and engine defaults.</p>
              <div className="mt-3 grid gap-3 lg:grid-cols-2 xl:grid-cols-4">
                <label className="block text-xs">
                  <span className="font-medium text-[var(--foreground)]">Max tool calls per run</span>
                  <input className="fx-field mt-2 w-full px-3 py-2 text-sm" value={maxToolCalls} onChange={(e) => setMaxToolCalls(e.target.value)} />
                </label>
                <label className="block text-xs">
                  <span className="font-medium text-[var(--foreground)]">Max retrieval items</span>
                  <input className="fx-field mt-2 w-full px-3 py-2 text-sm" value={maxRetrievalItems} onChange={(e) => setMaxRetrievalItems(e.target.value)} />
                </label>
                <label className="block text-xs">
                  <span className="font-medium text-[var(--foreground)]">Max collaborators</span>
                  <input className="fx-field mt-2 w-full px-3 py-2 text-sm" value={maxCollaborationAgents} onChange={(e) => setMaxCollaborationAgents(e.target.value)} />
                </label>
                <label className="block text-xs">
                  <span className="font-medium text-[var(--foreground)]">Default runtime engine</span>
                  <select
                    className="fx-field mt-2 w-full px-3 py-2 text-sm"
                    value={settings?.default_runtime_engine ?? "native"}
                    onChange={(e) => setSettings((current) => (current ? { ...current, default_runtime_engine: e.target.value as PlatformSettings["default_runtime_engine"] } : current))}
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={engine} value={engine}>{engine}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="mt-3 grid gap-2 lg:grid-cols-2">
                <ToggleField
                  label="Allow runtime override"
                  description="Lets lower scopes narrow engine choices inside the platform allowlist."
                  checked={Boolean(settings?.allow_runtime_engine_override)}
                  onChange={(next) => setSettings((current) => (current ? { ...current, allow_runtime_engine_override: next } : current))}
                />
                <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.7)] px-3 py-3 text-sm">
                  <p className="font-medium text-[var(--foreground)]">Default memory scopes</p>
                  <p className="mt-1 fx-muted text-xs leading-5">{policy?.platform_defaults.allowed_memory_scopes.join(", ") ?? "run, session, workflow, agent"}</p>
                </div>
              </div>

              <div className="mt-3">
                <ListField
                  label="Allowed runtime engines"
                  description="Comma-separated engine allowlist available below the platform scope."
                  value={allowedRuntimeEngines}
                  onChange={setAllowedRuntimeEngines}
                  placeholder="native, langgraph, langchain"
                />
              </div>
            </article>
          </div>
        ) : null}

        {view === "governance" ? (
          <article className="fx-panel p-3">
            <h3 className="text-sm font-semibold">Approvals and operational governance</h3>
            <p className="fx-muted text-xs leading-5">Separate approval policy and emergency operating controls from prompt guardrails so they can be audited and changed independently.</p>

            <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <ToggleField
                label="Require approval for every run"
                description="Escalates every run into a human review lane before completion or export."
                checked={Boolean(settings?.require_human_approval)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_human_approval: next } : current))}
              />
              <ToggleField
                label="Require approval for high-risk tools"
                description="Keeps sensitive tools behind a reviewer, even when the rest of the run can proceed automatically."
                checked={Boolean(settings?.require_human_approval_for_high_risk_tools ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_human_approval_for_high_risk_tools: next } : current))}
              />
              <ToggleField
                label="Require authenticated requests"
                description="Blocks anonymous writes to admin and orchestration routes unless a caller identity is supplied."
                checked={Boolean(settings?.require_authenticated_requests)}
                onChange={(next) => setSettings((current) => (current ? { ...current, require_authenticated_requests: next } : current))}
              />
              <ToggleField
                label="Emergency read-only mode"
                description="Blocks write actions across the platform when an incident requires a fail-safe operating posture."
                checked={Boolean(settings?.emergency_read_only_mode)}
                onChange={(next) => setSettings((current) => (current ? { ...current, emergency_read_only_mode: next } : current))}
              />
              <ToggleField
                label="Show console classification banner"
                description="Displays the top-of-console environment or classification banner for all operators."
                checked={Boolean(settings?.console_classification_banner_enabled ?? true)}
                onChange={(next) => setSettings((current) => (current ? { ...current, console_classification_banner_enabled: next } : current))}
              />
              <ToggleField
                label="Block new workflow runs"
                description="Stops new orchestrated runs from starting while leaving existing history available."
                checked={Boolean(settings?.block_new_runs)}
                onChange={(next) => setSettings((current) => (current ? { ...current, block_new_runs: next } : current))}
              />
              <ToggleField
                label="Block graph runs"
                description="Freezes graph execution paths without disabling the broader builder surface."
                checked={Boolean(settings?.block_graph_runs)}
                onChange={(next) => setSettings((current) => (current ? { ...current, block_graph_runs: next } : current))}
              />
              <ToggleField
                label="Block tool calls"
                description="Prevents runtime tool invocation while keeping policy inspection and configuration available."
                checked={Boolean(settings?.block_tool_calls)}
                onChange={(next) => setSettings((current) => (current ? { ...current, block_tool_calls: next } : current))}
              />
              <ToggleField
                label="Block retrieval calls"
                description="Disables retrieval execution when external or indexed data access must be paused quickly."
                checked={Boolean(settings?.block_retrieval_calls)}
                onChange={(next) => setSettings((current) => (current ? { ...current, block_retrieval_calls: next } : current))}
              />
            </div>

            <div className="mt-3 grid gap-3 lg:grid-cols-3">
              <ListField
                label="Tenant-scoped /skills"
                description="Slash skills inherited across the authenticated tenant scope. Enter one skill per line."
                value={tenantScopedSkills}
                onChange={setTenantScopedSkills}
                placeholder="/tenant-oncall\n/tenant-research\n/tenant-support"
              />
              <label className="block text-xs lg:col-span-3">
                <span className="font-medium text-[var(--foreground)]">Console banner text</span>
                <span className="mt-1 block fx-muted">Message displayed in the remaining top banner after the soft-launch strip is removed.</span>
                <input
                  className="fx-field mt-2 w-full px-3 py-2 text-sm"
                  value={settings?.console_classification_banner_text ?? "Internal • Operational Console"}
                  onChange={(e) => setSettings((current) => (current ? { ...current, console_classification_banner_text: e.target.value } : current))}
                />
              </label>
              <label className="block text-xs">
                <span className="font-medium text-[var(--foreground)]">Banner background color</span>
                <span className="mt-1 block fx-muted">Hex color used for the banner background.</span>
                <input
                  type="color"
                  aria-label="Banner background color"
                  className="fx-field mt-2 h-10 w-full px-2 py-1"
                  value={normalizeHexColor(settings?.console_classification_banner_background_color ?? "#2e2a28", "#2e2a28")}
                  onChange={(e) => setSettings((current) => (current ? { ...current, console_classification_banner_background_color: e.target.value } : current))}
                />
              </label>
              <label className="block text-xs">
                <span className="font-medium text-[var(--foreground)]">Banner text color</span>
                <span className="mt-1 block fx-muted">Hex color used for the banner copy.</span>
                <input
                  type="color"
                  aria-label="Banner text color"
                  className="fx-field mt-2 h-10 w-full px-2 py-1"
                  value={normalizeHexColor(settings?.console_classification_banner_text_color ?? "#e7dcc0", "#e7dcc0")}
                  onChange={(e) => setSettings((current) => (current ? { ...current, console_classification_banner_text_color: e.target.value } : current))}
                />
              </label>
              <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.78)] px-3 py-3 text-xs lg:self-end">
                <p className="font-medium text-[var(--foreground)]">Banner preview</p>
                <div
                  className="mt-2 rounded-[0.9rem] border border-[var(--ui-border)] px-3 py-2 text-[11px] font-medium tracking-[0.02em]"
                  style={{
                    background: normalizeHexColor(settings?.console_classification_banner_background_color ?? "#2e2a28", "#2e2a28"),
                    color: normalizeHexColor(settings?.console_classification_banner_text_color ?? "#e7dcc0", "#e7dcc0"),
                  }}
                >
                  {(settings?.console_classification_banner_text ?? "Internal • Operational Console").trim() || "Internal • Operational Console"}
                </div>
              </div>
            </div>
          </article>
        ) : null}
      </section>
    </div>
  );
}
