"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiStatusBanner } from "@/components/api-status-banner";
import { ClassificationBanner, useClassificationBanner } from "@/components/classification-banner";
import { ModeSwitch } from "@/components/mode-switch";
import { LeftNav } from "@/components/navigation/left-nav";
import { UserConsoleSidebar } from "@/components/navigation/user-console-sidebar";
import { PLATFORM_SETTINGS_UPDATED_EVENT, getOperatorSession, getPlatformHealthDetails, getPlatformSettings, getPlatformVersionStatus, logoutOperator } from "@/lib/api";
import type { AppMode, OperatorSession, PlatformHealthDetails, PlatformSettings, PlatformVersionStatus } from "@/types/frontier";

function resolveOperatorLabel(session: OperatorSession | null): string {
  if (!session) {
    return "Operator";
  }
  return session.display_name || session.preferred_username || session.email || session.actor || "Operator";
}

function resolveOperatorSecondaryLabel(session: OperatorSession | null): string {
  if (!session) {
    return "";
  }
  return session.email || session.subject || session.principal_id || "";
}

function resolveOperatorInitials(session: OperatorSession | null): string {
  const label = resolveOperatorLabel(session)
    .trim()
    .replace(/[^\p{L}\p{N}\s]+/gu, " ");
  if (!label) {
    return "OP";
  }
  const parts = label.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0][0] ?? ""}${parts[1][0] ?? ""}`.toUpperCase();
  }
  return label.slice(0, 2).toUpperCase();
}

function readLocalStorage(key: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const storage = window.localStorage;
  if (!storage || typeof storage.getItem !== "function") {
    return null;
  }

  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocalStorage(key: string, value: string): void {
  if (typeof window === "undefined") {
    return;
  }

  const storage = window.localStorage;
  if (!storage || typeof storage.setItem !== "function") {
    return;
  }

  try {
    storage.setItem(key, value);
  } catch {
    // Ignore storage write failures and continue with in-memory theme state.
  }
}

function normalizeBannerColor(value: string | undefined, fallback: string): string {
  if (!value) {
    return fallback;
  }
  const normalized = value.trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(normalized) ? normalized : fallback;
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const isPublicAuthRoute = pathname.startsWith("/auth");
  const isProtectedRoute = !isPublicAuthRoute;
  const requestedMode: AppMode = pathname.startsWith("/builder") ? "builder" : "user";
  const inAdmin = pathname.startsWith("/admin");
  const [operatorSession, setOperatorSession] = useState<OperatorSession | null>(null);
  const [platformVersion, setPlatformVersion] = useState<PlatformVersionStatus | null>(null);
  const [platformSettings, setPlatformSettings] = useState<PlatformSettings | null>(null);
  const [platformHealth, setPlatformHealth] = useState<PlatformHealthDetails | null>(null);
  const sessionRequestIdRef = useRef(0);
  const [activeSessionRequestId, setActiveSessionRequestId] = useState(0);
  const [resolvedSessionRequestId, setResolvedSessionRequestId] = useState(0);

  const SHOW_READ_ONLY = false;

  const CLASSIFICATION_HEIGHT = 32;
  const TOP_NAV_HEIGHT = 48;
  const READ_ONLY_HEIGHT = 24;

  // Platform settings (org-wide, server-persisted) are authoritative for the
  // classification banner. The device-local banner state is the fallback when
  // platform settings could not be loaded.
  const [localClassificationBanner] = useClassificationBanner();
  const classificationBannerEnabled = platformSettings
    ? platformSettings.console_classification_banner_enabled ?? true
    : localClassificationBanner.enabled;
  const classificationBannerText = platformSettings
    ? platformSettings.console_classification_banner_text?.trim() || "Internal • Operational Console"
    : localClassificationBanner.text;
  const classificationBannerColors = platformSettings
    ? {
        background: normalizeBannerColor(platformSettings.console_classification_banner_background_color, "#2e2a28"),
        foreground: normalizeBannerColor(platformSettings.console_classification_banner_text_color, "#e7dcc0"),
      }
    : undefined;
  const classificationBanner = {
    ...localClassificationBanner,
    enabled: classificationBannerEnabled,
    text: classificationBannerText,
  };
  const classificationHeight = classificationBannerEnabled ? CLASSIFICATION_HEIGHT : 0;

  const topLayerOffset = classificationHeight;
  const contentTopOffset = topLayerOffset + TOP_NAV_HEIGHT + (SHOW_READ_ONLY ? READ_ONLY_HEIGHT : 0);

  const sidebarWidthExpanded = 236;
  const sidebarWidthCollapsed = 0;
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    if (typeof window === "undefined") {
      return "light";
    }

    const stored = readLocalStorage("frontier-theme");
    return stored === "dark" ? "dark" : "light";
  });
  const [menuOpen, setMenuOpen] = useState(false);
  // User mode offers two sidebars: the session list (Sessions) and the grouped
  // chat tree plus console navigation (Library).
  const [userSidebarView, setUserSidebarViewState] = useState<"sessions" | "library">(() =>
    readLocalStorage("frontier-user-sidebar-view") === "library" ? "library" : "sessions",
  );
  const setUserSidebarView = (view: "sessions" | "library") => {
    setUserSidebarViewState(view);
    writeLocalStorage("frontier-user-sidebar-view", view);
  };
  const [sidebarExpanded, setSidebarExpanded] = useState(() => {
    if (typeof window === "undefined") {
      return true;
    }
    return window.innerWidth >= 768;
  });

  const breadcrumbParts = useMemo(() => {
    const segments = pathname.split("/").filter(Boolean);
    const label = segments
      .slice(0, 3)
      .map((segment) => segment.replace(/[-_]/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()));
    return ["Console", ...label];
  }, [pathname]);

  useEffect(() => {
    const html = document.documentElement;
    html.classList.remove("theme-light", "theme-dark");
    html.classList.add(`theme-${theme}`);
    writeLocalStorage("frontier-theme", theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    const requestId = sessionRequestIdRef.current + 1;
    sessionRequestIdRef.current = requestId;
    setActiveSessionRequestId(requestId);

    getOperatorSession()
      .then((session) => {
        if (!cancelled) {
          setOperatorSession(session);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setOperatorSession(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setResolvedSessionRequestId(requestId);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [pathname]);

  useEffect(() => {
    let cancelled = false;

    Promise.allSettled([getPlatformVersionStatus(), getPlatformSettings(), getPlatformHealthDetails()])
      .then(([versionResult, settingsResult, healthResult]) => {
        if (cancelled) {
          return;
        }

        setPlatformVersion(versionResult.status === "fulfilled" ? versionResult.value : null);
        setPlatformSettings(settingsResult.status === "fulfilled" ? settingsResult.value : null);
        setPlatformHealth(healthResult.status === "fulfilled" ? healthResult.value : null);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const handlePlatformSettingsUpdated = (event: Event) => {
      const nextSettings = (event as CustomEvent<PlatformSettings>).detail;
      if (nextSettings) {
        setPlatformSettings(nextSettings);
      }
    };

    window.addEventListener(PLATFORM_SETTINGS_UPDATED_EVENT, handlePlatformSettingsUpdated as EventListener);
    return () => {
      window.removeEventListener(PLATFORM_SETTINGS_UPDATED_EVENT, handlePlatformSettingsUpdated as EventListener);
    };
  }, []);

  const sessionResolved = activeSessionRequestId !== 0 && resolvedSessionRequestId === activeSessionRequestId;
  const operatorLabel = resolveOperatorLabel(operatorSession);
  const operatorSecondaryLabel = resolveOperatorSecondaryLabel(operatorSession);
  const operatorInitials = resolveOperatorInitials(operatorSession);

  useEffect(() => {
    if (!sessionResolved || requestedMode !== "builder") {
      return;
    }
    if (operatorSession?.capabilities.can_builder) {
      return;
    }
    router.replace(operatorSession?.authenticated ? "/inbox" : "/auth");
  }, [operatorSession?.authenticated, operatorSession?.capabilities.can_builder, requestedMode, router, sessionResolved]);

  useEffect(() => {
    if (!isProtectedRoute || !sessionResolved || operatorSession?.authenticated) {
      return;
    }
    router.replace("/auth");
  }, [isProtectedRoute, operatorSession?.authenticated, router, sessionResolved]);

  useEffect(() => {
    if (!isPublicAuthRoute || !sessionResolved || !operatorSession?.authenticated) {
      return;
    }
    router.replace("/inbox");
  }, [isPublicAuthRoute, operatorSession?.authenticated, router, sessionResolved]);

  const canBuilder = operatorSession?.capabilities.can_builder ?? false;
  const canAdmin = operatorSession?.capabilities.can_admin ?? false;
  const activeMode: AppMode = requestedMode === "builder" && canBuilder ? "builder" : "user";
  const selectedSessionId = searchParams.get("session");
  const databaseBadge = useMemo(() => {
    if (!platformHealth) {
      return {
        label: "DB unchecked",
        dotClassName: "bg-[hsl(var(--state-warning))]",
        title: "Database health details are unavailable.",
      };
    }
    if (platformHealth.postgres === "connected") {
      return {
        label: "DB OK",
        dotClassName: "bg-[hsl(var(--state-success))]",
        title: "Postgres connectivity verified by the backend health endpoint.",
      };
    }
    return {
      label: "DB degraded",
      dotClassName: "bg-[var(--fx-danger)]",
      title: platformHealth.postgres_reason?.trim() || `Postgres status: ${platformHealth.postgres}`,
    };
  }, [platformHealth]);

  if (isPublicAuthRoute) {
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

  if (!sessionResolved || !operatorSession?.authenticated) {
    return (
      <div className="fx-app min-h-screen text-[var(--foreground)]">
        <div className="fixed inset-x-0 top-0 z-30 border-b border-[var(--ui-border)] bg-[color-mix(in_srgb,var(--fx-header)_94%,transparent)] backdrop-blur-sm">
          <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-3 px-4 sm:px-6 lg:px-8">
            <div className="flex min-w-0 items-center gap-3">
              <span className="text-sm font-semibold tracking-[-0.02em] text-[hsl(var(--foreground))]">Lattix xFrontier</span>
              <span className="fx-badge-local px-2 py-0.5 text-[10px]">Locked</span>
            </div>
            <span className="text-xs font-medium text-[var(--fx-muted)]">
              {sessionResolved ? "Redirecting to login…" : "Checking operator session…"}
            </span>
          </div>
        </div>
        <ApiStatusBanner />
        <main className="flex min-h-screen items-center justify-center px-4 pt-14">
          <div className="fx-panel max-w-md p-5 text-center">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--fx-muted)]">Secure local access</p>
            <h1 className="mt-3 text-xl font-semibold text-[hsl(var(--foreground))]">
              {sessionResolved ? "Login required" : "Verifying your session"}
            </h1>
            <p className="mt-2 text-sm leading-6 text-[var(--fx-muted)]">
              {sessionResolved
                ? "Protected routes stay fail-closed until an authenticated operator session is present."
                : "Hold tight while the console validates your operator session before rendering protected data."}
            </p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="fx-app min-h-screen text-[var(--foreground)]">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[9999] focus:px-3 focus:py-2 focus:text-sm focus:font-medium fx-btn-primary"
      >
        Skip to content
      </a>
      <ClassificationBanner state={classificationBanner} colors={classificationBannerColors} top={0} height={CLASSIFICATION_HEIGHT} />

      <header className="fx-header fixed inset-x-0 z-[80]" style={{ top: `${topLayerOffset}px`, height: `${TOP_NAV_HEIGHT}px` }}>
        <div className="flex h-full items-center justify-between gap-3 px-3">
          <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
            <button
              onClick={() => setSidebarExpanded((value) => !value)}
              className="fx-btn-secondary inline-flex h-7 w-7 shrink-0 items-center justify-center text-xs"
              aria-label="Toggle sidebar"
            >
              <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
                <path d="M2 4h12M2 8h12M2 12h12" />
              </svg>
            </button>
            <span className="shrink-0 text-[13px] font-bold tracking-wide text-[var(--foreground)]">
              Lattix xFrontier
            </span>
            <span className="fx-badge-local shrink-0 whitespace-nowrap px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-[0.1em]">
              Local
            </span>
            <nav
              className="hidden min-w-0 items-center gap-1 truncate text-[11px] text-[var(--fx-muted)] sm:flex"
              aria-label="Breadcrumb"
            >
              {breadcrumbParts.map((part, index) => (
                <span key={`${part}-${index}`} className="flex shrink-0 items-center">
                  {index > 0 ? <span className="mx-1 text-[var(--fx-muted)]">/</span> : null}
                  <span
                    className={
                      index === breadcrumbParts.length - 1
                        ? "max-w-[18ch] truncate text-[var(--foreground)]"
                        : "shrink-0"
                    }
                  >
                    {part}
                  </span>
                </span>
              ))}
            </nav>
          </div>

          <div className="relative flex shrink-0 items-center gap-1.5">
            <a
              href="mailto:9ff6ac2b6c9d@intake.linear.app?subject=%5BFeedback%5D%20Lattix%20Frontier&body=%0A---%20Feedback%20---%0A%0AType%3A%20%5B%20Bug%20%7C%20Feature%20Request%20%7C%20Improvement%20%7C%20Other%20%5D%0A%0ADescription%3A%0A%0A%0ASteps%20to%20reproduce%20(if%20bug)%3A%0A1.%20%0A2.%20%0A3.%20%0A%0AExpected%20behavior%3A%0A%0A%0AActual%20behavior%3A%0A%0A%0AAdditional%20context%3A%0A"
              className="fx-btn-secondary hidden whitespace-nowrap px-2 py-1 text-[11px] no-underline md:inline-flex"
            >
              Feedback
            </a>
            <button
              className="fx-btn-secondary hidden h-7 w-7 items-center justify-center px-0 md:inline-flex"
              aria-label="Docs"
              title="Docs & help"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-3.5 w-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="9" />
                <path d="M9.4 9.4a2.7 2.7 0 0 1 5.3.5c0 1.8-2.5 2.1-2.5 3.6" />
                <circle cx="12.2" cy="16.8" r="0.9" fill="currentColor" stroke="none" />
              </svg>
            </button>
            <button
              className="fx-btn-secondary hidden h-7 w-7 items-center justify-center px-0 md:inline-flex"
              aria-label="Notifications"
              title="Notifications"
            >
              <svg
                viewBox="0 0 24 24"
                className="h-3.5 w-3.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M6 9.5a6 6 0 0 1 12 0c0 4.2 1.5 5.5 1.5 5.5h-15S6 13.7 6 9.5z" />
                <path d="M10.3 18.5a1.8 1.8 0 0 0 3.4 0" />
              </svg>
            </button>
            <span
              className="fx-db-chip inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2 py-[3px] text-[10px] font-medium text-[var(--fx-muted)]"
              title={databaseBadge.title}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${databaseBadge.dotClassName}`} aria-hidden="true" />
              {databaseBadge.label}
            </span>
            <ModeSwitch activeMode={activeMode} canAccessBuilder={canBuilder} />
            {activeMode === "user" ? (
              <Link href="/workflows/start" className="fx-btn-primary whitespace-nowrap px-2.5 py-1 text-[11px] font-medium">
                Start Workflow
              </Link>
            ) : null}
            <button
              onClick={() => setMenuOpen((value) => !value)}
              className="inline-flex h-7 w-7 items-center justify-center rounded-full border-2 border-[hsl(var(--primary))] bg-[hsl(var(--primary)/0.12)] font-mono text-[10px] font-bold text-[hsl(var(--primary))] transition-[filter] hover:brightness-95"
              aria-label="User menu"
              title={operatorLabel}
            >
              {operatorInitials}
            </button>
            {menuOpen ? (
              <div className="fx-panel absolute right-0 top-10 z-[90] min-w-72 overflow-hidden p-1">
                <div className="border-b border-[var(--ui-border)] px-3 py-3">
                  <p className="text-[11px] font-medium tracking-[0.04em] text-[var(--fx-muted)]">Signed in as</p>
                  <p className="mt-1 text-[0.95rem] font-semibold tracking-[-0.01em] text-[hsl(var(--foreground))]">{operatorLabel}</p>
                  {operatorSecondaryLabel ? (
                    <p className="mt-1 break-all text-[12px] text-[var(--fx-muted)]">{operatorSecondaryLabel}</p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    <span className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2.5 py-1 text-[10px] font-medium text-[hsl(var(--foreground))]">
                      {operatorSession?.authenticated ? "Authenticated" : "Anonymous"}
                    </span>
                    <span className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2.5 py-1 text-[10px] font-medium text-[hsl(var(--foreground))]">
                      {canBuilder ? "Builder access enabled" : "Builder access locked"}
                    </span>
                    {canAdmin ? (
                      <span className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2.5 py-1 text-[10px] font-medium text-[hsl(var(--foreground))]">
                        Admin access enabled
                      </span>
                    ) : null}
                    {(operatorSession?.roles ?? []).map((role) => (
                      <span key={role} className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2.5 py-1 text-[10px] font-medium text-[hsl(var(--foreground))]">
                        {role}
                      </span>
                    ))}
                  </div>
                </div>
                <button
                  onClick={() => {
                    setTheme("light");
                    setMenuOpen(false);
                  }}
                  className="block w-full rounded-[10px] px-3 py-2 text-left text-[12px] font-medium text-[var(--foreground)] hover:bg-[var(--fx-nav-hover)]"
                >
                  Light mode
                </button>
                <button
                  onClick={() => {
                    setTheme("dark");
                    setMenuOpen(false);
                  }}
                  className="block w-full rounded-[10px] px-3 py-2 text-left text-[12px] font-medium text-[var(--foreground)] hover:bg-[var(--fx-nav-hover)]"
                >
                  Dark mode
                </button>
                <button
                  onClick={async () => {
                    try {
                      await logoutOperator();
                    } finally {
                      setOperatorSession(null);
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
          </div>
        </div>
      </header>

      {SHOW_READ_ONLY ? (
        <div
          className="fixed inset-x-0 z-40 flex items-center justify-center border-b border-[var(--ui-border)] bg-[hsl(var(--muted))] text-[10px]"
          style={{ top: `${topLayerOffset + TOP_NAV_HEIGHT}px`, height: `${READ_ONLY_HEIGHT}px` }}
        >
          Read-only mode enabled
        </div>
      ) : null}

      <div
        className="min-h-screen"
        style={{ paddingTop: `${contentTopOffset}px`, ["--fx-content-top" as string]: `${contentTopOffset}px` } as React.CSSProperties}
      >
        <aside
          className="fixed left-0 z-[70] overflow-hidden border-r border-[var(--ui-border)] bg-[var(--fx-sidebar)] transition-[width] duration-200 ease-out"
          style={{
            top: `${contentTopOffset}px`,
            width: `${sidebarExpanded ? sidebarWidthExpanded : sidebarWidthCollapsed}px`,
            height: `calc(100vh - ${contentTopOffset}px)`,
            borderRightWidth: sidebarExpanded ? "1px" : "0px",
          }}
        >
          {activeMode === "user" ? (
            sidebarExpanded ? (
              <div className="flex h-full flex-col">
                <div role="tablist" aria-label="Sidebar view" className="flex shrink-0 gap-1 border-b border-[var(--ui-border)] px-3 py-2">
                  {(["sessions", "library"] as const).map((view) => (
                    <button
                      key={view}
                      type="button"
                      role="tab"
                      aria-selected={userSidebarView === view}
                      onClick={() => setUserSidebarView(view)}
                      className={`flex-1 rounded-md px-2 py-1 text-[11px] font-medium transition ${
                        userSidebarView === view
                          ? "bg-[hsl(var(--card))] text-[hsl(var(--foreground))] shadow-[0_1px_2px_rgba(0,0,0,0.08)]"
                          : "text-[var(--fx-muted)] hover:bg-[var(--fx-nav-hover)] hover:text-[hsl(var(--foreground))]"
                      }`}
                    >
                      {view === "sessions" ? "Sessions" : "Library"}
                    </button>
                  ))}
                </div>
                <div className="min-h-0 flex-1">
                  {userSidebarView === "sessions" ? (
                    <UserConsoleSidebar
                      pathname={pathname}
                      selectedSessionId={selectedSessionId}
                      expanded={sidebarExpanded}
                      platformVersion={platformVersion}
                    />
                  ) : (
                    <LeftNav
                      mode="user"
                      pathname={pathname}
                      inAdmin={inAdmin && canAdmin}
                      expanded={sidebarExpanded}
                      platformVersion={platformVersion}
                    />
                  )}
                </div>
              </div>
            ) : null
          ) : (
            <LeftNav
              mode={activeMode}
              pathname={pathname}
              inAdmin={inAdmin && canAdmin}
              expanded={sidebarExpanded}
              platformVersion={platformVersion}
            />
          )}
        </aside>

        <main
          id="main-content"
          className="min-h-[calc(100vh-var(--fx-content-top,57px))] transition-[margin-left] duration-200"
          style={{ marginLeft: `${sidebarExpanded ? sidebarWidthExpanded : sidebarWidthCollapsed}px` }}
        >
          <ApiStatusBanner />
          <div className="p-4 md:p-5 lg:p-6">{children}</div>
        </main>
      </div>
    </div>
  );
}
