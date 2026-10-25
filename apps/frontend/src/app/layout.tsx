import type { Metadata } from "next";
import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";
import { DesktopLifecycle } from "@/components/desktop-lifecycle";
import { ToastProvider } from "@/components/toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "Locus",
  description: "Local-first AI operator by Lattix",
};

/**
 * Static server-rendered fallback for the client shell boundary.
 *
 * The desktop routes rely on client hydration. If bundled JavaScript is
 * missing or fails to initialize, a blank div makes the problem invisible
 * to the operator. Keep this message self-contained (no JS or CSS required).
 */
function StartupFallback() {
  return (
    <main
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        minHeight: "100vh",
        alignItems: "center",
        justifyContent: "center",
        background: "#101114",
        color: "#f1f2f4",
        fontFamily: "system-ui, Segoe UI, sans-serif",
        padding: 32,
        boxSizing: "border-box",
      }}
    >
      <div style={{ maxWidth: 520, width: "100%" }}>
        <div style={{ fontSize: 12, letterSpacing: 2, fontWeight: 700, opacity: 0.65 }}>
          LATTIX LOCUS / STARTUP
        </div>
        <h1 style={{ marginTop: 16, fontSize: 25, fontWeight: 600 }}>
          Loading Locus interface
        </h1>
        <p style={{ marginTop: 12, lineHeight: 1.6, color: "#bcc1c8" }}>
          If this screen remains visible, the interface has not finished
          initializing. Check the browser developer console and bundled
          JavaScript requests before reinstalling.
        </p>
      </div>
    </main>
  );
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className="antialiased"
      >
        <ToastProvider>
          <DesktopLifecycle />
          <Suspense fallback={<StartupFallback />}>
            <AppShell>{children}</AppShell>
          </Suspense>
        </ToastProvider>
      </body>
    </html>
  );
}
