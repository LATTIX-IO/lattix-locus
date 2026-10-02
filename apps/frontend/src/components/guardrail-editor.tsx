"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { publishGuardrailRuleset, saveGuardrailRuleset } from "@/lib/api";
import type { GuardrailRuleSet } from "@/types/frontier";

type ControlGroup = "All controls" | "Jailbreak" | "Content safety" | "Protected materials";

type ControlRow = {
  group: Exclude<ControlGroup, "All controls">;
  riskType: string;
  interventionPoint: string;
  action: "Block" | "Review";
};

const defaultControls: ControlRow[] = [
  { group: "Jailbreak", riskType: "Jailbreak", interventionPoint: "User input", action: "Block" },
  { group: "Content safety", riskType: "Hate: Medium blocking", interventionPoint: "User input, Output", action: "Block" },
  { group: "Content safety", riskType: "Self-harm: Medium blocking", interventionPoint: "User input, Output", action: "Block" },
  { group: "Content safety", riskType: "Sexual: Medium blocking", interventionPoint: "User input, Output", action: "Block" },
  { group: "Content safety", riskType: "Violence: Medium blocking", interventionPoint: "User input, Output", action: "Block" },
  { group: "Protected materials", riskType: "Protected material for code", interventionPoint: "Output", action: "Block" },
  { group: "Protected materials", riskType: "Protected material for text", interventionPoint: "Output", action: "Block" },
];

type Props = {
  mode: "new" | "edit";
  ruleset?: GuardrailRuleSet;
};

function parseConfiguredControlIds(value: unknown): string[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const ids = value
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  return ids.length > 0 ? ids : null;
}

export function GuardrailEditor({ mode, ruleset }: Props) {
  const router = useRouter();
  const existingConfig = (ruleset?.config_json ?? {}) as Record<string, unknown>;
  const configuredControlIds = parseConfiguredControlIds(existingConfig.selected_controls);
  const [selectedControlIds, setSelectedControlIds] = useState<string[]>(
    configuredControlIds ?? defaultControls.map((_, index) => String(index)),
  );
  const [controlFilter, setControlFilter] = useState<ControlGroup>("All controls");
  const [displayName, setDisplayName] = useState(ruleset?.name ?? "New Guardrail Set");
  const [stage, setStage] = useState(String(existingConfig.stage ?? "output"));
  const [tripwireAction, setTripwireAction] = useState(String(existingConfig.tripwire_action ?? "reject_content"));
  const [detectSecrets, setDetectSecrets] = useState(Boolean(existingConfig.detect_secrets ?? true));
  const [runInParallel, setRunInParallel] = useState(Boolean(existingConfig.run_in_parallel ?? false));
  const [blockedKeywords, setBlockedKeywords] = useState(
    Array.isArray(existingConfig.blocked_keywords)
      ? (existingConfig.blocked_keywords as unknown[]).filter((item): item is string => typeof item === "string").join(", ")
      : "",
  );
  const [requiredKeywords, setRequiredKeywords] = useState(
    Array.isArray(existingConfig.required_keywords)
      ? (existingConfig.required_keywords as unknown[]).filter((item): item is string => typeof item === "string").join(", ")
      : "",
  );
  const [rejectMessage, setRejectMessage] = useState(String(existingConfig.reject_message ?? "Blocked by policy"));
  const [minLength, setMinLength] = useState(String(existingConfig.min_length ?? ""));
  const [maxLength, setMaxLength] = useState(String(existingConfig.max_length ?? ""));
  const [applyModel, setApplyModel] = useState(String(existingConfig.apply_model ?? "all"));
  const [applyWorkflow, setApplyWorkflow] = useState(String(existingConfig.apply_workflow ?? "all"));
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "published" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const publishId = ruleset?.id;

  const filteredControls = useMemo(() => {
    return defaultControls.filter((control) => controlFilter === "All controls" || control.group === controlFilter);
  }, [controlFilter]);

  function parseList(value: string): string[] {
    return value
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }

  function buildConfigPayload(): Record<string, unknown> {
    const minParsed = Number(minLength);
    const maxParsed = Number(maxLength);
    return {
      stage,
      tripwire_action: tripwireAction,
      detect_secrets: detectSecrets,
      run_in_parallel: runInParallel,
      blocked_keywords: parseList(blockedKeywords),
      required_keywords: parseList(requiredKeywords),
      reject_message: rejectMessage,
      min_length: Number.isFinite(minParsed) && minLength !== "" ? minParsed : null,
      max_length: Number.isFinite(maxParsed) && maxLength !== "" ? maxParsed : null,
      selected_controls: selectedControlIds,
      apply_model: applyModel,
      apply_workflow: applyWorkflow,
    };
  }

  async function handleSaveDraft() {
    setSaveState("saving");
    setErrorMessage(null);
    try {
      const saved = await saveGuardrailRuleset({
        id: ruleset?.id,
        name: displayName,
        config_json: buildConfigPayload(),
      });
      setSaveState("saved");

      if (mode === "new" && saved.id) {
        router.replace(`/builder/guardrails/${encodeURIComponent(saved.id)}`);
      } else {
        router.refresh();
      }
    } catch (error) {
      setSaveState("error");
      setErrorMessage(error instanceof Error ? error.message : "Unable to persist guardrail changes.");
    }
  }

  async function handlePublish() {
    if (!publishId) {
      return;
    }
    setPublishState("publishing");
    setErrorMessage(null);
    try {
      await publishGuardrailRuleset(publishId);
      setPublishState("published");
      router.refresh();
    } catch (error) {
      setPublishState("error");
      setErrorMessage(error instanceof Error ? error.message : "Unable to publish the guardrail ruleset.");
    }
  }

  function toggleControl(id: string) {
    setSelectedControlIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  return (
    <section className="space-y-5">
      <header className="rounded-[1.7rem] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_97%,hsl(var(--background))_3%)] px-5 py-4 shadow-[0_22px_56px_rgba(15,23,42,0.06)]">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Builder workspace</p>
            <h1 className="mt-2 text-[1.5rem] font-semibold tracking-[-0.03em] text-[var(--foreground)]">{mode === "new" ? "Create Guardrail Set" : "Guardrail Set Editor"}</h1>
            <p className="mt-2 text-sm leading-6 text-[var(--fx-muted)]">Define the protective controls, choose the runtime targets they apply to, and stage the ruleset for save or publish without leaving the builder context.</p>
          </div>
          <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Policy-first editing</div>
        </div>
        <div className="mt-4 max-w-xl">
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">Guardrail set name</span>
            <input
              className="fx-field mt-1 w-full px-2 py-2 text-sm"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Core Messaging Guardrails"
            />
          </label>
        </div>
        {ruleset ? <p className="mt-3 font-mono text-xs text-[var(--foreground)]">guardrail_id: {ruleset.id}</p> : null}
      </header>

      <div className="grid gap-4 xl:grid-cols-[260px_1fr]">
        <aside className="fx-panel rounded-[1.5rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
          <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Checklist</p>
          <h2 className="mt-2 mb-3 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Create guardrail controls</h2>
          <ol className="space-y-2 text-xs">
            <li className="rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3">
              <p className="font-semibold">1. Add controls</p>
              <p className="fx-muted">Select risk controls to detect and respond to flagged behavior.</p>
            </li>
            <li className="rounded-[1rem] border border-[var(--fx-border)] p-3">
              <p className="font-semibold">2. Select agents and models</p>
              <p className="fx-muted">Apply controls to model endpoints and workflow executions.</p>
            </li>
            <li className="rounded-[1rem] border border-[var(--fx-border)] p-3">
              <p className="font-semibold">3. Review and save</p>
              <p className="fx-muted">Persist the ruleset before publishing it for platform use.</p>
            </li>
          </ol>

          <label className="mt-4 block text-xs text-[var(--foreground)]">
            <span className="font-medium">Risk group filter</span>
            <select
              className="fx-field mt-1 w-full px-2 py-2 text-xs"
              value={controlFilter}
              onChange={(event) => setControlFilter(event.target.value as ControlGroup)}
            >
              <option value="All controls">All controls</option>
              <option value="Jailbreak">Jailbreak</option>
              <option value="Content safety">Content safety</option>
              <option value="Protected materials">Protected materials</option>
            </select>
          </label>
        </aside>

        <div className="space-y-3">
          <div className="fx-panel overflow-hidden rounded-[1.5rem] shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
            <div className="flex items-center justify-between gap-3 border-b border-[var(--ui-border)] px-4 py-4">
              <div>
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Controls</p>
                <h3 className="mt-2 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Risk control library</h3>
              </div>
              <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">{filteredControls.length} visible</div>
            </div>
            <table className="w-full text-sm">
              <thead className="fx-table-head text-xs">
                <tr>
                  <th className="px-3 py-2 text-left">Select</th>
                  <th className="px-3 py-2 text-left">Risk type</th>
                  <th className="px-3 py-2 text-left">Intervention point</th>
                  <th className="px-3 py-2 text-left">Action</th>
                  <th className="px-3 py-2 text-left">Group</th>
                </tr>
              </thead>
              <tbody>
                {filteredControls.map((control, index) => {
                  const originalIndex = defaultControls.findIndex((candidate) => candidate === control);
                  const id = String(originalIndex >= 0 ? originalIndex : index);
                  return (
                    <tr key={`${control.group}-${control.riskType}`} className="border-t border-[var(--fx-border)] hover:bg-[hsl(var(--muted)/0.16)]">
                      <td className="px-3 py-2">
                        <input
                          type="checkbox"
                          checked={selectedControlIds.includes(id)}
                          onChange={() => toggleControl(id)}
                          aria-label={`Select ${control.riskType}`}
                        />
                      </td>
                      <td className="px-3 py-2 text-[var(--foreground)]">{control.riskType}</td>
                      <td className="fx-muted px-3 py-2">{control.interventionPoint}</td>
                      <td className="px-3 py-2 text-[var(--foreground)]">{control.action}</td>
                      <td className="fx-muted px-3 py-2">{control.group}</td>
                    </tr>
                  );
                })}
                {filteredControls.length === 0 ? (
                  <tr className="border-t border-[var(--fx-border)]">
                    <td colSpan={5} className="px-3 py-4 text-xs text-[var(--fx-muted)]">
                      No controls match the selected filter.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <div className="fx-panel rounded-[1.5rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Targeting</p>
            <h3 className="mt-2 mb-3 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Apply to targets</h3>
            <div className="grid gap-2 md:grid-cols-2">
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Models</span>
                <select className="fx-field mt-1 w-full px-2 py-2 text-xs" value={applyModel} onChange={(event) => setApplyModel(event.target.value)}>
                  <option value="all">All default models</option>
                  <option value="gpt-5.2">gpt-5.2</option>
                  <option value="gpt-5.2-mini">gpt-5.2-mini</option>
                </select>
              </label>
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Workflows</span>
                <select className="fx-field mt-1 w-full px-2 py-2 text-xs" value={applyWorkflow} onChange={(event) => setApplyWorkflow(event.target.value)}>
                  <option value="all">All workflows</option>
                  <option value="investor-outreach-pack">Investor Outreach Pack</option>
                  <option value="prospect-outreach-pack">Prospect Outreach Pack</option>
                </select>
              </label>
            </div>

            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Stage</span>
                <select className="fx-field mt-1 w-full px-2 py-2 text-xs" value={stage} onChange={(event) => setStage(event.target.value)}>
                  <option value="input">Input</option>
                  <option value="output">Output</option>
                  <option value="tool_input">Tool Input</option>
                  <option value="tool_output">Tool Output</option>
                </select>
              </label>
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Tripwire action</span>
                <select className="fx-field mt-1 w-full px-2 py-2 text-xs" value={tripwireAction} onChange={(event) => setTripwireAction(event.target.value)}>
                  <option value="allow">Allow</option>
                  <option value="reject_content">Reject content</option>
                  <option value="raise_exception">Raise exception</option>
                </select>
              </label>
            </div>
          </div>

          <div className="fx-panel rounded-[1.5rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Policy copy</p>
            <h3 className="mt-2 mb-3 text-[1.02rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Blocklists and policy text</h3>
            <div className="grid gap-2 md:grid-cols-2">
              <label className="block text-xs text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Blocked keywords (comma-separated)</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-xs"
                  value={blockedKeywords}
                  onChange={(event) => setBlockedKeywords(event.target.value)}
                  placeholder="password,private_key,internal-only"
                />
              </label>
              <label className="block text-xs text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Required keywords (comma-separated)</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-xs"
                  value={requiredKeywords}
                  onChange={(event) => setRequiredKeywords(event.target.value)}
                  placeholder="approved,citation"
                />
              </label>
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Min length</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-xs"
                  value={minLength}
                  onChange={(event) => setMinLength(event.target.value)}
                  inputMode="numeric"
                  placeholder="0"
                />
              </label>
              <label className="block text-xs text-[var(--foreground)]">
                <span className="font-medium">Max length</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-xs"
                  value={maxLength}
                  onChange={(event) => setMaxLength(event.target.value)}
                  inputMode="numeric"
                  placeholder="4000"
                />
              </label>
              <label className="block text-xs text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Reject message</span>
                <textarea
                  className="fx-field mt-1 min-h-20 w-full px-2 py-2 text-xs"
                  value={rejectMessage}
                  onChange={(event) => setRejectMessage(event.target.value)}
                />
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={detectSecrets} onChange={(event) => setDetectSecrets(event.target.checked)} />
                Detect secrets in payload
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={runInParallel} onChange={(event) => setRunInParallel(event.target.checked)} />
                Run in parallel
              </label>
            </div>
          </div>

          <div className="fx-panel rounded-[1.5rem] p-4 shadow-[0_18px_44px_rgba(15,23,42,0.05)]">
            <div className="flex flex-wrap gap-2">
              <button
                onClick={handleSaveDraft}
                className="fx-btn-secondary px-3 py-2 text-sm"
                disabled={saveState === "saving"}
              >
                {saveState === "saving" ? "Saving..." : saveState === "saved" ? "Saved" : "Save draft"}
              </button>
              {publishId ? (
                <button onClick={handlePublish} className="fx-btn-primary px-3 py-2 text-sm" disabled={publishState === "publishing"}>
                  {publishState === "publishing" ? "Publishing..." : publishState === "published" ? "Published" : "Publish"}
                </button>
              ) : null}
            </div>

            {errorMessage ? (
              <p className="mt-3 rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-danger)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] px-3 py-2 text-xs text-[var(--foreground)]">{errorMessage}</p>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
