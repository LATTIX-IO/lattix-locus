export default function BuilderLoading() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div
          className="h-8 w-8 animate-spin rounded-full border-2 border-t-transparent"
          style={{ borderColor: "var(--fx-primary)", borderTopColor: "transparent" }}
        />
        <span className="text-xs" style={{ color: "var(--fx-muted)" }}>Loading builder...</span>
      </div>
    </div>
  );
}
