import type { Metadata } from "next";
import { Geist, IBM_Plex_Mono } from "next/font/google";
import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";
import { DesktopLifecycle } from "@/components/desktop-lifecycle";
import { ToastProvider } from "@/components/toast";
import "./globals.css";

const geist = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const ibmPlexMono = IBM_Plex_Mono({
  variable: "--font-ibm-plex-mono",
  weight: ["400", "700"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Lattix xFrontier",
  description: "Dual-mode local-first orchestration UI for users and builders",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geist.variable} ${ibmPlexMono.variable} antialiased`}
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
