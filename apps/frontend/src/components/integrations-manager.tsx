"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TypedDeleteButton } from "@/components/typed-delete-button";
import {
  connectIntegrationOAuth,
  disconnectIntegrationOAuth,
  getIntegrationOAuthStatus,
  getIntegrationStarterTemplates,
  getIntegrations,
  refreshIntegrationOAuth,
  saveIntegration,
  testIntegration,
} from "@/lib/api";
import { ConnectionCatalog } from "@/components/connection-catalog";
import { McpConnectionsPanel } from "@/components/mcp-connections-panel";
import type {
  IntegrationDefinition,
  IntegrationOAuthStatus,
  IntegrationStarterTemplate,
} from "@/types/locus";

type LastTestMetadata = {
  at?: string;
  ok?: boolean;
  warnings?: string[];
  checks?: Record<string, boolean>;
};

type ApiKeyLocation = "header" | "query";
type SupportedAuthType = IntegrationDefinition["auth_type"];
type OAuthProvider = "microsoft" | "google" | "salesforce" | "custom";
type OAuthGrantType = "authorization_code" | "client_credentials";

type OAuthGrantPreset = {
  authorizeUrl: string;
  tokenUrl: string;
  scopes: string[];
  audience: string;
  resource: string;
  tenant: string;
  guidance: string;
  notes: string[];
  clientIdPlaceholder: string;
  accountLabelPlaceholder: string;
  clientSecretPlaceholder: string;
  tokenSecretPlaceholder: string;
  refreshTokenSecretPlaceholder: string;
};

type OAuthPresetMetadata = {
  source: "provider-default";
  provider: Exclude<OAuthProvider, "custom">;
  grant_type: OAuthGrantType;
  recommended_auth: {
    authorize_url: string;
    token_url: string;
    scopes: string[];
    audience: string;
    resource: string;
    tenant: string;
    redirect_path: string;
  };
};

type OAuthProviderPreset = {
  label: string;
  summary: string;
  authorization_code: OAuthGrantPreset;
  client_credentials: OAuthGrantPreset;
};

const OAUTH_PROVIDER_PRESETS: Record<Exclude<OAuthProvider, "custom">, OAuthProviderPreset> = {
  microsoft: {
    label: "Microsoft",
    summary: "Microsoft Graph supports delegated user consent and tenant-wide daemon access through Entra ID.",
    authorization_code: {
      authorizeUrl: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
      tokenUrl: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
      scopes: ["User.Read", "Mail.ReadWrite", "offline_access"],
      audience: "https://graph.microsoft.com",
      resource: "",
      tenant: "common",
      guidance: "Use authorization code for delegated mailbox, calendar, Teams, and SharePoint access on behalf of a signed-in user.",
      notes: [
        "Keep offline_access when you need refresh tokens for background actions.",
        "Set a specific tenant instead of common once you know the production directory boundary.",
      ],
      clientIdPlaceholder: "locus-microsoft-client",
      accountLabelPlaceholder: "Customer Success shared mailbox",
      clientSecretPlaceholder: "secret/integrations/microsoft/client-secret",
      tokenSecretPlaceholder: "secret/integrations/microsoft/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/microsoft/refresh-token",
    },
    client_credentials: {
      authorizeUrl: "",
      tokenUrl: "https://login.microsoftonline.com/common/oauth2/v2.0/token",
      scopes: ["https://graph.microsoft.com/.default"],
      audience: "https://graph.microsoft.com",
      resource: "",
      tenant: "common",
      guidance: "Use client credentials for daemon-style tenant app permissions where no interactive user session is involved.",
      notes: [
        "Admin consent for Graph application permissions must already be granted in Entra ID.",
        "User-centric endpoints like /me do not work with client credentials.",
      ],
      clientIdPlaceholder: "locus-microsoft-client",
      accountLabelPlaceholder: "Tenant app",
      clientSecretPlaceholder: "secret/integrations/microsoft/client-secret",
      tokenSecretPlaceholder: "secret/integrations/microsoft/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/microsoft/refresh-token",
    },
  },
  google: {
    label: "Google",
    summary: "Google Workspace APIs are usually user-delegated; service-account style access is a separate pattern from generic client credentials.",
    authorization_code: {
      authorizeUrl: "https://accounts.google.com/o/oauth2/v2/auth",
      tokenUrl: "https://oauth2.googleapis.com/token",
      scopes: [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
      ],
      audience: "https://www.googleapis.com",
      resource: "",
      tenant: "",
      guidance: "Use authorization code for Gmail, Drive, Calendar, and other Workspace user data APIs that act on behalf of a signed-in user.",
      notes: [
        "Google adds refresh token behavior most reliably when offline access and consent prompting are requested.",
        "Domain-wide delegation via service accounts is a separate pattern and is better modeled as a custom provider flow if needed.",
      ],
      clientIdPlaceholder: "locus-google-client",
      accountLabelPlaceholder: "Workspace operations",
      clientSecretPlaceholder: "secret/integrations/google/client-secret",
      tokenSecretPlaceholder: "secret/integrations/google/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/google/refresh-token",
    },
    client_credentials: {
      authorizeUrl: "",
      tokenUrl: "https://oauth2.googleapis.com/token",
      scopes: [],
      audience: "https://www.googleapis.com",
      resource: "",
      tenant: "",
      guidance: "Generic client credentials is intentionally rejected for Google user-data connectors. Use authorization code unless you are brokering tokens through a separate custom provider flow.",
      notes: [
        "If you truly need server-to-server Google access, document whether you are using service-account impersonation outside this generic flow.",
        "Leave scopes empty here unless your broker expects a specific scope string for token minting.",
      ],
      clientIdPlaceholder: "locus-google-client",
      accountLabelPlaceholder: "Workspace backend sync",
      clientSecretPlaceholder: "secret/integrations/google/client-secret",
      tokenSecretPlaceholder: "secret/integrations/google/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/google/refresh-token",
    },
  },
  salesforce: {
    label: "Salesforce",
    summary: "Salesforce connected apps support both delegated user sessions and server-to-server application access.",
    authorization_code: {
      authorizeUrl: "https://login.salesforce.com/services/oauth2/authorize",
      tokenUrl: "https://login.salesforce.com/services/oauth2/token",
      scopes: ["api", "refresh_token", "offline_access"],
      audience: "https://login.salesforce.com",
      resource: "",
      tenant: "",
      guidance: "Use authorization code when the integration should act as a Salesforce user and respect that user’s sharing model.",
      notes: [
        "Sandbox orgs usually switch the host from login.salesforce.com to test.salesforce.com.",
        "Keep refresh_token or offline_access when you need long-lived background synchronization.",
      ],
      clientIdPlaceholder: "locus-salesforce-client",
      accountLabelPlaceholder: "Revenue operations",
      clientSecretPlaceholder: "secret/integrations/salesforce/client-secret",
      tokenSecretPlaceholder: "secret/integrations/salesforce/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/salesforce/refresh-token",
    },
    client_credentials: {
      authorizeUrl: "",
      tokenUrl: "https://login.salesforce.com/services/oauth2/token",
      scopes: ["api"],
      audience: "https://login.salesforce.com",
      resource: "",
      tenant: "",
      guidance: "Use client credentials for server-to-server connected apps where no user consent screen should appear at runtime.",
      notes: [
        "The Salesforce connected app must explicitly allow the client credentials flow.",
        "Server-to-server access is best for org-wide automation, not user-personalized views.",
      ],
      clientIdPlaceholder: "locus-salesforce-client",
      accountLabelPlaceholder: "Salesforce server-to-server app",
      clientSecretPlaceholder: "secret/integrations/salesforce/client-secret",
      tokenSecretPlaceholder: "secret/integrations/salesforce/access-token",
      refreshTokenSecretPlaceholder: "secret/integrations/salesforce/refresh-token",
    },
  },
};

function parseLineList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function formatLineList(values: string[] | undefined): string {
  return (values ?? []).join("\n");
}

function formatScopeList(values: string[] | undefined): string {
  return (values ?? []).join(" ");
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

function readOauthPresetMetadata(metadata: Record<string, unknown> | undefined): OAuthPresetMetadata | null {
  if (!metadata || typeof metadata !== "object") {
    return null;
  }
  const raw = metadata.oauth_preset;
  if (!raw || typeof raw !== "object") {
    return null;
  }
  const provider = String((raw as Record<string, unknown>).provider ?? "").trim() as OAuthProvider;
  const grantType = String((raw as Record<string, unknown>).grant_type ?? "").trim() as OAuthGrantType;
  const recommendedAuthRaw = (raw as Record<string, unknown>).recommended_auth;
  if (provider === "custom" || !provider || !grantType || !recommendedAuthRaw || typeof recommendedAuthRaw !== "object") {
    return null;
  }
  return raw as OAuthPresetMetadata;
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
    const provider = String(auth.provider ?? "custom").trim();
    const grantType = String(auth.grant_type ?? "client_credentials");
    const clientId = String(auth.client_id ?? "").trim();
    const lead = provider ? `${provider} OAuth2 ${grantType}` : `OAuth2 ${grantType}`;
    return clientId ? `${lead} (${clientId})` : lead;
  }
  return item.auth_type;
}

function integrationStatusTone(status: string): string {
  if (/configured|active|healthy|connected/i.test(status)) {
    return "border-[color-mix(in_srgb,var(--fx-success)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-success)_12%,transparent)] text-[var(--foreground)]";
  }
  if (/failed|error|blocked/i.test(status)) {
    return "border-[color-mix(in_srgb,var(--fx-danger)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] text-[var(--foreground)]";
  }
  return "border-[var(--ui-border)] bg-[hsl(var(--card))] text-[var(--foreground)]";
}

function starterAuthLabel(authType: SupportedAuthType): string {
  if (authType === "api_key") {
    return "API key";
  }
  if (authType === "basic") {
    return "Basic auth";
  }
  if (authType === "bearer") {
    return "Bearer token";
  }
  if (authType === "oauth2") {
    return "OAuth2";
  }
  return "No auth";
}

function readWindowOauthPanelState(): { integrationId: string | null; outcome: string } {
  if (typeof window === "undefined") {
    return { integrationId: null, outcome: "" };
  }
  const params = new URLSearchParams(window.location.search);
  const integrationId = params.get("integration_id");
  const panelEnabled = params.get("oauth_panel") === "1";
  const outcome = params.get("oauth") ?? "";
  if (!integrationId || (!panelEnabled && !outcome)) {
    return { integrationId: null, outcome: "" };
  }
  return { integrationId, outcome };
}

function clearWindowOauthPanelState(): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.delete("oauth_panel");
  url.searchParams.delete("oauth");
  url.searchParams.delete("integration_id");
  window.history.replaceState({}, "", url.pathname + url.search + url.hash);
}

function oauthOutcomeLabel(outcome: string): string {
  if (outcome === "connected") {
    return "Your account is connected.";
  }
  if (outcome === "error") {
    return "Sign-in did not finish. Review the message below and try again.";
  }
  if (outcome === "connecting") {
    return "Complete sign-in in the provider window, then return here.";
  }
  return "Review account access and next steps below.";
}

function oauthConnectionLabel(status: IntegrationOAuthStatus | null): string {
  if (status?.connected) {
    return "Connected";
  }
  if (status?.pending) {
    return "Pending authorization";
  }
  return "Not connected";
}

function oauthConnectionTone(status: IntegrationOAuthStatus | null): string {
  if (status?.connected) {
    return integrationStatusTone("connected");
  }
  if (status?.pending) {
    return integrationStatusTone("draft");
  }
  return integrationStatusTone("error");
}

type IntegrationHealth = {
  label: string;
  description: string;
  variant: "success" | "warning" | "destructive" | "outline";
  signIn: string;
  check: string;
};

export function integrationHealth(
  item: IntegrationDefinition,
  oauthStatus: IntegrationOAuthStatus | null,
  lastTest: LastTestMetadata | null,
): IntegrationHealth {
  const signIn =
    item.auth_type === "oauth2"
      ? oauthStatus?.connected
        ? "Signed in"
        : oauthStatus?.pending
          ? "Sign-in in progress"
          : "Sign-in needed"
      : item.auth_type === "none"
        ? "Not needed"
        : item.secret_configured
          ? "Credential saved"
          : "Credential needed";
  const check = lastTest ? (lastTest.ok ? "Passed" : "Failed") : item.status === "error" ? "Needs review" : "Not checked yet";

  if (item.status === "error" || lastTest?.ok === false) {
    const description =
      lastTest?.ok === false
        ? oauthStatus?.connected
          ? "Your account is signed in, but the last service check failed. Review the connection details or run the check again."
          : "The last service check failed. Review the setup, then run the check again."
        : lastTest?.ok === true
          ? "The last check passed, but Locus still reports a setup issue. Review the connection details."
          : "Locus reports a setup issue. Review the connection details, then run a check.";
    return {
      label: "Needs attention",
      description,
      variant: "destructive",
      signIn,
      check,
    };
  }
  if (item.auth_type === "oauth2" && oauthStatus?.pending) {
    return {
      label: "Sign-in in progress",
      description: "Finish the provider sign-in in your browser, then return here to check the connection.",
      variant: "warning",
      signIn,
      check,
    };
  }
  if (item.auth_type === "oauth2" && !oauthStatus?.connected) {
    return {
      label: "Sign-in needed",
      description: "Connect your account to finish setting up this service.",
      variant: "warning",
      signIn,
      check,
    };
  }
  if (item.auth_type !== "none" && item.auth_type !== "oauth2" && !item.secret_configured) {
    return {
      label: "Finish setup",
      description: "Add the saved credential for this service in Advanced setup.",
      variant: "warning",
      signIn,
      check,
    };
  }
  if (lastTest?.ok === true) {
    return {
      label: "Ready",
      description: "The sign-in and connection check passed.",
      variant: "success",
      signIn,
      check,
    };
  }
  if (item.status === "configured" || oauthStatus?.connected) {
    return {
      label: "Ready to check",
      description: "Setup is saved. Run a connection check before using this service.",
      variant: "outline",
      signIn,
      check,
    };
  }
  return {
    label: "Finish setup",
    description: "Complete the connection details in Advanced setup, then check the service.",
    variant: "warning",
    signIn,
    check,
  };
}

function buildOauthPresetMetadata(provider: OAuthProvider, grantType: OAuthGrantType): OAuthPresetMetadata | null {
  if (provider === "custom") {
    return null;
  }
  const preset = OAUTH_PROVIDER_PRESETS[provider][grantType];
  return {
    source: "provider-default",
    provider,
    grant_type: grantType,
    recommended_auth: {
      authorize_url: grantType === "authorization_code" ? preset.authorizeUrl : "",
      token_url: preset.tokenUrl,
      scopes: [...preset.scopes],
      audience: preset.audience,
      resource: preset.resource,
      tenant: preset.tenant,
      redirect_path: "/library/connections?oauth_panel=1",
    },
  };
}

function oauthPresetDriftLabel(metadata: Record<string, unknown> | undefined): string {
  const preset = readOauthPresetMetadata(metadata);
  if (!preset) {
    return "";
  }
  const auth = readAuthConfig(metadata);
  const scopes = Array.isArray(auth.scopes) ? auth.scopes.map((value) => String(value)) : [];
  const recommendedScopes = preset.recommended_auth.scopes.map((value) => String(value));
  const matches =
    String(auth.provider ?? "") === preset.provider &&
    String(auth.grant_type ?? "") === preset.grant_type &&
    String(auth.authorize_url ?? "") === preset.recommended_auth.authorize_url &&
    String(auth.token_url ?? "") === preset.recommended_auth.token_url &&
    String(auth.audience ?? "") === preset.recommended_auth.audience &&
    String(auth.resource ?? "") === preset.recommended_auth.resource &&
    String(auth.tenant ?? "") === preset.recommended_auth.tenant &&
    String(auth.redirect_path ?? "") === preset.recommended_auth.redirect_path &&
    scopes.length === recommendedScopes.length &&
    scopes.every((value, index) => value === recommendedScopes[index]);
  const providerLabel = OAUTH_PROVIDER_PRESETS[preset.provider]?.label ?? preset.provider;
  return matches
    ? `Matches ${providerLabel} recommended preset`
    : `Customized from ${providerLabel} recommended preset`;
}

export function IntegrationsManager() {
  const [items, setItems] = useState<IntegrationDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [starterTemplates, setStarterTemplates] = useState<IntegrationStarterTemplate[]>([]);
  const [starterTemplatesLoading, setStarterTemplatesLoading] = useState(true);
  const [starterCatalogError, setStarterCatalogError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [previewTemplateId, setPreviewTemplateId] = useState<string | null>(null);
  const [oauthPanelIntegrationId, setOauthPanelIntegrationId] = useState<string | null>(null);
  const [oauthPanelOutcome, setOauthPanelOutcome] = useState("");
  const [oauthStatuses, setOauthStatuses] = useState<Record<string, IntegrationOAuthStatus>>({});
  const [oauthBusyKey, setOauthBusyKey] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [type, setType] = useState<IntegrationDefinition["type"]>("http");
  const [baseUrl, setBaseUrl] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const [authType, setAuthType] = useState<SupportedAuthType>("none");
  const [secretRef, setSecretRef] = useState("");
  const [apiKeyLocation, setApiKeyLocation] = useState<ApiKeyLocation>("header");
  const [apiKeyName, setApiKeyName] = useState("x-api-key");
  const [bearerPrefix, setBearerPrefix] = useState("Bearer");
  const [basicUsername, setBasicUsername] = useState("");
  const [oauthProvider, setOauthProvider] = useState<OAuthProvider>("custom");
  const [oauthGrantType, setOauthGrantType] = useState<OAuthGrantType>("client_credentials");
  const [oauthAuthorizeUrl, setOauthAuthorizeUrl] = useState("");
  const [oauthTokenUrl, setOauthTokenUrl] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthScopes, setOauthScopes] = useState("");
  const [oauthAudience, setOauthAudience] = useState("");
  const [oauthResource, setOauthResource] = useState("");
  const [oauthTenant, setOauthTenant] = useState("");
  const [oauthRedirectPath, setOauthRedirectPath] = useState("/library/connections?oauth_panel=1");
  const [oauthClientSecretRef, setOauthClientSecretRef] = useState("");
  const [oauthTokenSecretRef, setOauthTokenSecretRef] = useState("");
  const [oauthRefreshTokenSecretRef, setOauthRefreshTokenSecretRef] = useState("");
  const [oauthAccountLabel, setOauthAccountLabel] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [testingId, setTestingId] = useState<string | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  function openAdvanced() {
    setAdvancedOpen(true);
    requestAnimationFrame(() => document.getElementById("integration-form")?.scrollIntoView?.({ behavior: "smooth", block: "start" }));
  }

  const selectedTemplate = starterTemplates.find((item) => item.id === selectedTemplateId) ?? null;
  const previewTemplate = starterTemplates.find((item) => item.id === previewTemplateId) ?? selectedTemplate ?? starterTemplates[0] ?? null;
  const starterTemplateGroups = Array.from(new Set(starterTemplates.map((item) => item.wave)))
    .sort((left, right) => left - right)
    .map((wave) => ({
      wave,
      items: starterTemplates.filter((item) => item.wave === wave),
    }));
  const oauthPanelItem = items.find((item) => item.id === oauthPanelIntegrationId) ?? null;
  const oauthPanelStatus = oauthPanelIntegrationId
    ? oauthStatuses[oauthPanelIntegrationId] ?? oauthPanelItem?.oauth_status ?? null
    : null;
  const oauthItems = items.filter((item) => item.auth_type === "oauth2");
  const oauthConnectedCount = oauthItems.filter((item) => (oauthStatuses[item.id] ?? item.oauth_status)?.connected).length;
  const oauthPendingCount = oauthItems.filter((item) => (oauthStatuses[item.id] ?? item.oauth_status)?.pending).length;
  const oauthDisconnectedCount = oauthItems.length - oauthConnectedCount - oauthPendingCount;
  const oauthProviderPreset = authType === "oauth2" && oauthProvider !== "custom" ? OAUTH_PROVIDER_PRESETS[oauthProvider] : null;
  const oauthGrantPreset = oauthProviderPreset ? oauthProviderPreset[oauthGrantType] : null;
  const currentOauthPresetLabel = authType === "oauth2" && oauthProvider !== "custom"
    ? oauthPresetDriftLabel({
        auth: buildAuthMetadata(),
        oauth_preset: buildOauthPresetMetadata(oauthProvider, oauthGrantType),
      })
    : "";

  function applyOauthProviderPreset(provider: OAuthProvider, grantType: OAuthGrantType): void {
    if (provider === "custom") {
      return;
    }
    const preset = OAUTH_PROVIDER_PRESETS[provider][grantType];
    setOauthAuthorizeUrl(grantType === "authorization_code" ? preset.authorizeUrl : "");
    setOauthTokenUrl(preset.tokenUrl);
    setOauthScopes(preset.scopes.join(" "));
    setOauthAudience(preset.audience);
    setOauthResource(preset.resource);
    setOauthTenant(preset.tenant);
    setOauthRedirectPath("/library/connections?oauth_panel=1");
    setOauthAccountLabel((current) => (current.trim() ? current : preset.accountLabelPlaceholder));
    setOauthClientSecretRef((current) => (current.trim() ? current : preset.clientSecretPlaceholder));
    setOauthTokenSecretRef((current) => (current.trim() ? current : preset.tokenSecretPlaceholder));
    setOauthRefreshTokenSecretRef((current) => (current.trim() ? current : preset.refreshTokenSecretPlaceholder));
    setSecretRef((current) => (current.trim() ? current : preset.clientSecretPlaceholder));
  }

  async function refresh() {
    setLoading(true);
    try {
      const integrations = await getIntegrations();
      setItems(integrations);
      setListError(null);
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Unable to load integrations.");
    } finally {
      setLoading(false);
    }
  }

  async function loadOauthStatus(integrationId: string): Promise<IntegrationOAuthStatus> {
    const status = await getIntegrationOAuthStatus(integrationId);
    setOauthStatuses((current) => ({ ...current, [integrationId]: status }));
    return status;
  }

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    const initialState = readWindowOauthPanelState();
    if (initialState.integrationId) {
      setOauthPanelIntegrationId(initialState.integrationId);
      setOauthPanelOutcome(initialState.outcome);
      void loadOauthStatus(initialState.integrationId).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadStarterTemplates() {
      setStarterTemplatesLoading(true);
      setStarterCatalogError("");
      try {
        const templates = await getIntegrationStarterTemplates();
        if (cancelled) {
          return;
        }
        setStarterTemplates(templates);
        setPreviewTemplateId((current) => current ?? templates[0]?.id ?? null);
      } catch (error) {
        if (!cancelled) {
          setStarterCatalogError(error instanceof Error ? error.message : "Unable to load starter catalog.");
        }
      } finally {
        if (!cancelled) {
          setStarterTemplatesLoading(false);
        }
      }
    }

    void loadStarterTemplates();

    return () => {
      cancelled = true;
    };
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
      provider: oauthProvider,
      grant_type: oauthGrantType,
      authorize_url: oauthGrantType === "authorization_code" ? oauthAuthorizeUrl.trim() : "",
      token_url: oauthTokenUrl.trim(),
      client_id: oauthClientId.trim(),
      scopes: oauthScopes
        .split(/[\s,]+/)
        .map((value) => value.trim())
        .filter(Boolean),
      audience: oauthAudience.trim(),
      resource: oauthResource.trim(),
      tenant: oauthTenant.trim(),
      redirect_path: oauthRedirectPath.trim() || "/library/connections?oauth_panel=1",
      client_secret_ref: oauthClientSecretRef.trim() || secretRef.trim(),
      token_secret_ref: oauthTokenSecretRef.trim(),
      refresh_token_secret_ref: oauthRefreshTokenSecretRef.trim(),
      account_label: oauthAccountLabel.trim(),
    };
  }

  function resetForm() {
    setEditingId(null);
    setSelectedTemplateId(null);
    setName("");
    setType("http");
    setBaseUrl("");
    setCapabilities("");
    setAuthType("none");
    setSecretRef("");
    setApiKeyLocation("header");
    setApiKeyName("x-api-key");
    setBearerPrefix("Bearer");
    setBasicUsername("");
    setOauthProvider("custom");
    setOauthGrantType("client_credentials");
    setOauthAuthorizeUrl("");
    setOauthTokenUrl("");
    setOauthClientId("");
    setOauthScopes("");
    setOauthAudience("");
    setOauthResource("");
    setOauthTenant("");
    setOauthRedirectPath("/library/connections?oauth_panel=1");
    setOauthClientSecretRef("");
    setOauthTokenSecretRef("");
    setOauthRefreshTokenSecretRef("");
    setOauthAccountLabel("");
  }

  function applyStarterTemplate(template: IntegrationStarterTemplate) {
    const auth = readAuthConfig(template.metadata_json);
    setEditingId(null);
    setSelectedTemplateId(template.id);
    setPreviewTemplateId(template.id);
    setName(template.name);
    setType(template.type);
    setBaseUrl(template.base_url);
    setCapabilities(formatLineList(template.capabilities));
    setAuthType(template.auth_type);
    setSecretRef(template.secret_ref);
    setApiKeyLocation(String(auth.location ?? "header") as ApiKeyLocation);
    setApiKeyName(String(auth.key_name ?? "x-api-key"));
    setBearerPrefix(String(auth.prefix ?? "Bearer"));
    setBasicUsername(String(auth.username ?? ""));
    setOauthProvider(String(auth.provider ?? "custom") as OAuthProvider);
    setOauthGrantType(String(auth.grant_type ?? "client_credentials") as OAuthGrantType);
    setOauthAuthorizeUrl(String(auth.authorize_url ?? ""));
    setOauthTokenUrl(String(auth.token_url ?? ""));
    setOauthClientId(String(auth.client_id ?? ""));
    setOauthScopes(formatScopeList(Array.isArray(auth.scopes) ? (auth.scopes as string[]) : []));
    setOauthAudience(String(auth.audience ?? ""));
    setOauthResource(String(auth.resource ?? ""));
    setOauthTenant(String(auth.tenant ?? ""));
    setOauthRedirectPath(String(auth.redirect_path ?? "/library/connections?oauth_panel=1"));
    setOauthClientSecretRef(String(auth.client_secret_ref ?? template.secret_ref ?? ""));
    setOauthTokenSecretRef(String(auth.token_secret_ref ?? ""));
    setOauthRefreshTokenSecretRef(String(auth.refresh_token_secret_ref ?? ""));
    setOauthAccountLabel(String(auth.account_label ?? ""));
    setStatusMessage(`${template.name} starter loaded.`);
  }

  function handleEdit(item: IntegrationDefinition) {
    const auth = readAuthConfig(item.metadata_json);
    setEditingId(item.id);
    setSelectedTemplateId(null);
    setName(item.name);
    setType(item.type);
    setBaseUrl(item.base_url);
    setCapabilities(formatLineList(item.capabilities));
    setAuthType(item.auth_type as SupportedAuthType);
    setSecretRef(item.secret_ref ?? "");
    setApiKeyLocation(String(auth.location ?? "header") as ApiKeyLocation);
    setApiKeyName(String(auth.key_name ?? "x-api-key"));
    setBearerPrefix(String(auth.prefix ?? "Bearer"));
    setBasicUsername(String(auth.username ?? ""));
    setOauthProvider(String(auth.provider ?? "custom") as OAuthProvider);
    setOauthGrantType(String(auth.grant_type ?? "client_credentials") as OAuthGrantType);
    setOauthAuthorizeUrl(String(auth.authorize_url ?? ""));
    setOauthTokenUrl(String(auth.token_url ?? ""));
    setOauthClientId(String(auth.client_id ?? ""));
    setOauthScopes(formatScopeList(Array.isArray(auth.scopes) ? (auth.scopes as string[]) : []));
    setOauthAudience(String(auth.audience ?? ""));
    setOauthResource(String(auth.resource ?? ""));
    setOauthTenant(String(auth.tenant ?? ""));
    setOauthRedirectPath(String(auth.redirect_path ?? "/library/connections?oauth_panel=1"));
    setOauthClientSecretRef(String(auth.client_secret_ref ?? item.secret_ref ?? ""));
    setOauthTokenSecretRef(String(auth.token_secret_ref ?? ""));
    setOauthRefreshTokenSecretRef(String(auth.refresh_token_secret_ref ?? ""));
    setOauthAccountLabel(String(auth.account_label ?? item.oauth_status?.account_label ?? ""));
    setStatusMessage("");
    openAdvanced();
  }

  async function handleCreate() {
    setStatusMessage("");
    const metadata_json: Record<string, unknown> = {
      ...(selectedTemplate?.metadata_json ?? {}),
      auth: buildAuthMetadata(),
    };
    const oauthPresetMetadata = authType === "oauth2" ? buildOauthPresetMetadata(oauthProvider, oauthGrantType) : null;
    if (oauthPresetMetadata) {
      metadata_json.oauth_preset = oauthPresetMetadata;
    } else {
      delete metadata_json.oauth_preset;
    }
    try {
      await saveIntegration({
        ...(editingId ? { id: editingId } : {}),
        name: name.trim() || "Untitled Integration",
        type,
        base_url: baseUrl,
        capabilities: parseLineList(capabilities),
        auth_type: authType,
        secret_ref:
          authType === "none"
            ? ""
            : authType === "oauth2"
              ? oauthClientSecretRef.trim() || secretRef.trim()
              : secretRef.trim(),
        status: "draft",
        metadata_json,
        ...(selectedTemplate
          ? {
              permission_scopes: selectedTemplate.permission_scopes,
              data_access: selectedTemplate.data_access,
              egress_allowlist: selectedTemplate.egress_allowlist,
              publisher: selectedTemplate.publisher,
              execution_mode: selectedTemplate.execution_mode,
              signature_verified: selectedTemplate.signature_verified,
              approved_for_marketplace: selectedTemplate.approved_for_marketplace,
            }
          : {}),
      });
      resetForm();
      await refresh();
      setStatusMessage(editingId ? "Integration updated." : "Integration saved.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to save integration.");
    }
  }

  async function handleTest(id: string) {
    setTestingId(id);
    try {
      const result = await testIntegration(id);
      const warnings = result.diagnostics?.warnings ?? [];
      const warningSuffix = warnings.length > 0 ? ` • warnings: ${warnings.join("; ")}` : "";
      setStatusMessage(`${result.message}${warningSuffix}`);
      await refresh();
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to test integration.");
    } finally {
      setTestingId(null);
    }
  }

  async function openOauthPanel(item: IntegrationDefinition, outcome = ""): Promise<void> {
    setOauthPanelIntegrationId(item.id);
    setOauthPanelOutcome(outcome);
    try {
      await loadOauthStatus(item.id);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to load OAuth status.");
    }
  }

  async function handleConnectOAuth(item: IntegrationDefinition): Promise<void> {
    setOauthBusyKey(`connect:${item.id}`);
    try {
      const response = await connectIntegrationOAuth(item.id, {
        return_to: "/library/connections?oauth_panel=1",
      });
      setOauthStatuses((current) => ({ ...current, [item.id]: response.status }));
      setOauthPanelIntegrationId(item.id);
      setOauthPanelOutcome(response.mode === "authorization_code" ? "connecting" : "connected");
      if (response.mode === "authorization_code" && response.connect_url && typeof window !== "undefined") {
        window.location.assign(response.connect_url);
        return;
      }
      await refresh();
      setStatusMessage(`${item.name} account connected.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to start sign-in.");
    } finally {
      setOauthBusyKey(null);
    }
  }

  async function handleRefreshOAuth(item: IntegrationDefinition): Promise<void> {
    setOauthBusyKey(`refresh:${item.id}`);
    try {
      const response = await refreshIntegrationOAuth(item.id);
      setOauthStatuses((current) => ({ ...current, [item.id]: response.status }));
      setOauthPanelIntegrationId(item.id);
      setOauthPanelOutcome("connected");
      await refresh();
      setStatusMessage(`${item.name} sign-in refreshed.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to refresh sign-in.");
    } finally {
      setOauthBusyKey(null);
    }
  }

  async function handleDisconnectOAuth(item: IntegrationDefinition): Promise<void> {
    setOauthBusyKey(`disconnect:${item.id}`);
    try {
      const response = await disconnectIntegrationOAuth(item.id);
      setOauthStatuses((current) => ({ ...current, [item.id]: response.status }));
      setOauthPanelIntegrationId(item.id);
      setOauthPanelOutcome("");
      await refresh();
      setStatusMessage(`${item.name} account disconnected.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to disconnect the account.");
    } finally {
      setOauthBusyKey(null);
    }
  }

  function dismissOauthPanel(): void {
    setOauthPanelIntegrationId(null);
    setOauthPanelOutcome("");
    clearWindowOauthPanelState();
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Connections</h1>
          <p className="fx-muted">Connect services Locus can use, then check sign-in and service health here.</p>
        </div>
        <button
          type="button"
          className="fx-btn-secondary px-3 py-2 text-sm font-medium"
          onClick={() => {
            resetForm();
            setStatusMessage("");
            openAdvanced();
          }}
        >
          Advanced setup
        </button>
      </header>

      {statusMessage ? (
        <p role="status" className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.84)] px-3 py-2 text-xs text-[var(--foreground)]">
          {statusMessage}
        </p>
      ) : null}

      <div className="fx-panel p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-wide">Find a connection</h2>
          <span className="fx-muted text-xs">Choose a recommended service, review access, then add it.</span>
        </div>
        <ConnectionCatalog integrations={items} onChanged={() => void refresh()} />
      </div>

      {oauthPanelItem && oauthPanelStatus ? (
        <div className="fx-panel rounded-[1.6rem] p-5 shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Sign-in and access</p>
              <h2 className="mt-2 text-[1.1rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">{oauthPanelItem.name}</h2>
              <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">{oauthOutcomeLabel(oauthPanelOutcome)}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={`inline-flex rounded-full border px-2.5 py-1 text-[0.72rem] font-medium ${oauthPanelStatus.connected ? integrationStatusTone("connected") : oauthPanelStatus.pending ? integrationStatusTone("draft") : integrationStatusTone("error")}`}>
                {oauthPanelStatus.connected ? "Signed in" : oauthPanelStatus.pending ? "Sign-in in progress" : "Sign-in needed"}
              </span>
              <button type="button" onClick={dismissOauthPanel} className="fx-btn-secondary px-3 py-1.5 text-xs">
                Close
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 lg:grid-cols-[1.4fr_1fr]">
            <div className="space-y-4 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-4">
              <div>
                <p className="text-sm font-semibold text-[var(--foreground)]">Account and permissions</p>
                <p className="mt-1 text-xs text-[var(--fx-muted)]">
                  {oauthPanelStatus.account_label ? `Account: ${oauthPanelStatus.account_label}` : "Review what Locus may access before continuing."}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {oauthPanelStatus.scopes.length > 0 ? (
                    oauthPanelStatus.scopes.map((scope) => (
                      <span key={scope} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{scope}</span>
                    ))
                  ) : (
                    <span className="text-xs text-[var(--fx-muted)]">No scopes configured.</span>
                  )}
                </div>
              </div>
              {oauthPanelStatus.last_error ? (
                <div role="alert" className="rounded-[0.9rem] border border-[color-mix(in_srgb,var(--fx-danger)_30%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] px-3 py-2 text-sm text-[var(--foreground)]">
                  <p className="font-medium">Locus could not finish sign-in</p>
                  <p className="mt-1">{oauthPanelStatus.last_error}</p>
                </div>
              ) : null}
            </div>

            <div className="space-y-3 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-4">
              <p className="text-sm font-semibold text-[var(--foreground)]">Manage this account</p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => void handleConnectOAuth(oauthPanelItem)}
                  className="fx-btn-primary px-3 py-2 text-sm"
                  disabled={oauthBusyKey === `connect:${oauthPanelItem.id}`}
                >
                  {oauthBusyKey === `connect:${oauthPanelItem.id}` ? "Opening sign-in…" : oauthPanelStatus.connected ? "Sign in again" : "Connect account"}
                </button>
                {oauthPanelStatus.connected ? (
                  <>
                    <button
                      type="button"
                      onClick={() => void handleRefreshOAuth(oauthPanelItem)}
                      className="fx-btn-secondary px-3 py-2 text-sm"
                      disabled={oauthBusyKey === `refresh:${oauthPanelItem.id}`}
                    >
                      {oauthBusyKey === `refresh:${oauthPanelItem.id}` ? "Refreshing sign-in…" : "Refresh sign-in"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleDisconnectOAuth(oauthPanelItem)}
                      className="fx-btn-warning px-3 py-2 text-sm"
                      disabled={oauthBusyKey === `disconnect:${oauthPanelItem.id}`}
                    >
                      {oauthBusyKey === `disconnect:${oauthPanelItem.id}` ? "Disconnecting…" : "Disconnect account"}
                    </button>
                  </>
                ) : null}
              </div>
              <p className="text-xs leading-6 text-[var(--fx-muted)]">
                After signing in with the provider, return here and run a connection check to confirm the service is ready.
              </p>
            </div>
          </div>

          <details className="mt-3 border-t border-[var(--fx-border)] pt-3">
            <summary className="cursor-pointer text-xs font-medium text-[var(--fx-muted)]">Advanced sign-in diagnostics</summary>
            <dl className="mt-3 grid gap-3 text-xs text-[var(--foreground)] sm:grid-cols-2">
              <div><dt className="font-medium text-[var(--fx-muted)]">Provider and sign-in method</dt><dd className="mt-1">{oauthPanelStatus.provider || "custom"} · {oauthPanelStatus.grant_type || "unknown"}</dd></div>
              <div><dt className="font-medium text-[var(--fx-muted)]">Client ID</dt><dd className="mt-1 break-all">{oauthPanelStatus.client_id || "(unset)"}</dd></div>
              <div><dt className="font-medium text-[var(--fx-muted)]">Redirect address</dt><dd className="mt-1 break-all">{oauthPanelStatus.redirect_uri || "(unavailable)"}</dd></div>
              <div><dt className="font-medium text-[var(--fx-muted)]">Token address</dt><dd className="mt-1 break-all">{oauthPanelStatus.token_url || "(unset)"}</dd></div>
              <div><dt className="font-medium text-[var(--fx-muted)]">Authorization address</dt><dd className="mt-1 break-all">{oauthPanelStatus.authorize_url || "Not used for this sign-in method"}</dd></div>
              <div><dt className="font-medium text-[var(--fx-muted)]">Sign-in health</dt><dd className="mt-1">{oauthPanelStatus.has_access_token ? "Access ready" : "No access token"}{oauthPanelStatus.has_refresh_token ? " · Can renew" : " · Cannot renew automatically"}</dd></div>
            </dl>
          </details>
        </div>
      ) : null}

      <details
        id="advanced-connections"
        open={advancedOpen}
        onToggle={(event) => setAdvancedOpen((event.currentTarget as HTMLDetailsElement).open)}
        className="fx-panel rounded-[1.6rem] p-4"
      >
        <summary className="cursor-pointer text-sm font-semibold">
          Advanced: custom integrations and MCP servers (raw URLs, API keys, tokens, headers)
        </summary>
        <div className="mt-4 space-y-4">
      <div id="integration-form" className="fx-panel scroll-mt-24 rounded-[1.6rem] p-5 shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Custom connector</p>
            <h2 className="mt-2 text-[1.1rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">{editingId ? "Edit integration" : "Add integration"}</h2>
            <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">Register the endpoint, choose the auth shape the runtime can actually exercise, and map skill capabilities so generic tool calls can resolve to the right connector.</p>
          </div>
          <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">{editingId ? "Editing existing connector" : "Secrets stay server-side"}</div>
        </div>

        <div className="mb-5 space-y-4 rounded-[1.25rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Starter catalog</p>
              <h3 className="mt-2 text-base font-semibold text-[var(--foreground)]">Prefill from recommended connections</h3>
              <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">
                Start from a vetted template, then customize names, secrets, and capabilities before saving.
              </p>
            </div>
            {selectedTemplate ? (
              <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">
                Using {selectedTemplate.name} starter
              </div>
            ) : (
              <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">{starterTemplateGroups.length} starter waves</div>
            )}
          </div>

          {starterTemplatesLoading ? (
            <div className="rounded-[1rem] border border-dashed border-[var(--fx-border)] px-3 py-3 text-sm text-[var(--fx-muted)]">
              Loading starter catalog...
            </div>
          ) : starterCatalogError ? (
            <div className="rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-danger)_30%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] px-3 py-3 text-sm text-[var(--foreground)]">
              {starterCatalogError}
            </div>
          ) : (
            <>
              {previewTemplate ? (
                <div className="grid gap-3 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.86)] p-4 lg:grid-cols-[1.2fr_1fr_1fr]">
                  <div className="space-y-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Template details</p>
                      <h4 className="mt-2 text-base font-semibold text-[var(--foreground)]">{previewTemplate.name}</h4>
                      <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">{previewTemplate.summary}</p>
                    </div>
                    <div className="grid gap-2 text-xs text-[var(--foreground)] sm:grid-cols-2">
                      <div className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-2.5">
                        <p className="font-medium text-[var(--fx-muted)]">Auth preset</p>
                        <p className="mt-1">{starterAuthLabel(previewTemplate.auth_type)}</p>
                      </div>
                      <div className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-2.5">
                        <p className="font-medium text-[var(--fx-muted)]">Runtime posture</p>
                        <p className="mt-1">{previewTemplate.publisher} / {previewTemplate.execution_mode}</p>
                      </div>
                    </div>
                    {previewTemplate.auth_type === "oauth2" ? (
                      <div className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-3 text-xs text-[var(--foreground)]">
                        <p className="font-medium text-[var(--fx-muted)]">OAuth foundation</p>
                        <p className="mt-2">Provider: {String(readAuthConfig(previewTemplate.metadata_json).provider ?? "custom")}</p>
                        <p className="mt-1">Grant type: {String(readAuthConfig(previewTemplate.metadata_json).grant_type ?? "authorization_code")}</p>
                        <p className="mt-1 break-all">Token URL: {String(readAuthConfig(previewTemplate.metadata_json).token_url ?? "") || "(unset)"}</p>
                        {String(readAuthConfig(previewTemplate.metadata_json).authorize_url ?? "") ? (
                          <p className="mt-1 break-all">Authorize URL: {String(readAuthConfig(previewTemplate.metadata_json).authorize_url ?? "")}</p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <div className="space-y-3 rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Permission scopes</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(previewTemplate.permission_scopes ?? []).length > 0 ? (
                          (previewTemplate.permission_scopes ?? []).map((scope) => (
                            <span key={scope} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{scope}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No predefined scopes.</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Data access</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(previewTemplate.data_access ?? []).length > 0 ? (
                          (previewTemplate.data_access ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No predefined data domains.</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-3 rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Egress allowlist</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(previewTemplate.egress_allowlist ?? []).length > 0 ? (
                          (previewTemplate.egress_allowlist ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No predefined egress domains.</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Capabilities</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(previewTemplate.capabilities ?? []).map((capability) => (
                          <span key={capability} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{capability}</span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="space-y-4">
                {starterTemplateGroups.map((group) => (
                  <div key={group.wave} className="space-y-2">
                    <p className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">
                      Wave {group.wave}
                    </p>
                    <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                      {group.items.map((template) => {
                        const isSelected = selectedTemplateId === template.id;
                        const isPreview = previewTemplate?.id === template.id;
                        return (
                          <div
                            key={template.id}
                            className={[
                              "rounded-[1rem] border p-3",
                              isSelected || isPreview
                                ? "border-[color-mix(in_srgb,var(--fx-primary)_45%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_8%,hsl(var(--card)))]"
                                : "border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)]",
                            ].join(" ")}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div>
                                <p className="text-sm font-semibold text-[var(--foreground)]">{template.name}</p>
                                <p className="mt-1 text-xs leading-5 text-[var(--fx-muted)]">{template.summary}</p>
                              </div>
                              <span className="fx-pill px-2.5 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">
                                {template.type}
                              </span>
                            </div>
                            <p className="mt-3 text-[11px] uppercase tracking-[0.12em] text-[var(--fx-muted)]">
                              {starterAuthLabel(template.auth_type)}
                            </p>
                            <p className="mt-1 text-xs text-[var(--fx-muted)]">{template.base_url}</p>
                            <div className="mt-3 flex flex-wrap gap-2">
                              {(template.capabilities ?? []).slice(0, 3).map((capability) => (
                                <span key={capability} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">
                                  {capability}
                                </span>
                              ))}
                            </div>
                            <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
                              <span className="text-[11px] text-[var(--fx-muted)]">{template.publisher} / {template.execution_mode}</span>
                              <div className="flex flex-wrap gap-2">
                                <button
                                  type="button"
                                  onClick={() => setPreviewTemplateId(template.id)}
                                  className="fx-btn-secondary px-3 py-1.5 text-xs"
                                >
                                  Inspect {template.name} details
                                </button>
                                <button
                                  type="button"
                                  onClick={() => applyStarterTemplate(template)}
                                  className={isSelected ? "fx-btn-secondary px-3 py-1.5 text-xs" : "fx-btn-primary px-3 py-1.5 text-xs"}
                                >
                                  Use {template.name} starter
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="rounded-[1rem] border border-dashed border-[var(--fx-border)] px-3 py-2.5 text-xs text-[var(--fx-muted)]">
            OAuth starters now land in the dedicated status panel after provider callbacks so builders can inspect connection state and token health without relying on a toast alone.
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">Name</span>
            <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={name} onChange={(event) => setName(event.target.value)} placeholder="Salesforce API" />
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">Type</span>
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={type} onChange={(event) => setType(event.target.value as IntegrationDefinition["type"])}>
              <option value="http">HTTP API</option>
              <option value="database">Database</option>
              <option value="queue">Queue</option>
              <option value="vector">Vector Store</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">Base URL / DSN</span>
            <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1 or postgresql://..." />
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">Capabilities / skill matches</span>
            <textarea
              className="fx-field mt-1 min-h-24 w-full px-2 py-2 text-sm"
              value={capabilities}
              onChange={(event) => setCapabilities(event.target.value)}
              placeholder="/incident-triage&#10;/tenant-oncall&#10;ops"
            />
            <span className="mt-1 block text-[11px] fx-muted">
              Enter one capability or skill per line. These values are matched against agent-selected /skills when generic tool nodes are resolved.
            </span>
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">Auth type</span>
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={authType} onChange={(event) => setAuthType(event.target.value as SupportedAuthType)}>
              <option value="none">None</option>
              <option value="api_key">API key</option>
              <option value="bearer">Bearer token</option>
              <option value="oauth2">OAuth2</option>
              <option value="basic">Basic</option>
            </select>
            <span className="mt-1 block text-[11px] fx-muted">
              OAuth2 is now supported in the builder through provider-aware fields and dedicated connect actions after the integration is saved.
            </span>
          </label>

          {authType === "none" ? (
            <div className="md:col-span-2 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-3 text-xs text-[var(--foreground)]">
              No authentication selected. Secret fields are hidden.
            </div>
          ) : null}

          {authType === "api_key" ? (
            <>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">API key location</span>
                <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={apiKeyLocation} onChange={(event) => setApiKeyLocation(event.target.value as ApiKeyLocation)}>
                  <option value="header">HTTP Header</option>
                  <option value="query">Query string</option>
                </select>
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">API key field name</span>
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
            <label className="block text-sm text-[var(--foreground)]">
              <span className="font-medium">Token prefix</span>
              <input
                className="fx-field mt-1 w-full px-2 py-2 text-sm"
                value={bearerPrefix}
                onChange={(event) => setBearerPrefix(event.target.value)}
                placeholder="Bearer"
              />
            </label>
          ) : null}

          {authType === "basic" ? (
            <label className="block text-sm text-[var(--foreground)]">
              <span className="font-medium">Username</span>
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
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">OAuth provider</span>
                <select
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthProvider}
                  onChange={(event) => {
                    const nextProvider = event.target.value as OAuthProvider;
                    setOauthProvider(nextProvider);
                    applyOauthProviderPreset(nextProvider, oauthGrantType);
                  }}
                >
                  <option value="microsoft">Microsoft</option>
                  <option value="google">Google</option>
                  <option value="salesforce">Salesforce</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Grant type</span>
                <select
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthGrantType}
                  onChange={(event) => {
                    const nextGrantType = event.target.value as OAuthGrantType;
                    setOauthGrantType(nextGrantType);
                    applyOauthProviderPreset(oauthProvider, nextGrantType);
                  }}
                >
                  <option value="authorization_code">Authorization code</option>
                  <option value="client_credentials">Client credentials</option>
                </select>
              </label>
              {oauthProviderPreset && oauthGrantPreset ? (
                <div className="md:col-span-2 rounded-[1rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-4 text-xs text-[var(--foreground)]">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">{oauthProviderPreset.label} preset guidance</p>
                      <p className="mt-2 text-sm font-medium text-[var(--foreground)]">{oauthProviderPreset.summary}</p>
                      <p className="mt-2 leading-6 text-[var(--foreground)]">{oauthGrantPreset.guidance}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => applyOauthProviderPreset(oauthProvider, oauthGrantType)}
                      className="fx-btn-secondary px-3 py-1.5 text-xs"
                    >
                      Apply {oauthProviderPreset.label} defaults
                    </button>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {oauthGrantPreset.notes.map((note) => (
                      <span key={note} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">
                        {note}
                      </span>
                    ))}
                  </div>
                  {currentOauthPresetLabel ? (
                    <p className="mt-3 text-[11px] text-[var(--fx-muted)]">{currentOauthPresetLabel}</p>
                  ) : null}
                </div>
              ) : null}
              {oauthGrantType === "authorization_code" ? (
                <label className="block text-sm text-[var(--foreground)] md:col-span-2">
                  <span className="font-medium">Authorize URL</span>
                  <input
                    className="fx-field mt-1 w-full px-2 py-2 text-sm"
                    value={oauthAuthorizeUrl}
                    onChange={(event) => setOauthAuthorizeUrl(event.target.value)}
                    placeholder={oauthGrantPreset?.authorizeUrl || "https://login.example.com/oauth2/authorize"}
                  />
                </label>
              ) : null}
              <label className="block text-sm text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Token URL</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthTokenUrl}
                  onChange={(event) => setOauthTokenUrl(event.target.value)}
                  placeholder={oauthGrantPreset?.tokenUrl || "https://login.example.com/oauth2/token"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Client ID</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthClientId}
                  onChange={(event) => setOauthClientId(event.target.value)}
                  placeholder={oauthGrantPreset?.clientIdPlaceholder || "locus-client"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Scopes</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthScopes}
                  onChange={(event) => setOauthScopes(event.target.value)}
                  placeholder={oauthGrantPreset?.scopes.join(" ") || "openid profile email"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Audience</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthAudience}
                  onChange={(event) => setOauthAudience(event.target.value)}
                  placeholder={oauthGrantPreset?.audience || "https://graph.microsoft.com"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Resource</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthResource}
                  onChange={(event) => setOauthResource(event.target.value)}
                  placeholder={oauthGrantPreset?.resource || "Optional provider resource"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Tenant / realm</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthTenant}
                  onChange={(event) => setOauthTenant(event.target.value)}
                  placeholder={oauthGrantPreset?.tenant || "common or your-tenant-id"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Account label</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthAccountLabel}
                  onChange={(event) => setOauthAccountLabel(event.target.value)}
                  placeholder={oauthGrantPreset?.accountLabelPlaceholder || "Customer Success shared mailbox"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Redirect path after callback</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthRedirectPath}
                  onChange={(event) => setOauthRedirectPath(event.target.value)}
                  placeholder="/library/connections?oauth_panel=1"
                />
                <span className="mt-1 block text-[11px] fx-muted">
                  The callback now returns to a dedicated OAuth status panel in the integrations manager instead of relying on a transient page-level toast.
                </span>
              </label>
              <label className="block text-sm text-[var(--foreground)] md:col-span-2">
                <span className="font-medium">Client secret reference</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthClientSecretRef}
                  onChange={(event) => {
                    setOauthClientSecretRef(event.target.value);
                    setSecretRef(event.target.value);
                  }}
                  placeholder={oauthGrantPreset?.clientSecretPlaceholder || "secret/integrations/provider/client-secret"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Access token secret ref</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthTokenSecretRef}
                  onChange={(event) => setOauthTokenSecretRef(event.target.value)}
                  placeholder={oauthGrantPreset?.tokenSecretPlaceholder || "secret/integrations/provider/access-token"}
                />
              </label>
              <label className="block text-sm text-[var(--foreground)]">
                <span className="font-medium">Refresh token secret ref</span>
                <input
                  className="fx-field mt-1 w-full px-2 py-2 text-sm"
                  value={oauthRefreshTokenSecretRef}
                  onChange={(event) => setOauthRefreshTokenSecretRef(event.target.value)}
                  placeholder={oauthGrantPreset?.refreshTokenSecretPlaceholder || "secret/integrations/provider/refresh-token"}
                />
              </label>
            </>
          ) : null}

          {authType !== "none" && authType !== "oauth2" ? (
            <label className="block text-sm text-[var(--foreground)] md:col-span-2">
              <span className="font-medium">Secret reference</span>
              <input
                className="fx-field mt-1 w-full px-2 py-2 text-sm"
                value={secretRef}
                onChange={(event) => setSecretRef(event.target.value)}
                placeholder={authType === "basic" ? "secret/db/password" : "secret/integrations/service-token"}
              />
              <span className="mt-1 block text-[11px] fx-muted">
                Use a secret reference path (for example: <code>secret/team/name</code>) — do not paste raw credentials.
              </span>
            </label>
          ) : null}
        </div>

        <div className="mt-3">
          <div className="flex flex-wrap gap-2">
            <button onClick={handleCreate} className="fx-btn-primary px-3 py-2 text-sm">
              {editingId ? "Update integration" : "Save integration"}
            </button>
            {editingId ? (
              <button onClick={resetForm} className="fx-btn-secondary px-3 py-2 text-sm">
                Cancel edit
              </button>
            ) : null}
          </div>
          {authType === "oauth2" ? (
            <p className="mt-2 text-[11px] text-[var(--fx-muted)]">
              Save the integration before running OAuth connect actions. After save, use the inventory row or status panel to connect, refresh, or disconnect tokens.
            </p>
          ) : null}
        </div>
      </div>

      <McpConnectionsPanel />
        </div>
      </details>

      <section aria-labelledby="saved-connections-heading" className="space-y-3">
        <div>
          <h2 id="saved-connections-heading" className="text-lg font-semibold text-[var(--foreground)]">Your connections</h2>
          <p className="mt-1 text-sm text-[var(--fx-muted)]">See whether each service is signed in and whether Locus can reach it.</p>
        </div>
        {loading ? (
          <p role="status" className="fx-panel rounded-[1rem] p-4 text-sm text-[var(--fx-muted)]">Loading your connections…</p>
        ) : listError ? (
          <div role="alert" className="fx-panel flex flex-wrap items-center justify-between gap-3 rounded-[1rem] p-4 text-sm">
            <p className="text-[var(--fx-danger)]">Could not load your connections: {listError}</p>
            <Button variant="secondary" size="sm" onClick={() => void refresh()}>Try again</Button>
          </div>
        ) : items.length === 0 ? (
          <div className="fx-panel rounded-[1rem] p-5">
            <p className="font-medium text-[var(--foreground)]">No connections yet</p>
            <p className="mt-1 text-sm text-[var(--fx-muted)]">Choose a service from the catalog above. Custom services are available in Advanced setup.</p>
          </div>
        ) : (
          <ul className="space-y-3">
            {items.map((item) => {
              const oauthStatus = oauthStatuses[item.id] ?? item.oauth_status ?? null;
              const lastTest = readLastTest(item.metadata_json);
              const health = integrationHealth(item, oauthStatus, lastTest);
              const protocol = String(readAuthConfig(item.metadata_json).protocol ?? item.metadata_json?.protocol ?? "") === "mcp";
              const connectionKind = protocol
                ? "MCP server"
                : item.type === "database"
                  ? "Database"
                  : item.type === "queue"
                    ? "Queue"
                    : item.type === "vector"
                      ? "Vector store"
                      : item.type === "custom"
                        ? "Custom service"
                        : "API";

              return (
                <li key={item.id} className="fx-panel rounded-[1rem] p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-semibold text-[var(--foreground)]">{item.name}</h3>
                        <Badge variant="outline">{connectionKind}</Badge>
                      </div>
                      <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--fx-muted)]">{health.description}</p>
                    </div>
                    <Badge variant={health.variant}>
                      <span aria-hidden="true">{health.variant === "success" ? "✓" : health.variant === "destructive" ? "!" : "○"}</span>
                      {health.label}
                    </Badge>
                  </div>

                  <dl className="mt-4 grid gap-3 rounded-[0.9rem] bg-[hsl(var(--muted)/0.24)] p-3 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-xs font-medium text-[var(--fx-muted)]">Sign-in</dt>
                      <dd className="mt-1 font-medium text-[var(--foreground)]">{health.signIn}</dd>
                    </div>
                    <div>
                      <dt className="text-xs font-medium text-[var(--fx-muted)]">Connection check</dt>
                      <dd className="mt-1 font-medium text-[var(--foreground)]">{health.check}</dd>
                    </div>
                  </dl>

                  <div className="mt-4">
                    <p className="text-xs font-medium text-[var(--fx-muted)]">What Locus can use</p>
                    {item.capabilities && item.capabilities.length > 0 ? (
                      <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`${item.name} available actions`}>
                        {item.capabilities.map((capability) => (
                          <li key={`${item.id}-${capability}`} className="fx-pill px-2.5 py-1 text-xs text-[var(--foreground)]">
                            {capability.replace(/^\/+/, "").replace(/[-_/]+/g, " ")}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="mt-1 text-sm text-[var(--fx-muted)]">No actions are mapped yet.</p>
                    )}
                    {lastTest?.warnings?.length ? (
                      <p className="mt-2 text-xs text-[var(--fx-muted)]">The last check reported {lastTest.warnings.length} warning{lastTest.warnings.length === 1 ? "" : "s"}.</p>
                    ) : null}
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--fx-border)] pt-4">
                    {item.auth_type === "oauth2" ? (
                      <Button
                        variant={oauthStatus?.connected ? "secondary" : "default"}
                        size="sm"
                        onClick={() => oauthStatus?.connected ? void openOauthPanel(item) : void handleConnectOAuth(item)}
                        disabled={oauthBusyKey === `connect:${item.id}`}
                      >
                        {oauthBusyKey === `connect:${item.id}` ? "Opening sign-in…" : oauthStatus?.connected ? "Sign-in & access" : "Connect account"}
                      </Button>
                    ) : null}
                    <Button variant="secondary" size="sm" onClick={() => void handleTest(item.id)} disabled={testingId === item.id}>
                      {testingId === item.id ? "Checking…" : lastTest?.ok === false || item.status === "error" ? "Check again" : "Check connection"}
                    </Button>
                    <Button variant="outline" size="sm" onClick={() => handleEdit(item)}>Edit setup</Button>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--fx-border)] pt-4">
                    <p className="max-w-xl text-xs leading-5 text-[var(--fx-muted)]">Removing a connection requires name confirmation.</p>
                    <TypedDeleteButton
                      itemType="integration"
                      itemId={item.id}
                      itemName={item.name}
                      buttonLabel="Remove connection"
                      buttonClassName="fx-btn-warning px-3 py-1.5 text-xs"
                      onDeleted={(id) => {
                        if (oauthPanelIntegrationId === id) {
                          setOauthPanelIntegrationId(null);
                          setOauthPanelOutcome("");
                          clearWindowOauthPanelState();
                        }
                        setStatusMessage("Connection removed.");
                        void refresh();
                      }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <details className="fx-panel rounded-[1.6rem] p-4">
        <summary className="cursor-pointer text-sm font-semibold text-[var(--foreground)]">Advanced connection diagnostics</summary>
        <div className="mt-4 overflow-x-auto">
      <div className="overflow-hidden rounded-[1rem]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--ui-border)] px-4 py-4">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Inventory</p>
            <h2 className="mt-2 text-[1.05rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Saved connection records</h2>
          </div>
          <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Test before promoting to live traffic</div>
        </div>
        {oauthItems.length > 0 ? (
          <div className="space-y-4 border-b border-[var(--ui-border)] px-4 py-4">
            <div className="grid gap-3 md:grid-cols-3">
              <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.86)] px-3 py-3">
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">OAuth connections</p>
                <p className="mt-2 text-xl font-semibold text-[var(--foreground)]">{oauthItems.length}</p>
              </div>
              <div className="rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-success)_30%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-success)_8%,hsl(var(--card)))] px-3 py-3">
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Connected</p>
                <p className="mt-2 text-xl font-semibold text-[var(--foreground)]">{oauthConnectedCount}</p>
              </div>
              <div className="rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-warning)_26%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-warning)_8%,hsl(var(--card)))] px-3 py-3">
                <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Pending / disconnected</p>
                <p className="mt-2 text-xl font-semibold text-[var(--foreground)]">{oauthPendingCount + oauthDisconnectedCount}</p>
              </div>
            </div>
            <div className="space-y-2">
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">OAuth connection overview</p>
              <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
                {oauthItems.map((item) => {
                  const oauthStatus = oauthStatuses[item.id] ?? item.oauth_status ?? null;
                  return (
                    <div key={`${item.id}-oauth-card`} className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.86)] p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--foreground)]">{item.name}</p>
                          <p className="mt-1 text-xs text-[var(--fx-muted)]">{authSummary(item)}</p>
                        </div>
                        <span className={`inline-flex rounded-full border px-2.5 py-1 text-[0.72rem] font-medium ${oauthConnectionTone(oauthStatus)}`}>
                          {oauthConnectionLabel(oauthStatus)}
                        </span>
                      </div>
                      <div className="mt-3 grid gap-2 text-xs text-[var(--foreground)] sm:grid-cols-2">
                        <div>
                          <p className="font-medium text-[var(--fx-muted)]">Account</p>
                          <p className="mt-1">{oauthStatus?.account_label || "(unassigned)"}</p>
                        </div>
                        <div>
                          <p className="font-medium text-[var(--fx-muted)]">Token health</p>
                          <p className="mt-1">{oauthStatus?.has_access_token ? "Access token present" : "No access token"}</p>
                        </div>
                      </div>
                      <div className="mt-3 flex items-center justify-between gap-2 text-xs text-[var(--fx-muted)]">
                          <div className="space-y-1">
                            <p>{oauthStatus?.scopes.length ?? 0} scope(s)</p>
                            {oauthPresetDriftLabel(item.metadata_json) ? (
                              <p>{oauthPresetDriftLabel(item.metadata_json)}</p>
                            ) : null}
                          </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : null}
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Auth</th>
              <th className="px-3 py-2 text-left">Secret ref</th>
              <th className="px-3 py-2 text-left">Last test</th>
              <th className="px-3 py-2 text-left">Base URL / DSN</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td className="px-3 py-3 text-xs text-[var(--foreground)]" colSpan={8}>Loading integrations...</td>
              </tr>
            ) : listError ? (
              <tr>
                <td role="alert" className="px-3 py-3 text-xs text-[var(--fx-danger)]" colSpan={8}>Could not load integrations: {listError}</td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-xs text-[var(--foreground)]" colSpan={8}>No integrations configured yet. Add one from the catalog above, or open Advanced to connect a custom API or MCP server.</td>
              </tr>
            ) : (
              items.map((item) => {
                const oauthStatus = oauthStatuses[item.id] ?? item.oauth_status ?? null;
                const isMcp = String(readAuthConfig(item.metadata_json).protocol ?? item.metadata_json?.protocol ?? "") === "mcp";
                return (
                  <tr key={item.id} className="border-t border-[var(--fx-border)] align-top hover:bg-[hsl(var(--muted)/0.16)]">
                    <td className="px-3 py-3 font-medium text-[var(--foreground)]">
                      <div className="space-y-1">
                        <p>{item.name}</p>
                        {item.capabilities && item.capabilities.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {item.capabilities.map((capability) => (
                              <span key={`${item.id}-${capability}`} className="fx-pill px-2 py-0.5 text-[0.68rem] font-medium text-[var(--foreground)]">
                                {capability}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="text-[11px] text-[var(--fx-muted)]">No capability mapping</p>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="fx-pill px-2.5 py-1 text-[0.72rem] font-medium text-[var(--foreground)]">{item.type}</span>
                        <span className="fx-muted rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[10px] uppercase">
                          {isMcp ? "MCP" : "API"}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <span className={`inline-flex rounded-full border px-2.5 py-1 text-[0.72rem] font-medium ${integrationStatusTone(item.status)}`}>
                        {item.status}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div>
                        <span className="fx-pill px-2.5 py-1 text-[0.72rem] font-medium text-[var(--foreground)]">{item.auth_type}</span>
                      </div>
                      <div className="fx-muted text-[11px]">{authSummary(item)}</div>
                      {item.auth_type === "oauth2" && oauthStatus ? (
                        <div className="mt-2 space-y-1">
                          <span className={`inline-flex rounded-full border px-2.5 py-1 text-[0.68rem] font-medium ${oauthConnectionTone(oauthStatus)}`}>
                            {oauthConnectionLabel(oauthStatus)}
                          </span>
                          {oauthPresetDriftLabel(item.metadata_json) ? (
                            <p className="text-[11px] text-[var(--fx-muted)]">{oauthPresetDriftLabel(item.metadata_json)}</p>
                          ) : null}
                        </div>
                      ) : null}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{item.secret_ref || "(none)"}</td>
                    <td className="px-3 py-2 text-xs">
                      {(() => {
                        const lastTest = readLastTest(item.metadata_json);
                        if (!lastTest) {
                          return <span className="fx-muted">Not tested</span>;
                        }
                        return (
                          <div className="space-y-0.5">
                            <p className={lastTest.ok ? "text-[var(--fx-success)]" : "text-[var(--fx-danger)]"}>{lastTest.ok ? "OK" : "Failed"}</p>
                            {Array.isArray(lastTest.warnings) && lastTest.warnings.length > 0 ? (
                              <p className="fx-muted">{lastTest.warnings.length} warning(s)</p>
                            ) : null}
                          </div>
                        );
                      })()}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{item.base_url || "(unset)"}</td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-2">
                        <button onClick={() => handleEdit(item)} className="fx-btn-secondary px-2.5 py-1.5 text-xs">
                          Edit
                        </button>
                        <button onClick={() => handleTest(item.id)} className="fx-btn-secondary px-2.5 py-1.5 text-xs" disabled={testingId === item.id}>
                          {testingId === item.id ? "Testing..." : "Test"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
        </div>
      </details>

    </section>
  );
}
