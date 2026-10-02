"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  approveMcpConnection,
  getMcpConnections,
  getMcpStarterTemplates,
  saveMcpConnection,
  validateMcpConnection,
} from "@/lib/api";
import type { MCPConnectionDefinition, MCPStarterTemplate } from "@/types/frontier";

function parseLineList(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function formatLineList(values: string[] | undefined): string {
  return (values ?? []).join("\n");
}

function mcpStatusTone(status: MCPConnectionDefinition["status"]): string {
  if (status === "approved") {
    return "border-[color-mix(in_srgb,var(--fx-success)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-success)_10%,transparent)] text-[var(--fx-success)]";
  }
  if (status === "validated") {
    return "border-[color-mix(in_srgb,var(--fx-primary)_36%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_10%,transparent)] text-[var(--foreground)]";
  }
  if (status === "validation_failed" || status === "rejected" || status === "disabled") {
    return "border-[color-mix(in_srgb,var(--fx-danger)_34%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_10%,transparent)] text-[var(--fx-danger)]";
  }
  return "border-[var(--fx-border)] bg-[hsl(var(--card)/0.74)] text-[var(--fx-muted)]";
}

function starterAuthLabel(authType: MCPStarterTemplate["auth_type"]): string {
  switch (authType) {
    case "api_key":
      return "API key";
    case "bearer":
      return "Bearer token";
    case "oauth2":
      return "OAuth2";
    case "basic":
      return "Basic auth";
    case "mcp_token":
      return "MCP token";
    default:
      return "No auth";
  }
}

export function McpConnectionsPanel() {
  const [connections, setConnections] = useState<MCPConnectionDefinition[]>([]);
  const [connectionsLoading, setConnectionsLoading] = useState(true);
  const [connectionsError, setConnectionsError] = useState("");
  const [starterTemplates, setStarterTemplates] = useState<MCPStarterTemplate[]>([]);
  const [starterTemplatesLoading, setStarterTemplatesLoading] = useState(true);
  const [starterCatalogError, setStarterCatalogError] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [selectedStarterId, setSelectedStarterId] = useState<string>("");
  const [name, setName] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [transport, setTransport] = useState<MCPConnectionDefinition["transport"]>("streamable_http");
  const [authType, setAuthType] = useState<MCPConnectionDefinition["auth_type"]>("none");
  const [secretRefInput, setSecretRefInput] = useState("");
  const [secretConfigured, setSecretConfigured] = useState(false);
  const [capabilities, setCapabilities] = useState("");
  const [permissionScopes, setPermissionScopes] = useState("");
  const [dataAccess, setDataAccess] = useState("");
  const [egressAllowlist, setEgressAllowlist] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const selectedStarter = starterTemplates.find((item) => item.id === selectedStarterId) ?? null;
  const approvedCount = connections.filter((item) => item.status === "approved").length;
  const validatedCount = connections.filter((item) => item.status === "validated").length;
  const draftCount = connections.filter((item) => item.status === "draft").length;
  const starterTemplateGroups = useMemo(
    () =>
      Array.from(new Set(starterTemplates.map((item) => item.wave)))
        .sort((left, right) => left - right)
        .map((wave) => ({
          wave,
          items: starterTemplates.filter((item) => item.wave === wave),
        })),
    [starterTemplates],
  );

  const applyStarterTemplate = useCallback((template: MCPStarterTemplate): void => {
    setEditingId(null);
    setSelectedStarterId(template.id);
    setName(template.name);
    setServerUrl("");
    setTransport(template.transport);
    setAuthType(template.auth_type);
    setSecretRefInput(template.secret_ref);
    setSecretConfigured(false);
    setCapabilities(formatLineList(template.capabilities));
    setPermissionScopes(formatLineList(template.permission_scopes));
    setDataAccess(formatLineList(template.data_access));
    setEgressAllowlist(formatLineList(template.egress_allowlist));
    setStatusMessage(`${template.name} MCP starter loaded.`);
  }, []);

  function resetForm(nextStarterId?: string): void {
    const fallbackStarter = starterTemplates.find((item) => item.id === (nextStarterId ?? selectedStarterId)) ?? starterTemplates[0] ?? null;
    if (fallbackStarter) {
      applyStarterTemplate(fallbackStarter);
      return;
    }
    setEditingId(null);
    setSelectedStarterId("");
    setName("");
    setServerUrl("");
    setTransport("streamable_http");
    setAuthType("none");
    setSecretRefInput("");
    setSecretConfigured(false);
    setCapabilities("");
    setPermissionScopes("");
    setDataAccess("");
    setEgressAllowlist("");
  }

  function handleEdit(connection: MCPConnectionDefinition): void {
    setEditingId(connection.id);
    setSelectedStarterId(connection.starter_id);
    setName(connection.name);
    setServerUrl(connection.server_url);
    setTransport(connection.transport);
    setAuthType(connection.auth_type);
    setSecretRefInput("");
    setSecretConfigured(Boolean(connection.secret_configured));
    setCapabilities(formatLineList(connection.capabilities));
    setPermissionScopes(formatLineList(connection.permission_scopes));
    setDataAccess(formatLineList(connection.data_access));
    setEgressAllowlist(formatLineList(connection.egress_allowlist));
    setStatusMessage("");
  }

  async function refreshConnections(): Promise<void> {
    setConnectionsLoading(true);
    setConnectionsError("");
    try {
      const nextConnections = await getMcpConnections();
      setConnections(nextConnections);
    } catch (error) {
      setConnectionsError(error instanceof Error ? error.message : "Unable to load staged MCP connections.");
    } finally {
      setConnectionsLoading(false);
    }
  }

  useEffect(() => {
    void refreshConnections();
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadStarterTemplates() {
      setStarterTemplatesLoading(true);
      setStarterCatalogError("");
      try {
        const templates = await getMcpStarterTemplates();
        if (cancelled) {
          return;
        }
        setStarterTemplates(templates);
      } catch (error) {
        if (!cancelled) {
          setStarterCatalogError(error instanceof Error ? error.message : "Unable to load MCP starter catalog.");
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
    const defaultStarter = starterTemplates[0];
    if (!defaultStarter || selectedStarterId || editingId !== null) {
      return;
    }
    applyStarterTemplate(defaultStarter);
  }, [applyStarterTemplate, editingId, selectedStarterId, starterTemplates]);

  async function handleSave() {
    setStatusMessage("");
    if (!selectedStarterId) {
      setStatusMessage("Choose an MCP starter before saving.");
      return;
    }
    const payload: Record<string, unknown> = {
      ...(editingId ? { id: editingId } : {}),
      starter_id: selectedStarterId,
      name: name.trim() || selectedStarter?.name || "Untitled MCP connection",
      server_url: serverUrl.trim(),
      transport,
      auth_type: authType,
      capabilities: parseLineList(capabilities),
      permission_scopes: parseLineList(permissionScopes),
      data_access: parseLineList(dataAccess),
      egress_allowlist: parseLineList(egressAllowlist),
    };
    if (secretRefInput.trim() || !editingId || !secretConfigured || authType === "none") {
      payload.secret_ref = authType === "none" ? "" : secretRefInput.trim();
    }

    setBusyKey(editingId ? `save:${editingId}` : "save:new");
    try {
      await saveMcpConnection(payload);
      await refreshConnections();
      resetForm(selectedStarterId);
      setStatusMessage(editingId ? "MCP connection updated." : "MCP connection saved as draft.");
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to save MCP connection.");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleValidate(connection: MCPConnectionDefinition): Promise<void> {
    setBusyKey(`validate:${connection.id}`);
    try {
      const response = await validateMcpConnection(connection.id);
      await refreshConnections();
      const details = [
        response.validation.errors.length > 0 ? response.validation.errors.join("; ") : "validation passed",
        response.validation.warnings.length > 0 ? `warnings: ${response.validation.warnings.join("; ")}` : "",
      ]
        .filter(Boolean)
        .join(" • ");
      setStatusMessage(`${connection.name}: ${details}`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to validate MCP connection.");
    } finally {
      setBusyKey(null);
    }
  }

  async function handleApprove(connection: MCPConnectionDefinition): Promise<void> {
    setBusyKey(`approve:${connection.id}`);
    try {
      await approveMcpConnection(connection.id);
      await refreshConnections();
      setStatusMessage(`${connection.name} approved for runtime use.`);
    } catch (error) {
      setStatusMessage(error instanceof Error ? error.message : "Unable to approve MCP connection.");
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div className="space-y-5">
      <div className="fx-panel rounded-[1.6rem] p-5 shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Secure MCP staging</p>
            <h2 className="mt-2 text-[1.1rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Staged MCP connections</h2>
            <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">
              Register approved MCP endpoints through a staged catalog, validate them against platform policy, then approve them before tool nodes can reference them.
            </p>
          </div>
          <div className="grid min-w-[220px] gap-2 sm:grid-cols-3">
            <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
              <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Draft</p>
              <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{draftCount}</p>
            </div>
            <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
              <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Validated</p>
              <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{validatedCount}</p>
            </div>
            <div className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.8)] px-3 py-2.5">
              <p className="text-[0.72rem] font-medium text-[var(--fx-muted)]">Approved</p>
              <p className="mt-1 text-lg font-semibold text-[var(--foreground)]">{approvedCount}</p>
            </div>
          </div>
        </div>

        <div className="mb-5 space-y-4 rounded-[1.25rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Starter catalog</p>
              <h3 className="mt-2 text-base font-semibold text-[var(--foreground)]">Wave-based MCP catalog</h3>
              <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">
                Pick a supported MCP starter, then fill the environment-specific server URL and secret reference that will be validated against platform guardrails.
              </p>
            </div>
            {selectedStarter ? (
              <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Using {selectedStarter.name}</div>
            ) : null}
          </div>

          {starterTemplatesLoading ? (
            <div className="rounded-[1rem] border border-dashed border-[var(--fx-border)] px-3 py-3 text-sm text-[var(--fx-muted)]">
              Loading MCP starter catalog...
            </div>
          ) : starterCatalogError ? (
            <div className="rounded-[1rem] border border-[color-mix(in_srgb,var(--fx-danger)_30%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-danger)_8%,transparent)] px-3 py-3 text-sm text-[var(--foreground)]">
              {starterCatalogError}
            </div>
          ) : (
            <>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {starterTemplateGroups.map((group) => (
                  <div key={group.wave} className="space-y-2 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.82)] p-3">
                    <p className="text-[0.72rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Wave {group.wave}</p>
                    <div className="flex flex-wrap gap-2">
                      {group.items.map((template) => (
                        <button
                          key={template.id}
                          type="button"
                          onClick={() => applyStarterTemplate(template)}
                          className={[
                            "rounded-full border px-3 py-1.5 text-xs font-medium",
                            selectedStarterId === template.id
                              ? "border-[color-mix(in_srgb,var(--fx-primary)_45%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_10%,transparent)] text-[var(--foreground)]"
                              : "border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] text-[var(--fx-muted)]",
                          ].join(" ")}
                        >
                          {template.name}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              {selectedStarter ? (
                <div className="grid gap-3 rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.86)] p-4 lg:grid-cols-[1.1fr_1fr_1fr]">
                  <div className="space-y-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Starter details</p>
                      <h4 className="mt-2 text-base font-semibold text-[var(--foreground)]">{selectedStarter.name}</h4>
                      <p className="mt-1 text-sm leading-6 text-[var(--fx-muted)]">{selectedStarter.summary}</p>
                    </div>
                    <div className="grid gap-2 text-xs text-[var(--foreground)] sm:grid-cols-2">
                      <div className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-2.5">
                        <p className="font-medium text-[var(--fx-muted)]">Transport</p>
                        <p className="mt-1">{selectedStarter.transport}</p>
                      </div>
                      <div className="rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-2.5">
                        <p className="font-medium text-[var(--fx-muted)]">Auth preset</p>
                        <p className="mt-1">{starterAuthLabel(selectedStarter.auth_type)}</p>
                      </div>
                    </div>
                  </div>
                  <div className="space-y-3 rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Capabilities</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(selectedStarter.capabilities ?? []).length > 0 ? (
                          (selectedStarter.capabilities ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No starter capabilities.</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Permission scopes</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(selectedStarter.permission_scopes ?? []).length > 0 ? (
                          (selectedStarter.permission_scopes ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No starter permission scopes.</span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="space-y-3 rounded-[0.9rem] border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] px-3 py-3">
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Data access</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(selectedStarter.data_access ?? []).length > 0 ? (
                          (selectedStarter.data_access ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No starter data domains.</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">Egress allowlist</p>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {(selectedStarter.egress_allowlist ?? []).length > 0 ? (
                          (selectedStarter.egress_allowlist ?? []).map((entry) => (
                            <span key={entry} className="fx-pill px-2 py-1 text-[0.68rem] font-medium text-[var(--fx-muted)]">{entry}</span>
                          ))
                        ) : (
                          <span className="text-xs text-[var(--fx-muted)]">No starter egress domains.</span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">MCP name</span>
            <input className="fx-field mt-1 w-full px-2 py-2 text-sm" value={name} onChange={(event) => setName(event.target.value)} placeholder="GitHub MCP" />
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">MCP starter</span>
            <select
              className="fx-field mt-1 w-full px-2 py-2 text-sm"
              value={selectedStarterId}
              onChange={(event) => {
                const template = starterTemplates.find((item) => item.id === event.target.value);
                if (template) {
                  applyStarterTemplate(template);
                }
              }}
            >
              {starterTemplates.map((template) => (
                <option key={template.id} value={template.id}>{template.name}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">MCP server URL</span>
            <input
              className="fx-field mt-1 w-full px-2 py-2 text-sm"
              value={serverUrl}
              onChange={(event) => setServerUrl(event.target.value)}
              placeholder="http://localhost:7071/mcp/github"
            />
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">MCP transport</span>
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={transport} onChange={(event) => setTransport(event.target.value as MCPConnectionDefinition["transport"])}>
              <option value="streamable_http">streamable_http</option>
              <option value="sse">sse</option>
              <option value="custom">custom</option>
            </select>
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">MCP authentication mode</span>
            <select className="fx-field mt-1 w-full px-2 py-2 text-sm" value={authType} onChange={(event) => setAuthType(event.target.value as MCPConnectionDefinition["auth_type"])}>
              <option value="none">No auth</option>
              <option value="api_key">API key</option>
              <option value="bearer">Bearer token</option>
              <option value="oauth2">OAuth2 (MCP)</option>
              <option value="basic">Basic auth</option>
              <option value="mcp_token">MCP token</option>
            </select>
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">MCP secret path</span>
            <input
              className="fx-field mt-1 w-full px-2 py-2 text-sm"
              value={secretRefInput}
              onChange={(event) => setSecretRefInput(event.target.value)}
              placeholder={selectedStarter?.secret_ref || "secret/integrations/mcp/provider/token"}
            />
            <span className="mt-1 block text-[11px] fx-muted">
              {editingId && secretConfigured && !secretRefInput.trim()
                ? "Leave blank to keep the existing server-side secret path."
                : "Use a secret path, not a raw token or password."}
            </span>
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">MCP capabilities</span>
            <textarea className="fx-field mt-1 min-h-24 w-full px-2 py-2 text-sm" value={capabilities} onChange={(event) => setCapabilities(event.target.value)} placeholder="/repo-read&#10;/issue-triage" />
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">MCP permission bundle</span>
            <textarea className="fx-field mt-1 min-h-24 w-full px-2 py-2 text-sm" value={permissionScopes} onChange={(event) => setPermissionScopes(event.target.value)} placeholder="repo:read&#10;issues:write" />
          </label>
          <label className="block text-sm text-[var(--foreground)]">
            <span className="font-medium">Data access</span>
            <textarea className="fx-field mt-1 min-h-24 w-full px-2 py-2 text-sm" value={dataAccess} onChange={(event) => setDataAccess(event.target.value)} placeholder="source-code-metadata&#10;issue-data" />
          </label>
          <label className="block text-sm text-[var(--foreground)] md:col-span-2">
            <span className="font-medium">Egress allowlist</span>
            <textarea className="fx-field mt-1 min-h-24 w-full px-2 py-2 text-sm" value={egressAllowlist} onChange={(event) => setEgressAllowlist(event.target.value)} placeholder="api.github.com&#10;uploads.github.com" />
          </label>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <button onClick={() => void handleSave()} className="fx-btn-primary px-3 py-2 text-sm" disabled={busyKey?.startsWith("save:") === true}>
            {editingId ? "Update MCP connection" : "Save MCP connection"}
          </button>
          {editingId ? (
            <button onClick={() => resetForm(selectedStarterId)} className="fx-btn-secondary px-3 py-2 text-sm">
              Cancel edit
            </button>
          ) : null}
        </div>
      </div>

      <div className="fx-panel overflow-hidden rounded-[1.6rem] shadow-[0_20px_48px_rgba(15,23,42,0.05)]">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--ui-border)] px-4 py-4">
          <div>
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-[var(--fx-muted)]">MCP inventory</p>
            <h2 className="mt-2 text-[1.05rem] font-semibold tracking-[-0.02em] text-[var(--foreground)]">Saved staged MCP connections</h2>
          </div>
          <div className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--fx-muted)]">Validation must pass before admin approval</div>
        </div>
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Starter</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Transport</th>
              <th className="px-3 py-2 text-left">Auth</th>
              <th className="px-3 py-2 text-left">Server URL</th>
              <th className="px-3 py-2 text-left">Approval</th>
              <th className="px-3 py-2 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {connectionsLoading ? (
              <tr>
                <td className="px-3 py-3 text-xs text-[var(--foreground)]" colSpan={8}>Loading staged MCP connections...</td>
              </tr>
            ) : connectionsError ? (
              <tr>
                <td className="px-3 py-3 text-xs text-[var(--foreground)]" colSpan={8}>{connectionsError}</td>
              </tr>
            ) : connections.length === 0 ? (
              <tr>
                <td className="px-3 py-3 text-xs text-[var(--foreground)]" colSpan={8}>No staged MCP connections configured yet.</td>
              </tr>
            ) : (
              connections.map((connection) => (
                <tr key={connection.id} className="border-t border-[var(--fx-border)] align-top hover:bg-[hsl(var(--muted)/0.16)]">
                  <td className="px-3 py-3 font-medium text-[var(--foreground)]">
                    <div className="space-y-1">
                      <p>{connection.name}</p>
                      {(connection.capabilities ?? []).length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {(connection.capabilities ?? []).slice(0, 3).map((entry) => (
                            <span key={`${connection.id}-${entry}`} className="fx-pill px-2 py-0.5 text-[0.68rem] font-medium text-[var(--foreground)]">{entry}</span>
                          ))}
                        </div>
                      ) : (
                        <p className="text-[11px] text-[var(--fx-muted)]">No capability mapping</p>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-xs text-[var(--foreground)]">{connection.starter_id} / wave {connection.wave}</td>
                  <td className="px-3 py-3">
                    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[0.72rem] font-medium ${mcpStatusTone(connection.status)}`}>
                      {connection.status}
                    </span>
                    {connection.last_validation_error ? (
                      <p className="mt-2 max-w-[220px] text-[11px] text-[var(--fx-danger)]">{connection.last_validation_error}</p>
                    ) : null}
                  </td>
                  <td className="px-3 py-3 text-xs text-[var(--foreground)]">{connection.transport}</td>
                  <td className="px-3 py-3 text-xs text-[var(--foreground)]">
                    <p>{connection.auth_type}</p>
                    <p className="mt-1 text-[var(--fx-muted)]">{connection.secret_configured ? "secret configured" : "no secret configured"}</p>
                  </td>
                  <td className="px-3 py-3 font-mono text-xs text-[var(--foreground)]">{connection.server_url || "(unset)"}</td>
                  <td className="px-3 py-3 text-xs text-[var(--foreground)]">
                    {connection.approved_at ? (
                      <div className="space-y-1">
                        <p>{connection.approved_by || "approved"}</p>
                        <p className="text-[var(--fx-muted)]">{connection.approved_at}</p>
                      </div>
                    ) : connection.last_validated_at ? (
                      <div className="space-y-1">
                        <p>Validated</p>
                        <p className="text-[var(--fx-muted)]">{connection.last_validated_at}</p>
                      </div>
                    ) : (
                      <span className="text-[var(--fx-muted)]">Awaiting validation</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => handleEdit(connection)} className="fx-btn-secondary px-2.5 py-1.5 text-xs">
                        Edit
                      </button>
                      <button
                        onClick={() => void handleValidate(connection)}
                        className="fx-btn-secondary px-2.5 py-1.5 text-xs"
                        disabled={busyKey === `validate:${connection.id}`}
                      >
                        {busyKey === `validate:${connection.id}` ? "Validating..." : "Validate"}
                      </button>
                      <button
                        onClick={() => void handleApprove(connection)}
                        className="fx-btn-primary px-2.5 py-1.5 text-xs"
                        disabled={connection.status !== "validated" || busyKey === `approve:${connection.id}`}
                      >
                        {busyKey === `approve:${connection.id}` ? "Approving..." : "Approve"}
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {statusMessage ? <p className="rounded-[1rem] border border-[var(--fx-border)] bg-[hsl(var(--card)/0.84)] px-3 py-2 text-xs text-[var(--foreground)]">{statusMessage}</p> : null}
    </div>
  );
}