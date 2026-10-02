"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  createWorkflowRun,
  getAgentDefinitions,
  getPlaybooks,
  getPublishedWorkflows,
  type ComposerOptions,
} from "@/lib/api";
import type { AgentDefinition, PlaybookDefinition, WorkflowDefinition } from "@/types/frontier";
import { ComposerControls } from "@/components/composer-controls";

type TokenKind = "data" | "tag" | "workflow" | "agent" | "playbook";

type MentionTrigger = "@" | "/" | "!";

type ParsedToken = {
  kind: TokenKind;
  value: string;
};

type MentionSuggestion = {
  trigger: MentionTrigger;
  value: string;
  label: string;
};

const delimiterLegend: Array<{ symbol: string; kind: TokenKind; meaning: string; example: string }> = [
  { symbol: "$", kind: "data", meaning: "Bring in data/source", example: "$crm_q1_pipeline" },
  { symbol: "#", kind: "tag", meaning: "Task tag/priority", example: "#need-review" },
  { symbol: "/", kind: "workflow", meaning: "Call workflow", example: "/investor-pack" },
  { symbol: "@", kind: "agent", meaning: "Assign agent", example: "@orchestration-agent" },
  { symbol: "!", kind: "playbook", meaning: "Call playbook", example: "!incident-response" },
];

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

function tokenClass(kind: TokenKind): string {
  if (kind === "data") return "border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] text-[var(--foreground)]";
  if (kind === "tag") return "border border-[var(--fx-warning)] bg-[color-mix(in_srgb,var(--fx-warning)_20%,transparent)] text-[var(--foreground)]";
  if (kind === "workflow") return "border border-[var(--fx-primary)] bg-[color-mix(in_srgb,var(--fx-primary)_20%,transparent)] text-[var(--foreground)]";
  if (kind === "playbook") return "border border-[var(--fx-warning)] bg-[color-mix(in_srgb,var(--fx-warning)_28%,transparent)] text-[var(--foreground)]";
  return "border border-[var(--fx-success)] bg-[color-mix(in_srgb,var(--fx-success)_20%,transparent)] text-[var(--foreground)]";
}

function tokenAccent(kind: TokenKind): string {
  if (kind === "tag") return "text-[var(--fx-warning)]";
  if (kind === "workflow") return "text-[var(--fx-primary)]";
  if (kind === "playbook") return "text-[var(--fx-warning)]";
  if (kind === "agent") return "text-[var(--fx-success)]";
  return "text-[var(--foreground)]";
}

function slugify(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function getActiveMentionToken(
  text: string,
  cursor: number,
): { trigger: MentionTrigger; query: string; start: number; end: number } | null {
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

export function TaskKickoffComposer({
  prefill,
}: {
  /** Bump `nonce` (with new `text`) to load a suggested draft from outside. */
  prefill?: { text: string; nonce: number };
} = {}) {
  const router = useRouter();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [draft, setDraft] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [createdRunId, setCreatedRunId] = useState<string | null>(null);
  const [submitInfo, setSubmitInfo] = useState<string | null>(null);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [publishedAgents, setPublishedAgents] = useState<AgentDefinition[]>([]);
  const [publishedWorkflows, setPublishedWorkflows] = useState<WorkflowDefinition[]>([]);
  const [activePlaybooks, setActivePlaybooks] = useState<PlaybookDefinition[]>([]);
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [composerOpts, setComposerOpts] = useState<ComposerOptions>({});

  const tokens = useMemo(() => parseTokens(draft), [draft]);

  useEffect(() => {
    let cancelled = false;

    async function loadMentions() {
      const [agentDefs, workflowDefs, playbookDefs] = await Promise.all([
        getAgentDefinitions(),
        getPublishedWorkflows(),
        // Wrapped so a synchronous throw also degrades to "no playbooks".
        Promise.resolve().then(() => getPlaybooks()).catch(() => [] as PlaybookDefinition[]),
      ]);
      if (cancelled) {
        return;
      }
      setPublishedAgents(agentDefs.filter((agent) => agent.status === "published"));
      setPublishedWorkflows(workflowDefs.filter((workflow) => workflow.status === "published"));
      setActivePlaybooks(playbookDefs.filter((playbook) => playbook.status === "published"));
    }

    void loadMentions();

    return () => {
      cancelled = true;
    };
  }, []);

  // Load an externally-supplied suggested draft (inbox quick-starts) when its
  // nonce changes, then focus the caret at the end so the user keeps typing.
  useEffect(() => {
    if (!prefill || !prefill.text) return;
    setDraft(prefill.text);
    setCursorPosition(prefill.text.length);
    requestAnimationFrame(() => {
      const element = textareaRef.current;
      if (element) {
        element.focus();
        element.setSelectionRange(prefill.text.length, prefill.text.length);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce]);

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
      return activePlaybooks
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
  }, [activeMention, publishedAgents, publishedWorkflows, activePlaybooks]);

  useEffect(() => {
    setActiveSuggestionIndex(0);
  }, [activeMention?.trigger, activeMention?.query]);

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

  // Clicking a legend card inserts its delimiter at the caret (with a leading
  // space when needed) and refocuses — for @ / ! this also opens the suggestion
  // list, turning the reference into a one-click helper.
  function insertDelimiter(symbol: string) {
    const element = textareaRef.current;
    const caret = element?.selectionStart ?? draft.length;
    const before = draft.slice(0, caret);
    const after = draft.slice(caret);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const insertion = `${needsSpace ? " " : ""}${symbol}`;
    const nextDraft = `${before}${insertion}${after}`;
    const nextCursor = before.length + insertion.length;
    setDraft(nextDraft);
    setCursorPosition(nextCursor);
    requestAnimationFrame(() => {
      if (textareaRef.current) {
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(nextCursor, nextCursor);
      }
    });
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

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft.trim()) return;
    setSubmitError(null);
    setSubmitInfo(null);

    try {
      setIsSubmitting(true);
      const publishedAgentSlugs = new Set(publishedAgents.map((agent) => slugify(agent.name) || slugify(agent.id)));
      const publishedWorkflowSlugs = new Set(publishedWorkflows.map((workflow) => slugify(workflow.name) || slugify(workflow.id)));
      const activePlaybookSlugs = new Set(activePlaybooks.map((playbook) => slugify(playbook.name) || slugify(playbook.id)));
      const filteredTokens = tokens.filter((token) => {
        if (token.kind === "agent") {
          return publishedAgentSlugs.has(token.value);
        }
        if (token.kind === "workflow") {
          return publishedWorkflowSlugs.has(token.value);
        }
        if (token.kind === "playbook") {
          return activePlaybookSlugs.has(token.value);
        }
        return true;
      });

      const playbooks = filteredTokens
        .filter((token) => token.kind === "playbook")
        .map((token) => token.value);

      const payload = {
        session_kind: "task",
        prompt: draft,
        tokens: filteredTokens,
        ...(playbooks.length > 0 ? { playbooks } : {}),
        ...composerOpts,
      };
      const result = await createWorkflowRun(payload, { timeoutMs: 120000 });
      setCreatedRunId(result.id);
      setSubmitInfo(`Task started. Opening run ${result.id}...`);
      setDraft("");
      // Tell the nav chat tree (and any listeners) to refetch the run list.
      window.dispatchEvent(new CustomEvent("frontier:runs-changed"));
      router.push(`/inbox?session=${encodeURIComponent(result.id)}`);
      router.refresh();
    } catch (error) {
      const messageText = error instanceof Error ? error.message : "Unable to start task run.";
      setSubmitError(`${messageText} If the run is still processing, wait a few seconds and try again.`);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="fx-panel p-4">
      <form onSubmit={onSubmit} className="space-y-3">
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setCursorPosition(event.target.selectionStart ?? event.target.value.length);
          }}
          onClick={(event) => setCursorPosition((event.target as HTMLTextAreaElement).selectionStart ?? draft.length)}
          onKeyUp={(event) => setCursorPosition((event.target as HTMLTextAreaElement).selectionStart ?? draft.length)}
          onKeyDown={handleTextareaKeyDown}
          placeholder="Draft outreach sequence for $federal_pipeline #need-review /prospect-outreach @orchestration-agent"
          className="fx-field min-h-24 w-full p-3 text-sm"
        />

        <div className="mt-1 rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3 py-2">
          <ComposerControls onChange={setComposerOpts} />
        </div>

        {activeMention && mentionSuggestions.length > 0 ? (
          <div className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)]">
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--fx-muted)]">
              {activeMention.trigger === "@"
                ? "Published Agents"
                : activeMention.trigger === "!"
                  ? "Active Playbooks"
                  : "Published Workflows"}
            </div>
            <ul className="max-h-40 overflow-auto text-xs">
              {mentionSuggestions.map((suggestion, index) => (
                <li key={`${suggestion.trigger}-${suggestion.value}`}>
                  <button
                    type="button"
                    className={`w-full px-2 py-1.5 text-left ${
                      index === activeSuggestionIndex
                        ? "bg-[color-mix(in_srgb,var(--fx-primary)_16%,transparent)] text-[var(--foreground)]"
                        : "hover:bg-[var(--fx-nav-hover)]"
                    }`}
                    onMouseDown={(event) => {
                      event.preventDefault();
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

        {tokens.length > 0 ? (
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {tokens.map((token, index) => (
              <span
                key={`${token.kind}-${token.value}-${index}`}
                className={`rounded px-1.5 py-0.5 ${tokenClass(token.kind)}`}
              >
                {token.kind}:{token.value}
              </span>
            ))}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <button type="submit" disabled={isSubmitting} className="fx-btn-primary px-3 py-2 text-sm font-medium disabled:opacity-60">
            {isSubmitting ? "Starting..." : "Start task"}
          </button>
          {createdRunId ? (
            <Link className="fx-btn-secondary px-3 py-2 text-sm" href={`/inbox?session=${encodeURIComponent(createdRunId)}`}>
              Open run
            </Link>
          ) : null}

          {/* Compact, click-to-insert delimiter reference — full meaning + example in the tooltip. */}
          <span className="mx-0.5 hidden h-5 w-px bg-[var(--ui-border)] sm:block" aria-hidden="true" />
          <div className="flex flex-wrap items-center gap-1">
            <span className="fx-muted mr-0.5 text-[11px]">Insert</span>
            {delimiterLegend.map((item) => (
              <button
                key={item.symbol}
                type="button"
                onClick={() => insertDelimiter(item.symbol)}
                title={`${item.meaning} — e.g. ${item.example}`}
                aria-label={`Insert ${item.symbol} (${item.meaning})`}
                className="inline-flex items-center gap-1 rounded-full border border-[var(--fx-border)] px-2 py-0.5 text-[11px] text-[var(--fx-muted)] transition-colors hover:border-[var(--fx-primary)] hover:bg-[var(--fx-nav-hover)] hover:text-[var(--foreground)]"
              >
                <span className={`font-mono font-bold ${tokenAccent(item.kind)}`} aria-hidden="true">
                  {item.symbol}
                </span>
                {item.kind}
              </button>
            ))}
          </div>
        </div>

        {submitInfo ? <p className="text-xs text-[var(--fx-muted)]">{submitInfo}</p> : null}
        {submitError ? <p className="text-xs text-[var(--fx-danger)]">{submitError}</p> : null}
      </form>
    </div>
  );
}
