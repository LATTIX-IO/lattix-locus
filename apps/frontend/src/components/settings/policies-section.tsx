"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  ListField,
  LoadState,
  SaveBar,
  SectionHeader,
  SelectField,
  SettingsGroup,
  TextField,
  ToggleRow,
  parseList,
  positiveNumber,
  toListText,
  useDraft,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import { getGuardrailRulesets, getUserSkills, saveUserSkills } from "@/lib/api";
import { useIsDesktopShell } from "@/lib/desktop-shell";
import type { GuardrailRuleSet, PlatformSettings, PlatformSignalEnforcement } from "@/types/locus";

const SIGNAL_OPTIONS: Array<{ value: PlatformSignalEnforcement; label: string }> = [
  { value: "off", label: "Off" },
  { value: "audit", label: "Audit only" },
  { value: "block_high", label: "Block high-risk" },
  { value: "raise_high", label: "Escalate high-risk" },
];

type PolicyDraft = {
  default_guardrail_ruleset_id: string;
  foss_guardrail_signal_enforcement: PlatformSignalEnforcement;
  enable_foss_guardrail_signals: boolean;
  global_blocked_keywords: string;
  high_risk_tool_patterns: string;
  require_human_approval: boolean;
  require_human_approval_for_high_risk_tools: boolean;
  mask_secrets_in_events: boolean;
  enforce_egress_allowlist: boolean;
  enforce_local_network_only: boolean;
  allow_local_network_hostnames: string;
  allowed_egress_hosts: string;
  retrieval_require_local_source_url: boolean;
  allowed_retrieval_sources: string;
  max_tool_calls_per_run: string;
  max_retrieval_items: string;
  collaboration_max_agents: string;
  emergency_read_only_mode: boolean;
  block_new_runs: boolean;
  block_graph_runs: boolean;
  block_tool_calls: boolean;
  block_retrieval_calls: boolean;
  require_authenticated_requests: boolean;
  require_a2a_runtime_headers: boolean;
  a2a_require_signed_messages: boolean;
  a2a_replay_protection: boolean;
  tenant_scoped_skills: string;
};

function toDraft(settings: PlatformSettings): PolicyDraft {
  return {
    default_guardrail_ruleset_id: settings.default_guardrail_ruleset_id ?? "",
    foss_guardrail_signal_enforcement: settings.foss_guardrail_signal_enforcement ?? "block_high",
    enable_foss_guardrail_signals: settings.enable_foss_guardrail_signals ?? true,
    global_blocked_keywords: toListText(settings.global_blocked_keywords),
    high_risk_tool_patterns: toListText(settings.high_risk_tool_patterns),
    require_human_approval: Boolean(settings.require_human_approval),
    require_human_approval_for_high_risk_tools: settings.require_human_approval_for_high_risk_tools ?? true,
    mask_secrets_in_events: Boolean(settings.mask_secrets_in_events),
    enforce_egress_allowlist: Boolean(settings.enforce_egress_allowlist),
    enforce_local_network_only: Boolean(settings.enforce_local_network_only),
    allow_local_network_hostnames: toListText(settings.allow_local_network_hostnames),
    allowed_egress_hosts: toListText(settings.allowed_egress_hosts),
    retrieval_require_local_source_url: Boolean(settings.retrieval_require_local_source_url),
    allowed_retrieval_sources: toListText(settings.allowed_retrieval_sources),
    max_tool_calls_per_run: String(settings.max_tool_calls_per_run ?? 8),
    max_retrieval_items: String(settings.max_retrieval_items ?? 8),
    collaboration_max_agents: String(settings.collaboration_max_agents ?? 8),
    emergency_read_only_mode: Boolean(settings.emergency_read_only_mode),
    block_new_runs: Boolean(settings.block_new_runs),
    block_graph_runs: Boolean(settings.block_graph_runs),
    block_tool_calls: Boolean(settings.block_tool_calls),
    block_retrieval_calls: Boolean(settings.block_retrieval_calls),
    require_authenticated_requests: Boolean(settings.require_authenticated_requests),
    require_a2a_runtime_headers: Boolean(settings.require_a2a_runtime_headers),
    a2a_require_signed_messages: settings.a2a_require_signed_messages ?? true,
    a2a_replay_protection: settings.a2a_replay_protection ?? true,
    tenant_scoped_skills: toListText(settings.tenant_scoped_skills),
  };
}

function toPatch(draft: PolicyDraft, settings: PlatformSettings, includeHosted: boolean): Partial<PlatformSettings> {
  const patch: Partial<PlatformSettings> = {
    default_guardrail_ruleset_id: draft.default_guardrail_ruleset_id.trim() || null,
    foss_guardrail_signal_enforcement: draft.foss_guardrail_signal_enforcement,
    enable_foss_guardrail_signals: draft.enable_foss_guardrail_signals,
    global_blocked_keywords: parseList(draft.global_blocked_keywords),
    high_risk_tool_patterns: parseList(draft.high_risk_tool_patterns),
    require_human_approval: draft.require_human_approval,
    require_human_approval_for_high_risk_tools: draft.require_human_approval_for_high_risk_tools,
    mask_secrets_in_events: draft.mask_secrets_in_events,
    enforce_egress_allowlist: draft.enforce_egress_allowlist,
    enforce_local_network_only: draft.enforce_local_network_only,
    // A hostname list, never a flag (a Boolean made pydantic reject the save).
    allow_local_network_hostnames: parseList(draft.allow_local_network_hostnames),
    allowed_egress_hosts: parseList(draft.allowed_egress_hosts),
    retrieval_require_local_source_url: draft.retrieval_require_local_source_url,
    allowed_retrieval_sources: parseList(draft.allowed_retrieval_sources),
    max_tool_calls_per_run: positiveNumber(draft.max_tool_calls_per_run, settings.max_tool_calls_per_run ?? 8),
    max_retrieval_items: positiveNumber(draft.max_retrieval_items, settings.max_retrieval_items ?? 8),
    collaboration_max_agents: positiveNumber(draft.collaboration_max_agents, settings.collaboration_max_agents ?? 8),
    emergency_read_only_mode: draft.emergency_read_only_mode,
    block_new_runs: draft.block_new_runs,
    block_graph_runs: draft.block_graph_runs,
    block_tool_calls: draft.block_tool_calls,
    block_retrieval_calls: draft.block_retrieval_calls,
    require_a2a_runtime_headers: draft.require_a2a_runtime_headers,
    a2a_require_signed_messages: draft.a2a_require_signed_messages,
    a2a_replay_protection: draft.a2a_replay_protection,
  };
  if (includeHosted) {
    patch.require_authenticated_requests = draft.require_authenticated_requests;
    patch.tenant_scoped_skills = parseList(draft.tenant_scoped_skills);
  }
  return patch;
}

/** Personal /skills: a widening list (skills.user.write, confirmed in the shell). */
function PersonalSkillsGroup() {
  const [loaded, setLoaded] = useState<{ skills: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getUserSkills()
      .then((response) => {
        if (cancelled) return;
        setLoaded({ skills: toListText(response.skills) });
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load your skills.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const { draft, dirty, saving, message, update, commit, reset } = useDraft(loaded);

  return (
    <SettingsGroup title="Personal skills" description="Slash skills loaded into your agents.">
      {!draft ? (
        <LoadState loading={!error} error={error} onRetry={() => setToken((value) => value + 1)} />
      ) : (
        <>
          <ListField
            id="personal-skills"
            label="Skills"
            value={draft.skills}
            onChange={(value) => update("skills", value)}
            placeholder={"/incident-triage\n/research-brief"}
          />
          <SaveBar
            dirty={dirty}
            saving={saving}
            message={message}
            onReset={reset}
            onSave={() =>
              void commit(async (next) => {
                await saveUserSkills({ skills: parseList(next.skills) });
              })
            }
          />
        </>
      )}
    </SettingsGroup>
  );
}

export function PoliciesSection() {
  const isDesktop = useIsDesktopShell();
  const platform = usePlatformResource();
  const [rulesets, setRulesets] = useState<GuardrailRuleSet[] | null>(null);
  const [rulesetError, setRulesetError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getGuardrailRulesets()
      .then((items) => {
        if (!cancelled) setRulesets(items);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setRulesetError(reason instanceof Error ? reason.message : "Could not load guardrail rulesets.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initial = useMemo(() => (platform.settings ? toDraft(platform.settings) : null), [platform.settings]);
  const { draft, dirty, saving, message, update, commit, reset } = useDraft<PolicyDraft>(initial);

  const header = (
    <SectionHeader
      title="Policies & autonomy"
      description="What the agents may do without asking, where they may reach, and how far a run may go."
    />
  );

  if (!draft || !platform.settings) {
    return (
      <div className="flex flex-col gap-4">
        {header}
        <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
      </div>
    );
  }

  const settings = platform.settings;
  const published = (rulesets ?? []).filter((ruleset) => ruleset.status === "published");
  const rulesetOptions = [
    { value: "__none__", label: "None" },
    ...published.map((ruleset) => ({ value: ruleset.id, label: ruleset.name })),
    ...(draft.default_guardrail_ruleset_id && !published.some((ruleset) => ruleset.id === draft.default_guardrail_ruleset_id)
      ? [{ value: draft.default_guardrail_ruleset_id, label: `${draft.default_guardrail_ruleset_id} (not published)` }]
      : []),
  ];

  const toggle = (key: keyof PolicyDraft, label: string, description?: string) => (
    <ToggleRow
      id={`policy-${String(key)}`}
      label={label}
      description={description}
      checked={Boolean(draft[key])}
      onCheckedChange={(next) => update(key, next as never)}
    />
  );

  const saveBar = (
    <SaveBar
      dirty={dirty}
      saving={saving}
      message={message}
      onReset={reset}
      onSave={() => void commit(async (next) => platform.save(toPatch(next, settings, !isDesktop)))}
    />
  );

  return (
    <div className="flex flex-col gap-4">
      {header}

      <SettingsGroup title="Approvals" description="When a person has to say yes before the agent continues.">
        <div className="grid gap-2 lg:grid-cols-2">
          {toggle("require_human_approval", "Approve every run", "Every run waits for your review before it completes.")}
          {toggle("require_human_approval_for_high_risk_tools", "Approve high-risk tools", "Risky tools ask first, even when the rest of the run proceeds.")}
        </div>
        <ListField
          id="policy-high-risk-tools"
          label="High-risk tool patterns"
          value={draft.high_risk_tool_patterns}
          onChange={(value) => update("high_risk_tool_patterns", value)}
          placeholder={"shell.exec\nfile.delete"}
        />
      </SettingsGroup>

      <SettingsGroup
        title="Guardrails"
        description="The baseline every agent and workflow may only tighten."
        actions={
          <Button asChild variant="secondary" size="sm">
            <Link href="/library/guardrails">Edit rulesets</Link>
          </Button>
        }
      >
        <div className="grid gap-3 lg:grid-cols-2">
          <SelectField
            id="policy-guardrail-ruleset"
            label="Default ruleset"
            description={rulesetError ? `Rulesets unavailable: ${rulesetError}` : "A published ruleset applied to every run."}
            value={draft.default_guardrail_ruleset_id || "__none__"}
            onValueChange={(value) => update("default_guardrail_ruleset_id", value === "__none__" ? "" : value)}
            options={rulesetOptions}
          />
          <SelectField
            id="policy-signal-enforcement"
            label="Signal enforcement"
            description="How prompt-injection and exfiltration signals affect a run."
            value={draft.foss_guardrail_signal_enforcement}
            onValueChange={(value) => update("foss_guardrail_signal_enforcement", value as PlatformSignalEnforcement)}
            options={SIGNAL_OPTIONS}
          />
        </div>
        {toggle("enable_foss_guardrail_signals", "Run guardrail signal checks")}
        <ListField
          id="policy-blocked-keywords"
          label="Blocked keywords"
          description="Prompts containing these are blocked before a run starts."
          value={draft.global_blocked_keywords}
          onChange={(value) => update("global_blocked_keywords", value)}
        />
      </SettingsGroup>

      <SettingsGroup title="Network & egress" description="Where agents and retrieval may connect.">
        <div className="grid gap-2 lg:grid-cols-2">
          {toggle("enforce_local_network_only", "Local network only", "Runtime calls stay on this machine or approved private hosts.")}
          {toggle("enforce_egress_allowlist", "Enforce the egress allowlist", "Only the hosts below may be contacted.")}
          {toggle("retrieval_require_local_source_url", "Local retrieval sources only")}
          {toggle("mask_secrets_in_events", "Mask secrets in run events")}
        </div>
        <div className="grid gap-3 lg:grid-cols-3">
          <ListField
            id="policy-egress-hosts"
            label="Allowed egress hosts"
            value={draft.allowed_egress_hosts}
            onChange={(value) => update("allowed_egress_hosts", value)}
            placeholder={"api.openai.com"}
          />
          <ListField
            id="policy-local-hostnames"
            label="Allowed local hostnames"
            value={draft.allow_local_network_hostnames}
            onChange={(value) => update("allow_local_network_hostnames", value)}
            placeholder={"localhost\n.local"}
          />
          <ListField
            id="policy-retrieval-sources"
            label="Allowed retrieval sources"
            value={draft.allowed_retrieval_sources}
            onChange={(value) => update("allowed_retrieval_sources", value)}
            placeholder={"kb://default"}
          />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Runtime limits" description="How far a single run may go.">
        <div className="grid gap-3 md:grid-cols-3">
          <TextField id="policy-max-tool-calls" label="Tool calls per run" inputMode="numeric" value={draft.max_tool_calls_per_run} onChange={(value) => update("max_tool_calls_per_run", value)} />
          <TextField id="policy-max-retrieval" label="Retrieval items" inputMode="numeric" value={draft.max_retrieval_items} onChange={(value) => update("max_retrieval_items", value)} />
          <TextField id="policy-max-agents" label="Collaborating agents" inputMode="numeric" value={draft.collaboration_max_agents} onChange={(value) => update("collaboration_max_agents", value)} />
        </div>
      </SettingsGroup>

      <SettingsGroup title="Emergency stops" description="Switches for an incident. Each one only narrows.">
        <div className="grid gap-2 lg:grid-cols-2">
          {toggle("emergency_read_only_mode", "Read-only mode", "Block every write action.")}
          {toggle("block_new_runs", "Block new runs")}
          {toggle("block_graph_runs", "Block graph runs")}
          {toggle("block_tool_calls", "Block tool calls")}
          {toggle("block_retrieval_calls", "Block retrieval")}
        </div>
      </SettingsGroup>

      <details className="rounded-[14px] border border-border p-4">
        <summary className="cursor-pointer text-sm font-semibold">Advanced</summary>
        <div className="mt-3 grid gap-2 lg:grid-cols-2">
          {toggle("require_a2a_runtime_headers", "Require agent-to-agent identity headers")}
          {toggle("a2a_require_signed_messages", "Require signed agent-to-agent messages")}
          {toggle("a2a_replay_protection", "Agent-to-agent replay protection")}
          {!isDesktop ? toggle("require_authenticated_requests", "Require authenticated requests", "Hosted installs: refuse anonymous writes.") : null}
        </div>
        {!isDesktop ? (
          <div className="mt-3">
            <ListField
              id="policy-tenant-skills"
              label="Tenant skills"
              description="Skills shared across this hosted tenant."
              value={draft.tenant_scoped_skills}
              onChange={(value) => update("tenant_scoped_skills", value)}
            />
          </div>
        ) : null}
      </details>

      <div className="sticky bottom-0 z-10 -mx-1 rounded-[12px] border border-border bg-card/95 px-3 py-2 backdrop-blur">{saveBar}</div>

      <PersonalSkillsGroup />
    </div>
  );
}

