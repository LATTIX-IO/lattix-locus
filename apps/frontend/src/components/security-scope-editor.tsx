"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAgentSecurityPolicy,
  getGuardrailRulesets,
  getWorkflowSecurityPolicy,
} from "@/lib/api";
import { useToast } from "@/components/toast";
import type {
  GuardrailRuleSet,
  PlatformSignalEnforcement,
  SecurityPolicyResponse,
  SecurityScopeConfig,
} from "@/types/frontier";

type Props = {
  entityType: "agent" | "workflow";
  entityId: string;
  entityName: string;
  value: SecurityScopeConfig;
  onChange: (next: SecurityScopeConfig) => void;
  onSave: () => Promise<void>;
};

const classificationOptions = ["public", "internal", "confidential", "restricted"] as const;
const signalOptions: Array<{ value: PlatformSignalEnforcement; label: string }> = [
  { value: "off", label: "Off" },
  { value: "audit", label: "Audit only" },
  { value: "block_high", label: "Block high-risk" },
  { value: "raise_high", label: "Escalate high-risk" },
];

function listToText(value?: string[]) {
  return (value ?? []).join(", ");
}

function textToList(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function parsePositiveNumber(value: string): number | undefined {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }
  return parsed;
}

function ToggleRow({
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
    <label className="flex items-start justify-between gap-3 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3.5 py-3 text-xs shadow-[inset_0_1px_0_rgba(255,255,255,0.34)]">
      <div className="min-w-0">
        <div className="font-medium text-[var(--foreground)] break-words">{label}</div>
        <p className="mt-0.5 fx-muted leading-5 break-words">{description}</p>
      </div>
      <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function TextListField({
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
    <label className="block min-w-0 text-xs">
      <span className="font-medium text-[var(--foreground)] break-words">{label}</span>
      <span className="mt-1 block fx-muted leading-5 break-words">{description}</span>
      <textarea
        className="fx-field mt-2 min-h-20 w-full px-3 py-2 text-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}

export function SecurityScopeEditor({ entityType, entityId, entityName, value, onChange, onSave }: Props) {
  const { addToast } = useToast();
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);
  const [rulesets, setRulesets] = useState<GuardrailRuleSet[]>([]);
  const [saving, setSaving] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [blockedKeywords, setBlockedKeywords] = useState(listToText(value.blocked_keywords));
  const [allowedEgressHosts, setAllowedEgressHosts] = useState(listToText(value.allowed_egress_hosts));
  const [allowedRetrievalSources, setAllowedRetrievalSources] = useState(listToText(value.allowed_retrieval_sources));
  const [allowedMcpServers, setAllowedMcpServers] = useState(listToText(value.allowed_mcp_server_urls));
  const [allowedRuntimeEngines, setAllowedRuntimeEngines] = useState(listToText(value.allowed_runtime_engines));
  const [allowedMemoryScopes, setAllowedMemoryScopes] = useState(listToText(value.allowed_memory_scopes));
  const [maxToolCalls, setMaxToolCalls] = useState(value.max_tool_calls_per_run ? String(value.max_tool_calls_per_run) : "");
  const [maxRetrievalItems, setMaxRetrievalItems] = useState(value.max_retrieval_items ? String(value.max_retrieval_items) : "");
  const [maxCollaborationAgents, setMaxCollaborationAgents] = useState(value.max_collaboration_agents ? String(value.max_collaboration_agents) : "");

  useEffect(() => {
    setBlockedKeywords(listToText(value.blocked_keywords));
    setAllowedEgressHosts(listToText(value.allowed_egress_hosts));
    setAllowedRetrievalSources(listToText(value.allowed_retrieval_sources));
    setAllowedMcpServers(listToText(value.allowed_mcp_server_urls));
    setAllowedRuntimeEngines(listToText(value.allowed_runtime_engines));
    setAllowedMemoryScopes(listToText(value.allowed_memory_scopes));
    setMaxToolCalls(value.max_tool_calls_per_run ? String(value.max_tool_calls_per_run) : "");
    setMaxRetrievalItems(value.max_retrieval_items ? String(value.max_retrieval_items) : "");
    setMaxCollaborationAgents(value.max_collaboration_agents ? String(value.max_collaboration_agents) : "");
  }, [value]);

  const loadPolicy = useCallback(async () => {
    try {
      const nextPolicy = entityType === "agent"
        ? await getAgentSecurityPolicy(entityId)
        : await getWorkflowSecurityPolicy(entityId);
      setPolicy(nextPolicy);
    } catch {
      setPolicy(null);
    }
  }, [entityId, entityType]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const [policyResult, rulesetResult] = await Promise.all([
        entityType === "agent" ? getAgentSecurityPolicy(entityId) : getWorkflowSecurityPolicy(entityId),
        getGuardrailRulesets(),
      ]);
      if (cancelled) {
        return;
      }
      setPolicy(policyResult);
      setRulesets(rulesetResult.filter((item) => item.status === "published"));
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [entityId, entityType]);

  const effectiveHighlights = useMemo(() => {
    if (!policy) {
      return [] as string[];
    }
    return [
      `Classification resolves to ${policy.effective.classification}`,
      `Effective engines: ${policy.effective.allowed_runtime_engines.join(", ") || "none"}`,
      `Effective tool-call cap: ${policy.effective.max_tool_calls_per_run}`,
      `Effective retrieval cap: ${policy.effective.max_retrieval_items}`,
      `Signals: ${policy.effective.enable_platform_signals ? policy.effective.platform_signal_enforcement : "off"}`,
    ];
  }, [policy]);

  function patchConfig(patch: Partial<SecurityScopeConfig>) {
    onChange({ ...value, ...patch });
  }

  async function handleSave() {
    setSaving(true);
    try {
      await onSave();
      await loadPolicy();
      addToast("success", `${entityType === "agent" ? "Agent" : "Workflow"} security policy saved.`);
    } catch {
      addToast("error", `Could not save ${entityType} security policy.`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fx-panel rounded-[1.45rem] p-4 text-[var(--foreground)] shadow-[0_22px_52px_rgba(15,23,42,0.08)]">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] fx-muted">Scoped security</p>
          <h3 className="mt-2 text-[1.02rem] font-semibold tracking-[-0.02em] break-words">{entityName}</h3>
          <p className="mt-1 text-xs fx-muted leading-5 break-words">
            Tighten policy for this {entityType} without widening the platform envelope.
          </p>
        </div>
        <button className="fx-btn-secondary px-2 py-1 text-[10px]" onClick={() => setCollapsed((current) => !current)}>
          {collapsed ? "Expand" : "Collapse"}
        </button>
      </div>

      {collapsed ? (
        <p className="mt-3 text-[10px] fx-muted">Panel collapsed to leave more room for the canvas.</p>
      ) : (
        <div className="mt-3 max-h-[42vh] space-y-3 overflow-auto pr-1">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block min-w-0 text-xs text-[var(--foreground)]">
              <span className="font-medium text-[var(--foreground)]">Classification</span>
              <select
                className="fx-field mt-2 w-full px-3 py-2 text-sm"
                value={value.classification ?? "internal"}
                onChange={(event) => patchConfig({ classification: event.target.value as SecurityScopeConfig["classification"] })}
              >
                {classificationOptions.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </label>
            <label className="block min-w-0 text-xs text-[var(--foreground)]">
              <span className="font-medium text-[var(--foreground)]">Guardrail ruleset</span>
              <select
                className="fx-field mt-2 w-full px-3 py-2 text-sm"
                value={value.guardrail_ruleset_id ?? ""}
                onChange={(event) => patchConfig({ guardrail_ruleset_id: event.target.value || null })}
              >
                <option value="">Platform default</option>
                {rulesets.map((item) => (
                  <option key={item.id} value={item.id}>{item.name} · {item.id}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <TextListField
              label="Blocked keywords"
              description="Additive keywords blocked for this scope."
              value={blockedKeywords}
              onChange={(next) => {
                setBlockedKeywords(next);
                patchConfig({ blocked_keywords: textToList(next) });
              }}
              placeholder="credentials, dump, exfiltration"
            />
            <TextListField
              label="Allowed egress hosts"
              description="This scope may only keep destinations that are also allowed by platform policy."
              value={allowedEgressHosts}
              onChange={(next) => {
                setAllowedEgressHosts(next);
                patchConfig({ allowed_egress_hosts: textToList(next) });
              }}
              placeholder="localhost, api.openai.com"
            />
            <TextListField
              label="Allowed retrieval sources"
              description="Trim retrieval to the sources this scope truly needs."
              value={allowedRetrievalSources}
              onChange={(next) => {
                setAllowedRetrievalSources(next);
                patchConfig({ allowed_retrieval_sources: textToList(next) });
              }}
              placeholder="kb://default, file://docs"
            />
            <TextListField
              label="Allowed MCP servers"
              description="Subset of platform-approved MCP endpoints."
              value={allowedMcpServers}
              onChange={(next) => {
                setAllowedMcpServers(next);
                patchConfig({ allowed_mcp_server_urls: textToList(next) });
              }}
              placeholder="http://localhost:8787"
            />
            <TextListField
              label="Allowed runtime engines"
              description="Optional narrower engine allowlist for this scope."
              value={allowedRuntimeEngines}
              onChange={(next) => {
                setAllowedRuntimeEngines(next);
                patchConfig({ allowed_runtime_engines: textToList(next) });
              }}
              placeholder="native, langgraph"
            />
            <TextListField
              label="Allowed memory scopes"
              description="Limit memory exposure for this scope."
              value={allowedMemoryScopes}
              onChange={(next) => {
                setAllowedMemoryScopes(next);
                patchConfig({ allowed_memory_scopes: textToList(next) });
              }}
              placeholder="run, session, workflow"
            />
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            <label className="block text-xs text-[var(--foreground)]">
              <span className="font-medium text-[var(--foreground)]">Max tool calls</span>
              <input
                className="fx-field mt-2 w-full px-3 py-2 text-sm"
                value={maxToolCalls}
                onChange={(event) => {
                  const next = event.target.value;
                  setMaxToolCalls(next);
                  patchConfig({ max_tool_calls_per_run: parsePositiveNumber(next) });
                }}
                placeholder="leave blank for platform"
              />
            </label>
            <label className="block text-xs text-[var(--foreground)]">
              <span className="font-medium text-[var(--foreground)]">Max retrieval items</span>
              <input
                className="fx-field mt-2 w-full px-3 py-2 text-sm"
                value={maxRetrievalItems}
                onChange={(event) => {
                  const next = event.target.value;
                  setMaxRetrievalItems(next);
                  patchConfig({ max_retrieval_items: parsePositiveNumber(next) });
                }}
                placeholder="leave blank for platform"
              />
            </label>
            <label className="block text-xs text-[var(--foreground)]">
              <span className="font-medium text-[var(--foreground)]">Max collaborators</span>
              <input
                className="fx-field mt-2 w-full px-3 py-2 text-sm"
                value={maxCollaborationAgents}
                onChange={(event) => {
                  const next = event.target.value;
                  setMaxCollaborationAgents(next);
                  patchConfig({ max_collaboration_agents: parsePositiveNumber(next) });
                }}
                placeholder="leave blank for platform"
              />
            </label>
          </div>

          <div className="grid gap-2 md:grid-cols-2">
            <ToggleRow
              label="Require human approval"
              description="Escalate this scope to human review before completion."
              checked={Boolean(value.require_human_approval)}
              onChange={(next) => patchConfig({ require_human_approval: next })}
            />
            <ToggleRow
              label="Require high-risk tool approval"
              description="Keep risky tools behind approval even if the rest of the scope is automated."
              checked={Boolean(value.require_human_approval_for_high_risk_tools)}
              onChange={(next) => patchConfig({ require_human_approval_for_high_risk_tools: next })}
            />
            <ToggleRow
              label="Allow runtime override"
              description="This scope may request a smaller engine set when platform policy allows it."
              checked={Boolean(value.allow_runtime_override)}
              onChange={(next) => patchConfig({ allow_runtime_override: next })}
            />
            <ToggleRow
              label="Enable platform signals"
              description="Keep platform signal detectors active for this scope."
              checked={Boolean(value.enable_platform_signals)}
              onChange={(next) => patchConfig({ enable_platform_signals: next })}
            />
          </div>

          <label className="block min-w-0 text-xs text-[var(--foreground)]">
            <span className="font-medium text-[var(--foreground)]">Platform signal enforcement</span>
            <select
              className="fx-field mt-2 w-full px-3 py-2 text-sm"
              value={value.platform_signal_enforcement ?? "block_high"}
              onChange={(event) => patchConfig({ platform_signal_enforcement: event.target.value as PlatformSignalEnforcement })}
            >
              {signalOptions.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>

          <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3.5 py-3 text-xs shadow-[inset_0_1px_0_rgba(255,255,255,0.34)]">
            <p className="font-medium text-[var(--foreground)]">Effective policy snapshot</p>
            <ul className="mt-2 space-y-1.5 text-[var(--foreground)]">
              {effectiveHighlights.map((item) => (
                <li key={item} className="break-words">{item}</li>
              ))}
            </ul>
            {policy?.backend_enforced_controls?.length ? (
              <p className="mt-2 break-words fx-muted leading-5">
                Backend-enforced rails: {policy.backend_enforced_controls.join(", ")}
              </p>
            ) : null}
          </div>

          <div className="flex items-center justify-between gap-3 border-t border-[var(--fx-border)] pt-3">
            <p className="min-w-0 break-words text-[10px] fx-muted">
              {entityType === "agent" ? "Agent" : "Workflow"} overrides can only tighten the platform baseline.
            </p>
            <button className="fx-btn-primary px-3 py-2 text-xs" onClick={handleSave} disabled={saving}>
              {saving ? "Saving..." : `Save ${entityType} policy`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
