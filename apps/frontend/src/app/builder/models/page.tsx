"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getModelsOverview, pullLocalModel, type ModelsOverview } from "@/lib/api";

const POLL_WHILE_DOWNLOADING_MS = 4000;

function formatGb(value: number): string {
  return `${Number(value ?? 0).toFixed(1)} GB`;
}

function ProviderBadge({ ok, okLabel, badLabel }: { ok: boolean; okLabel: string; badLabel: string }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
        ok
          ? "border-[hsl(var(--state-success)/0.45)] bg-[hsl(var(--state-success)/0.14)] text-[hsl(var(--state-success))]"
          : "border-[hsl(var(--state-warning)/0.45)] bg-[hsl(var(--state-warning)/0.14)] text-[hsl(var(--state-warning))]"
      }`}
    >
      {ok ? okLabel : badLabel}
    </span>
  );
}

export default function ModelsPage() {
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingModel, setPendingModel] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getModelsOverview();
      setOverview(data);
      setError(null);
      return data;
    } catch {
      setError("Unable to load model providers. Check that the backend is reachable.");
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      const data = await refresh();
      if (cancelled) {
        return;
      }
      const downloading = data?.catalog.some((item) => item.pull?.status === "downloading");
      if (downloading) {
        pollTimer.current = setTimeout(tick, POLL_WHILE_DOWNLOADING_MS);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (pollTimer.current) {
        clearTimeout(pollTimer.current);
      }
    };
  }, [refresh]);

  async function enableModel(modelId: string) {
    setPendingModel(modelId);
    setNotice(null);
    try {
      await pullLocalModel(modelId);
      setNotice(`Download started for ${modelId}. It will appear as installed when ready.`);
      const data = await refresh();
      if (data?.catalog.some((item) => item.pull?.status === "downloading") && !pollTimer.current) {
        pollTimer.current = setTimeout(() => void refresh(), POLL_WHILE_DOWNLOADING_MS);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to start the download.";
      setNotice(message);
    } finally {
      setPendingModel(null);
    }
  }

  const providers = overview?.providers;

  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Models</h1>
        <p className="fx-muted text-sm">
          Connect hosted providers or run open-weight models locally. Reference a model from an
          agent&apos;s <code className="font-mono text-xs">model_defaults.model</code> using the
          provider-qualified id shown below.
        </p>
      </header>

      {error ? <div className="fx-panel border-[hsl(var(--state-critical)/0.4)] p-3 text-sm">{error}</div> : null}

      <div className="fx-panel flex flex-wrap items-center justify-between gap-2 p-3">
        <p className="fx-muted text-xs">
          Showing connected providers only. Map additional providers (Anthropic, Azure OpenAI, Google
          Gemini, Mistral, xAI, NVIDIA NIM, ...) from platform settings.
        </p>
        <Link href="/builder/settings?tab=providers" className="fx-btn-secondary px-3 py-1.5 text-xs font-medium">
          Configure API connections
        </Link>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(overview?.external ?? [])
          .filter((entry) => entry.configured)
          .map((entry) => (
            <article key={entry.id} className="fx-panel p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-semibold">{entry.label}</h2>
                <ProviderBadge
                  ok
                  okLabel={entry.id === "ollama" ? "Runtime online" : "Configured"}
                  badLabel=""
                />
              </div>
              {entry.id === "ollama" ? (
                <>
                  <p className="fx-muted mt-2">
                    Open-weight models downloaded and served on this deployment. Nothing leaves the host.
                  </p>
                  <p className="mt-2">
                    Installed: <span className="font-semibold">{providers?.ollama.installed_models.length ?? 0}</span>
                  </p>
                </>
              ) : (
                <p className="fx-muted mt-2 break-all">
                  Endpoint: <code className="font-mono">{entry.base_url || "provider default"}</code>
                </p>
              )}
              <p className="mt-2 break-all">
                Use: <code className="font-mono">{entry.reference_example || "—"}</code>
              </p>
            </article>
          ))}
        {overview && (overview.external ?? []).every((entry) => !entry.configured) ? (
          <article className="fx-panel p-3 text-xs md:col-span-2 xl:col-span-3">
            <p className="fx-muted">
              No inference providers are connected yet. Open{" "}
              <Link href="/builder/settings?tab=providers" className="underline decoration-dotted underline-offset-2">
                Settings → AI Providers
              </Link>{" "}
              to add an API key, or start the local Ollama runtime for fully on-host models.
            </p>
          </article>
        ) : null}
      </div>

      <article className="fx-panel overflow-hidden">
        <div className="flex items-center justify-between border-b border-[var(--ui-border)] px-3 py-2">
          <h2 className="text-sm font-semibold">Local model catalog</h2>
          <span className="fx-muted text-xs">Curated allowlist — downloads run on the platform host</span>
        </div>
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Model</th>
              <th className="px-3 py-2 text-left">Download</th>
              <th className="px-3 py-2 text-left">Min host RAM</th>
              <th className="px-3 py-2 text-left">Use in agents as</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {(overview?.catalog ?? []).map((item) => {
              const downloading = item.pull?.status === "downloading";
              const failed = item.pull?.status === "error";
              return (
                <tr key={item.id} className="border-t border-[var(--fx-border)]">
                  <td className="px-3 py-2">
                    <p className="font-medium text-[var(--foreground)]">{item.label}</p>
                    <p className="fx-muted text-xs">{item.notes}</p>
                  </td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{formatGb(item.size_gb)}</td>
                  <td className="px-3 py-2 text-[var(--foreground)]">{item.min_ram_gb} GB</td>
                  <td className="px-3 py-2">
                    <code className="font-mono text-xs text-[var(--foreground)]">{item.reference}</code>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {item.installed ? (
                      <span className="text-[hsl(var(--state-success))]">Installed</span>
                    ) : downloading ? (
                      <span className="text-[hsl(var(--state-info))]">
                        Downloading {item.pull?.progress_percent ?? 0}%
                      </span>
                    ) : failed ? (
                      <span className="text-[hsl(var(--state-critical))]" title={item.pull?.detail}>
                        Failed — retry
                      </span>
                    ) : (
                      <span className="fx-muted">Not installed</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {item.installed ? (
                      <span className="fx-muted text-xs">Ready</span>
                    ) : (
                      <button
                        type="button"
                        disabled={downloading || pendingModel === item.id || !providers?.ollama.available}
                        onClick={() => void enableModel(item.id)}
                        className="fx-btn-primary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                      >
                        {downloading ? "Downloading..." : "Enable"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {!overview ? (
              <tr>
                <td colSpan={6} className="fx-muted px-3 py-4 text-sm">
                  Loading model catalog...
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </article>

      {notice ? <p className="fx-muted text-xs">{notice}</p> : null}
    </section>
  );
}
