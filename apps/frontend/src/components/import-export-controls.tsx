"use client";

import { useRef, useState } from "react";

import {
  downloadDefinitionExport,
  importDefinitionFile,
  type ExportKind,
} from "@/lib/api";

/**
 * Export / import controls for a definition (agent, workflow, playbook) or the
 * whole platform bundle. Export streams a JSON/YAML download; import reads a
 * JSON/YAML file and applies it via the backend (create/update + persist).
 */
export function ImportExportControls({
  kind,
  id,
  onImported,
  compact,
}: {
  kind: ExportKind;
  /** Required for per-item export; omitted for import-only / bundle. */
  id?: string | null;
  onImported?: (result: { id?: string }) => void;
  compact?: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canExport = kind === "bundle" || Boolean(id);

  async function onExport(format: "json" | "yaml") {
    setError(null);
    setBusy(true);
    try {
      await downloadDefinitionExport(kind, id ?? null, format);
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  async function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-importing the same file
    if (!file) return;
    setError(null);
    setBusy(true);
    try {
      const result = await importDefinitionFile(kind, file);
      if (onImported) {
        onImported({ id: typeof result.id === "string" ? result.id : undefined });
      } else {
        window.location.reload(); // refresh server-rendered lists
      }
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err));
    } finally {
      setBusy(false);
    }
  }

  const btn: React.CSSProperties = {
    border: "1px solid var(--ui-border, #2a2f3a)",
    borderRadius: 6,
    padding: compact ? "2px 8px" : "4px 10px",
    background: "transparent",
    color: "inherit",
    fontSize: 12,
    lineHeight: 1.2,
    whiteSpace: "nowrap",
    cursor: busy ? "default" : "pointer",
  };

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
      <button type="button" style={btn} disabled={busy || !canExport} onClick={() => onExport("json")}>
        Export JSON
      </button>
      <button type="button" style={btn} disabled={busy || !canExport} onClick={() => onExport("yaml")}>
        Export YAML
      </button>
      <button type="button" style={btn} disabled={busy} onClick={() => fileRef.current?.click()}>
        {busy ? "Working…" : "Import"}
      </button>
      <input
        ref={fileRef}
        type="file"
        accept=".json,.yaml,.yml,application/json,application/x-yaml"
        style={{ display: "none" }}
        onChange={onPickFile}
      />
      {error ? (
        <span role="alert" style={{ color: "var(--ui-danger, #f87171)", fontSize: 12 }}>
          {error}
        </span>
      ) : null}
    </div>
  );
}
