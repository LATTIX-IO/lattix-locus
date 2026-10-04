"use client";

import { MenuIcon, MoonIcon, SunIcon } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiStatusBanner } from "@/components/api-status-banner";
import { ClassificationBanner, useClassificationBanner } from "@/components/classification-banner";
import { FirstRunWizard } from "@/components/first-run-wizard";
import { LocusMark } from "@/components/locus-mark";
import { LeftNav } from "@/components/navigation/left-nav";
import { Button } from "@/components/ui/button";
import {
  PLATFORM_SETTINGS_UPDATED_EVENT,
  getOperatorSession,
  getPlatformHealthDetails,
  getPlatformSettings,
  getPlatformVersionStatus,
  logoutOperator,
} from "@/lib/api";
import { useIsDesktopShell } from "@/lib/desktop-shell";
import { applyTheme, setTheme, useTheme } from "@/lib/theme";
import type { OperatorSession, PlatformHealthDetails, PlatformSettings, PlatformVersionStatus } from "@/types/locus";

const CLASSIFICATION_HEIGHT = 32;
const TOP_NAV_HEIGHT = 48;
const SIDEBAR_WIDTH = 236;

function resolveOperatorLabel(session: OperatorSession | null): string {
  if (!session) {
    return "Operator";
  }
  return session.display_name || session.preferred_username || session.email || session.actor || "Operator";
}

function resolveOperatorInitials(session: OperatorSession | null): string {
  const label = resolveOperatorLabel(session)
    .trim()
    .replace(/[^\p{L}\p{N}\s]+/gu, " ");
  const parts = label.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0] ?? ""}${parts[1][0] ?? ""}`.toUpperCase();
  }
  return label.slice(0, 2).toUpperCase() || "OP";
}

function normalizeBannerColor(value: string | undefined, fallback: string): string {
  const normalized = (value ?? "").trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(normalized) ? normalized : fallback;
}

type SessionState =
  | { status: "loading" }
  | { status: "ready"; session: OperatorSession }
  | { status: "error"; message: string };

/**
 * The one shell for every screen (LOCUS-353). On the desktop app the loopback
 * operator is always signed in, so there is no /auth wall, sign-out, role or
 * org UI, and no classification banner. A web profile keeps /auth.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const isDesktop = useIsDesktopShell();
  const theme = useTheme();
  const isAuthRoute = pathname.startsWith("/auth");

  const [sessionState, setSessionState] = useState<SessionState>({ status: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const sessionRequestIdRef = useRef(0);
  const [platformVersion, setPlatformVersion] = useState<PlatformVersionStatus | null>(null);
  const [platformSettings, setPlatformSettings] = useState<PlatformSettings | null>(null);
  const [platformHealth, setPlatformHealth] = useState<PlatformHealthDetails | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarExpanded, setSidebarExpanded] = useState(() =>
    typeof window === "undefined" ? true : window.innerWidth >= 768,
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    const requestId = sessionRequestIdRef.current + 1;
    sessionRequestIdRef.current = requestId;

    getOperatorSession()
      .then((session) => {
        if (!cancelled && sessionRequestIdRef.current === requestId) {
          setSessionState({ status: "ready", session });
        }
      })
      .catch((error: unknown) => {
        if (!cancelled && sessionRequestIdRef.current === requestId) {
          setSessionState({
            status: "error",
            message: error instanceof Error ? error.message : "The Locus backend did not answer.",
          });
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pathname, reloadToken]);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getPlatformVersionStatus(), getPlatformSettings(), getPlatformHealthDetails()]).then(
      ([versionResult, settingsResult, healthResult]) => {
        if (cancelled) {
          return;
        }
        setPlatformVersion(versionResult.status === "fulfilled" ? versionResult.value : null);
        setPlatformSettings(settingsResult.status === "fulfilled" ? settingsResult.value : null);
        setPlatformHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handlePlatformSettingsUpdated = (event: Event) => {
      const nextSettings = (event as CustomEvent<PlatformSettings>).detail;
      if (nextSettings) {
        setPlatformSettings(nextSettings);
      }
    };
    window.addEventListener(PLATFORM_SETTINGS_UPDATED_EVENT, handlePlatformSettingsUpdated as EventListener);
    return () => window.removeEventListener(PLATFORM_SETTINGS_UPDATED_EVENT, handlePlatformSettingsUpdated as EventListener);
  }, []);

  const session = sessionState.status === "ready" ? sessionState.session : null;
  const authenticated = Boolean(session?.authenticated);

  // Routing around /auth. Desktop: never show it (the loopback operator is the
  // signed-in principal). Web: protected routes need a session; /auth with a
  // session goes Home.
  useEffect(() => {
    if (sessionState.status !== "ready") {
      return;
    }
    if (isAuthRoute && (isDesktop || authenticated)) {
      router.replace("/home");
    } else if (!isAuthRoute && !isDesktop && !authenticated) {
      router.replace("/auth");
    }
  }, [authenticated, isAuthRoute, isDesktop, router, sessionState.status]);

  // The web profile keeps its classification banner; the desktop app has none.
  const [localClassificationBanner] = useClassificationBanner();
  const classificationBannerEnabled =
    !isDesktop &&
    (platformSettings ? platformSettings.console_classification_banner_enabled ?? true : localClassificationBanner.enabled);
  const classificationBanner = {
    ...localClassificationBanner,
    enabled: classificationBannerEnabled,
    text: platformSettings
      ? platformSettings.console_classification_banner_text?.trim() || "Internal • Operational Console"
      : localClassificationBanner.text,
  };
  const classificationBannerColors = platformSettings
    ? {
        background: normalizeBannerColor(platformSettings.console_classification_banner_background_color, "#2e2a28"),
        foreground: normalizeBannerColor(platformSettings.console_classification_banner_text_color, "#e7dcc0"),
      }
    : undefined;
  const classificationHeight = classificationBannerEnabled ? CLASSIFICATION_HEIGHT : 0;
  const contentTopOffset = classificationHeight + TOP_NAV_HEIGHT;

  const databaseBadge = useMemo(() => {
    if (!platformHealth) {
      return { label: "DB unchecked", dotClassName: "bg-[hsl(var(--state-warning))]", title: "Database health details are unavailable." };
    }
    if (platformHealth.postgres === "connected") {
      return { label: "DB OK", dotClassName: "bg-[hsl(var(--state-success))]", title: "Postgres connectivity verified by the backend health endpoint." };
    }
    return {
      label: "DB degraded",
      dotClassName: "bg-[var(--fx-danger)]",
      title: platformHealth.postgres_reason?.trim() || `Postgres status: ${platformHealth.postgres}`,
    };
  }, [platformHealth]);

  const retrySession = useCallback(() => {
    setSessionState({ status: "loading" });
    setReloadToken((value) => value + 1);
  }, []);

  if (isAuthRoute && !isDesktop) {
    return (
      <div className="fx-app min-h-screen text-[var(--foreground)]">
        <ClassificationBanner state={classificationBanner} colors={classificationBannerColors} top={0} height={CLASSIFICATION_HEIGHT} />
        <ApiStatusBanner />
        <main className="min-h-screen" style={{ paddingTop: `${classificationHeight}px` }}>
          {children}
        </main>
      </div>
    );
  }

  if (sessionState.status !== "ready" || !authenticated || isAuthRoute) {
    return <SessionGate state={sessionState} isDesktop={isDesktop} onRetry={retrySession} />;
  }

  const operatorLabel = resolveOperatorLabel(session);
  const operatorSecondary = session?.email || session?.subject || "";
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <div className="fx-app min-h-screen text-[var(--foreground)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[9999] focus:px-3 focus:py-2 focus:text-sm focus:font-medium fx-btn-primary"
      >
        Skip to content
      </a>
      <ClassificationBanner state={classificationBanner} colors={classificationBannerColors} top={0} height={CLASSIFICATION_HEIGHT} />

      <header className="fx-header fixed inset-x-0 z-[80]" style={{ top: `${classificationHeight}px`, height: `${TOP_NAV_HEIGHT}px` }}>
        <div className="flex h-full items-center justify-between gap-3 px-3">
          <div className="flex min-w-0 items-center gap-2">
            <Button
              variant="secondary"
              size="icon"
              className="size-7"
              onClick={() => setSidebarExpanded((value) => !value)}
              aria-label="Toggle sidebar"
              aria-expanded={sidebarExpanded}
            >
              <MenuIcon aria-hidden="true" />
            </Button>
            <LocusMark className="h-5 w-5 shrink-0" />
            <span className="shrink-0 text-[13px] font-bold tracking-wide text-[var(--foreground)]">Locus</span>
          </div>

          <div className="relative flex shrink-0 items-center gap-1.5">
            <span
              className="fx-db-chip inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-[3px] text-[10px] font-medium text-[var(--fx-muted)]"
              title={databaseBadge.title}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${databaseBadge.dotClassName}`} aria-hidden="true" />
              {databaseBadge.label}
            </span>
            <Button
              variant="secondary"
              size="icon"
              className="size-7"
              onClick={() => setTheme(nextTheme)}
              aria-label={`Switch to ${nextTheme} mode`}
            >
              {theme === "dark" ? <SunIcon aria-hidden="true" /> : <MoonIcon aria-hidden="true" />}
            </Button>
            {!isDesktop ? (
              <>
                <button
                  type="button"
                  onClick={() => setMenuOpen((value) => !value)}
                  className="inline-flex h-7 w-7 items-center justify-center rounded-full border-2 border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.12)] font-mono text-[10px] font-bold text-[hsl(var(--primary))]"
                  aria-label="Account menu"
                  aria-expanded={menuOpen}
                  title={operatorLabel}
                >
                  {resolveOperatorInitials(session)}
                </button>
                {menuOpen ? (
                  <div className="fx-panel absolute right-0 top-10 z-[90] min-w-64 overflow-hidden p-1">
                    <div className="border-b border-[var(--ui-border)] px-3 py-3">
                      <p className="text-[11px] font-medium text-[var(--fx-muted)]">Signed in as</p>
                      <p className="mt-1 text-[0.95rem] font-semibold text-[hsl(var(--foreground))]">{operatorLabel}</p>
                      {operatorSecondary ? <p className="mt-1 break-all text-[12px] text-[var(--fx-muted)]">{operatorSecondary}</p> : null}
                    </div>
                    <button
                      type="button"
                      onClick={async () => {
                        try {
                          await logoutOperator();
                        } finally {
                          setMenuOpen(false);
                          router.replace("/auth");
                          router.refresh();
                        }
                      }}
                      className="block w-full rounded-[10px] px-3 py-2 text-left text-[12px] font-medium text-[var(--foreground)] hover:bg-[var(--fx-nav-hover)]"
                    >
                      Sign out
                    </button>
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      </header>

      <div
        className="min-h-screen"
        style={{ paddingTop: `${contentTopOffset}px`, ["--fx-content-top" as string]: `${contentTopOffset}px` } as React.CSSProperties}
      >
        <aside
          aria-label="Sidebar"
          className="fixed left-0 z-[70] overflow-hidden bg-[var(--fx-sidebar)] transition-[width] duration-200 ease-out"
          style={{
            top: `${contentTopOffset}px`,
            width: `${sidebarExpanded ? SIDEBAR_WIDTH : 0}px`,
            height: `calc(100vh - ${contentTopOffset}px)`,
            borderRight: sidebarExpanded ? "1px solid var(--ui-border)" : "0",
          }}
        >
          {sidebarExpanded ? (
            <LeftNav pathname={pathname} selectedSessionId={searchParams.get("session")} platformVersion={platformVersion} />
          ) : null}
        </aside>

        <main
          id="main-content"
          className="min-h-[calc(100vh-var(--fx-content-top,57px))] transition-[margin-left] duration-200"
          style={{ marginLeft: `${sidebarExpanded ? SIDEBAR_WIDTH : 0}px` }}
        >
          <ApiStatusBanner />
          <div className="p-4 md:p-5 lg:p-6">{children}</div>
        </main>
      </div>
      <FirstRunWizard />
    </div>
  );
}

function SessionGate({ state, isDesktop, onRetry }: { state: SessionState; isDesktop: boolean; onRetry: () => void }) {
  let title = "Starting Locus";
  let message = "Checking the local session…";
  let canRetry = false;
  if (state.status === "error") {
    title = "Can't reach the Locus backend";
    message = isDesktop
      ? `The app could not reach its local backend. It may still be starting. (${state.message})`
      : `The console could not check your session. (${state.message})`;
    canRetry = true;
  } else if (state.status === "ready" && !state.session.authenticated) {
    if (isDesktop) {
      title = "The local backend did not accept this app";
      message = "The desktop backend treats this app as you. Restart Locus; if this persists, check the backend logs.";
      canRetry = true;
    } else {
      message = "Redirecting to sign in…";
    }
  } else if (state.status === "ready") {
    message = "Opening Home…";
  }

  return (
    <div className="fx-app min-h-screen text-[var(--foreground)]">
      <ApiStatusBanner />
      <main className="flex min-h-screen items-center justify-center px-4">
        <div className="fx-panel max-w-md p-5 text-center" role={canRetry ? "alert" : "status"}>
          <LocusMark className="mx-auto h-7 w-7" />
          <h1 className="mt-3 text-lg font-semibold text-[hsl(var(--foreground))]">{title}</h1>
          <p className="mt-2 text-sm leading-6 text-[var(--fx-muted)]">{message}</p>
          {canRetry ? (
            <Button className="mt-4" onClick={onRetry}>
              Retry
            </Button>
          ) : null}
        </div>
      </main>
    </div>
  );
}
