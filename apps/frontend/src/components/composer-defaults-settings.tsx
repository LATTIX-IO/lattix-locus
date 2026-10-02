"use client";

import { useEffect, useState } from "react";
import {
  getModelsOverview,
  getUserSettings,
  getWorkspaceFolders,
  saveUserSettings,
  type UserSettings,
} from "@/lib/api";

function isReasoningModel(value: string): boolean {
  return /gpt-oss|o1|o3|o4|gpt-5|reason|think|deepseek-r/i.test(value);
}

export function ComposerDefaultsSettings() {
  const [settings, setSettings] = useState<UserSettings>({
    default_working_folder: "",
    preferred_model: "",
    preferred_reasoning_effort: "",
    default_mode: "execute",
  });
  const [folders, setFolders] = useState<{ name: string; path: string }[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [status, setStatus] = useState<"" | "saving" | "saved" | "error">("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [s, folderList, overview] = await Promise.all([
        getUserSettings(),
        getWorkspaceFolders().catch(() => null),
        getModelsOverview().catch(() => null),
      ]);
      if (cancelled) return;
      setSettings(s);
      if (folderList) setFolders(folderList.folders ?? []);
      const ov = overview as unknown as {
        providers?: { ollama?: { available?: boolean; installed_models?: { id: string }[] } };
        external?: { id: string; configured: boolean; default_model: string }[];
      } | null;
      const opts: string[] = [];
      if (ov?.providers?.ollama?.available) {
        for (const m of ov.providers.ollama.installed_models ?? []) opts.push(`ollama/${m.id}`);
      }
      for (const p of ov?.external ?? []) {
        if (p.configured && p.default_model)
          opts.push(p.id === "openai" ? p.default_model : `${p.id}/${p.default_model}`);
      }
      setModels(opts);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    setStatus("saving");
    try {
      const saved = await saveUserSettings(settings);
      setSettings(saved);
      setStatus("saved");
      setTimeout(() => setStatus(""), 2000);
    } catch {
      setStatus("error");
    }
  }

  const reasoningCapable = isReasoningModel(settings.preferred_model);

  return (
    <article id="section-composer-defaults" className="fx-panel p-3 scroll-mt-32">
      <h2 className="text-sm font-semibold">Composer defaults</h2>
      <p className="fx-muted text-xs">
        Your personal defaults for the chat composer — working folder, model, reasoning, and mode.
      </p>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <label className="block text-xs">
          Default working folder
          <select
            className="fx-field mt-1 w-full px-2 py-2 text-sm"
            value={settings.default_working_folder}
            onChange={(e) => setSettings({ ...settings, default_working_folder: e.target.value })}
          >
            <option value="">None</option>
            {folders.map((f) => (
              <option key={f.path} value={f.path}>
                {f.name}
              </option>
            ))}
            {settings.default_working_folder &&
            !folders.some((f) => f.path === settings.default_working_folder) ? (
              <option value={settings.default_working_folder}>{settings.default_working_folder}</option>
            ) : null}
          </select>
        </label>
        <label className="block text-xs">
          Preferred model
          <select
            className="fx-field mt-1 w-full px-2 py-2 text-sm"
            value={settings.preferred_model}
            onChange={(e) => setSettings({ ...settings, preferred_model: e.target.value })}
          >
            <option value="">Auto (agent default)</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs">
          Default mode
          <select
            className="fx-field mt-1 w-full px-2 py-2 text-sm"
            value={settings.default_mode}
            onChange={(e) =>
              setSettings({ ...settings, default_mode: e.target.value as UserSettings["default_mode"] })
            }
          >
            <option value="chat">Chat</option>
            <option value="plan">Plan</option>
            <option value="execute">Execute</option>
          </select>
        </label>
        <label className="block text-xs">
          Reasoning effort {reasoningCapable ? "" : "(reasoning models only)"}
          <select
            className="fx-field mt-1 w-full px-2 py-2 text-sm"
            value={settings.preferred_reasoning_effort}
            disabled={!reasoningCapable}
            onChange={(e) =>
              setSettings({
                ...settings,
                preferred_reasoning_effort: e.target.value as UserSettings["preferred_reasoning_effort"],
              })
            }
          >
            <option value="">Default</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button type="button" className="fx-btn-primary px-3 py-1.5 text-xs" onClick={() => void save()} disabled={status === "saving"}>
          {status === "saving" ? "Saving…" : "Save composer defaults"}
        </button>
        {status === "saved" ? <span className="text-xs text-[var(--fx-success,#3fb950)]">Saved</span> : null}
        {status === "error" ? <span className="text-xs text-[var(--fx-danger)]">Save failed</span> : null}
      </div>
    </article>
  );
}
