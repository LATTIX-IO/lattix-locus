"use client";

import { useEffect, useState } from "react";
import {
  CLASSIFICATION_PRESETS,
  type ClassificationPreset,
  useClassificationBanner,
} from "@/components/classification-banner";
import { ComposerDefaultsSettings } from "@/components/composer-defaults-settings";
import { ControlStatusList } from "@/components/control-status";
import { SettingsRailCard, SettingsShell } from "@/components/settings-shell";
import { getAtfAlignmentReport, getPlatformSettings, getUserSkills, savePlatformSettings, saveUserSkills } from "@/lib/api";
import type { AtfAlignmentReport } from "@/types/locus";

function normalizeHexColor(value: string, fallback: string): string {
  const trimmed = value.trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed) ? trimmed : fallback;
}

function parseSkillList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

const sectionNav = [
  { id: "section-brand", label: "Brand & Identity", description: "Identity and support metadata exposed across the console." },
  { id: "section-classification", label: "Classification Banner", description: "Device-local banner preset used when platform settings are unavailable." },
  { id: "section-security", label: "Security & Governance", description: "Default controls for approvals, secrets, and emergency posture." },
  { id: "section-runtime", label: "Runtime Policy", description: "Shared engine strategy and hybrid routing defaults." },
  { id: "section-user-defaults", label: "User Defaults", description: "Kickoff and review preferences applied to new work." },
  { id: "section-composer-defaults", label: "Composer Defaults", description: "Default model, folder, and reasoning preferences for new work." },
  { id: "section-user-skills", label: "Personal /skills", description: "User-scoped slash skills available only to your operator profile." },
] as const;

const CLASSIFICATION_PRESET_META: Array<{
  key: ClassificationPreset;
  label: string;
  description: string;
}> = [
  { key: "neutral", label: "Neutral", description: "Informational banner with no classification signal." },
  { key: "success", label: "All Clear", description: "Green check; zero-trust posture nominal." },
  { key: "confidential", label: "Confidential", description: "Amber; handling guidance required." },
  { key: "restricted", label: "Restricted", description: "Red; elevated access control." },
  { key: "secret", label: "Secret", description: "Blue; secret-level controls in effect." },
  { key: "topsecret", label: "Top Secret", description: "Black; highest-level classification." },
];

export default function SettingsPage() {
  const runtimeEngineOptions = ["native", "langgraph", "langchain", "semantic-kernel", "autogen"] as const;
  const recommendedHybridRouting = {
    default: "native",
    orchestration: "langgraph",
    retrieval: "langchain",
    tooling: "semantic-kernel",
    collaboration: "autogen",
  } as const;
  const [classificationBanner, setClassificationBanner] = useClassificationBanner();
  const [loaded, setLoaded] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveErrorMessage, setSaveErrorMessage] = useState<string | null>(null);
  const [atfReport, setAtfReport] = useState<AtfAlignmentReport | null>(null);
  const [atfLoading, setAtfLoading] = useState(true);
  const [orgName, setOrgName] = useState("Lattix Locus");
  const [orgSlug, setOrgSlug] = useState("lattix-locus");
  const [supportEmail, setSupportEmail] = useState("support@lattix.io");
  const [website, setWebsite] = useState("https://lattix.io");
  const [classificationBannerEnabled, setClassificationBannerEnabled] = useState(true);
  const [classificationBannerText, setClassificationBannerText] = useState("Internal • Operational Console");
  const [classificationBannerBackgroundColor, setClassificationBannerBackgroundColor] = useState("#2e2a28");
  const [classificationBannerTextColor, setClassificationBannerTextColor] = useState("#e7dcc0");
  const [defaultKickoffWorkflow, setDefaultKickoffWorkflow] = useState("Auto-select from intent");
  const [preferredReviewDepth, setPreferredReviewDepth] = useState("Standard");
  const [idleTimeout, setIdleTimeout] = useState("30 minutes");
  const [localOnlyMode, setLocalOnlyMode] = useState(true);
  const [maskSecrets, setMaskSecrets] = useState(true);
  const [requireHumanApproval, setRequireHumanApproval] = useState(false);
  const [emergencyReadOnlyMode, setEmergencyReadOnlyMode] = useState(false);
  const [blockNewRuns, setBlockNewRuns] = useState(false);
  const [blockGraphRuns, setBlockGraphRuns] = useState(false);
  const [blockToolCalls, setBlockToolCalls] = useState(false);
  const [blockRetrievalCalls, setBlockRetrievalCalls] = useState(false);
  const [requireAuthenticatedRequests, setRequireAuthenticatedRequests] = useState(false);
  const [requireA2aRuntimeHeaders, setRequireA2aRuntimeHeaders] = useState(false);
  const [defaultGuardrailRulesetId, setDefaultGuardrailRulesetId] = useState("");
  const [globalBlockedKeywords, setGlobalBlockedKeywords] = useState("");
  const [collaborationMaxAgents, setCollaborationMaxAgents] = useState("8");
  const [userScopedSkills, setUserScopedSkills] = useState("");
  const [userSkillsSaveState, setUserSkillsSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [userSkillsSaveError, setUserSkillsSaveError] = useState<string | null>(null);
  const [defaultRuntimeStrategy, setDefaultRuntimeStrategy] = useState<"single" | "hybrid">("single");
  const [defaultHybridRouting, setDefaultHybridRouting] = useState<{
    default: string;
    orchestration: string;
    retrieval: string;
    tooling: string;
    collaboration: string;
  }>({
    default: "native",
    orchestration: "native",
    retrieval: "native",
    tooling: "native",
    collaboration: "native",
  });

  useEffect(() => {
    let cancelled = false;
    async function loadSettings() {
      const [settings, report, userSkills] = await Promise.all([getPlatformSettings(), getAtfAlignmentReport(), getUserSkills()]);
      if (cancelled) {
        return;
      }
      if (settings.org_name) setOrgName(settings.org_name);
      if (settings.org_slug) setOrgSlug(settings.org_slug);
      if (settings.support_email) setSupportEmail(settings.support_email);
      if (settings.website) setWebsite(settings.website);
      setClassificationBannerEnabled(settings.console_classification_banner_enabled ?? true);
      setClassificationBannerText(settings.console_classification_banner_text ?? "Internal • Operational Console");
      setClassificationBannerBackgroundColor(normalizeHexColor(settings.console_classification_banner_background_color ?? "#2e2a28", "#2e2a28"));
      setClassificationBannerTextColor(normalizeHexColor(settings.console_classification_banner_text_color ?? "#e7dcc0", "#e7dcc0"));
      if (settings.default_kickoff_workflow) setDefaultKickoffWorkflow(settings.default_kickoff_workflow);
      if (settings.preferred_review_depth) setPreferredReviewDepth(settings.preferred_review_depth);
      if (settings.idle_timeout) setIdleTimeout(settings.idle_timeout);
      setLocalOnlyMode(settings.local_only_mode);
      setMaskSecrets(settings.mask_secrets_in_events);
      setRequireHumanApproval(settings.require_human_approval);
      setEmergencyReadOnlyMode(Boolean(settings.emergency_read_only_mode));
      setBlockNewRuns(Boolean(settings.block_new_runs));
      setBlockGraphRuns(Boolean(settings.block_graph_runs));
      setBlockToolCalls(Boolean(settings.block_tool_calls));
      setBlockRetrievalCalls(Boolean(settings.block_retrieval_calls));
      setRequireAuthenticatedRequests(Boolean(settings.require_authenticated_requests));
      setRequireA2aRuntimeHeaders(Boolean(settings.require_a2a_runtime_headers));
      setDefaultGuardrailRulesetId(settings.default_guardrail_ruleset_id ?? "");
      setGlobalBlockedKeywords(settings.global_blocked_keywords.join(", "));
      setCollaborationMaxAgents(String(settings.collaboration_max_agents));
      setUserScopedSkills((userSkills.skills ?? []).join("\n"));
      setDefaultRuntimeStrategy((settings.default_runtime_strategy ?? "single") as "single" | "hybrid");
      setDefaultHybridRouting({
        default: settings.default_hybrid_runtime_routing?.default ?? settings.default_runtime_engine ?? "native",
        orchestration: settings.default_hybrid_runtime_routing?.orchestration ?? settings.default_hybrid_runtime_routing?.default ?? settings.default_runtime_engine ?? "native",
        retrieval: settings.default_hybrid_runtime_routing?.retrieval ?? settings.default_hybrid_runtime_routing?.default ?? settings.default_runtime_engine ?? "native",
        tooling: settings.default_hybrid_runtime_routing?.tooling ?? settings.default_hybrid_runtime_routing?.default ?? settings.default_runtime_engine ?? "native",
        collaboration: settings.default_hybrid_runtime_routing?.collaboration ?? settings.default_hybrid_runtime_routing?.default ?? settings.default_runtime_engine ?? "native",
      });
      setAtfReport(report);
      setAtfLoading(false);
      setLoaded(true);
    }

    void loadSettings();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSaveUserSkills() {
    setUserSkillsSaveState("saving");
    setUserSkillsSaveError(null);
    try {
      const response = await saveUserSkills({
        skills: parseSkillList(userScopedSkills),
      });
      setUserScopedSkills((response.skills ?? []).join("\n"));
      setUserSkillsSaveState("saved");
      setTimeout(() => setUserSkillsSaveState("idle"), 2500);
    } catch (error) {
      setUserSkillsSaveState("error");
      setUserSkillsSaveError(error instanceof Error ? error.message : "Could not save personal skills.");
      setTimeout(() => {
        setUserSkillsSaveState("idle");
        setUserSkillsSaveError(null);
      }, 4000);
    }
  }

  async function handleSavePlatformSettings() {
    setSaveState("saving");
    setSaveErrorMessage(null);
    try {
      const maxAgents = Number(collaborationMaxAgents);
      await savePlatformSettings({
        org_name: orgName,
        org_slug: orgSlug,
        support_email: supportEmail,
        website: website,
        console_classification_banner_enabled: classificationBannerEnabled,
        console_classification_banner_text: classificationBannerText.trim() || "Internal • Operational Console",
        console_classification_banner_background_color: normalizeHexColor(classificationBannerBackgroundColor, "#2e2a28"),
        console_classification_banner_text_color: normalizeHexColor(classificationBannerTextColor, "#e7dcc0"),
        default_kickoff_workflow: defaultKickoffWorkflow,
        preferred_review_depth: preferredReviewDepth,
        idle_timeout: idleTimeout,
        local_only_mode: localOnlyMode,
        mask_secrets_in_events: maskSecrets,
        require_human_approval: requireHumanApproval,
        emergency_read_only_mode: emergencyReadOnlyMode,
        block_new_runs: blockNewRuns,
        block_graph_runs: blockGraphRuns,
        block_tool_calls: blockToolCalls,
        block_retrieval_calls: blockRetrievalCalls,
        require_authenticated_requests: requireAuthenticatedRequests,
        require_a2a_runtime_headers: requireA2aRuntimeHeaders,
        default_guardrail_ruleset_id: defaultGuardrailRulesetId.trim() || null,
        global_blocked_keywords: globalBlockedKeywords
          .split(",")
          .map((item) => item.trim())
          .filter((item) => item.length > 0),
        collaboration_max_agents: Number.isFinite(maxAgents) && maxAgents > 0 ? maxAgents : 8,
        default_runtime_strategy: defaultRuntimeStrategy,
        default_hybrid_runtime_routing: {
          default: defaultHybridRouting.default,
          orchestration: defaultHybridRouting.orchestration,
          retrieval: defaultHybridRouting.retrieval,
          tooling: defaultHybridRouting.tooling,
          collaboration: defaultHybridRouting.collaboration,
        },
      });
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2500);
    } catch (error) {
      setSaveState("error");
      setSaveErrorMessage(error instanceof Error ? error.message : "Could not save platform settings.");
      setTimeout(() => {
        setSaveState("idle");
        setSaveErrorMessage(null);
      }, 4000);
    }
  }

  function handleResetToRecommendedHybridProfile() {
    setDefaultRuntimeStrategy("hybrid");
    setDefaultHybridRouting({
      default: recommendedHybridRouting.default,
      orchestration: recommendedHybridRouting.orchestration,
      retrieval: recommendedHybridRouting.retrieval,
      tooling: recommendedHybridRouting.tooling,
      collaboration: recommendedHybridRouting.collaboration,
    });
  }

  const atfPillars = atfReport?.pillars ?? null;
  const topGaps = atfPillars
    ? Object.entries(atfPillars)
        .flatMap(([pillar, details]) => (details.gaps ?? []).map((gap) => `${pillar.replace(/_/g, " ")}: ${gap}`))
        .slice(0, 3)
    : [];

  const maturityLabel = atfReport?.maturity_estimate ? atfReport.maturity_estimate.toUpperCase() : "UNKNOWN";
  const postureSummary = [
    localOnlyMode ? "Local-only network posture enabled" : "External network posture permitted",
    requireHumanApproval ? "Every workflow requires review" : "Approval gate applies selectively",
    `${defaultRuntimeStrategy === "hybrid" ? "Hybrid" : "Single-engine"} runtime strategy is the default`,
  ];

  return (
    <SettingsShell
      eyebrow="Console / Settings"
      title="Organization Settings"
      description="Manage platform identity, runtime defaults, and operational safety baselines with the same lighter settings frame used across the console."
      sections={sectionNav}
      navLabel="Organization settings"
      action={
        <div className="flex items-center gap-2">
          {saveState === "error" ? <p className="text-xs text-red-300">{saveErrorMessage ?? "Could not save platform settings."}</p> : null}
          <button
            className="fx-btn-primary px-3 py-2 text-sm"
            onClick={handleSavePlatformSettings}
            disabled={!loaded || saveState === "saving"}
            aria-label="Save preferences"
          >
            {saveState === "saving" ? "Saving..." : saveState === "saved" ? "Saved" : "Save changes"}
          </button>
        </div>
      }
      rightRail={
        <>
          <SettingsRailCard
            title="ATF posture snapshot"
            description="Live alignment estimate from trust controls and recent audit evidence."
            badge={atfLoading ? "Loading" : `${atfReport?.coverage_percent ?? 0}% • ${maturityLabel}`}
          >
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
              <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-2 text-xs">
                <p className="fx-muted">Audit events (24h)</p>
                <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{atfReport?.evidence.audit_event_count_24h ?? 0}</p>
              </div>
              <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-2 text-xs">
                <p className="fx-muted">Allowed vs blocked</p>
                <p className="mt-1 text-base font-semibold text-[var(--foreground)]">
                  {atfReport?.evidence.audit_allowed_24h ?? 0} / {atfReport?.evidence.audit_blocked_24h ?? 0}
                </p>
              </div>
              <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-2 text-xs">
                <p className="fx-muted">Errors (24h)</p>
                <p className="mt-1 text-base font-semibold text-[var(--foreground)]">{atfReport?.evidence.audit_error_24h ?? 0}</p>
              </div>
            </div>
            {topGaps.length > 0 ? (
              <ul className="mt-3 space-y-2 text-xs text-[var(--foreground)]">
                {topGaps.map((gap) => (
                  <li key={gap} className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-2">{gap}</li>
                ))}
              </ul>
            ) : null}
            <div className="mt-3">
              <ControlStatusList report={atfReport?.control_status} showEvidence={false} />
            </div>
          </SettingsRailCard>

          <SettingsRailCard title="Current posture" description="The short version of what new work inherits today.">
            <ul className="space-y-2 text-xs text-[var(--foreground)]">
              {postureSummary.map((item) => (
                <li key={item} className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-2">{item}</li>
              ))}
            </ul>
          </SettingsRailCard>
        </>
      }
    >
          <article id="section-brand" className="fx-panel p-3 scroll-mt-32">
            <h2 className="text-sm font-semibold">Brand and identity</h2>
            <p className="fx-muted text-xs">Operational details visible across collaboration, approvals, and audit exports.</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="block text-xs">
                Organization name
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={orgName} onChange={(e) => setOrgName(e.target.value)} />
              </label>
              <label className="block text-xs">
                Organization slug
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={orgSlug} onChange={(e) => setOrgSlug(e.target.value)} />
              </label>
              <label className="block text-xs">
                Support email
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={supportEmail} onChange={(e) => setSupportEmail(e.target.value)} />
              </label>
              <label className="block text-xs">
                Website
                <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={website} onChange={(e) => setWebsite(e.target.value)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs md:col-span-2">
                <span>Show console classification banner</span>
                <input
                  aria-label="Show console classification banner"
                  type="checkbox"
                  checked={classificationBannerEnabled}
                  onChange={(event) => setClassificationBannerEnabled(event.target.checked)}
                />
              </label>
              <label className="block text-xs md:col-span-2">
                Console banner text
                <input
                  aria-label="Console banner text"
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={classificationBannerText}
                  onChange={(e) => setClassificationBannerText(e.target.value)}
                />
              </label>
              <label className="block text-xs">
                Banner background color
                <input
                  aria-label="Banner background color"
                  type="color"
                  className="fx-field mt-1 h-10 w-full px-2 py-1"
                  value={normalizeHexColor(classificationBannerBackgroundColor, "#2e2a28")}
                  onChange={(e) => setClassificationBannerBackgroundColor(e.target.value)}
                />
              </label>
              <label className="block text-xs">
                Banner text color
                <input
                  aria-label="Banner text color"
                  type="color"
                  className="fx-field mt-1 h-10 w-full px-2 py-1"
                  value={normalizeHexColor(classificationBannerTextColor, "#e7dcc0")}
                  onChange={(e) => setClassificationBannerTextColor(e.target.value)}
                />
              </label>
              <div className="border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] px-3 py-3 text-xs md:col-span-2">
                <p className="font-medium text-[var(--foreground)]">Banner preview</p>
                <div
                  className="mt-2 border border-[var(--ui-border)] px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em]"
                  style={{
                    background: normalizeHexColor(classificationBannerBackgroundColor, "#2e2a28"),
                    color: normalizeHexColor(classificationBannerTextColor, "#e7dcc0"),
                  }}
                >
                  {classificationBannerText.trim() || "Internal • Operational Console"}
                </div>
              </div>
            </div>
          </article>

          <article id="section-classification" className="fx-panel p-3 scroll-mt-32">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Classification banner</h2>
                <p className="fx-muted text-xs">
                  Device-local preset for the operator-visible strip. Org-wide banner text and colors configured above take precedence whenever platform settings are available.
                </p>
              </div>
              <label className="inline-flex items-center gap-2 text-xs">
                <input
                  type="checkbox"
                  checked={classificationBanner.enabled}
                  onChange={(event) => setClassificationBanner({ enabled: event.target.checked })}
                />
                <span>Visible</span>
              </label>
            </div>

            <label className="mt-3 block text-xs">
              Banner text
              <input
                className="fx-field mt-1 w-full px-2 py-2 font-mono text-[12px]"
                value={classificationBanner.text}
                placeholder="Internal · Operational Console"
                onChange={(event) => setClassificationBanner({ text: event.target.value })}
              />
            </label>

            <div className="mt-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
                Preset
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {CLASSIFICATION_PRESET_META.map(({ key, label, description }) => {
                  const preset = CLASSIFICATION_PRESETS[key];
                  const isActive = classificationBanner.preset === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setClassificationBanner({ preset: key })}
                      aria-pressed={isActive}
                      className={`flex flex-col gap-2 rounded-md border px-2.5 py-2 text-left text-[11px] transition ${
                        isActive
                          ? "border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.08)]"
                          : "border-[var(--ui-border)] hover:bg-[var(--fx-nav-hover)]"
                      }`}
                    >
                      <span
                        className="flex items-center justify-center rounded-sm px-2 py-1 font-mono text-[10px] font-bold uppercase tracking-[0.1em]"
                        style={{
                          background: preset.background,
                          color: preset.foreground,
                          border: `1px solid ${preset.border}`,
                        }}
                      >
                        {label}
                      </span>
                      <span className="text-[10px] text-[var(--fx-muted)]">{description}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </article>

          <article id="section-security" className="fx-panel p-3 scroll-mt-32">
            <h2 className="text-sm font-semibold">Security and governance</h2>
            <p className="fx-muted text-xs">Default controls applied across workspace sessions and run execution.</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Mask sensitive values in previews</span>
                <input type="checkbox" checked={maskSecrets} onChange={(event) => setMaskSecrets(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Local-only mode</span>
                <input type="checkbox" checked={localOnlyMode} onChange={(event) => setLocalOnlyMode(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs md:col-span-2">
                <span>Require human approval on all workflow runs</span>
                <input type="checkbox" checked={requireHumanApproval} onChange={(event) => setRequireHumanApproval(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs md:col-span-2">
                <span>Emergency read-only mode (block write actions)</span>
                <input type="checkbox" checked={emergencyReadOnlyMode} onChange={(event) => setEmergencyReadOnlyMode(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Block new workflow runs</span>
                <input type="checkbox" checked={blockNewRuns} onChange={(event) => setBlockNewRuns(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Block graph runs</span>
                <input type="checkbox" checked={blockGraphRuns} onChange={(event) => setBlockGraphRuns(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Block tool calls</span>
                <input type="checkbox" checked={blockToolCalls} onChange={(event) => setBlockToolCalls(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs">
                <span>Block retrieval calls</span>
                <input type="checkbox" checked={blockRetrievalCalls} onChange={(event) => setBlockRetrievalCalls(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs md:col-span-2">
                <span>Require authenticated write requests</span>
                <input type="checkbox" checked={requireAuthenticatedRequests} onChange={(event) => setRequireAuthenticatedRequests(event.target.checked)} />
              </label>
              <label className="flex items-center justify-between gap-3 border border-[var(--fx-border)] px-2 py-2 text-xs md:col-span-2">
                <span>Require A2A runtime headers (subject/signature/nonce)</span>
                <input type="checkbox" checked={requireA2aRuntimeHeaders} onChange={(event) => setRequireA2aRuntimeHeaders(event.target.checked)} />
              </label>
              <label className="block text-xs md:col-span-2">
                Default guardrail ruleset ID
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={defaultGuardrailRulesetId}
                  onChange={(event) => setDefaultGuardrailRulesetId(event.target.value)}
                  placeholder="12121212-1212-4121-8121-121212121212"
                />
              </label>
              <label className="block text-xs md:col-span-2">
                Globally blocked keywords
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={globalBlockedKeywords}
                  onChange={(event) => setGlobalBlockedKeywords(event.target.value)}
                  placeholder="private_key,secret,password"
                />
              </label>
            </div>
          </article>

          <article id="section-runtime" className="fx-panel p-3 scroll-mt-32">
            <h2 className="text-sm font-semibold">Runtime policy</h2>
            <p className="fx-muted text-xs">Default execution strategy and role-based framework routing profile.</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="block text-xs">
                Max collaborating agents per run
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={collaborationMaxAgents}
                  onChange={(event) => setCollaborationMaxAgents(event.target.value)}
                  inputMode="numeric"
                />
              </label>
              <label className="block text-xs">
                Default runtime strategy
                <select
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={defaultRuntimeStrategy}
                  onChange={(event) => setDefaultRuntimeStrategy(event.target.value as "single" | "hybrid")}
                >
                  <option value="single">single</option>
                  <option value="hybrid">hybrid</option>
                </select>
              </label>
            </div>

            <div className="mt-3 space-y-2 border border-[var(--fx-border)] p-2">
              <div className="flex items-center justify-between">
                <div className="text-xs uppercase tracking-wide fx-muted">Global hybrid role → engine profile</div>
                <button
                  type="button"
                  className="fx-btn-secondary px-2 py-1 text-xs"
                  onClick={handleResetToRecommendedHybridProfile}
                >
                  Reset to recommended profile
                </button>
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                <label className="block text-xs">
                  default
                  <select
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={defaultHybridRouting.default}
                    onChange={(event) =>
                      setDefaultHybridRouting((current) => ({
                        ...current,
                        default: event.target.value,
                      }))
                    }
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={`settings-default-${engine}`} value={engine}>
                        {engine}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs">
                  orchestration
                  <select
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={defaultHybridRouting.orchestration}
                    onChange={(event) =>
                      setDefaultHybridRouting((current) => ({
                        ...current,
                        orchestration: event.target.value,
                      }))
                    }
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={`settings-orchestration-${engine}`} value={engine}>
                        {engine}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs">
                  retrieval
                  <select
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={defaultHybridRouting.retrieval}
                    onChange={(event) =>
                      setDefaultHybridRouting((current) => ({
                        ...current,
                        retrieval: event.target.value,
                      }))
                    }
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={`settings-retrieval-${engine}`} value={engine}>
                        {engine}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs">
                  tooling
                  <select
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={defaultHybridRouting.tooling}
                    onChange={(event) =>
                      setDefaultHybridRouting((current) => ({
                        ...current,
                        tooling: event.target.value,
                      }))
                    }
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={`settings-tooling-${engine}`} value={engine}>
                        {engine}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block text-xs">
                  collaboration
                  <select
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={defaultHybridRouting.collaboration}
                    onChange={(event) =>
                      setDefaultHybridRouting((current) => ({
                        ...current,
                        collaboration: event.target.value,
                      }))
                    }
                  >
                    {runtimeEngineOptions.map((engine) => (
                      <option key={`settings-collaboration-${engine}`} value={engine}>
                        {engine}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          </article>

          <article id="section-user-defaults" className="fx-panel p-3 scroll-mt-32">
            <h2 className="text-sm font-semibold">User defaults</h2>
            <p className="fx-muted text-xs">Personal productivity defaults for review and session management.</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="block text-xs">
                Default kickoff workflow
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={defaultKickoffWorkflow} onChange={(e) => setDefaultKickoffWorkflow(e.target.value)}>
                  <option>Auto-select from intent</option>
                  <option>Investor Outreach Pack</option>
                  <option>Prospect Outreach Pack</option>
                </select>
              </label>
              <label className="block text-xs">
                Preferred review depth
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={preferredReviewDepth} onChange={(e) => setPreferredReviewDepth(e.target.value)}>
                  <option>Concise</option>
                  <option>Standard</option>
                  <option>Detailed</option>
                </select>
              </label>
              <label className="block text-xs md:col-span-2">
                Idle timeout
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={idleTimeout} onChange={(e) => setIdleTimeout(e.target.value)}>
                  <option>15 minutes</option>
                  <option>30 minutes</option>
                  <option>60 minutes</option>
                </select>
              </label>
            </div>
          </article>

          <ComposerDefaultsSettings />

          <article id="section-user-skills" className="fx-panel p-3 scroll-mt-32">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">Personal /skills</h2>
                <p className="fx-muted text-xs">Define user-scoped slash skills that only apply to your operator profile.</p>
              </div>
              <div className="flex items-center gap-2">
                {userSkillsSaveState === "error" ? <p className="text-xs text-red-300">{userSkillsSaveError ?? "Could not save personal skills."}</p> : null}
                <button
                  type="button"
                  className="fx-btn-secondary px-3 py-2 text-sm"
                  onClick={handleSaveUserSkills}
                  disabled={!loaded || userSkillsSaveState === "saving"}
                >
                  {userSkillsSaveState === "saving" ? "Saving..." : userSkillsSaveState === "saved" ? "Saved" : "Save /skills"}
                </button>
              </div>
            </div>

            <label className="mt-3 block text-xs">
              Personal /skills
              <textarea
                className="fx-field mt-1 min-h-28 w-full px-2 py-2 text-sm"
                value={userScopedSkills}
                onChange={(event) => setUserScopedSkills(event.target.value)}
                placeholder="/incident-triage&#10;/research-brief&#10;/customer-followup"
              />
              <span className="mt-2 block fx-muted">Enter one skill per line. A leading slash is optional and will be normalized on save.</span>
            </label>
          </article>
    </SettingsShell>
  );
}
