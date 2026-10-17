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
          <Suspense fallback={<div className="min-h-screen bg-[hsl(var(--background))]" />}>
            <AppShell>{children}</AppShell>
          </Suspense>
        </ToastProvider>
      </body>
    </html>
  );
}
