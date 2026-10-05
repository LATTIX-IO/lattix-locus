"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { describeSaveError } from "@/components/settings/settings-kit";
import {
  connectIntegrationOAuth,
  getIntegrationCatalog,
  getIntegrations,
  installCatalogIntegration,
  type IntegrationCatalogEntry,
} from "@/lib/api";
import { useIsDesktopShell } from "@/lib/desktop-shell";
import type { IntegrationDefinition } from "@/types/locus";

/** Where the provider sends the principal back after signing in. */
export const CATALOG_OAUTH_RETURN_PATH = "/library/connections?oauth_panel=1";

type ConnectionState = {
  label: string;
  variant: "success" | "warning" | "secondary" | "destructive" | "outline";
  glyph: string;
};

type AddStep = "review" | "adding" | "signing-in" | "done" | "error";

type AddFlow = {
  entry: IntegrationCatalogEntry;
  step: AddStep;
  message: string | null;
};

const AUTH_EXPLANATION: Record<string, string> = {
  none: "No sign-in. It runs with what the platform allows it to reach.",
  oauth2: "You sign in with the provider in your browser. Locus keeps the tokens; you never paste a password here.",
  bearer: "Needs an access token. Store it as a native secret, then name that secret in Advanced.",
  api_key: "Needs an API key. Store it as a native secret, then name that secret in Advanced.",
  basic: "Needs a user name and password secret. Set them up in Advanced.",
};

function protocolOf(entry: IntegrationCatalogEntry): "MCP" | "API" {
  return String(entry.metadata_json?.protocol ?? "http") === "mcp" ? "MCP" : "API";
}

function installedFor(entry: IntegrationCatalogEntry, integrations: IntegrationDefinition[]): IntegrationDefinition | null {
  return (
    integrations.find((item) => String((item.metadata_json as { catalog_id?: unknown } | undefined)?.catalog_id ?? "") === entry.catalog_id) ??
    null
  );
}

/** The connection status as the backend reports it; never inferred upward. */
export function catalogConnectionState(entry: IntegrationCatalogEntry, installed: IntegrationDefinition | null): ConnectionState {
  if (!installed && !entry.installed) {
    return { label: "Not added", variant: "outline", glyph: "○" };
  }
  if (!installed) {
    return { label: "Added", variant: "secondary", glyph: "●" };
  }
  if (installed.status === "error") {
    return { label: "Error", variant: "destructive", glyph: "■" };
  }
  if (installed.auth_type === "oauth2") {
    const oauth = installed.oauth_status;
    if (oauth?.connected) return { label: "Connected", variant: "success", glyph: "●" };
    if (oauth?.pending) return { label: "Sign-in pending", variant: "warning", glyph: "◐" };
    return { label: "Not signed in", variant: "warning", glyph: "◐" };
  }
  if (installed.auth_type !== "none" && !installed.secret_configured && installed.status !== "configured") {
    return { label: "Needs a credential", variant: "warning", glyph: "◐" };
  }
  return { label: installed.status === "configured" ? "Ready" : "Added", variant: "success", glyph: "●" };
}

function isMissingOauthApp(error: unknown): boolean {
  return error instanceof Error && /oauth2 auth metadata is missing/i.test(error.message);
}

/**
 * The one-click way to connect an MCP server or API from the vetted catalog:
 * pick an entry, review what it will access and how it signs in, then Add.
 * Adding is widening, so lib/api.ts sends it through the desktop shell's
 * confirmation (integration.catalog.install, LOCUS-357); OAuth servers then
 * open the provider's sign-in (integration.oauth.connect). Every outcome,
 * including a cancelled or failed confirmation, is shown in the dialog.
 */
export function ConnectionCatalog({
  integrations: providedIntegrations,
  onChanged,
}: {
  /** The saved integrations when the parent already loads them (their status is shown). */
  integrations?: IntegrationDefinition[];
  onChanged?: () => void;
}) {
  const isDesktop = useIsDesktopShell();
  const [catalog, setCatalog] = useState<IntegrationCatalogEntry[] | null>(null);
  const [loadedIntegrations, setLoadedIntegrations] = useState<IntegrationDefinition[]>([]);
  const integrations = providedIntegrations ?? loadedIntegrations;
  const ownsIntegrations = providedIntegrations === undefined;
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const [flow, setFlow] = useState<AddFlow | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getIntegrationCatalog(), ownsIntegrations ? getIntegrations() : Promise.resolve(null)])
      .then(([entries, items]) => {
        if (cancelled) return;
        setCatalog(entries);
        if (items) setLoadedIntegrations(items);
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLoadError(reason instanceof Error ? reason.message : "Could not load the connection catalog.");
      });
    return () => {
      cancelled = true;
    };
  }, [ownsIntegrations, token]);

  const reload = useCallback(() => setToken((value) => value + 1), []);

  async function add(entry: IntegrationCatalogEntry) {
    setFlow({ entry, step: "adding", message: null });
    let integrationId: string;
    try {
      const result = await installCatalogIntegration(entry.catalog_id);
      integrationId = result.id;
    } catch (error) {
      setFlow({ entry, step: "error", message: describeSaveError(error, `Could not add ${entry.name}.`) });
      return;
    }
    reload();
    onChanged?.();

    if (entry.auth_type !== "oauth2") {
      setFlow({
        entry,
        step: "done",
        message:
          entry.auth_type === "none"
            ? `${entry.name} is added.`
            : `${entry.name} is added. Name its credential secret in Advanced to finish connecting.`,
      });
      return;
    }

    setFlow({ entry, step: "signing-in", message: null });
    try {
      const response = await connectIntegrationOAuth(integrationId, { return_to: CATALOG_OAUTH_RETURN_PATH });
      if (response.mode === "authorization_code" && response.connect_url) {
        setFlow({ entry, step: "signing-in", message: `Opening ${entry.name} sign-in…` });
        window.location.assign(response.connect_url);
        return;
      }
      setFlow({ entry, step: "done", message: `${entry.name} is connected.` });
      reload();
      onChanged?.();
    } catch (error) {
      setFlow({
        entry,
        step: "error",
        message: isMissingOauthApp(error)
          ? `${entry.name} is added, but it needs an OAuth app (client ID and sign-in URLs) before you can sign in. Set it up in Advanced, then use Connect.`
          : `${entry.name} is added, but sign-in did not start: ${describeSaveError(error, "unknown error")}`,
      });
    }
  }

  if (loadError) {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px]">
        <span>Could not load the connection catalog: {loadError}</span>
        <Button variant="secondary" size="sm" onClick={reload}>
          Retry
        </Button>
      </div>
    );
  }
  if (!catalog) {
    return (
      <p role="status" className="text-[13px] text-muted-foreground">
        Loading the connection catalog…
      </p>
    );
  }

  const entry = flow?.entry ?? null;
  const busy = flow?.step === "adding" || flow?.step === "signing-in";

  return (
    <section aria-label="Connection catalog" className="flex flex-col gap-3">
      {catalog.length === 0 ? (
        <p className="text-[13px] text-muted-foreground">The catalog is empty. Use Advanced to add a custom connection.</p>
      ) : (
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {catalog.map((item) => {
            const installed = installedFor(item, integrations);
            const state = catalogConnectionState(item, installed);
            return (
              <li key={item.catalog_id} className="flex flex-col gap-2 rounded-[10px] border border-border bg-card p-3 text-xs">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-[13px] font-medium">{item.name}</p>
                  <Badge variant="outline">{protocolOf(item)}</Badge>
                </div>
                <p className="truncate text-muted-foreground">{item.capabilities.join(", ")}</p>
                <div className="mt-auto flex items-center justify-between gap-2">
                  <Badge variant={state.variant} aria-label={`${item.name}: ${state.label}`}>
                    <span aria-hidden="true">{state.glyph}</span>
                    {state.label}
                  </Badge>
                  {installed || item.installed ? null : (
                    <Button size="sm" variant="secondary" onClick={() => setFlow({ entry: item, step: "review", message: null })}>
                      Add
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <Dialog open={flow !== null} onOpenChange={(open) => (!open && !busy ? setFlow(null) : undefined)}>
        <DialogContent>
          {entry ? (
            <>
              <DialogHeader>
                <DialogTitle>Add {entry.name}?</DialogTitle>
                <DialogDescription>
                  {protocolOf(entry) === "MCP" ? "An MCP server" : "An API"} from {entry.publisher === "first_party" ? "Lattix" : "a third party"}. Review what it may do before you add it.
                </DialogDescription>
              </DialogHeader>
              <dl className="grid gap-2 text-[13px]">
                <div>
                  <dt className="font-medium">What it will access</dt>
                  <dd className="text-muted-foreground">{entry.capabilities.length ? entry.capabilities.join(", ") : "Nothing declared."}</dd>
                </div>
                <div>
                  <dt className="font-medium">Network</dt>
                  <dd className="text-muted-foreground">
                    {entry.egress_allowlist.length
                      ? `Reaches ${entry.egress_allowlist.join(", ")}.`
                      : entry.base_url
                        ? `Reaches ${entry.base_url}.`
                        : "Runs on this machine; reaches only what the platform allowlist permits."}
                  </dd>
                </div>
                <div>
                  <dt className="font-medium">How it signs in</dt>
                  <dd className="text-muted-foreground">{AUTH_EXPLANATION[entry.auth_type] ?? entry.auth_type}</dd>
                </div>
              </dl>
              {isDesktop && flow?.step === "review" ? (
                <p className="text-xs text-muted-foreground">Locus asks you to confirm in its own window before anything is added.</p>
              ) : null}
              {flow?.message ? (
                <p role={flow.step === "error" ? "alert" : "status"} className={flow.step === "error" ? "text-[13px] text-destructive" : "text-[13px] text-muted-foreground"}>
                  {flow.message}
                </p>
              ) : flow?.step === "adding" ? (
                <p role="status" className="text-[13px] text-muted-foreground">
                  {isDesktop ? "Waiting for your confirmation…" : "Adding…"}
                </p>
              ) : flow?.step === "signing-in" ? (
                <p role="status" className="text-[13px] text-muted-foreground">Starting sign-in…</p>
              ) : null}
              <DialogFooter>
                {flow?.step === "review" ? (
                  <>
                    <Button variant="secondary" onClick={() => setFlow(null)}>
                      Cancel
                    </Button>
                    <Button onClick={() => void add(entry)}>
                      {entry.auth_type === "oauth2" ? `Add and sign in${isDesktop ? "…" : ""}` : `Add ${entry.name}${isDesktop ? "…" : ""}`}
                    </Button>
                  </>
                ) : (
                  <Button variant="secondary" disabled={busy} onClick={() => setFlow(null)}>
                    Done
                  </Button>
                )}
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}
