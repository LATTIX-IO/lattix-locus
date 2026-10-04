"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ListField,
  LoadState,
  SaveBar,
  SectionHeader,
  SettingsGroup,
  ToggleRow,
  parseList,
  toListText,
  useDraft,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import { getIntegrations, getMcpConnections } from "@/lib/api";
import type { IntegrationDefinition, MCPConnectionDefinition, PlatformSettings } from "@/types/locus";

type ConnectionsDraft = {
  mcp_require_local_server: boolean;
  allowed_mcp_server_urls: string;
  enforce_integration_policies: boolean;
  require_signed_integrations: boolean;
  require_sandbox_for_third_party: boolean;
  allow_local_unsigned_integrations: boolean;
};

function toDraft(settings: PlatformSettings): ConnectionsDraft {
  return {
    mcp_require_local_server: Boolean(settings.mcp_require_local_server),
    allowed_mcp_server_urls: toListText(settings.allowed_mcp_server_urls),
    enforce_integration_policies: Boolean(settings.enforce_integration_policies),
    require_signed_integrations: Boolean(settings.require_signed_integrations),
    require_sandbox_for_third_party: Boolean(settings.require_sandbox_for_third_party),
    allow_local_unsigned_integrations: Boolean(settings.allow_local_unsigned_integrations),
  };
}

type Inventory = { mcp: MCPConnectionDefinition[]; integrations: IntegrationDefinition[] };

/**
 * The rules for connections. The connections themselves (MCP servers and
 * integrations, with OAuth and approval) are managed in Library → Connections.
 */
export function ConnectionsSection() {
  const platform = usePlatformResource();
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [inventoryError, setInventoryError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getMcpConnections(), getIntegrations()])
      .then(([mcp, integrations]) => {
        if (!cancelled) setInventory({ mcp, integrations });
      })
      .catch((reason: unknown) => {
        if (!cancelled) setInventoryError(reason instanceof Error ? reason.message : "Could not load connections.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initial = useMemo(() => (platform.settings ? toDraft(platform.settings) : null), [platform.settings]);
  const { draft, dirty, saving, message, update, commit, reset } = useDraft<ConnectionsDraft>(initial);

  const approvedMcp = inventory?.mcp.filter((item) => item.status === "approved").length ?? 0;
  const configuredIntegrations = inventory?.integrations.filter((item) => item.status === "configured").length ?? 0;

  return (
    <div className="flex flex-col gap-4">
      <SectionHeader
        title="Connections"
        description="Which MCP servers and integrations the agents may use."
        actions={
          <Button asChild variant="secondary" size="sm">
            <Link href="/library/connections">Manage connections</Link>
          </Button>
        }
      />

      <SettingsGroup title="Connected now">
        {inventoryError ? (
          <p role="alert" className="text-[13px] text-destructive">
            Could not load connections: {inventoryError}
          </p>
        ) : !inventory ? (
          <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>
        ) : (
          <div className="flex flex-wrap gap-2 text-[13px]">
            <Badge variant={approvedMcp ? "success" : "outline"}>
              {approvedMcp} of {inventory.mcp.length} MCP servers approved
            </Badge>
            <Badge variant={configuredIntegrations ? "success" : "outline"}>
              {configuredIntegrations} of {inventory.integrations.length} integrations configured
            </Badge>
          </div>
        )}
      </SettingsGroup>

      {!draft ? (
        <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
      ) : (
        <SettingsGroup title="Rules" description="Applied to every connection, whoever adds it.">
          <div className="grid gap-2 lg:grid-cols-2">
            <ToggleRow
              id="connections-mcp-local"
              label="Local MCP servers only"
              description="Remote MCP servers need to be on the list below."
              checked={draft.mcp_require_local_server}
              onCheckedChange={(next) => update("mcp_require_local_server", next)}
            />
            <ToggleRow
              id="connections-enforce-policies"
              label="Enforce integration policies"
              checked={draft.enforce_integration_policies}
              onCheckedChange={(next) => update("enforce_integration_policies", next)}
            />
            <ToggleRow
              id="connections-signed"
              label="Require signed integrations"
              checked={draft.require_signed_integrations}
              onCheckedChange={(next) => update("require_signed_integrations", next)}
            />
            <ToggleRow
              id="connections-sandbox"
              label="Sandbox third-party integrations"
              checked={draft.require_sandbox_for_third_party}
              onCheckedChange={(next) => update("require_sandbox_for_third_party", next)}
            />
            <ToggleRow
              id="connections-local-unsigned"
              label="Allow unsigned local integrations"
              checked={draft.allow_local_unsigned_integrations}
              onCheckedChange={(next) => update("allow_local_unsigned_integrations", next)}
            />
          </div>
          <ListField
            id="connections-mcp-urls"
            label="Allowed MCP server URLs"
            value={draft.allowed_mcp_server_urls}
            onChange={(value) => update("allowed_mcp_server_urls", value)}
            placeholder={"http://127.0.0.1:8787"}
          />
          <SaveBar
            dirty={dirty}
            saving={saving}
            message={message}
            onReset={reset}
            onSave={() =>
              void commit((next) =>
                platform.save({
                  mcp_require_local_server: next.mcp_require_local_server,
                  allowed_mcp_server_urls: parseList(next.allowed_mcp_server_urls),
                  enforce_integration_policies: next.enforce_integration_policies,
                  require_signed_integrations: next.require_signed_integrations,
                  require_sandbox_for_third_party: next.require_sandbox_for_third_party,
                  allow_local_unsigned_integrations: next.allow_local_unsigned_integrations,
                }),
              )
            }
          />
        </SettingsGroup>
      )}
    </div>
  );
}
