type Props = { status: string };

const styleMap: Record<string, { backgroundColor: string; borderColor: string; color: string }> = {
  Running: {
    backgroundColor: "color-mix(in srgb, var(--fx-primary) 20%, transparent)",
    borderColor: "color-mix(in srgb, var(--fx-primary) 45%, var(--fx-border))",
    color: "var(--foreground)",
  },
  Blocked: {
    backgroundColor: "color-mix(in srgb, var(--fx-danger) 20%, transparent)",
    borderColor: "color-mix(in srgb, var(--fx-danger) 45%, var(--fx-border))",
    color: "var(--foreground)",
  },
  "Needs Review": {
    backgroundColor: "color-mix(in srgb, var(--fx-warning) 20%, transparent)",
    borderColor: "color-mix(in srgb, var(--fx-warning) 45%, var(--fx-border))",
    color: "var(--foreground)",
  },
  Done: {
    backgroundColor: "color-mix(in srgb, var(--fx-success) 20%, transparent)",
    borderColor: "color-mix(in srgb, var(--fx-success) 45%, var(--fx-border))",
    color: "var(--foreground)",
  },
  Archived: {
    backgroundColor: "color-mix(in srgb, var(--fx-nav-active) 55%, transparent)",
    borderColor: "var(--fx-border)",
    color: "var(--foreground)",
  },
  Failed: {
    backgroundColor: "color-mix(in srgb, var(--fx-danger) 20%, transparent)",
    borderColor: "color-mix(in srgb, var(--fx-danger) 45%, var(--fx-border))",
    color: "var(--foreground)",
  },
};

export function StatusChip({ status }: Props) {
  const style = styleMap[status] ?? {
    backgroundColor: "color-mix(in srgb, var(--fx-nav-active) 65%, transparent)",
    borderColor: "var(--fx-border)",
    color: "var(--foreground)",
  };

  return (
    <span className="inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium tracking-[0.01em]" style={style}>
      {status}
    </span>
  );
}
