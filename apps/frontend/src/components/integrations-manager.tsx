"use client";

import { useEffect, useState, useCallback } from "react";
import {
  deleteIntegration,
  getIntegrationCatalog,
  getIntegrations,
  installCatalogIntegration,
  saveIntegration,
  testIntegration,
  oauthAuthorize,
  oauthDisconnect,
  type IntegrationCatalogEntry,
} from "@/lib/api";
import type { IntegrationDefinition } from "@/types/frontier";

type LastTestMetadata = {
  at?: string;
  ok?: boolean;
  warnings?: string[];
  checks?: Record<string, boolean>;
};

type ApiKeyLocation = "header" | "query";
type OauthGrantType = "client_credentials" | "authorization_code";

function readOAuthTokens(metadata: Record<string, unknown> | undefined): { connected: boolean; hasRefreshToken: boolean } {
  if (!metadata || typeof metadata !== "object") {
    return { connected: false, hasRefreshToken: false };
  }
  const tokens = metadata.tokens;
  if (!tokens || typeof tokens !== "object") {
    return { connected: false, hasRefreshToken: false };
  }
  const accessToken = String((tokens as Record<string, unknown>).access_token ?? "").trim();
  const refreshToken = String((tokens as Record<string, unknown>).refresh_token ?? "").trim();
  const expiresAt = String((tokens as Record<string, unknown>).expires_at ?? "").trim();
  const now = new Date().toISOString();
  const connected = !!accessToken && (!expiresAt || expiresAt > now);
  return { connected, hasRefreshToken: !!refreshToken };
}

function readLastTest(metadata: Record<string, unknown> | undefined): LastTestMetadata | null {
  if (!metadata || typeof metadata !== "object") {
    return null;
  }
  const raw = metadata.last_test;
  if (!raw || typeof raw !== "object") {
    return null;
  }
  return raw as LastTestMetadata;
}

function readAuthConfig(metadata: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!metadata || typeof metadata !== "object") {
    return {};
  }
  const raw = metadata.auth;
  if (!raw || typeof raw !== "object") {
    return {};
  }
  return raw as Record<string, unknown>;
}

function authSummary(item: IntegrationDefinition): string {
  const auth = readAuthConfig(item.metadata_json);
  if (item.auth_type === "none") {
    return "No auth";
  }
  if (item.auth_type === "api_key") {
    const location = String(auth.location ?? "header");
    const keyName = String(auth.key_name ?? "x-api-key");
    return `API key via ${location} (${keyName})`;
  }
  if (item.auth_type === "bearer") {
    const prefix = String(auth.prefix ?? "Bearer");
    return `${prefix} token`;
  }
  if (item.auth_type === "basic") {
    const username = String(auth.username ?? "").trim();
    return username ? `Basic auth (${username})` : "Basic auth";
  }
  if (item.auth_type === "oauth2") {
    const grantType = String(auth.grant_type ?? "client_credentials");
    const clientId = String(auth.client_id ?? "").trim();
    return clientId ? `OAuth2 ${grantType} (${clientId})` : `OAuth2 ${grantType}`;
  }
  return item.auth_type;
}

export function IntegrationsManager() {
  const [items, setItems] = useState<IntegrationDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [type, setType] = useState<IntegrationDefinition["type"]>("http");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState<IntegrationDefinition["auth_type"]>("none");
  const [secretRef, setSecretRef] = useState("");
  const [apiKeyLocation, setApiKeyLocation] = useState<ApiKeyLocation>("header");
  const [apiKeyName, setApiKeyName] = useState("x-api-key");
  const [bearerPrefix, setBearerPrefix] = useState("Bearer");
  const [basicUsername, setBasicUsername] = useState("");
  const [oauthTokenUrl, setOauthTokenUrl] = useState("");
  const [oauthAuthUrl, setOauthAuthUrl] = useState("");
  const [oauthRedirectUri, setOauthRedirectUri] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [oauthGrantType, setOauthGrantType] = useState<OauthGrantType>("client_credentials");
  const [oauthScopes, setOauthScopes] = useState("");
  const [oauthAudience, setOauthAudience] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<IntegrationCatalogEntry[]>([]);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [showCustom, setShowCustom] = useState(false);
  const [connectingId, setConnectingId] = useState<string | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);

  const isOAuthIntegration = useCallback((item: IntegrationDefinition): boolean => {
    const auth = readAuthConfig(item.metadata_json);
    return item.auth_type === "oauth2" && String(auth.grant_type ?? "client_credentials") === "authorization_code";
  }, []);

  const getOAuthStatus = useCallback((item: IntegrationDefinition) => {
    return readOAuthTokens(item.metadata_json);
  }, []);

  async function refreshCatalog() {
    try {
      setCatalog(await getIntegrationCatalog());
    } catch {
      // Catalog is additive; the manager remains usable without it.
    }
  }

  useEffect(() => {
    void refreshCatalog();
  }, []);

  async function installFromCatalog(entry: IntegrationCatalogEntry) {
    setInstallingId(entry.catalog_id);
    try {
      const result = await installCatalogIntegration(entry.catalog_id);
      setStatusMessage(
        result.already_installed
          ? `${entry.name} is already installed.`
          : `${entry.name} added as a draft — configure its credentials below.`,
      );
      await Promise.all([refresh(), refreshCatalog()]);
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : `Unable to install ${entry.name}.`);
    } finally {
      setInstallingId(null);
    }
  }

  async function refresh() {
    setLoading(true);
    try {
      const integrations = await getIntegrations();
      setItems(integrations);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (authType === "none") {
      setSecretRef("");
    }
  }, [authType]);

  function buildAuthMetadata(): Record<string, unknown> {
    if (authType === "none") {
      return { method: "none" };
    }
    if (authType === "api_key") {
      return {
        method: "api_key",
        location: apiKeyLocation,
        key_name: apiKeyName.trim() || "x-api-key",
      };
    }
    if (authType === "bearer") {
      return {
        method: "bearer",
        prefix: bearerPrefix.trim() || "Bearer",
      };
    }
    if (authType === "basic") {
      return {
        method: "basic",
        username: basicUsername.trim(),
      };
    }
    return {
      method: "oauth2",
      grant_type: oauthGrantType,
      token_url: oauthTokenUrl.trim(),
      authorization_url: oauthAuthUrl.trim(),
      redirect_uri: oauthRedirectUri.trim(),
      client_id: oauthClientId.trim(),
      client_secret: oauthClientSecret.trim(),
      scopes: oauthScopes
        .split(/[\s,]+/)
        .map((value) => value.trim())
        .filter(Boolean),
      audience: oauthAudience.trim(),
    };
  }

  function resetForm() {
    setName("");
    setType("http");
    setBaseUrl("");
    setAuthType("none");
    setSecretRef("");
    setApiKeyLocation("header");
    setApiKeyName("x-api-key");
    setBearerPrefix("Bearer");
    setBasicUsername("");
    setOauthTokenUrl("");
    setOauthAuthUrl("");
    setOauthRedirectUri("");
    setOauthClientId("");
    setOauthClientSecret("");
    setOauthGrantType("client_credentials");
    setOauthScopes("");
    setOauthAudience("");
  }

  async function handleCreate() {
    setStatusMessage("");
    const metadata_json: Record<string, unknown> = {
      auth: buildAuthMetadata(),
    };

    await saveIntegration({
      name: name.trim() || "Untitled Integration",
      type,
      base_url: baseUrl,
      auth_type: authType,
      secret_ref: authType === "none" ? "" : secretRef.trim(),
      status: "draft",
      metadata_json,
    });
    resetForm();
    setShowCustom(false);
    await refresh();
    setStatusMessage("Integration saved.");
  }

  async function handleTest(id: string) {
    setTestingId(id);
    try {
      const result = await testIntegration(id);
      const warnings = result.diagnostics?.warnings ?? [];
      const warningSuffix = warnings.length > 0 ? ` • warnings: ${warnings.join("; ")}` : "";
      setStatusMessage(`${result.message}${warningSuffix}`);
      await refresh();
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(id: string) {
    await deleteIntegration(id);
    await refresh();
    setStatusMessage("Integration removed.");
  }

  async function handleOAuthConnect(id: string) {
    setConnectingId(id);
    setStatusMessage("");
    try {
      const result = await oauthAuthorize(id);
      const authorizeUrl = result.authorize_url;
      const popup = window.open(
        authorizeUrl,
        "oauth-popup",
        "width=600,height=700,scrollbars=yes,resizable=yes"
      );
      if (!popup) {
        setStatusMessage("Popup blocked. Please allow popups for this site.");
        setConnectingId(null);
        return;
      }
      const checkClosed = setInterval(() => {
        if (popup.closed) {
          clearInterval(checkClosed);
          setConnectingId(null);
          void refresh();
        }
      }, 500);
      setTimeout(() => clearInterval(checkClosed), 5 * 60 * 1000);
    } catch (err) {
      setConnectingId(null);
      setStatusMessage(err instanceof Error ? err.message : "Failed to start OAuth flow");
    }
  }

  async function handleOAuthDisconnect(id: string) {
    setDisconnectingId(id);
    setStatusMessage("");
    try {
      await oauthDisconnect(id);
      await refresh();
      setStatusMessage("Integration disconnected.");
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : "Failed to disconnect integration");
    } finally {
      setDisconnectingId(null);
    }
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Integrations</h1>
          <p className="fx-muted">Connect MCP servers and APIs for tools, data stores, and queues.</p>
        </div>
        <button
          type="button"
          className="fx-btn-primary px-3 py-2 text-sm font-medium"
          onClick={() => {
            setShowCustom((v) => !v);
            setStatusMessage("");
          }}
        >
          {showCustom ? "Close" : "Add Custom"}
        </button>
      </header>

      <div className="fx-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide">Catalog — MCP servers &amp; APIs</h2>
          <span className="fx-muted text-xs">Preloaded, vetted entries. Credentials are configured after install.</span>
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {catalog.map((entry) => {
            const protocol = String(entry.metadata_json?.protocol ?? "http");
            return (
              <div key={entry.catalog_id} className="border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2.5 text-xs">
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-[var(--foreground)]">{entry.name}</p>
                  <span className="fx-muted rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[10px] uppercase">
                    {protocol === "mcp" ? "MCP" : "API"}
                  </span>
                </div>
                <p className="fx-muted mt-1 truncate">{entry.capabilities.join(", ")}</p>
                <div className="mt-2 flex items-center justify-between">
                  <span className="fx-muted text-[10px] uppercase">{entry.auth_type === "none" ? "no auth" : entry.auth_type}</span>
                  {entry.installed ? (
                    <span className="text-[hsl(var(--state-success))]">Installed</span>
                  ) : (
                    <button
                      type="button"
                      disabled={installingId === entry.catalog_id}
                      onClick={() => void installFromCatalog(entry)}
                      className="fx-btn-secondary px-2 py-1 text-[11px] font-medium disabled:opacity-60"
                    >
                      {installingId === entry.catalog_id ? "Adding..." : "Add"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
          {catalog.length === 0 ? <p className="fx-muted text-xs">Catalog unavailable.</p> : null}
        </div>
      </div>

      {showCustom ? (
      <div className="fx-panel p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide">Connect an API or MCP server</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block text-sm">
            Name
            <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={name} onChange={(event) => setName(event.target.value)} placeholder="Salesforce API" />
          </label>
          <label className="block text-sm">
            Type
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={type} onChange={(event) => setType(event.target.value as IntegrationDefinition["type"])}>
              <option value="http">HTTP API</option>
              <option value="database">Database</option>
              <option value="queue">Queue</option>
              <option value="vector">Vector Store</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          <label className="block text-sm md:col-span-2">
            Base URL / DSN
            <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1 or postgresql://..." />
          </label>
          <label className="block text-sm">
            Auth type
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={authType} onChange={(event) => setAuthType(event.target.value as IntegrationDefinition["auth_type"])}>
              <option value="none">None</option>
              <option value="api_key">API key</option>
              <option value="bearer">Bearer token</option>
              <option value="oauth2">OAuth2</option>
              <option value="basic">Basic</option>
            </select>
          </label>

          {authType === "none" ? (
            <div className="md:col-span-2 rounded-md border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3 text-xs text-[var(--foreground)]">
              No authentication selected. Secret fields are hidden.
            </div>
          ) : null}

          {authType === "api_key" ? (
            <>
              <label className="block text-sm">
                API key location
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={apiKeyLocation} onChange={(event) => setApiKeyLocation(event.target.value as ApiKeyLocation)}>
                  <option value="header">HTTP Header</option>
                  <option value="query">Query string</option>
                </select>
              </label>
              <label className="block text-sm">
                API key field name
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={apiKeyName}
                  onChange={(event) => setApiKeyName(event.target.value)}
                  placeholder="x-api-key"
                />
              </label>
            </>
          ) : null}

          {authType === "bearer" ? (
            <label className="block text-sm">
              Token prefix
              <input
                className="fx-field mt-1 w-full px-2 py-2 text-sm"
                value={bearerPrefix}
                onChange={(event) => setBearerPrefix(event.target.value)}
                placeholder="Bearer"
              />
            </label>
          ) : null}

          {authType === "basic" ? (
            <label className="block text-sm">
              Username
              <input
                className="fx-field mt-1 w-full px-2 py-2 text-sm"
                value={basicUsername}
                onChange={(event) => setBasicUsername(event.target.value)}
                placeholder="service-account"
              />
            </label>
          ) : null}

          {authType === "oauth2" ? (
            <>
              <label className="block text-sm md:col-span-2">
                Token URL
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthTokenUrl}
                  onChange={(event) => setOauthTokenUrl(event.target.value)}
                  placeholder="https://login.example.com/oauth2/token"
                />
              </label>
              <label className="block text-sm md:col-span-2">
                Authorization URL
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthAuthUrl}
                  onChange={(event) => setOauthAuthUrl(event.target.value)}
                  placeholder="https://login.example.com/oauth2/authorize"
                />
              </label>
              <label className="block text-sm md:col-span-2">
                Redirect URI
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthRedirectUri}
                  onChange={(event) => setOauthRedirectUri(event.target.value)}
                  placeholder="http://localhost:3000/integrations/callback"
                />
              </label>
              <label className="block text-sm">
                Client ID
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthClientId}
                  onChange={(event) => setOauthClientId(event.target.value)}
                  placeholder="frontier-client"
                />
              </label>
              <label className="block text-sm">
                Client Secret (optional - use secret reference instead)
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  type="password"
                  value={oauthClientSecret}
                  onChange={(event) => setOauthClientSecret(event.target.value)}
                  placeholder="••••••••"
                />
              </label>
              <label className="block text-sm">
                Grant type
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={oauthGrantType} onChange={(event) => setOauthGrantType(event.target.value as OauthGrantType)}>
                  <option value="client_credentials">Client credentials</option>
                  <option value="authorization_code">Authorization code</option>
                </select>
              </label>
              <label className="block text-sm">
                Scopes
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthScopes}
                  onChange={(event) => setOauthScopes(event.target.value)}
                  placeholder="read write admin"
                />
              </label>
              <label className="block text-sm">
                Audience (optional)
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthAudience}
                  onChange={(event) => setOauthAudience(event.target.value)}
                  placeholder="https://api.example.com"
                />
              </label>
            </>
          ) : null}

          {authType !== "none" ? (
            <label className="block text-sm md:col-span-2">
              Secret reference
              <input
                className="fx-field mt-1 w-full px-2 py-2 text-sm"
                value={secretRef}
                onChange={(event) => setSecretRef(event.target.value)}
                placeholder={
                  authType === "oauth2"
                    ? "secret/oauth/client-secret"
                    : authType === "basic"
                      ? "secret/db/password"
                      : "secret/integrations/service-token"
                }
              />
              <span className="mt-1 block text-[11px] fx-muted">
                Use a secret reference path (for example: <code>secret/team/name</code>) — do not paste raw credentials.
              </span>
            </label>
          ) : null}
        </div>

        <div className="mt-3 flex gap-2">
          <button onClick={handleCreate} className="fx-btn-primary px-3 py-2 text-sm">
            Save integration
          </button>
          <button
            type="button"
            onClick={() => {
              resetForm();
              setShowCustom(false);
            }}
            className="fx-btn-secondary px-3 py-2 text-sm"
          >
            Cancel
          </button>
        </div>
      </div>
      ) : null}

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide">Configured integrations</h2>
          <span className="fx-muted text-xs">{items.length} connected</span>
        </div>
        {loading ? (
          <p className="fx-muted text-sm">Loading integrations...</p>
        ) : items.length === 0 ? (
          <div className="fx-panel p-6 text-center text-sm fx-muted">
            No integrations connected yet. Install one from the catalog above, or use{" "}
            <span className="text-[var(--foreground)]">Add Custom</span> to connect an API or MCP server.
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
{items.map((item) => {
                const lastTest = readLastTest(item.metadata_json);
                const isMcp = String(readAuthConfig(item.metadata_json).protocol ?? item.metadata_json?.protocol ?? "") === "mcp"
                  || item.type === "custom";
                const isOAuth = isOAuthIntegration(item);
                const oauthStatus = getOAuthStatus(item);
                return (
                  <article key={item.id} className="fx-panel flex flex-col gap-2 p-3 text-xs">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate font-medium text-[var(--foreground)]">{item.name}</p>
                        <p className="fx-muted truncate font-mono text-[11px]">{item.base_url || "(no endpoint)"}</p>
                      </div>
                      <span className="fx-muted shrink-0 rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[10px] uppercase">
                        {isMcp ? "MCP" : item.type === "http" ? "API" : item.type}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] uppercase ${
                          item.status === "configured"
                            ? "border-[hsl(var(--state-success)/0.5)] text-[hsl(var(--state-success))]"
                            : item.status === "error"
                              ? "border-[hsl(var(--state-critical)/0.5)] text-[hsl(var(--state-critical))]"
                              : "border-[var(--ui-border)] fx-muted"
                        }`}
                      >
                        {item.status}
                      </span>
                      <span className="rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[10px] fx-muted">
                        {authSummary(item)}
                      </span>
                      {isOAuth && (
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] ${
                            oauthStatus.connected
                              ? "border-[hsl(var(--state-success)/0.5)] text-[hsl(var(--state-success))]"
                              : "border-[var(--ui-border)] fx-muted"
                          }`}
                        >
                          {oauthStatus.connected ? "Connected" : "Not connected"}
                        </span>
                      )}
                      {lastTest ? (
                        <span
                          className={`rounded-full border px-2 py-0.5 text-[10px] ${
                            lastTest.ok
                              ? "border-[hsl(var(--state-success)/0.5)] text-[hsl(var(--state-success))]"
                              : "border-[hsl(var(--state-critical)/0.5)] text-[hsl(var(--state-critical))]"
                          }`}
                        >
                          test {lastTest.ok ? "OK" : "failed"}
                        </span>
                      ) : null}
                    </div>
                    {item.secret_ref ? (
                      <p className="fx-muted font-mono text-[10px]">secret: {item.secret_ref}</p>
                    ) : null}
                    <div className="mt-auto flex gap-2 pt-1">
                      <button
                        onClick={() => handleTest(item.id)}
                        className="fx-btn-secondary px-2.5 py-1 text-[11px] font-medium disabled:opacity-60"
                        disabled={testingId === item.id}
                      >
                        {testingId === item.id ? "Testing…" : "Test"}
                      </button>
                      {isOAuth ? (
                        oauthStatus.connected ? (
                          <button
                            onClick={() => handleOAuthDisconnect(item.id)}
                            className="fx-btn-warning px-2.5 py-1 text-[11px] font-medium disabled:opacity-60"
                            disabled={disconnectingId === item.id}
                          >
                            {disconnectingId === item.id ? "Disconnecting…" : "Disconnect"}
                          </button>
                        ) : (
                          <button
                            onClick={() => handleOAuthConnect(item.id)}
                            className="fx-btn-primary px-2.5 py-1 text-[11px] font-medium disabled:opacity-60"
                            disabled={connectingId === item.id}
                          >
                            {connectingId === item.id ? "Connecting…" : "Connect"}
                          </button>
                        )
                      ) : null}
                      <button
                        onClick={() => handleDelete(item.id)}
                        className="fx-btn-warning px-2.5 py-1 text-[11px] font-medium"
                      >
                        Delete
                      </button>
                    </div>
                  </article>
                );
              })}
          </div>
        )}
      </div>

      {statusMessage ? <p className="text-xs text-[var(--foreground)]">{statusMessage}</p> : null}
    </section>
  );
}
