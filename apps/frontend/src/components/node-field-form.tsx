"use client";

import type { NodeFieldSpec } from "@/lib/api";

/**
 * Generic config-panel renderer driven by a node's declarative input schema
 * (Langflow-style). One component renders every control type so node config
 * panels no longer need hand-coded forms per node type.
 *
 * `options_source` fields resolve their choices at render time from
 * `dynamicOptions` (e.g. live model/agent/integration lists); when absent they
 * fall back to the static `options` on the spec.
 */
export function NodeFieldForm({
  fields,
  values,
  onChange,
  dynamicOptions = {},
  readOnly = false,
}: {
  fields: NodeFieldSpec[];
  values?: Record<string, unknown>;
  onChange?: (name: string, value: unknown) => void;
  dynamicOptions?: Record<string, string[]>;
  readOnly?: boolean;
}) {
  const resolved = values ?? {};
  const visible = fields.filter((field) => !field.advanced);
  const advanced = fields.filter((field) => field.advanced);

  function renderField(field: NodeFieldSpec) {
    const current = resolved[field.name] ?? field.default ?? "";
    const set = (value: unknown) => onChange?.(field.name, value);
    const options =
      field.options_source && dynamicOptions[field.options_source]?.length
        ? dynamicOptions[field.options_source]
        : field.options ?? [];

    const labelEl = (
      <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">
        {field.label}
        {field.required ? <span className="text-[hsl(var(--state-critical))]"> *</span> : null}
      </span>
    );

    let control: React.ReactNode;
    switch (field.field_type) {
      case "textarea":
      case "code":
        control = (
          <textarea
            className={`fx-field min-h-20 w-full p-2 text-sm ${field.field_type === "code" ? "font-mono" : ""}`}
            value={String(current)}
            placeholder={field.placeholder}
            disabled={readOnly}
            onChange={(e) => set(e.target.value)}
          />
        );
        break;
      case "bool":
        control = (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(current)}
              disabled={readOnly}
              onChange={(e) => set(e.target.checked)}
            />
            <span className="fx-muted">{field.description || "Enabled"}</span>
          </label>
        );
        break;
      case "dropdown":
        control = (
          <>
            <input
              className="fx-field h-8 w-full px-2 text-sm"
              value={String(current)}
              placeholder={field.placeholder || "select or type"}
              disabled={readOnly}
              list={`nf-${field.name}-opts`}
              onChange={(e) => set(e.target.value)}
            />
            <datalist id={`nf-${field.name}-opts`}>
              {options.map((opt) => (
                <option key={opt} value={opt} />
              ))}
            </datalist>
          </>
        );
        break;
      case "slider":
        control = (
          <div className="flex items-center gap-2">
            <input
              type="range"
              className="w-full"
              min={field.min ?? 0}
              max={field.max ?? 1}
              step={field.step ?? 0.01}
              value={Number(current) || 0}
              disabled={readOnly}
              onChange={(e) => set(Number(e.target.value))}
            />
            <span className="fx-muted w-10 text-right text-xs">{Number(current) || 0}</span>
          </div>
        );
        break;
      case "number":
        control = (
          <input
            type="number"
            className="fx-field h-8 w-full px-2 text-sm"
            value={String(current)}
            min={field.min ?? undefined}
            max={field.max ?? undefined}
            step={field.step ?? undefined}
            disabled={readOnly}
            onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))}
          />
        );
        break;
      case "secret":
        control = (
          <input
            type="password"
            autoComplete="off"
            className="fx-field h-8 w-full px-2 text-sm"
            value={String(current)}
            placeholder={field.placeholder || "••••••••"}
            disabled={readOnly}
            onChange={(e) => set(e.target.value)}
          />
        );
        break;
      default:
        control = (
          <input
            className="fx-field h-8 w-full px-2 text-sm"
            value={String(current)}
            placeholder={field.placeholder}
            disabled={readOnly}
            onChange={(e) => set(e.target.value)}
          />
        );
    }

    return (
      <div key={field.name} className="text-xs">
        {field.field_type === "bool" ? (
          <>
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">{field.label}</span>
            {control}
          </>
        ) : (
          <label className="block">
            {labelEl}
            {control}
          </label>
        )}
        {field.description && field.field_type !== "bool" ? (
          <p className="fx-muted mt-1 text-[11px] leading-4">{field.description}</p>
        ) : null}
      </div>
    );
  }

  if (fields.length === 0) {
    return <p className="fx-muted text-xs">This node has no configurable inputs.</p>;
  }

  return (
    <div className="space-y-3">
      {visible.map(renderField)}
      {advanced.length > 0 ? (
        <details className="rounded border border-[var(--fx-border)] p-2">
          <summary className="cursor-pointer text-[11px] font-medium uppercase tracking-wide fx-muted">
            Advanced ({advanced.length})
          </summary>
          <div className="mt-2 space-y-3">{advanced.map(renderField)}</div>
        </details>
      ) : null}
    </div>
  );
}
