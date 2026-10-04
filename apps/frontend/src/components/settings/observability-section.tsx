"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  ControlRow,
  LoadState,
  SaveBar,
  SectionHeader,
  SettingsGroup,
  TextField,
  ToggleRow,
  findControl,
  positiveNumber,
  useDraft,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import { getTelemetrySummary, type TelemetrySummary } from "@/lib/api";
import type { PlatformSettings } from "@/types/locus";

type TelemetryDraft = {
  telemetry_capture_content: boolean;
  telemetry_payload_retention_days: string;
  telemetry_otlp_enabled: boolean;
  telemetry_otlp_endpoint: string;
  telemetry_otlp_auth_secret_ref: string;
  telemetry_langsmith_enabled: boolean;
  telemetry_langsmith_endpoint: string;
  telemetry_langsmith_project: string;
  telemetry_langsmith_api_key_ref: string;
};

/** Missing fields read as off: every exporter and content capture default off (LOCUS-375). */
function toDraft(settings: PlatformSettings): TelemetryDraft {
  return {
    telemetry_capture_content: settings.telemetry_capture_content === true,
    telemetry_payload_retention_days: String(settings.telemetry_payload_retention_days ?? 90),
    telemetry_otlp_enabled: settings.telemetry_otlp_enabled === true,
    telemetry_otlp_endpoint: settings.telemetry_otlp_endpoint ?? "",
    telemetry_otlp_auth_secret_ref: settings.telemetry_otlp_auth_secret_ref ?? "",
    telemetry_langsmith_enabled: settings.telemetry_langsmith_enabled === true,
    telemetry_langsmith_endpoint: settings.telemetry_langsmith_endpoint ?? "",
    telemetry_langsmith_project: settings.telemetry_langsmith_project ?? "",
    telemetry_langsmith_api_key_ref: settings.telemetry_langsmith_api_key_ref ?? "",
  };
}

function toPatch(draft: TelemetryDraft): Partial<PlatformSettings> {
  return {
    telemetry_capture_content: draft.telemetry_capture_content,
    telemetry_payload_retention_days: positiveNumber(draft.telemetry_payload_retention_days, 90),
    telemetry_otlp_enabled: draft.telemetry_otlp_enabled,
    telemetry_otlp_endpoint: draft.telemetry_otlp_endpoint.trim(),
    telemetry_otlp_auth_secret_ref: draft.telemetry_otlp_auth_secret_ref.trim(),
    telemetry_langsmith_enabled: draft.telemetry_langsmith_enabled,
    telemetry_langsmith_endpoint: draft.telemetry_langsmith_endpoint.trim(),
    telemetry_langsmith_project: draft.telemetry_langsmith_project.trim(),
    telemetry_langsmith_api_key_ref: draft.telemetry_langsmith_api_key_ref.trim(),
  };
}

export function ObservabilitySection() {
  const platform = usePlatformResource({ withPolicy: true });
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getTelemetrySummary(24)
      .then((next) => {
        if (!cancelled) setSummary(next);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setSummaryError(reason instanceof Error ? reason.message : "The local trace store is unavailable.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const initial = useMemo(() => (platform.settings ? toDraft(platform.settings) : null), [platform.settings]);
  const { draft, dirty, saving, message, update, commit, reset } = useDraft<TelemetryDraft>(initial);

  return (
    <div className="flex flex-col gap-4">
      <SectionHeader
        title="Observability"
        description="Traces of what the agents did, kept on this machine. Nothing leaves unless you turn on an exporter."
        actions={
          <Button asChild variant="secondary" size="sm">
            <Link href="/activity/traces">Open traces</Link>
          </Button>
        }
      />

      {!draft ? (
        <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
      ) : (
        <>
          <SettingsGroup title="Posture">
            <ControlRow control={findControl(platform.policy, "telemetry_local")} fallbackLabel="Local telemetry" />
            <p className="text-[13px] text-muted-foreground" role="status">
              {summaryError
                ? `Trace store: ${summaryError}`
                : !summary
                  ? "Loading the last 24 hours…"
                  : summary.empty || !summary.runs
                    ? "No traced runs in the last 24 hours."
                    : `${summary.runs} traced run${summary.runs === 1 ? "" : "s"} in the last 24 hours.`}
            </p>
          </SettingsGroup>

          <SettingsGroup title="Content capture" description="Prompts and outputs in traces. Off keeps only timings, tokens and outcomes.">
            <ToggleRow
              id="telemetry-capture-content"
              label="Capture prompt and output content"
              description="Redacted for PII before it is stored."
              checked={draft.telemetry_capture_content}
              onCheckedChange={(next) => update("telemetry_capture_content", next)}
            />
            <TextField
              id="telemetry-retention"
              label="Keep captured content for (days)"
              inputMode="numeric"
              value={draft.telemetry_payload_retention_days}
              onChange={(value) => update("telemetry_payload_retention_days", value)}
            />
          </SettingsGroup>

          <SettingsGroup title="Exporters" description="Copies of traces sent off this machine. Keys are named, never typed here.">
            <ToggleRow
              id="telemetry-otlp-enabled"
              label="OpenTelemetry (OTLP) collector"
              checked={draft.telemetry_otlp_enabled}
              onCheckedChange={(next) => update("telemetry_otlp_enabled", next)}
            />
            <div className="grid gap-3 md:grid-cols-2">
              <TextField
                id="telemetry-otlp-endpoint"
                label="OTLP endpoint"
                value={draft.telemetry_otlp_endpoint}
                onChange={(value) => update("telemetry_otlp_endpoint", value)}
                placeholder="http://127.0.0.1:4318"
              />
              <TextField
                id="telemetry-otlp-secret"
                label="Auth secret name"
                description="A native secret such as OTLP_TOKEN."
                value={draft.telemetry_otlp_auth_secret_ref}
                onChange={(value) => update("telemetry_otlp_auth_secret_ref", value)}
              />
            </div>
            <ToggleRow
              id="telemetry-langsmith-enabled"
              label="LangSmith (hosted)"
              description="Data leaves this machine for a hosted, proprietary service."
              checked={draft.telemetry_langsmith_enabled}
              onCheckedChange={(next) => update("telemetry_langsmith_enabled", next)}
            />
            <div className="grid gap-3 md:grid-cols-3">
              <TextField
                id="telemetry-langsmith-endpoint"
                label="LangSmith endpoint"
                value={draft.telemetry_langsmith_endpoint}
                onChange={(value) => update("telemetry_langsmith_endpoint", value)}
              />
              <TextField
                id="telemetry-langsmith-project"
                label="Project"
                value={draft.telemetry_langsmith_project}
                onChange={(value) => update("telemetry_langsmith_project", value)}
              />
              <TextField
                id="telemetry-langsmith-key"
                label="API key secret name"
                value={draft.telemetry_langsmith_api_key_ref}
                onChange={(value) => update("telemetry_langsmith_api_key_ref", value)}
              />
            </div>
          </SettingsGroup>

          <SaveBar
            dirty={dirty}
            saving={saving}
            message={message}
            onReset={reset}
            onSave={() => void commit((next) => platform.save(toPatch(next)))}
          />
        </>
      )}
    </div>
  );
}
