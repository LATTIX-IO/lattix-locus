type BuilderLibraryStatusBadge = {
  label: string;
  count: number;
};

type BuilderLibraryStatusBadgesProps = {
  counts: BuilderLibraryStatusBadge[];
};

export function BuilderLibraryStatusBadges({ counts }: BuilderLibraryStatusBadgesProps) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      {counts.map((count) => (
        <div
          key={count.label}
          className="fx-pill px-3 py-1.5 text-[0.72rem] font-medium text-[var(--foreground)]"
        >
          {count.label} {count.count}
        </div>
      ))}
    </div>
  );
}