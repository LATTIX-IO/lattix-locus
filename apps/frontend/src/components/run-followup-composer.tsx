"use client";

import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  createWorkflowRun,
  getAgentDefinitions,
  getPlaybooks,
  getPublishedWorkflows,
  getRuntimeProviders,
  getUserRuntimeProviders,
  sendRunMessage,
  type ComposerOptions,
} from "@/lib/api";
import type { AgentDefinition, PlaybookDefinition, WorkflowDefinition } from "@/types/frontier";
import { ComposerControls } from "@/components/composer-controls";

type TokenKind = "data" | "tag" | "workflow" | "agent" | "playbook";

type MentionTrigger = "@" | "/" | "!";

/**
 * How a message is delivered:
 * - "same-run": appended to THIS run's conversation via sendRunMessage; the live
 *   event stream renders the reply in place. Uses the inline composer controls
 *   (model / reasoning / mode / MCP servers / working folder).
 * - "follow-up-run": starts a follow-up chat run seeded with recent context and
 *   the selected runtime provider/model.
 */
export type FollowupDeliveryMode = "same-run" | "follow-up-run";

type ParsedToken = {
  kind: TokenKind;
  value: string;
};

type MentionSuggestion = {
  trigger: MentionTrigger;
  value: string;
  label: string;
};

type ComposerRuntimeOption = {
  provider: string;
  label: string;
  model: string;
  models: string[];
  source: "user" | "environment";
  preferred: boolean;
};

type ComposerModelOption = {
  value: string;
  provider: string;
  providerLabel: string;
  model: string;
  label: string;
};

type ComposerCommandOption = {
  id: string;
  token: string;
  label: string;
  typeLabel: string;
};

export type FollowupComposerStatus = {
  state: "idle" | "submitting" | "success" | "error";
  message: string | null;
  createdRunId: string | null;
  provider: string;
  model: string;
  source: string | null;
};

type RuntimeSeed = {
  provider?: string;
  model?: string;
};

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Gemini",
  "openai-compatible": "Local / OpenAI Compatible",
};

function parseTokens(text: string): ParsedToken[] {
  const matches = text.match(/([$#/@!])[^\s$#/@!]+/g) ?? [];

  return matches.map((token) => {
    const prefix = token[0];
    const value = token.slice(1);

    if (prefix === "$") return { kind: "data", value };
    if (prefix === "#") return { kind: "tag", value };
    if (prefix === "/") return { kind: "workflow", value };
    if (prefix === "!") return { kind: "playbook", value };
    return { kind: "agent", value };
  });
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function getActiveMentionToken(text: string, cursor: number): { trigger: MentionTrigger; query: string; start: number; end: number } | null {
  if (cursor < 0 || cursor > text.length) {
    return null;
  }

  let start = cursor - 1;
  while (start >= 0 && !/\s/.test(text[start])) {
    start -= 1;
  }
  start += 1;

  const token = text.slice(start, cursor);
  if (!token || (token[0] !== "@" && token[0] !== "/" && token[0] !== "!")) {
    return null;
  }

  if (token.length > 1 && /[@/!]/.test(token.slice(1))) {
    return null;
  }

  return {
    trigger: token[0] as MentionTrigger,
    query: token.slice(1),
    start,
    end: cursor,
  };
}

type Props = {
  runId: string;
  recentContext?: string;
  initialRuntime?: RuntimeSeed;
  onStatusChange?: (status: FollowupComposerStatus) => void;
  /** Defaults to "follow-up-run". */
  delivery?: FollowupDeliveryMode;
};

const COMPOSER_MIN_HEIGHT = 44;
const COMPOSER_MAX_HEIGHT = 240;

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function buildRuntimeOptions(
  userProviders: Array<{
    provider: string;
    configured: boolean;
    model: string;
    available_models?: string[];
    preferred?: boolean;
    source: "user" | "environment";
  }>,
  runtimeProviders: Array<{ provider: string; configured: boolean; model: string }>,
): ComposerRuntimeOption[] {
  const options = new Map<string, ComposerRuntimeOption>();

  for (const provider of userProviders) {
    if (!provider.configured) {
      continue;
    }
    const models = [...new Set([provider.model, ...(provider.available_models ?? [])].filter((item) => item.trim().length > 0))];
    options.set(provider.provider, {
      provider: provider.provider,
      label: providerLabel(provider.provider),
      model: provider.model,
      models: models.length > 0 ? models : [provider.model],
      source: provider.source,
      preferred: Boolean(provider.preferred),
    });
  }

  for (const provider of runtimeProviders) {
    if (!provider.configured || options.has(provider.provider)) {
      continue;
    }
    options.set(provider.provider, {
      provider: provider.provider,
      label: providerLabel(provider.provider),
      model: provider.model,
      models: [provider.model].filter(Boolean),
      source: "environment",
      preferred: false,
    });
  }

  return [...options.values()].sort((left, right) => {
    if (left.preferred !== right.preferred) {
      return left.preferred ? -1 : 1;
    }
    return left.label.localeCompare(right.label);
  });
}

function buildModelOptions(runtimeOptions: ComposerRuntimeOption[]): ComposerModelOption[] {
  return runtimeOptions.flatMap((option) =>
    option.models.map((model) => ({
      value: `${option.provider}::${model}`,
      provider: option.provider,
      providerLabel: option.label,
      model,
      label: `${option.label} · ${model}`,
    })),
  );
}

export function RunFollowupComposer({
  runId,
  recentContext = "",
  initialRuntime,
  onStatusChange,
  delivery = "follow-up-run",
}: Props) {
  const sameRun = delivery === "same-run";
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const controlsMenuRef = useRef<HTMLDivElement | null>(null);
  const [draft, setDraft] = useState("");
  const [cursorPosition, setCursorPosition] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [publishedAgents, setPublishedAgents] = useState<AgentDefinition[]>([]);
  const [publishedWorkflows, setPublishedWorkflows] = useState<WorkflowDefinition[]>([]);
  const [publishedPlaybooks, setPublishedPlaybooks] = useState<PlaybookDefinition[]>([]);
  const [composerOpts, setComposerOpts] = useState<ComposerOptions>({});
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitInfo, setSubmitInfo] = useState<string | null>(null);
  const [createdRunId, setCreatedRunId] = useState<string | null>(null);
  const [composerCollapsed, setComposerCollapsed] = useState(false);
  const [composerCanCollapse, setComposerCanCollapse] = useState(false);
  const [runtimeOptions, setRuntimeOptions] = useState<ComposerRuntimeOption[]>([]);
  const [selectedProvider, setSelectedProvider] = useState(initialRuntime?.provider ?? "");
  const [selectedModel, setSelectedModel] = useState(initialRuntime?.model ?? "");
  const [controlsOpen, setControlsOpen] = useState(false);
  const [runtimeLoadError, setRuntimeLoadError] = useState<string | null>(null);
  const [commandQuery, setCommandQuery] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function loadComposerData() {
      // Each loader is wrapped so a synchronous throw becomes a settled rejection.
      const [agentDefs, workflowDefs, playbookDefs, userProviders, runtimeProviders] = await Promise.allSettled([
        Promise.resolve().then(() => getAgentDefinitions()),
        Promise.resolve().then(() => getPublishedWorkflows()),
        Promise.resolve().then(() => getPlaybooks()),
        // Same-run delivery uses ComposerControls for model selection instead.
        sameRun ? Promise.resolve([]) : Promise.resolve().then(() => getUserRuntimeProviders()),
        sameRun ? Promise.resolve({ providers: [] }) : Promise.resolve().then(() => getRuntimeProviders()),
      ]);
      if (cancelled) {
        return;
      }

      if (agentDefs.status === "fulfilled") {
        setPublishedAgents(agentDefs.value.filter((agent) => agent.status === "published"));
      }
      if (workflowDefs.status === "fulfilled") {
        setPublishedWorkflows(workflowDefs.value.filter((workflow) => workflow.status === "published"));
      }
      if (playbookDefs.status === "fulfilled") {
        setPublishedPlaybooks(playbookDefs.value.filter((playbook) => playbook.status === "published"));
      }

      if (userProviders.status === "fulfilled" || runtimeProviders.status === "fulfilled") {
        const nextRuntimeOptions = buildRuntimeOptions(
          userProviders.status === "fulfilled" ? userProviders.value : [],
          runtimeProviders.status === "fulfilled" ? runtimeProviders.value.providers : [],
        );
        setRuntimeOptions(nextRuntimeOptions);
        setRuntimeLoadError(null);
      } else {
        setRuntimeOptions([]);
        setRuntimeLoadError("Runtime settings are unavailable right now.");
      }
    }

    void loadComposerData();

    return () => {
      cancelled = true;
    };
  }, [sameRun]);

  useEffect(() => {
    if (runtimeOptions.length === 0) {
      return;
    }

    setSelectedProvider((current) => {
      if (current && runtimeOptions.some((option) => option.provider === current)) {
        return current;
      }
      if (initialRuntime?.provider && runtimeOptions.some((option) => option.provider === initialRuntime.provider)) {
        return initialRuntime.provider;
      }
      return runtimeOptions[0]?.provider ?? current;
    });
  }, [initialRuntime?.provider, runtimeOptions]);

  const activeRuntimeOption = useMemo(
    () => runtimeOptions.find((option) => option.provider === selectedProvider) ?? runtimeOptions[0] ?? null,
    [runtimeOptions, selectedProvider],
  );

  const modelOptions = useMemo(() => buildModelOptions(runtimeOptions), [runtimeOptions]);

  useEffect(() => {
    if (!activeRuntimeOption) {
      return;
    }

    setSelectedModel((current) => {
      if (current && activeRuntimeOption.models.includes(current)) {
        return current;
      }
      if (
        initialRuntime?.provider === activeRuntimeOption.provider
        && initialRuntime.model
        && activeRuntimeOption.models.includes(initialRuntime.model)
      ) {
        return initialRuntime.model;
      }
      return activeRuntimeOption.model;
    });
  }, [activeRuntimeOption, initialRuntime?.model, initialRuntime?.provider]);

  const selectedModelValue = activeRuntimeOption && selectedModel
    ? `${activeRuntimeOption.provider}::${selectedModel}`
    : "";

  const currentModelLabel = activeRuntimeOption
    ? `${activeRuntimeOption.label} · ${selectedModel || activeRuntimeOption.model}`
    : "Default model";

  const activeMention = useMemo(() => getActiveMentionToken(draft, cursorPosition), [draft, cursorPosition]);

  const mentionSuggestions = useMemo<MentionSuggestion[]>(() => {
    if (!activeMention) {
      return [];
    }

    const query = activeMention.query.toLowerCase();
    if (activeMention.trigger === "@") {
      return publishedAgents
        .map((agent) => {
          const value = slugify(agent.name) || slugify(agent.id);
          return {
            trigger: "@" as const,
            value,
            label: `${agent.name} (v${agent.version})`,
          };
        })
        .filter((item) => item.value.includes(query) || item.label.toLowerCase().includes(query))
        .slice(0, 8);
    }

    if (activeMention.trigger === "!") {
      return publishedPlaybooks
        .map((playbook) => ({
          trigger: "!" as const,
          value: slugify(playbook.name) || slugify(playbook.id),
          label: playbook.name,
        }))
        .filter((item) => item.value.includes(query) || item.label.toLowerCase().includes(query))
        .slice(0, 8);
    }

    return publishedWorkflows
      .map((workflow) => ({
        trigger: "/" as const,
        value: slugify(workflow.name) || slugify(workflow.id),
        label: `${workflow.name} (v${workflow.version})`,
      }))
      .filter((item) => item.value.includes(query) || item.label.toLowerCase().includes(query))
      .slice(0, 8);
  }, [activeMention, publishedAgents, publishedWorkflows, publishedPlaybooks]);

  useEffect(() => {
    setActiveSuggestionIndex(0);
  }, [activeMention?.trigger, activeMention?.query]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }

    textarea.style.height = "0px";
    const naturalHeight = Math.max(textarea.scrollHeight, COMPOSER_MIN_HEIGHT);
    const canCollapse = naturalHeight > COMPOSER_MIN_HEIGHT + 6 || draft.includes("\n");
    const collapsed = composerCollapsed && canCollapse;

    setComposerCanCollapse(canCollapse);
    textarea.style.height = `${collapsed ? COMPOSER_MIN_HEIGHT : Math.min(naturalHeight, COMPOSER_MAX_HEIGHT)}px`;
    textarea.style.overflowY = !collapsed && naturalHeight > COMPOSER_MAX_HEIGHT ? "auto" : "hidden";

    if (!draft.trim() && composerCollapsed) {
      setComposerCollapsed(false);
    }
  }, [composerCollapsed, draft]);

  useEffect(() => {
    onStatusChange?.({
      state: submitError ? "error" : isSubmitting ? "submitting" : submitInfo ? "success" : "idle",
      message: submitError ?? submitInfo,
      createdRunId,
      provider: activeRuntimeOption?.provider ?? selectedProvider,
      model: selectedModel || activeRuntimeOption?.model || "",
      source: activeRuntimeOption?.source ?? null,
    });
  }, [activeRuntimeOption?.model, activeRuntimeOption?.provider, activeRuntimeOption?.source, createdRunId, isSubmitting, onStatusChange, selectedModel, selectedProvider, submitError, submitInfo]);

  const commandOptions = useMemo<ComposerCommandOption[]>(() => {
    const agentOptions = publishedAgents.map((agent) => ({
      id: `agent:${agent.id}`,
      token: `@${slugify(agent.name) || slugify(agent.id)}`,
      label: `${agent.name} (v${agent.version})`,
      typeLabel: "Agent",
    }));
    const workflowOptions = publishedWorkflows.map((workflow) => ({
      id: `workflow:${workflow.id}`,
      token: `/${slugify(workflow.name) || slugify(workflow.id)}`,
      label: `${workflow.name} (v${workflow.version})`,
      typeLabel: "Workflow",
    }));
    const playbookOptions = publishedPlaybooks.map((playbook) => ({
      id: `playbook:${playbook.id}`,
      token: `!${slugify(playbook.name) || slugify(playbook.id)}`,
      label: playbook.name,
      typeLabel: "Playbook",
    }));
    const query = commandQuery.trim().toLowerCase();
    return [...agentOptions, ...workflowOptions, ...playbookOptions]
      .filter((option) => !query || option.token.toLowerCase().includes(query) || option.label.toLowerCase().includes(query))
      .slice(0, 8);
  }, [commandQuery, publishedAgents, publishedWorkflows, publishedPlaybooks]);

  useEffect(() => {
    if (!controlsOpen) {
      return;
    }

    function handlePointerDown(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (controlsMenuRef.current?.contains(target)) {
        return;
      }
      setControlsOpen(false);
    }

    function handleEscape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setControlsOpen(false);
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [controlsOpen]);

  function applyMentionSuggestion(suggestion: MentionSuggestion) {
    if (!activeMention) {
      return;
    }

    const nextDraft = `${draft.slice(0, activeMention.start)}${suggestion.trigger}${suggestion.value} ${draft.slice(activeMention.end)}`;
    const nextCursor = activeMention.start + suggestion.value.length + 2;
    setDraft(nextDraft);
    setCursorPosition(nextCursor);

    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(nextCursor, nextCursor);
      }
    });
  }


  function insertShortcutToken(token: string) {
    const base = draft.trimEnd();
    const nextDraft = base ? `${base}${base.endsWith(" ") ? "" : " "}${token} ` : `${token} `;
    const nextCursor = nextDraft.length;
    setDraft(nextDraft);
    setCursorPosition(nextCursor);

    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(nextCursor, nextCursor);
      }
    });
    setControlsOpen(false);
  }

  function handleModelSelection(value: string) {
    const [provider, model] = value.split("::");
    if (!provider || !model) {
      return;
    }
    setSelectedProvider(provider);
    setSelectedModel(model);
  }

  function handleTextareaKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (!isSubmitting && draft.trim()) {
        event.currentTarget.form?.requestSubmit();
      }
      return;
    }

    if (!activeMention || mentionSuggestions.length === 0) {
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveSuggestionIndex((previous) => (previous + 1) % mentionSuggestions.length);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveSuggestionIndex((previous) => (previous - 1 + mentionSuggestions.length) % mentionSuggestions.length);
      return;
    }

    if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      const selected = mentionSuggestions[activeSuggestionIndex] ?? mentionSuggestions[0];
      if (selected) {
        applyMentionSuggestion(selected);
      }
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setCursorPosition(-1);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || isSubmitting) {
      return;
    }
    setSubmitError(null);
    setSubmitInfo(null);
    setCreatedRunId(null);

    if (sameRun) {
      try {
        setIsSubmitting(true);
        // Sends into THIS run's conversation; the live event stream renders the
        // user message and the agent's reply in place.
        await sendRunMessage(runId, message, composerOpts as Record<string, unknown>);
        setCreatedRunId(runId);
        setSubmitInfo("Message sent.");
        setDraft("");
        setComposerCollapsed(false);
        window.dispatchEvent(new CustomEvent("frontier:runs-changed"));
        router.refresh();
        requestAnimationFrame(() => textareaRef.current?.focus());
      } catch (error) {
        const messageText = error instanceof Error ? error.message : "Unable to send the message.";
        setSubmitError(messageText);
      } finally {
        setIsSubmitting(false);
      }
      return;
    }

    const parsedTokens = parseTokens(message);
    const publishedAgentSlugs = new Set(publishedAgents.map((agent) => slugify(agent.name) || slugify(agent.id)));
    const publishedWorkflowSlugs = new Set(publishedWorkflows.map((workflow) => slugify(workflow.name) || slugify(workflow.id)));
    const publishedPlaybookSlugs = new Set(publishedPlaybooks.map((playbook) => slugify(playbook.name) || slugify(playbook.id)));

    const filteredTokens = parsedTokens.filter((token) => {
      if (token.kind === "agent") {
        return publishedAgentSlugs.has(token.value);
      }
      if (token.kind === "workflow") {
        return publishedWorkflowSlugs.has(token.value);
      }
      if (token.kind === "playbook") {
        return publishedPlaybookSlugs.has(token.value);
      }
      return true;
    });
    const playbooks = filteredTokens.filter((token) => token.kind === "playbook").map((token) => token.value);
    const runtimePayload = activeRuntimeOption && selectedModel
      ? {
          provider: activeRuntimeOption.provider,
          model: selectedModel,
        }
      : undefined;

    try {
      setIsSubmitting(true);
      const nextRun = await createWorkflowRun({
        session_kind: "chat",
        prompt: message,
        tokens: filteredTokens,
        ...(playbooks.length > 0 ? { playbooks } : {}),
        context: {
          source_run_id: runId,
          mode: "follow_up",
          recent_context: recentContext,
        },
        runtime: runtimePayload,
        ...(runtimePayload ? runtimePayload : {}),
        source_run_id: runId,
        follow_up_to_run_id: runId,
      }, {
        timeoutMs: 120000,
      });

      setCreatedRunId(nextRun.id);
      if (nextRun.id === runId) {
        setSubmitInfo("Follow-up sent. Refreshing this run timeline...");
        router.refresh();
      } else {
        setSubmitInfo(`Follow-up sent. Opening run ${nextRun.id}...`);
        router.push(`/inbox?session=${encodeURIComponent(nextRun.id)}`);
        router.refresh();
      }
      setDraft("");
      setComposerCollapsed(false);
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "Unable to start follow-up run.";
      setSubmitError(`${messageText} If the run is still processing, wait a few seconds and try again.`);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="relative space-y-1.5">
      <label htmlFor="continue-message" className="sr-only">
        Message this run
      </label>
      <div className="relative flex items-end gap-2 rounded-[16px] border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3.5 py-2.5 shadow-[var(--fx-shadow-soft)]">
        <textarea
          ref={textareaRef}
          id="continue-message"
          name="message"
          required
          rows={1}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setCursorPosition(event.target.selectionStart ?? event.target.value.length);
          }}
          onClick={(event) => setCursorPosition((event.target as HTMLTextAreaElement).selectionStart ?? draft.length)}
          onKeyUp={(event) => setCursorPosition((event.target as HTMLTextAreaElement).selectionStart ?? draft.length)}
          onKeyDown={handleTextareaKeyDown}
          placeholder="Message this run... (use @agent, /workflow, or !playbook)"
          className="w-full resize-none bg-transparent py-[0.65rem] text-[0.82rem] leading-6 text-[hsl(var(--foreground))] outline-none placeholder:text-[var(--fx-muted)]"
        />

        <div className="flex items-center gap-2 self-end pb-0.5">
          {sameRun ? null : (
          <button
            type="button"
            onClick={() => setControlsOpen((current) => !current)}
            aria-label="Current follow-up model"
            className="hidden max-w-[11rem] items-center truncate rounded-[10px] border border-[var(--ui-border)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--fx-muted)] transition hover:bg-[var(--fx-nav-hover)] md:inline-flex"
          >
            <span className="truncate">{currentModelLabel}</span>
          </button>
          )}
          <button
            type="button"
            aria-label={controlsOpen ? "Hide follow-up controls" : "Show follow-up controls"}
            onClick={() => setControlsOpen((current) => !current)}
            className="fx-btn-secondary h-9 w-9 px-0 text-base"
          >
            +
          </button>

          {composerCanCollapse || composerCollapsed ? (
            <button
              type="button"
              aria-label={composerCollapsed ? "Expand message composer" : "Collapse message composer"}
              onClick={() => setComposerCollapsed((current) => !current)}
              className="fx-btn-secondary h-9 w-9 px-0 text-xs"
            >
              <svg viewBox="0 0 16 16" className={`mx-auto h-3.5 w-3.5 ${composerCollapsed ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth="1.6">
                <path d="M3.5 6 8 10.5 12.5 6" strokeLinecap="square" strokeLinejoin="miter" />
              </svg>
            </button>
          ) : null}

          <button type="submit" disabled={isSubmitting} className="fx-btn-primary h-9 px-3.5 text-xs font-medium disabled:opacity-60">
            {isSubmitting ? "Sending..." : "Send"}
          </button>
        </div>
      </div>

      {controlsOpen ? (
        <div
          ref={controlsMenuRef}
          className="absolute bottom-full right-0 z-30 mb-2 w-[min(20rem,calc(100vw-3rem))] overflow-hidden rounded-[16px] border border-[var(--ui-border)] bg-[hsl(var(--card))] shadow-[var(--fx-shadow-panel)]"
          role="menu"
          aria-label="Follow-up controls menu"
        >
          <div className="border-b border-[color-mix(in_srgb,var(--ui-border)_72%,transparent)] px-3 py-2">
            <p className="text-[11px] font-medium tracking-[0.04em] text-[var(--fx-muted)]">Composer Tools</p>
            {sameRun ? null : (
              <p className="mt-1 text-[11px] text-[var(--fx-muted)]">
                {currentModelLabel}
              </p>
            )}
          </div>

          <div className="space-y-3 px-3 py-3">
            {sameRun ? null : (
            <label className="block text-[11px] text-[var(--fx-muted)]">
              Current model
              <select
                aria-label="Follow-up current model"
                className="fx-field mt-1 w-full px-2 py-1.5 text-[11px]"
                value={selectedModelValue}
                onChange={(event) => handleModelSelection(event.target.value)}
                disabled={modelOptions.length === 0}
              >
                {modelOptions.length === 0 ? <option value="">No configured runtimes</option> : null}
                {modelOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            )}

            {!sameRun && runtimeLoadError ? <p className="text-[11px] text-[var(--fx-danger)]">{runtimeLoadError}</p> : null}

            <div className="space-y-2">
              <p className="text-[11px] font-medium tracking-[0.04em] text-[var(--fx-muted)]">Command Insert</p>
              <input
                type="text"
                aria-label="Search follow-up commands"
                value={commandQuery}
                onChange={(event) => setCommandQuery(event.target.value)}
                placeholder="Search @agents, /workflows, or !playbooks"
                className="fx-field w-full px-2 py-1.5 text-[11px]"
              />
              <div className="overflow-hidden rounded-[12px] border border-[color-mix(in_srgb,var(--ui-border)_72%,transparent)] bg-[hsl(var(--card)/0.72)]">
                {commandOptions.length > 0 ? (
                  <ul className="max-h-44 overflow-auto">
                    {commandOptions.map((option) => (
                      <li key={option.id}>
                        <button
                          type="button"
                          onClick={() => insertShortcutToken(option.token)}
                          className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-[var(--fx-nav-hover)]"
                        >
                          <span className="min-w-0">
                            <span className="block font-mono text-[11px] text-[hsl(var(--foreground))]">{option.token}</span>
                            <span className="block truncate text-[11px] text-[var(--fx-muted)]">{option.label}</span>
                          </span>
                          <span className="shrink-0 text-[10px] font-medium tracking-[0.03em] text-[var(--fx-muted)]">{option.typeLabel}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="px-3 py-2 text-[11px] text-[var(--fx-muted)]">No matching published agents, workflows, or playbooks.</p>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {activeMention && mentionSuggestions.length > 0 ? (
        <div className="overflow-hidden rounded-[14px] border border-[var(--ui-border)] bg-[hsl(var(--card))] shadow-[var(--fx-shadow-panel)]">
          <div className="px-2.5 py-1.5 text-[10px] font-medium tracking-[0.04em] text-[var(--fx-muted)]">
            {activeMention.trigger === "@"
              ? "Published Agents"
              : activeMention.trigger === "!"
                ? "Published Playbooks"
                : "Published Workflows"}
          </div>
          <ul className="max-h-40 overflow-auto text-xs">
            {mentionSuggestions.map((suggestion, index) => (
              <li key={`${suggestion.trigger}-${suggestion.value}`}>
                <button
                  type="button"
                  className={`w-full px-2 py-1.5 text-left ${
                    index === activeSuggestionIndex
                      ? "bg-[hsl(var(--primary)/0.15)] text-[var(--foreground)]"
                      : "hover:bg-[var(--fx-nav-hover)]"
                  }`}
                  onMouseDown={(mouseEvent) => {
                    mouseEvent.preventDefault();
                    applyMentionSuggestion(suggestion);
                  }}
                >
                  <div className="font-mono text-[var(--foreground)]">{suggestion.trigger}{suggestion.value}</div>
                  <div className="fx-muted">{suggestion.label}</div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {sameRun ? (
        <div className="rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3 py-1.5">
          <ComposerControls onChange={setComposerOpts} />
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-2">
        <p className="fx-muted text-[11px]">
          Published @agents, /workflows, and !playbooks are supported. Without any, this routes through the default chat agent.
        </p>
      </div>

      {!onStatusChange && submitInfo ? <p className="text-xs text-[hsl(var(--state-success))]">{submitInfo}</p> : null}
      {!onStatusChange && createdRunId ? (
        <p className="text-xs text-[var(--foreground)]">
          Run created: <Link href={`/inbox?session=${encodeURIComponent(createdRunId)}`} className="underline decoration-dotted underline-offset-2">{createdRunId}</Link>
        </p>
      ) : null}
      {!onStatusChange && submitError ? <p className="text-xs text-[var(--fx-danger)]">{submitError}</p> : null}
    </form>
  );
}
