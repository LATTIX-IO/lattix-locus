"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  getMcpServers,
  getModelsOverview,
  getSkills,
  getUserSettings,
  getWorkspaceFolders,
  type ComposerOptions,
  type McpServer,
  type SkillDefinition,
} from "@/lib/api";

type ModelOption = { value: string; label: string; reasoning: boolean };
type Mode = "chat" | "plan" | "execute";
type Panel = "mode" | "folder" | "model" | "mcp" | "skills" | null;

function isReasoningModel(value: string): boolean {
  return /gpt-oss|o1|o3|o4|gpt-5|reason|think|deepseek-r/i.test(value);
}

function shortModel(value: string): string {
  if (!value) return "model";
  return value.includes("/") ? value.split("/").pop() ?? value : value;
}

function shortFolder(path: string): string {
  if (!path) return "";
  const segs = path.replace(/\\/g, "/").split("/").filter(Boolean);
  return segs[segs.length - 1] || path;
}

// --- compact monochrome icons (14px, currentColor) --------------------------
const I = {
  chat: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z",
  plan: "M9 4h6M5 7h14v14H5zM8 12h8M8 16h5",
  execute: "M13 2L4 14h6v8l9-12h-6z",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  cpu: "M6 6h12v12H6zM9 2v2M15 2v2M9 20v2M15 20v2M2 9h2M2 15h2M20 9h2M20 15h2",
  plug: "M9 2v5M15 2v5M7 7h10v4a5 5 0 0 1-10 0zM12 16v6",
  spark: "M12 3l1.8 4.7L18 9l-4.2 1.3L12 15l-1.8-4.7L6 9l4.2-1.3z",
  chevron: "M6 9l6 6 6-6",
  clip: "M21 11l-8.5 8.5a4.5 4.5 0 0 1-6.4-6.4L14 5a3 3 0 0 1 4.2 4.2l-8.5 8.5a1.5 1.5 0 0 1-2.1-2.1l7.8-7.8",
  mic: "M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3",
  check: "M4 12l5 5L20 6",
};

function Icon({ d, className = "h-3.5 w-3.5" }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {d.split("M").filter(Boolean).map((seg, i) => (
        <path key={i} d={`M${seg}`} />
      ))}
    </svg>
  );
}

const MODES: { value: Mode; label: string; icon: string; hint: string }[] = [
  { value: "chat", label: "Chat", icon: I.chat, hint: "Pure conversation — no tools or file changes" },
  { value: "plan", label: "Plan", icon: I.plan, hint: "Analyze and produce an execution plan only" },
  { value: "execute", label: "Execute", icon: I.execute, hint: "Tools, MCP, and file edits enabled" },
];

type Props = { onChange: (options: ComposerOptions) => void };

export function ComposerControls({ onChange }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [folders, setFolders] = useState<{ name: string; path: string }[]>([]);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [skills, setSkills] = useState<SkillDefinition[]>([]);

  const [workingFolder, setWorkingFolder] = useState("");
  const [model, setModel] = useState("");
  const [reasoning, setReasoning] = useState<"" | "low" | "medium" | "high">("");
  const [mode, setMode] = useState<Mode>("execute");
  const [mcpIds, setMcpIds] = useState<string[]>([]);
  const [skillIds, setSkillIds] = useState<string[]>([]);
  const [panel, setPanel] = useState<Panel>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [settings, overview, servers, skillDefs, folderList] = await Promise.all([
        getUserSettings(),
        getModelsOverview().catch(() => null),
        getMcpServers().catch(() => []),
        getSkills().catch(() => []),
        getWorkspaceFolders().catch(() => null),
      ]);
      if (cancelled) return;
      const opts: ModelOption[] = [];
      const ov = overview as unknown as {
        providers?: { ollama?: { available?: boolean; installed_models?: { id: string }[] } };
        external?: { id: string; label: string; configured: boolean; default_model: string }[];
      } | null;
      if (ov?.providers?.ollama?.available) {
        for (const m of ov.providers.ollama.installed_models ?? []) {
          const id = String(m.id);
          opts.push({ value: `ollama/${id}`, label: `${id} · ollama`, reasoning: isReasoningModel(id) });
        }
      }
      for (const p of ov?.external ?? []) {
        if (p.configured && p.default_model) {
          const value = p.id === "openai" ? p.default_model : `${p.id}/${p.default_model}`;
          opts.push({ value, label: `${p.default_model} · ${p.label}`, reasoning: isReasoningModel(value) });
        }
      }
      setModels(opts);
      setMcpServers((servers as McpServer[]).filter((s) => s.configured));
      setSkills((skillDefs as SkillDefinition[]).filter((s) => s.status === "enabled"));
      if (folderList) setFolders(folderList.folders ?? []);
      setMode((settings.default_mode as Mode) || "execute");
      setReasoning((settings.preferred_reasoning_effort as "" | "low" | "medium" | "high") || "");
      setModel(settings.preferred_model || opts[0]?.value || "");
      if (settings.default_working_folder) setWorkingFolder(settings.default_working_folder);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // close popovers on outside click
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setPanel(null);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const lastEmit = useRef("");
  useEffect(() => {
    const options: ComposerOptions = {
      mode,
      model: model || undefined,
      reasoning_effort: reasoning || undefined,
      mcp_server_ids: mode === "execute" && mcpIds.length ? mcpIds : undefined,
      skill_ids: skillIds.length ? skillIds : undefined,
      workspace: workingFolder ? { repo_path: workingFolder, allow_outside: "ask" } : undefined,
    };
    const serialized = JSON.stringify(options);
    if (serialized !== lastEmit.current) {
      lastEmit.current = serialized;
      onChange(options);
    }
  }, [mode, model, reasoning, mcpIds, skillIds, workingFolder, onChange]);

  const currentMode = MODES.find((m) => m.value === mode) ?? MODES[2];
  const selectedModel = useMemo(() => models.find((m) => m.value === model), [models, model]);
  const reasoningCapable = selectedModel?.reasoning ?? isReasoningModel(model);

  function toggle(p: Panel) {
    setPanel((cur) => (cur === p ? null : p));
  }
  function toggleId(list: string[], id: string, set: (v: string[]) => void) {
    set(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  const btn =
    "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ui-border)] px-2.5 py-1.5 text-[12px] text-[var(--foreground)] transition-colors hover:bg-[var(--fx-nav-hover)]";
  const btnMuted =
    "inline-flex items-center gap-1.5 rounded-lg border border-[var(--ui-border)] px-2.5 py-1.5 text-[12px] text-[var(--fx-muted)] transition-colors hover:bg-[var(--fx-nav-hover)]";
  const pop =
    "absolute bottom-full left-0 z-30 mb-1.5 min-w-[200px] max-h-56 overflow-auto rounded-xl border border-[var(--ui-border)] bg-[hsl(var(--card))] p-1 shadow-[0_12px_32px_rgba(0,0,0,0.4)]";
  const item =
    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[var(--fx-nav-hover)]";
  const badge =
    "ml-0.5 rounded-full bg-[hsl(var(--primary)/0.2)] px-1.5 text-[10px] font-semibold text-[var(--foreground)]";

  return (
    <div ref={rootRef} className="flex flex-wrap items-center gap-2">
      {/* Mode — click current to open selector */}
      <div className="relative">
        <button type="button" className={btn} onClick={() => toggle("mode")} title={currentMode.hint} aria-label={`Mode: ${currentMode.label}`}>
          <Icon d={currentMode.icon} />
          <span>{currentMode.label}</span>
          <Icon d={I.chevron} className="h-3 w-3 opacity-50" />
        </button>
        {panel === "mode" ? (
          <div className={pop}>
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                className={item}
                title={m.hint}
                onClick={() => {
                  setMode(m.value);
                  setPanel(null);
                }}
              >
                <Icon d={m.icon} />
                <span className="flex-1">{m.label}</span>
                {m.value === mode ? <Icon d={I.check} className="h-3.5 w-3.5 text-[hsl(var(--primary))]" /> : null}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* Working folder — icon only until configured */}
      <div className="relative">
        <button type="button" className={workingFolder ? btn : btnMuted} onClick={() => toggle("folder")} title={workingFolder || "Set working folder"} aria-label="Working folder">
          <Icon d={I.folder} />
          {workingFolder ? <span className="max-w-[120px] truncate">{shortFolder(workingFolder)}</span> : null}
        </button>
        {panel === "folder" ? (
          <div className={pop}>
            <button type="button" className={item} onClick={() => { setWorkingFolder(""); setPanel(null); }}>
              <span className="flex-1 text-[var(--fx-muted)]">No folder</span>
              {!workingFolder ? <Icon d={I.check} className="h-3.5 w-3.5 text-[hsl(var(--primary))]" /> : null}
            </button>
            {folders.map((f) => (
              <button key={f.path} type="button" className={item} title={f.path} onClick={() => { setWorkingFolder(f.path); setPanel(null); }}>
                <Icon d={I.folder} />
                <span className="flex-1 truncate">{f.name}</span>
                {workingFolder === f.path ? <Icon d={I.check} className="h-3.5 w-3.5 text-[hsl(var(--primary))]" /> : null}
              </button>
            ))}
            {folders.length === 0 ? <div className="px-2 py-1.5 text-[11px] text-[var(--fx-muted)]">No folders under projects root</div> : null}
          </div>
        ) : null}
      </div>

      {/* Model (+ reasoning folded in) */}
      <div className="relative">
        <button type="button" className={btn} onClick={() => toggle("model")} title="Model (and reasoning effort)" aria-label="Model">
          <Icon d={I.cpu} />
          <span className="max-w-[110px] truncate">{shortModel(model)}</span>
          {reasoningCapable && reasoning ? <span className="text-[10px] text-[var(--fx-muted)]">·{reasoning}</span> : null}
          <Icon d={I.chevron} className="h-3 w-3 opacity-50" />
        </button>
        {panel === "model" ? (
          <div className={`${pop} min-w-[230px]`}>
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--fx-muted)]">Model</div>
            {models.length === 0 ? <div className="px-2 py-1.5 text-[11px] text-[var(--fx-muted)]">No models configured</div> : null}
            {models.map((m) => (
              <button key={m.value} type="button" className={item} onClick={() => setModel(m.value)}>
                <Icon d={I.cpu} />
                <span className="flex-1 truncate">{m.label}</span>
                {model === m.value ? <Icon d={I.check} className="h-3.5 w-3.5 text-[hsl(var(--primary))]" /> : null}
              </button>
            ))}
            {reasoningCapable ? (
              <>
                <div className="mt-1 border-t border-[var(--ui-border)] px-2 pb-1 pt-1.5 text-[10px] uppercase tracking-wide text-[var(--fx-muted)]">Reasoning effort</div>
                <div className="flex gap-1 px-1 pb-1">
                  {(["", "low", "medium", "high"] as const).map((r) => (
                    <button
                      key={r || "default"}
                      type="button"
                      onClick={() => setReasoning(r)}
                      className={`flex-1 rounded-md px-1.5 py-1 text-[11px] ${
                        reasoning === r
                          ? "bg-[hsl(var(--primary)/0.18)] text-[var(--foreground)] border border-[hsl(var(--primary)/0.4)]"
                          : "border border-[var(--ui-border)] text-[var(--fx-muted)] hover:bg-[var(--fx-nav-hover)]"
                      }`}
                    >
                      {r || "default"}
                    </button>
                  ))}
                </div>
              </>
            ) : null}
          </div>
        ) : null}
      </div>

      <span className="mx-0.5 h-5 w-px bg-[var(--ui-border)]" aria-hidden />

      {/* MCP */}
      <div className="relative">
        <button type="button" className={mcpIds.length ? btn : btnMuted} onClick={() => toggle("mcp")} title="MCP servers / tools" aria-label="MCP servers">
          <Icon d={I.plug} />
          {mcpIds.length ? <span className={badge}>{mcpIds.length}</span> : null}
        </button>
        {panel === "mcp" ? (
          <div className={pop}>
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--fx-muted)]">MCP servers</div>
            {mcpServers.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] text-[var(--fx-muted)]">No configured MCP servers</div>
            ) : (
              mcpServers.map((s) => (
                <label key={s.id} className={`${item} cursor-pointer`}>
                  <input type="checkbox" className="accent-[hsl(var(--primary))]" checked={mcpIds.includes(s.id)} onChange={() => toggleId(mcpIds, s.id, setMcpIds)} />
                  <span className="flex-1 truncate">{s.name}</span>
                </label>
              ))
            )}
          </div>
        ) : null}
      </div>

      {/* Skills */}
      <div className="relative">
        <button type="button" className={skillIds.length ? btn : btnMuted} onClick={() => toggle("skills")} title="Skills" aria-label="Skills">
          <Icon d={I.spark} />
          {skillIds.length ? <span className={badge}>{skillIds.length}</span> : null}
        </button>
        {panel === "skills" ? (
          <div className={pop}>
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--fx-muted)]">Skills</div>
            {skills.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] text-[var(--fx-muted)]">No enabled skills</div>
            ) : (
              skills.map((s) => (
                <label key={s.id} className={`${item} cursor-pointer`}>
                  <input type="checkbox" className="accent-[hsl(var(--primary))]" checked={skillIds.includes(s.id)} onChange={() => toggleId(skillIds, s.id, setSkillIds)} />
                  <span className="flex-1 truncate">{s.name}</span>
                </label>
              ))
            )}
          </div>
        ) : null}
      </div>

      {/* Phase 2 placeholders */}
      <button type="button" disabled title="Attach files (coming soon)" className="inline-flex items-center rounded-lg border border-[var(--ui-border)] px-2 py-1.5 text-[var(--fx-muted)] opacity-40" aria-label="Attach files">
        <Icon d={I.clip} />
      </button>
      <button type="button" disabled title="Voice input (coming soon)" className="inline-flex items-center rounded-lg border border-[var(--ui-border)] px-2 py-1.5 text-[var(--fx-muted)] opacity-40" aria-label="Voice input">
        <Icon d={I.mic} />
      </button>
    </div>
  );
}
