"use client";

/**
 * LOCUS-475: avoid an uninformative white webview if the root AppShell throws.
 *
 * Keep this boundary self-contained: it must render even when theme providers,
 * API clients and Tailwind styles have failed. Do not expose exception text,
 * request headers or local secrets in the UI.
 */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: "system-ui, Segoe UI, sans-serif" }}>
        <main
          role="alert"
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#101114",
            color: "#f1f2f4",
            padding: "32px",
            boxSizing: "border-box",
          }}
        >
          <div style={{ maxWidth: 510, width: "100%" }}>
            <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 2, opacity: 0.6 }}>
              LATTIX LOCUS / STARTUP
            </div>
            <h1 style={{ marginTop: 16, fontSize: 25, fontWeight: 600 }}>
              The interface could not load
            </h1>
            <p style={{ lineHeight: 1.6, color: "#bcc1c8" }}>
              The desktop application encountered an interface error.
              Your local data has not been reset. Retry this screen, or check
              the local frontend and backend service health before reinstalling.
            </p>
            <button
              type="button"
              onClick={() => reset()}
              style={{
                marginTop: 14,
                background: "#f0f1f2",
                color: "#15171a",
                border: 0,
                borderRadius: 6,
                padding: "11px 18px",
                fontSize: 14,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Retry interface
            </button>
            <p style={{ marginTop: 24, fontSize: 12, color: "#959ba4" }}>
              Diagnostics: http://127.0.0.1:8000/healthz and
              http://127.0.0.1:3000/home
            </p>
          </div>
        </main>
      </body>
    </html>
  );
}
