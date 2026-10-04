import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <div
        className="flex h-14 w-14 items-center justify-center rounded-full"
        style={{ background: "hsl(var(--state-info) / 0.12)" }}
      >
        <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="hsl(var(--state-info))" strokeWidth="1.8">
          <circle cx="11" cy="11" r="8" />
          <path d="M21 21l-4.35-4.35" />
        </svg>
      </div>

      <h1 className="text-lg font-semibold" style={{ color: "hsl(var(--foreground))" }}>
        Page not found
      </h1>

      <p className="max-w-md text-sm" style={{ color: "var(--fx-muted)" }}>
        The page you are looking for does not exist or has been moved.
      </p>

      <Link href="/home" className="fx-btn-primary mt-2 px-4 py-2 text-sm font-medium no-underline">
        Go to Home
      </Link>
    </div>
  );
}
