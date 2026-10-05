"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  LoadState,
  SaveBar,
  SectionHeader,
  SelectField,
  SettingsGroup,
  TextField,
  describeSaveError,
  useDraft,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import {
  clearProviderKey,
  getModelsOverview,
  getProviderModels,
  getUserSettings,
  getWorkspaceFolders,
  pullLocalModel,
  saveUserSettings,
  setProviderKey,
  type ModelsOverview,
  type UserSettings,
} from "@/lib/api";

/** Hosted and self-hosted providers (mirrors the backend registry); the
 * status of each comes from /models/overview. Keys go to the OS keychain. */
export const ENGINE_PROVIDERS: Array<{ id: string; label: string; keyHint: string; endpointHint: string; modelHint: string; keyRequired: boolean }> = [
  { id: "openai", label: "OpenAI", keyHint: "sk-…", endpointHint: "default endpoint", modelHint: "gpt-5.2", keyRequired: true },
  { id: "anthropic", label: "Anthropic Claude", keyHint: "sk-ant-…", endpointHint: "https://api.anthropic.com/v1", modelHint: "claude-sonnet-4-6", keyRequired: true },
  { id: "azure", label: "Microsoft Azure OpenAI", keyHint: "Azure API key", endpointHint: "https://<resource>.openai.azure.com/openai/v1", modelHint: "deployment name", keyRequired: true },
  { id: "google", label: "Google Gemini", keyHint: "AIza…", endpointHint: "Gemini OpenAI-compatible endpoint", modelHint: "gemini-2.5-pro", keyRequired: true },
  { id: "mistral", label: "Mistral", keyHint: "Mistral API key", endpointHint: "https://api.mistral.ai/v1", modelHint: "mistral-large-latest", keyRequired: true },
  { id: "xai", label: "xAI Grok", keyHint: "xai-…", endpointHint: "https://api.x.ai/v1", modelHint: "grok-4", keyRequired: true },
  { id: "nim", label: "NVIDIA NIM", keyHint: "nvapi-…", endpointHint: "blank = NVIDIA-hosted", modelHint: "meta/llama-3.3-70b-instruct", keyRequired: true },
  { id: "ollama", label: "Local (Ollama)", keyHint: "", endpointHint: "http://127.0.0.1:11434", modelHint: "llama3.2:3b", keyRequired: false },
];

/* ------------------------------ providers ------------------------------ */

function ProviderCard({
  provider,
  overview,
  stored,
  onSaveRoute,
  onKeyChanged,
}: {
  provider: (typeof ENGINE_PROVIDERS)[number];
  overview: ModelsOverview | null;
  stored: { base_url?: string; default_model?: string; api_key_configured?: boolean } | undefined;
  onSaveRoute: (providerId: string, route: { base_url: string; default_model: string }) => Promise<void>;
  onKeyChanged: () => void;
}) {
  const status = overview?.external.find((entry) => entry.id === provider.id);
  const [apiKey, setApiKey] = useState("");
  const [endpoint, setEndpoint] = useState(stored?.base_url ?? "");
  const [model, setModel] = useState(stored?.default_model ?? "");
  const [busy, setBusy] = useState<"" | "key" | "clear" | "route" | "test">("");
  const [note, setNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const keyId = `engine-${provider.id}-key`;
  const configured = Boolean(status?.configured);

  async function run(kind: typeof busy, action: () => Promise<string>) {
    setBusy(kind);
    setNote(null);
    try {
      setNote({ tone: "success", text: await action() });
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error) });
    } finally {
      setBusy("");
    }
  }

  return (
    <section aria-label={provider.label} className="flex flex-col gap-3 rounded-[12px] border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[13px] font-semibold">{provider.label}</h4>
        <Badge variant={configured ? "success" : "outline"}>
          <span aria-hidden="true">{configured ? "●" : "○"}</span>
          {configured ? (provider.id === "ollama" ? "Running" : "Ready") : "Not set up"}
        </Badge>
      </div>
      {provider.keyRequired ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={keyId}>API key</Label>
          <div className="flex gap-2">
            <Input
              id={keyId}
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={apiKey}
              placeholder={stored?.api_key_configured || configured ? "Stored. Enter a new key to replace it." : provider.keyHint}
              onChange={(event) => setApiKey(event.target.value)}
            />
            <Button
              variant="secondary"
              size="sm"
              className="h-9"
              disabled={!apiKey.trim() || busy !== ""}
              onClick={() =>
                void run("key", async () => {
                  await setProviderKey(provider.id, apiKey.trim());
                  setApiKey("");
                  onKeyChanged();
                  return "Key stored in the OS keychain.";
                })
              }
            >
              {busy === "key" ? "Saving…" : "Save key"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">Stored in the OS keychain and never shown again.</p>
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField id={`engine-${provider.id}-endpoint`} label="Endpoint" value={endpoint} onChange={setEndpoint} placeholder={provider.endpointHint} />
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`engine-${provider.id}-model`}>Default model</Label>
          <Input
            id={`engine-${provider.id}-model`}
            value={model}
            list={`engine-${provider.id}-models`}
            placeholder={provider.modelHint}
            onChange={(event) => setModel(event.target.value)}
          />
          <datalist id={`engine-${provider.id}-models`}>
            {models.map((item) => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={busy !== ""}
            onClick={() =>
              void run("test", async () => {
                const listing = await getProviderModels(provider.id);
                if (!listing.configured) return listing.reason || "Not set up yet.";
                setModels(listing.models);
                return listing.models.length ? `Connected: ${listing.models.length} models.` : listing.reason || "Connected, no models reported.";
              })
            }
          >
            {busy === "test" ? "Testing…" : "Test"}
          </Button>
          {provider.keyRequired && configured ? (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== ""}
              onClick={() =>
                void run("clear", async () => {
                  await clearProviderKey(provider.id);
                  onKeyChanged();
                  return "Key removed from the keychain.";
                })
              }
            >
              Remove key
            </Button>
          ) : null}
        </div>
        <Button
          size="sm"
          disabled={busy !== "" || (endpoint === (stored?.base_url ?? "") && model === (stored?.default_model ?? ""))}
          onClick={() =>
            void run("route", async () => {
              await onSaveRoute(provider.id, { base_url: endpoint.trim(), default_model: model.trim() });
              return "Saved.";
            })
          }
        >
          {busy === "route" ? "Saving…" : "Save"}
        </Button>
      </div>
      {note ? (
        <p role={note.tone === "error" ? "alert" : "status"} className={note.tone === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
          {note.text}
        </p>
      ) : null}
    </section>
  );
}

/* ---------------------------- local models ----------------------------- */

const POLL_WHILE_DOWNLOADING_MS = 4000;

export function LocalModelsPanel({ overview, onRefresh }: { overview: ModelsOverview | null; onRefresh: () => Promise<ModelsOverview | null> }) {
  const [pending, setPending] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const downloading = overview?.catalog.some((item) => item.pull?.status === "downloading") ?? false;

  useEffect(() => {
    if (!downloading) return;
    timer.current = setTimeout(() => void onRefresh(), POLL_WHILE_DOWNLOADING_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [downloading, onRefresh, overview]);

  async function enable(modelId: string) {
    setPending(modelId);
    setNote(null);
    try {
      await pullLocalModel(modelId);
      setNote(`Downloading ${modelId}. It shows as installed when ready.`);
      await onRefresh();
    } catch (error) {
      setNote(describeSaveError(error, "Could not start the download."));
    } finally {
      setPending(null);
    }
  }

  const ollama = overview?.providers.ollama;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px]">
        <Badge variant={ollama?.available ? "success" : "warning"}>
          <span aria-hidden="true">{ollama?.available ? "●" : "◐"}</span>
          {ollama?.available ? "Ollama running" : "Ollama not detected"}
        </Badge>{" "}
        <span className="text-muted-foreground">
          {ollama?.available
            ? `${ollama.installed_models.length} model${ollama.installed_models.length === 1 ? "" : "s"} installed. Nothing leaves this machine.`
            : "Install or start Ollama to run open-weight models on this machine."}
        </span>
      </p>
      {overview?.catalog.length ? (
        <div className="overflow-x-auto rounded-[10px] border border-border">
          <table className="w-full text-[13px]">
            <caption className="sr-only">Local model catalog</caption>
            <thead className="fx-table-head">
              <tr>
                <th scope="col" className="px-3 py-2 text-left">Model</th>
                <th scope="col" className="px-3 py-2 text-left">Size</th>
                <th scope="col" className="px-3 py-2 text-left">Min RAM</th>
                <th scope="col" className="px-3 py-2 text-left">Use as</th>
                <th scope="col" className="px-3 py-2 text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {overview.catalog.map((item) => {
                const isDownloading = item.pull?.status === "downloading";
                return (
                  <tr key={item.id} className="border-t border-border">
                    <td className="px-3 py-2">
                      <p className="font-medium">{item.label}</p>
                      <p className="text-xs text-muted-foreground">{item.notes}</p>
                    </td>
                    <td className="px-3 py-2 tabular-nums">{Number(item.size_gb ?? 0).toFixed(1)} GB</td>
                    <td className="px-3 py-2 tabular-nums">{item.min_ram_gb} GB</td>
                    <td className="px-3 py-2">
                      <code className="font-mono text-xs">{item.reference}</code>
                    </td>
                    <td className="px-3 py-2 text-right">
                      {item.installed ? (
                        <span className="text-xs">● Installed</span>
                      ) : isDownloading ? (
                        <span className="text-xs tabular-nums">◐ Downloading {item.pull?.progress_percent ?? 0}%</span>
                      ) : (
                        <Button
                          size="sm"
                          variant={item.pull?.status === "error" ? "secondary" : "default"}
                          disabled={pending === item.id || !ollama?.available}
                          onClick={() => void enable(item.id)}
                          title={item.pull?.status === "error" ? item.pull.detail : undefined}
                        >
                          {item.pull?.status === "error" ? "Retry download" : "Download"}
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-[13px] text-muted-foreground">No local models are in the catalog.</p>
      )}
      {note ? <p role="status" className="text-xs text-muted-foreground">{note}</p> : null}
    </div>
  );
}

/* ---------------------------- composer defaults ------------------------ */

function isReasoningModel(value: string): boolean {
  return /gpt-oss|o1|o3|o4|gpt-5|reason|think|deepseek-r/i.test(value);
}

function DefaultsGroup({ overview }: { overview: ModelsOverview | null }) {
  const [loaded, setLoaded] = useState<UserSettings | null>(null);
  const [folders, setFolders] = useState<{ name: string; path: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getUserSettings(), getWorkspaceFolders().catch(() => null)])
      .then(([settings, folderList]) => {
        if (cancelled) return;
        setLoaded(settings);
        setFolders(folderList?.folders ?? []);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load your defaults.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const { draft, dirty, saving, message, update, commit, reset } = useDraft<UserSettings>(loaded);

  const preferredModel = draft?.preferred_model ?? "";
  const modelOptions = useMemo(() => {
    const options: string[] = [];
    if (overview?.providers.ollama.available) {
      for (const item of overview.providers.ollama.installed_models) options.push(`ollama/${item.id}`);
    }
    for (const provider of overview?.external ?? []) {
      if (provider.configured && provider.default_model) {
        options.push(provider.id === "openai" ? provider.default_model : `${provider.id}/${provider.default_model}`);
      }
    }
    if (preferredModel && !options.includes(preferredModel)) options.unshift(preferredModel);
    return options;
  }, [preferredModel, overview]);

  if (!draft) {
    return <LoadState loading={!error} error={error} onRetry={() => setToken((value) => value + 1)} />;
  }

  const folderOptions = [
    { value: "__none__", label: "None" },
    ...folders.map((folder) => ({ value: folder.path, label: folder.name })),
    ...(draft.default_working_folder && !folders.some((folder) => folder.path === draft.default_working_folder)
      ? [{ value: draft.default_working_folder, label: draft.default_working_folder }]
      : []),
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 md:grid-cols-2">
        <SelectField
          id="default-model"
          label="Default model"
          value={draft.preferred_model || "__auto__"}
          onValueChange={(value) => update("preferred_model", value === "__auto__" ? "" : value)}
          options={[{ value: "__auto__", label: "Auto (agent default)" }, ...modelOptions.map((value) => ({ value, label: value }))]}
        />
        <SelectField
          id="default-mode"
          label="New chats start in"
          description="Plan and Execute let the agent do more without you switching modes."
          value={draft.default_mode}
          onValueChange={(value) => update("default_mode", value as UserSettings["default_mode"])}
          options={[
            { value: "chat", label: "Chat" },
            { value: "plan", label: "Plan" },
            { value: "execute", label: "Execute" },
          ]}
        />
        <SelectField
          id="default-folder"
          label="Default working folder"
          value={draft.default_working_folder || "__none__"}
          onValueChange={(value) => update("default_working_folder", value === "__none__" ? "" : value)}
          options={folderOptions}
        />
        <SelectField
          id="default-reasoning"
          label="Reasoning effort"
          description={isReasoningModel(draft.preferred_model) ? undefined : "Applies to reasoning models only."}
          value={draft.preferred_reasoning_effort || "__default__"}
          onValueChange={(value) => update("preferred_reasoning_effort", (value === "__default__" ? "" : value) as UserSettings["preferred_reasoning_effort"])}
          options={[
            { value: "__default__", label: "Default" },
            { value: "low", label: "Low" },
            { value: "medium", label: "Medium" },
            { value: "high", label: "High" },
          ]}
        />
      </div>
      <SaveBar
        dirty={dirty}
        saving={saving}
        message={message}
        onReset={reset}
        onSave={() => void commit(async (next) => void (await saveUserSettings(next)))}
      />
    </div>
  );
}

/* ------------------------------- section ------------------------------- */

export function EnginesSection() {
  const platform = usePlatformResource();
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  const refreshOverview = useCallback(async () => {
    try {
      const next = await getModelsOverview();
      setOverview(next);
      setOverviewError(null);
      return next;
    } catch (error) {
      setOverviewError(error instanceof Error ? error.message : "Could not load model providers.");
      return null;
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(refreshOverview);
  }, [refreshOverview]);

  async function saveRoute(providerId: string, route: { base_url: string; default_model: string }) {
    // Only the endpoint and default model: keys go to the keychain above.
    await platform.save({ ai_providers: { [providerId]: route } });
    await refreshOverview();
  }

  return (
    <div className="flex flex-col gap-4">
      <SectionHeader title="Engines" description="Where the agents think: local models, hosted providers, and your defaults." />
      {overviewError ? <LoadState loading={false} error={overviewError} onRetry={() => void refreshOverview()} /> : null}

      <SettingsGroup title="Local models" description="Open-weight models served by Ollama on this machine.">
        <LocalModelsPanel overview={overview} onRefresh={refreshOverview} />
      </SettingsGroup>

      <SettingsGroup title="Providers" description="Reference a model as provider/model; a bare id means OpenAI.">
        {platform.loading || platform.error ? (
          <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
        ) : (
          <div className="grid gap-3 xl:grid-cols-2">
            {ENGINE_PROVIDERS.map((provider) => (
              <ProviderCard
                key={provider.id}
                provider={provider}
                overview={overview}
                stored={platform.settings?.ai_providers?.[provider.id]}
                onSaveRoute={saveRoute}
                onKeyChanged={() => void refreshOverview()}
              />
            ))}
          </div>
        )}
      </SettingsGroup>

      <SettingsGroup title="Defaults" description="What a new chat uses unless you pick something else in the composer.">
        <DefaultsGroup overview={overview} />
      </SettingsGroup>
      {platform.confirmationDialog}
    </div>
  );
}
