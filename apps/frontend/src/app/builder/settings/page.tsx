"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { BuilderSettingsWorkspace } from "@/components/builder-settings-workspace";
import {
  getModelsOverview,
  getPlatformSecurityPolicy,
  getPlatformSettings,
  getProviderModels,
  savePlatformSettings,
  type ModelsOverview,
} from "@/lib/api";
import { useToast } from "@/components/toast";
import type { PlatformSettings, PlatformSignalEnforcement, SecurityPolicyResponse } from "@/types/frontier";

const runtimeEngineOptions = ["native", "langgraph", "langchain", "semantic-kernel", "autogen"] as const;

type SettingsTab = "providers" | "guardrails" | "network" | "runtime";

const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
  { id: "providers", label: "AI Providers" },
  { id: "guardrails", label: "Guardrails & Approvals" },
  { id: "network", label: "Network & Retrieval" },
  { id: "runtime", label: "Runtime & Limits" },
];

type ProviderDraft = { api_key: string; base_url: string; default_model: string };

// Stable provider list (mirrors the backend registry); status/badges enrich
// from /models/overview when available.
const providerList: Array<{ id: string; label: string; keyHint: string; baseHint: string; modelHint: string; keyRequired: boolean }> = [
  { id: "openai", label: "OpenAI", keyHint: "sk-...", baseHint: "default endpoint", modelHint: "gpt-5.2", keyRequired: true },
  { id: "anthropic", label: "Anthropic Claude", keyHint: "sk-ant-...", baseHint: "https://api.anthropic.com/v1", modelHint: "claude-sonnet-4-6", keyRequired: true },
  { id: "azure", label: "Microsoft Azure OpenAI", keyHint: "Azure API key", baseHint: "https://<resource>.openai.azure.com/openai/v1", modelHint: "your deployment name", keyRequired: true },
  { id: "google", label: "Google Gemini", keyHint: "AIza...", baseHint: "Gemini OpenAI-compat endpoint", modelHint: "gemini-2.5-pro", keyRequired: true },
  { id: "mistral", label: "Mistral", keyHint: "Mistral API key", baseHint: "https://api.mistral.ai/v1", modelHint: "mistral-large-latest", keyRequired: true },
  { id: "xai", label: "xAI Grok", keyHint: "xai-...", baseHint: "https://api.x.ai/v1", modelHint: "grok-4", keyRequired: true },
  { id: "nim", label: "NVIDIA NIM", keyHint: "nvapi-...", baseHint: "blank = NVIDIA-hosted; set for self-hosted NIM", modelHint: "meta/llama-3.3-70b-instruct", keyRequired: true },
  { id: "ollama", label: "Local (Ollama)", keyHint: "", baseHint: "http://ollama:11434", modelHint: "llama3.2:3b", keyRequired: false },
];
const signalEnforcementOptions: Array<{ value: PlatformSignalEnforcement; label: string }> = [
  { value: "off", label: "Off" },
  { value: "audit", label: "Audit only" },
  { value: "block_high", label: "Block high-risk" },
  { value: "raise_high", label: "Escalate high-risk" },
];

function toListString(values?: string[]): string {
  return (values ?? []).join(", ");
}

function parseList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function numberOrFallback(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
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
    <label className="flex items-start justify-between gap-3 rounded border border-[var(--fx-border)] px-3 py-3 text-sm">
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

function BuilderSecurityConfiguration() {
  const { addToast } = useToast();
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [providerOverview, setProviderOverview] = useState<ModelsOverview | null>(null);
  const [guardrailRulesetId, setGuardrailRulesetId] = useState("");
  const [blockedKeywords, setBlockedKeywords] = useState("");
  const [allowedEgressHosts, setAllowedEgressHosts] = useState("");
  const [allowedRetrievalSources, setAllowedRetrievalSources] = useState("");
  const [allowedMcpServers, setAllowedMcpServers] = useState("");
  const [allowedRuntimeEngines, setAllowedRuntimeEngines] = useState("");
  const [highRiskToolPatterns, setHighRiskToolPatterns] = useState("");
  const [maxToolCalls, setMaxToolCalls] = useState("8");
  const [maxRetrievalItems, setMaxRetrievalItems] = useState("8");
  const [maxCollaborationAgents, setMaxCollaborationAgents] = useState("8");
  // AI providers — unified map; key fields are write-only (blank keeps stored).
  const [activeTab, setActiveTab] = useState<SettingsTab>("providers");
  const [providerDrafts, setProviderDrafts] = useState<Record<string, ProviderDraft>>({});
  const [providerModels, setProviderModels] = useState<Record<string, string[]>>({});
  const [providerTests, setProviderTests] = useState<Record<string, string>>({});
  const [openaiFallbackModel, setOpenaiFallbackModel] = useState("");
  const [allowLocalHostnames, setAllowLocalHostnames] = useState("");

  // Deep-link support: /builder/settings?tab=providers|guardrails|network|runtime
  useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested && settingsTabs.some((tab) => tab.id === requested)) {
      setActiveTab(requested as SettingsTab);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [settingsResponse, policyResponse] = await Promise.all([getPlatformSettings(), getPlatformSecurityPolicy()]);
        if (cancelled) {
          return;
        }
        setSettings(settingsResponse);
        setPolicy(policyResponse);
        void getModelsOverview()
          .then((overview) => {
            if (cancelled) {
              return;
            }
            setProviderOverview(overview);
            // Populate default-model dropdowns for connected providers.
            for (const entry of overview.external ?? []) {
              if (!entry.configured) {
                continue;
              }
              void getProviderModels(entry.id)
                .then((listing) => {
                  if (!cancelled && listing.models.length > 0) {
                    setProviderModels((current) => ({ ...current, [entry.id]: listing.models }));
                  }
                })
                .catch(() => {
                  // Best-effort; the model field stays free-text.
                });
            }
          })
          .catch(() => {
            // Provider status is informational; the form remains editable.
          });
        setGuardrailRulesetId(settingsResponse.default_guardrail_ruleset_id ?? "");
        setBlockedKeywords(toListString(settingsResponse.global_blocked_keywords));
        setAllowedEgressHosts(toListString(settingsResponse.allowed_egress_hosts));
        setAllowedRetrievalSources(toListString(settingsResponse.allowed_retrieval_sources));
        setAllowedMcpServers(toListString(settingsResponse.allowed_mcp_server_urls));
        setAllowedRuntimeEngines(toListString(settingsResponse.allowed_runtime_engines));
        setHighRiskToolPatterns(toListString(settingsResponse.high_risk_tool_patterns));
        setMaxToolCalls(String(settingsResponse.max_tool_calls_per_run ?? 8));
        setMaxRetrievalItems(String(settingsResponse.max_retrieval_items ?? 8));
        setMaxCollaborationAgents(String(settingsResponse.collaboration_max_agents ?? 8));
        setOpenaiFallbackModel(settingsResponse.openai_fallback_model ?? "");
        setAllowLocalHostnames(toListString(Array.isArray(settingsResponse.allow_local_network_hostnames) ? settingsResponse.allow_local_network_hostnames : []));
        const drafts: Record<string, ProviderDraft> = {};
        for (const provider of providerList) {
          const stored = settingsResponse.ai_providers?.[provider.id] ?? {};
          drafts[provider.id] = {
            api_key: "",
            base_url: stored.base_url ?? "",
            default_model: stored.default_model ?? "",
          };
        }
        setProviderDrafts(drafts);
        setLoaded(true);
      } catch {
        if (!cancelled) {
          addToast("error", "Could not load builder security settings.");
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [addToast]);

  async function handleSave() {
    if (!settings) {
      return;
    }

    setSaving(true);
    try {
      const payload = {
        default_guardrail_ruleset_id: guardrailRulesetId.trim() || null,
        global_blocked_keywords: parseList(blockedKeywords),
        allowed_egress_hosts: parseList(allowedEgressHosts),
        allowed_retrieval_sources: parseList(allowedRetrievalSources),
        allowed_mcp_server_urls: parseList(allowedMcpServers),
        allowed_runtime_engines: parseList(allowedRuntimeEngines),
        high_risk_tool_patterns: parseList(highRiskToolPatterns),
        max_tool_calls_per_run: numberOrFallback(maxToolCalls, settings.max_tool_calls_per_run ?? 8),
        max_retrieval_items: numberOrFallback(maxRetrievalItems, settings.max_retrieval_items ?? 8),
        collaboration_max_agents: numberOrFallback(maxCollaborationAgents, settings.collaboration_max_agents ?? 8),
        require_human_approval: Boolean(settings.require_human_approval),
        require_human_approval_for_high_risk_tools: Boolean(settings.require_human_approval_for_high_risk_tools ?? true),
        enforce_egress_allowlist: Boolean(settings.enforce_egress_allowlist),
        enforce_local_network_only: Boolean(settings.enforce_local_network_only),
        // This field is a hostname list, not a flag — sending a Boolean made
        // pydantic reject the whole settings save.
        allow_local_network_hostnames: parseList(allowLocalHostnames),
        retrieval_require_local_source_url: Boolean(settings.retrieval_require_local_source_url),
        mcp_require_local_server: Boolean(settings.mcp_require_local_server),
        default_runtime_engine: settings.default_runtime_engine ?? "native",
        allow_runtime_engine_override: Boolean(settings.allow_runtime_engine_override),
        require_authenticated_requests: Boolean(settings.require_authenticated_requests),
        require_a2a_runtime_headers: Boolean(settings.require_a2a_runtime_headers),
        a2a_require_signed_messages: Boolean(settings.a2a_require_signed_messages ?? true),
        a2a_replay_protection: Boolean(settings.a2a_replay_protection ?? true),
        enable_foss_guardrail_signals: Boolean(settings.enable_foss_guardrail_signals ?? true),
        foss_guardrail_signal_enforcement: settings.foss_guardrail_signal_enforcement ?? "block_high",
        // Unified provider map. API keys are write-only server-side: blank
        // submissions keep the stored key; "__clear__" removes it.
        ai_providers: Object.fromEntries(
          Object.entries(providerDrafts).map(([providerId, draft]) => [
            providerId,
            {
              api_key: draft.api_key.trim(),
              base_url: draft.base_url.trim(),
              default_model: draft.default_model.trim(),
            },
          ]),
        ),
        openai_fallback_model: openaiFallbackModel.trim(),
      };

      await savePlatformSettings(payload);
      setProviderDrafts((current) =>
        Object.fromEntries(
          Object.entries(current).map(([providerId, draft]) => [providerId, { ...draft, api_key: "" }]),
        ),
      );
      const [settingsResponse, policyResponse] = await Promise.all([getPlatformSettings(), getPlatformSecurityPolicy()]);
      setSettings(settingsResponse);
      setPolicy(policyResponse);
      addToast("success", "Builder security settings saved.");
    } catch {
      addToast("error", "Could not save builder security settings. Check guardrail references and try again.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--ui-border)] pb-3">
        <div>
          <p className="text-[11px] uppercase tracking-wide fx-muted">
            <Link href="/builder/settings" className="no-underline hover:underline">Builder settings</Link> / Security
          </p>
          <h1 className="text-xl font-semibold">Security Configuration</h1>
          <p className="fx-muted max-w-3xl text-sm leading-6">
            Configure the bounded inputs builders are allowed to tune. The enforcement rails stay server-side — because letting the UI reorder policy gates would be a very exciting outage.
          </p>
        </div>
        <button className="fx-btn-primary px-3 py-2 text-sm" disabled={!loaded || saving || !settings} onClick={handleSave}>
          {saving ? "Saving..." : "Save security defaults"}
        </button>
      </div>

      <div className="flex items-center gap-1 rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--card))] p-1 text-sm">
        {settingsTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`rounded px-3 py-1.5 ${
              activeTab === tab.id
                ? "bg-[hsl(var(--primary)/0.18)] font-medium text-[var(--foreground)]"
                : "text-[hsl(var(--muted-foreground))]"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="space-y-4">
          <article hidden={activeTab !== "providers"} className="fx-panel p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">AI inference providers</h2>
              <div className="flex flex-wrap gap-1.5 text-[10px]">
                {(providerOverview?.external ?? []).map((entry) => (
                  <span
                    key={entry.id}
                    className={`rounded-full border px-2 py-0.5 font-semibold ${
                      entry.configured
                        ? "border-[hsl(var(--state-success)/0.45)] text-[hsl(var(--state-success))]"
                        : "border-[hsl(var(--state-warning)/0.45)] text-[hsl(var(--state-warning))]"
                    }`}
                  >
                    {entry.label}: {entry.configured ? "ready" : "not configured"}
                  </span>
                ))}
              </div>
            </div>
            <p className="fx-muted text-xs leading-5">
              All providers speak OpenAI-compatible chat completions; reference models from agents as{" "}
              <code className="font-mono">&lt;provider&gt;/&lt;model&gt;</code> (bare ids default to OpenAI).
              Settings override environment variables and apply immediately to new runs. API keys are
              write-only: blank keeps the stored value, <code className="font-mono">__clear__</code> removes
              it. Local model downloads are managed on the{" "}
              <a href="/builder/models" className="underline decoration-dotted underline-offset-2">Models page</a>.
            </p>

            <div className="mt-3 grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
              {providerList.map((provider) => {
                const draft = providerDrafts[provider.id] ?? { api_key: "", base_url: "", default_model: "" };
                const storedKey = Boolean(settings?.ai_providers?.[provider.id]?.api_key_configured);
                const updateDraft = (field: keyof ProviderDraft, value: string) =>
                  setProviderDrafts((current) => ({
                    ...current,
                    [provider.id]: { ...draft, ...current[provider.id], [field]: value },
                  }));
                const runProviderTest = async () => {
                  setProviderTests((current) => ({ ...current, [provider.id]: "Testing..." }));
                  try {
                    const listing = await getProviderModels(provider.id);
                    if (!listing.configured) {
                      setProviderTests((current) => ({
                        ...current,
                        [provider.id]: listing.reason || "Not configured — save a key first.",
                      }));
                    } else if (listing.models.length > 0) {
                      setProviderTests((current) => ({
                        ...current,
                        [provider.id]: `Connected — ${listing.models.length} models available.`,
                      }));
                      setProviderModels((current) => ({ ...current, [provider.id]: listing.models }));
                    } else {
                      setProviderTests((current) => ({
                        ...current,
                        [provider.id]: listing.reason
                          ? `Connection failed: ${listing.reason}`
                          : "Connected, but no models reported.",
                      }));
                    }
                  } catch {
                    setProviderTests((current) => ({
                      ...current,
                      [provider.id]: "Test failed — backend unreachable.",
                    }));
                  }
                };
                return (
                  <div key={provider.id} className="space-y-2 rounded border border-[var(--fx-border)] p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-[var(--foreground)]">{provider.label}</p>
                      <button
                        type="button"
                        onClick={() => void runProviderTest()}
                        className="fx-btn-secondary px-2 py-0.5 text-[11px] font-medium"
                      >
                        Test
                      </button>
                    </div>
                    {providerTests[provider.id] ? (
                      <p className="fx-muted text-[11px] leading-4">{providerTests[provider.id]}</p>
                    ) : null}
                    {provider.keyRequired ? (
                      <label className="block text-xs">
                        <span className="fx-muted">
                          API key {storedKey ? "(stored — blank keeps it)" : "(not stored; env may apply)"}
                        </span>
                        <input
                          type="password"
                          autoComplete="off"
                          className="fx-field mt-1 w-full px-2 py-1.5 text-sm"
                          value={draft.api_key}
                          onChange={(e) => updateDraft("api_key", e.target.value)}
                          placeholder={storedKey ? "••••••••" : provider.keyHint}
                        />
                      </label>
                    ) : null}
                    <label className="block text-xs">
                      <span className="fx-muted">Endpoint (blank = default)</span>
                      <input
                        className="fx-field mt-1 w-full px-2 py-1.5 text-sm"
                        value={draft.base_url}
                        onChange={(e) => updateDraft("base_url", e.target.value)}
                        placeholder={provider.baseHint}
                      />
                    </label>
                    <label className="block text-xs">
                      <span className="fx-muted">
                        Default model
                        {providerModels[provider.id]?.length
                          ? ` (${providerModels[provider.id].length} available)`
                          : ""}
                      </span>
                      <input
                        className="fx-field mt-1 w-full px-2 py-1.5 text-sm"
                        value={draft.default_model}
                        onChange={(e) => updateDraft("default_model", e.target.value)}
                        placeholder={provider.modelHint}
                        list={`provider-models-${provider.id}`}
                      />
                      <datalist id={`provider-models-${provider.id}`}>
                        {(providerModels[provider.id] ?? []).map((modelId) => (
                          <option key={modelId} value={modelId} />
                        ))}
                      </datalist>
                    </label>
                    {provider.id === "openai" ? (
                      <label className="block text-xs">
                        <span className="fx-muted">Fallback model</span>
                        <input
                          className="fx-field mt-1 w-full px-2 py-1.5 text-sm"
                          value={openaiFallbackModel}
                          onChange={(e) => setOpenaiFallbackModel(e.target.value)}
                          placeholder="gpt-5.1"
                        />
                      </label>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </article>

          <article hidden={activeTab !== "guardrails"} className="fx-panel p-3">
            <h2 className="text-sm font-semibold">Guardrails and approvals</h2>
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

          <article hidden={activeTab !== "network"} className="fx-panel p-3">
            <h2 className="text-sm font-semibold">Network and retrieval boundaries</h2>
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
                label="Allowed local hostnames"
                description="Localhost-style hostnames permitted when local-network mode is active. Comma-separated."
                value={allowLocalHostnames}
                onChange={setAllowLocalHostnames}
                placeholder="localhost, .local"
              />
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

          <article hidden={activeTab !== "runtime"} className="fx-panel p-3">
            <h2 className="text-sm font-semibold">Runtime and collaboration ceilings</h2>
            <p className="fx-muted text-xs leading-5">Cap how far builders can stretch engines, tools, retrieval, and collaboration at lower scopes.</p>
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
                description="Lets workflow and agent scopes narrow engine choices, but only inside the platform allowlist."
                checked={Boolean(settings?.allow_runtime_engine_override)}
                onChange={(next) => setSettings((current) => (current ? { ...current, allow_runtime_engine_override: next } : current))}
              />
              <div className="rounded border border-[var(--fx-border)] px-3 py-3 text-sm">
                <p className="font-medium text-[var(--foreground)]">Builder-visible default memory scopes</p>
                <p className="mt-1 fx-muted text-xs leading-5">{policy?.platform_defaults.allowed_memory_scopes.join(", ") ?? "run, session, user, tenant, agent, workflow, global"}</p>
              </div>
            </div>

            <div className="mt-3">
              <ListField
                label="Allowed runtime engines"
                description="Comma-separated engine allowlist available to workflows and agents beneath the platform scope."
                value={allowedRuntimeEngines}
                onChange={setAllowedRuntimeEngines}
                placeholder="native, langgraph, langchain"
              />
            </div>
          </article>
        </div>
    </section>
  );
}

/**
 * `/builder/settings` renders the settings overview workspace; the tabbed
 * security configuration (unified AI provider map, guardrails, network, runtime
 * limits) is reached via `?tab=providers|guardrails|network|runtime`.
 */
export default function BuilderSettingsOverviewPage() {
  const searchParams = useSearchParams();
  const requestedTab = searchParams?.get("tab") ?? null;
  const showSecurityConfiguration = Boolean(requestedTab && settingsTabs.some((tab) => tab.id === requestedTab));

  if (showSecurityConfiguration) {
    return <BuilderSecurityConfiguration />;
  }

  return (
    <div className="space-y-4">
      <BuilderSettingsWorkspace view="overview" />
      <article className="fx-panel flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Security configuration</h2>
          <p className="fx-muted text-xs leading-5">
            Unified AI provider keys and default models, plus guardrail, network, and runtime limits in one tabbed editor.
          </p>
        </div>
        <Link href="/builder/settings?tab=providers" className="fx-btn-secondary px-3 py-1.5 text-xs font-medium no-underline">
          Open AI providers &amp; security
        </Link>
      </article>
    </div>
  );
}
