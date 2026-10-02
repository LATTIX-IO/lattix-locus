"use client";

import {
  type ChangeEvent,
  type CSSProperties,
  useEffect,
  useRef,
  useState,
} from "react";
import { FX_ACCENT } from "@/components/fx-ui";

export type ModelOption = {
  id: string;
  label: string;
  provider: string;
  note: string;
};

export type SlashOption = {
  kind: "skill" | "agent" | "workflow" | "playbook";
  icon: string;
  label: string;
  desc: string;
};

export const DEFAULT_MODELS: ModelOption[] = [
  { id: "gpt-4o", label: "GPT-4o", provider: "OpenAI", note: "Balanced · 128K" },
  { id: "gpt-4o-mini", label: "GPT-4o mini", provider: "OpenAI", note: "Fast · cheap" },
  { id: "claude-sonnet", label: "Claude Sonnet 4", provider: "Anthropic", note: "Best reasoning · 200K" },
  { id: "claude-opus", label: "Claude Opus 4.1", provider: "Anthropic", note: "Deepest · 200K" },
  { id: "gemini-pro", label: "Gemini 2.5 Pro", provider: "Google", note: "Multimodal · 1M" },
  { id: "llama-local", label: "Llama 3.1 70B", provider: "Local", note: "On-prem · air-gapped" },
  { id: "mixtral-local", label: "Mixtral 8×22B", provider: "Local", note: "On-prem · MoE" },
];

export const DEFAULT_SLASH: SlashOption[] = [
  { kind: "skill", icon: "⚡", label: "Summarize document", desc: "Skill · extract key facts + takeaways" },
  { kind: "skill", icon: "⚡", label: "Extract PII", desc: "Skill · scan text for personal info" },
  { kind: "skill", icon: "⚡", label: "Redline contract", desc: "Skill · mark risks + suggest edits" },
  { kind: "agent", icon: "◆", label: "Compliance Agent", desc: "Agent · GDPR, SOC 2, HIPAA audits" },
  { kind: "agent", icon: "◆", label: "Research Agent", desc: "Agent · multi-source synthesis" },
  { kind: "agent", icon: "◆", label: "Data Room Analyst", desc: "Agent · due diligence review" },
  { kind: "workflow", icon: "❖", label: "Q4 Audit Pipeline", desc: "Workflow · 7 steps · ~12m" },
  { kind: "workflow", icon: "❖", label: "Data Room Provisioning", desc: "Workflow · 6 steps · ~4m" },
  { kind: "playbook", icon: "▣", label: "GDPR Compliance Sweep", desc: "Playbook · 5 phases · 14 workflows" },
  { kind: "playbook", icon: "▣", label: "M&A Due Diligence", desc: "Playbook · 4 phases · 22 workflows" },
];

const KIND_COLORS: Record<
  SlashOption["kind"],
  { bg: string; fg: string; label: string }
> = {
  skill: { bg: "hsl(45 90% 60% / 0.14)", fg: "hsl(35 85% 32%)", label: "Skill" },
  agent: { bg: "hsl(205 90% 56% / 0.14)", fg: "hsl(202 88% 40%)", label: "Agent" },
  workflow: { bg: "hsl(265 60% 58% / 0.14)", fg: "hsl(265 55% 42%)", label: "Workflow" },
  playbook: { bg: "hsl(145 55% 46% / 0.14)", fg: "hsl(145 55% 26%)", label: "Playbook" },
};

const composerBtnStyle = (active: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  gap: 5,
  height: 28,
  padding: "0 8px",
  border: `1px solid ${active ? FX_ACCENT.primary : "var(--ui-border)"}`,
  background: active ? "hsl(35 95% 52% / 0.08)" : "transparent",
  borderRadius: 6,
  cursor: "pointer",
  color: active ? FX_ACCENT.primaryDark : "var(--fx-muted)",
  transition: "all 120ms",
});

const composerIconStyle = (active: boolean): CSSProperties => ({
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  width: 28,
  height: 28,
  border: `1px solid ${active ? FX_ACCENT.primary : "var(--ui-border)"}`,
  background: active ? "hsl(35 95% 52% / 0.08)" : "transparent",
  borderRadius: 6,
  cursor: "pointer",
  color: active ? FX_ACCENT.primaryDark : "var(--fx-muted)",
  transition: "all 120ms",
});

export function TasksComposer({
  onSubmit,
  models = DEFAULT_MODELS,
  slashOptions = DEFAULT_SLASH,
  initialModelId,
  placeholder = "Ask a follow-up, attach files, or type / to run a skill, agent, workflow, or playbook…",
}: {
  onSubmit?: (payload: {
    text: string;
    model: ModelOption;
    attachments: { name: string; size: number }[];
  }) => void;
  models?: ModelOption[];
  slashOptions?: SlashOption[];
  initialModelId?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState("");
  const [model, setModel] = useState<ModelOption>(
    () => models.find((m) => m.id === initialModelId) ?? models[2] ?? models[0],
  );
  const [openPicker, setOpenPicker] = useState<"model" | "slash" | null>(null);
  const [attachments, setAttachments] = useState<{ name: string; size: number }[]>([]);
  const [slashFilter, setSlashFilter] = useState("");
  const [listening, setListening] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (t.closest("[data-composer-picker]") || t.closest("[data-composer-trigger]"))
        return;
      setOpenPicker(null);
    };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  const onTextChange = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setText(v);
    if (v.startsWith("/")) {
      setOpenPicker("slash");
      setSlashFilter(v.slice(1).toLowerCase());
    } else if (openPicker === "slash") {
      setOpenPicker(null);
    }
    const ta = taRef.current;
    if (ta) {
      ta.style.height = "auto";
      ta.style.height = `${Math.min(ta.scrollHeight, 160)}px`;
    }
  };

  const onFilePick = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    setAttachments((a) => [...a, ...files.map((f) => ({ name: f.name, size: f.size }))]);
    e.target.value = "";
  };

  const onSlashSelect = (opt: SlashOption) => {
    setText(`[${opt.label}] `);
    setOpenPicker(null);
    setTimeout(() => taRef.current?.focus(), 0);
  };

  const toggleMic = () => {
    setListening((v) => !v);
    if (!listening) {
      setTimeout(() => {
        setText((t) => (t ? `${t} ` : "") + "Draft the executive summary for the Q4 findings.");
        setListening(false);
      }, 2200);
    }
  };

  const filteredSlash = slashOptions.filter(
    (o) =>
      !slashFilter ||
      o.label.toLowerCase().includes(slashFilter) ||
      o.kind.includes(slashFilter),
  );

  const send = () => {
    if (!text.trim() && attachments.length === 0) return;
    onSubmit?.({ text, model, attachments });
    setText("");
    setAttachments([]);
    if (taRef.current) taRef.current.style.height = "auto";
  };

  const disabled = !text.trim() && attachments.length === 0;

  return (
    <div
      className="relative flex-shrink-0 border-t border-[var(--ui-border)] bg-[hsl(var(--card))] px-7 pb-4 pt-3.5"
    >
      <div className="relative mx-auto max-w-[880px]">
        {attachments.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((f, i) => (
              <div
                key={`${f.name}-${i}`}
                className="flex items-center gap-1.5 rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted))] px-2 py-1 text-[11px] text-[hsl(var(--foreground))]"
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                >
                  <path d="M9 2h3l2 2v10H2V2h7zm0 0v3h3" />
                </svg>
                <span>{f.name}</span>
                <button
                  type="button"
                  onClick={() => setAttachments((a) => a.filter((_, j) => j !== i))}
                  className="flex h-3.5 w-3.5 items-center justify-center border-none bg-transparent text-[var(--fx-muted)]"
                  aria-label="Remove attachment"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : null}

        {openPicker === "model" ? (
          <div
            data-composer-picker
            className="absolute bottom-[calc(100%+6px)] left-0 z-50 max-h-[320px] w-[320px] overflow-y-auto rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--card))] shadow-xl"
          >
            <div className="font-mono border-b border-[var(--ui-border)] px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
              Model
            </div>
            {models.map((m) => {
              const active = model.id === m.id;
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    setModel(m);
                    setOpenPicker(null);
                  }}
                  className="flex w-full flex-col items-start gap-0.5 border-l-[3px] border-transparent px-3 py-2 text-left"
                  style={{
                    borderLeftColor: active ? FX_ACCENT.primary : "transparent",
                    background: active ? "hsl(35 95% 52% / 0.08)" : "transparent",
                  }}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-[12px] font-semibold text-[hsl(var(--foreground))]">
                      {m.label}
                    </span>
                    <span className="font-mono inline-block rounded border border-[var(--ui-border)] px-1.5 py-0 text-[9px] font-bold uppercase tracking-[0.08em] text-[var(--fx-muted)]">
                      {m.provider}
                    </span>
                  </div>
                  <span className="text-[10px] text-[var(--fx-muted)]">{m.note}</span>
                </button>
              );
            })}
          </div>
        ) : null}

        {openPicker === "slash" && filteredSlash.length > 0 ? (
          <div
            data-composer-picker
            className="absolute bottom-[calc(100%+6px)] left-0 z-50 max-h-[340px] w-[380px] overflow-y-auto rounded-lg border border-[var(--ui-border)] bg-[hsl(var(--card))] shadow-xl"
          >
            <div className="font-mono flex items-center justify-between border-b border-[var(--ui-border)] px-3 py-2 text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
              <span>Run</span>
              <span className="font-normal text-[var(--fx-muted)]">
                {filteredSlash.length} matches
              </span>
            </div>
            {filteredSlash.map((o, i) => {
              const c = KIND_COLORS[o.kind];
              return (
                <button
                  key={`${o.label}-${i}`}
                  type="button"
                  onClick={() => onSlashSelect(o)}
                  className="flex w-full items-center gap-2.5 border-b border-[var(--ui-border)] px-3 py-2 text-left last:border-b-0 hover:bg-[hsl(var(--muted))]"
                >
                  <span
                    className="flex h-[26px] w-[26px] flex-shrink-0 items-center justify-center rounded text-[12px] font-bold"
                    style={{ background: c.bg, color: c.fg }}
                  >
                    {o.icon}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="m-0 text-[12px] font-semibold text-[hsl(var(--foreground))]">
                      {o.label}
                    </p>
                    <p className="m-0 mt-0.5 text-[10px] text-[var(--fx-muted)]">{o.desc}</p>
                  </div>
                  <span
                    className="font-mono flex-shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.08em]"
                    style={{ color: c.fg, background: c.bg }}
                  >
                    {c.label}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}

        <div className="rounded-xl border border-[var(--ui-border)] bg-[hsl(var(--card))] px-3 pb-2 pt-2.5 shadow-sm">
          <textarea
            ref={taRef}
            value={text}
            onChange={onTextChange}
            rows={1}
            placeholder={placeholder}
            className="w-full resize-none border-none bg-transparent px-0 py-1 text-[13px] leading-relaxed text-[hsl(var(--foreground))] outline-none"
          />
          <div className="mt-1.5 flex items-center gap-1">
            <button
              type="button"
              data-composer-trigger
              onClick={() => setOpenPicker((p) => (p === "model" ? null : "model"))}
              title="Select model"
              style={composerBtnStyle(openPicker === "model")}
            >
              <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                <ellipse cx="8" cy="3.5" rx="5.5" ry="1.8" />
                <path d="M2.5 3.5v4c0 1 2.5 1.8 5.5 1.8s5.5-.8 5.5-1.8v-4" />
                <path d="M2.5 7.5v4c0 1 2.5 1.8 5.5 1.8s5.5-.8 5.5-1.8v-4" />
              </svg>
              <span
                className="font-mono text-[11px] font-medium"
                style={{ color: "hsl(var(--foreground))" }}
              >
                {model.label}
              </span>
              <svg
                width="8"
                height="8"
                viewBox="0 0 10 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                style={{ color: "var(--fx-muted)", marginLeft: 2 }}
              >
                <path d="M2 4l3 3 3-3" />
              </svg>
            </button>

            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              title="Attach files"
              style={composerIconStyle(false)}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              >
                <path d="M10.5 7.5L6 12a2.5 2.5 0 01-3.5-3.5l5-5a4 4 0 015.5 5.5L7 14.5" />
              </svg>
            </button>
            <input ref={fileRef} type="file" multiple onChange={onFilePick} className="hidden" />

            <button
              type="button"
              data-composer-trigger
              onClick={() => {
                setOpenPicker((p) => (p === "slash" ? null : "slash"));
                setSlashFilter("");
              }}
              title="Run a skill, agent, workflow, or playbook"
              style={composerIconStyle(openPicker === "slash")}
            >
              <span
                className="font-mono text-[14px] font-bold leading-none"
                style={{ color: "inherit" }}
              >
                /
              </span>
            </button>

            <button
              type="button"
              onClick={toggleMic}
              title={listening ? "Stop listening" : "Voice to text"}
              style={{
                ...composerIconStyle(listening),
                background: listening ? "hsl(358 75% 56% / 0.12)" : "transparent",
                borderColor: listening ? "hsl(358 75% 56% / 0.4)" : "var(--ui-border)",
                color: listening ? "hsl(358 65% 48%)" : "var(--fx-muted)",
              }}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              >
                <rect x="6" y="2" width="4" height="8" rx="2" />
                <path d="M3.5 7.5a4.5 4.5 0 009 0M8 12v2M6 14h4" />
              </svg>
              {listening ? (
                <span
                  className="ml-1 h-1.5 w-1.5 animate-pulse rounded-full"
                  style={{ background: "hsl(358 65% 48%)" }}
                />
              ) : null}
            </button>

            <div className="flex-1" />

            <button
              type="button"
              onClick={send}
              disabled={disabled}
              className="fx-btn-primary inline-flex items-center px-3 py-1.5 text-[12px] font-medium"
            >
              Send
            </button>
          </div>
        </div>

        <p className="mt-1.5 text-center text-[10px] text-[var(--fx-muted)]">
          {listening ? (
            <span style={{ color: "hsl(358 65% 48%)" }}>🎙 Listening…</span>
          ) : (
            <>
              Shift + Enter for newline · Enter to send ·{" "}
              <span className="font-mono">/</span> for commands
            </>
          )}
        </p>
      </div>
    </div>
  );
}
