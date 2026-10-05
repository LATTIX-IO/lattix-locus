"use client";

import Link from "next/link";
import { PlatformUpdatePanel } from "@/components/navigation/platform-update-panel";
import { UserConsoleSidebar } from "@/components/navigation/user-console-sidebar";
import { PRIMARY_NAV, activeNavItem, isNavLinkActive } from "@/components/navigation/nav-config";
import { cn } from "@/lib/utils";
import type { PlatformVersionStatus } from "@/types/locus";

type LeftNavProps = {
  pathname: string;
  selectedSessionId: string | null;
  platformVersion?: PlatformVersionStatus | null;
};

/**
 * The single left navigation: Home, Activity, Memory, Library, Settings. The
 * active area shows its sub-pages underneath; Activity also lists its sessions.
 */
export function LeftNav({ pathname, selectedSessionId, platformVersion }: LeftNavProps) {
  const current = activeNavItem(pathname);
  const showSessions = current?.href === "/activity";

  return (
    <div className="flex h-full flex-col">
      <nav aria-label="Primary" className="shrink-0 px-2.5 pb-2 pt-3">
        <ul className="space-y-0.5">
          {PRIMARY_NAV.map((item) => {
            const active = current?.href === item.href;
            const Icon = item.icon;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={active && pathname === item.href ? "page" : undefined}
                  className={active ? "fx-nav-item fx-nav-item-active" : "fx-nav-item"}
                >
                  <span className="fx-nav-item-icon" aria-hidden="true">
                    <Icon className={cn("size-4", active ? "text-[var(--fx-primary-strong)]" : "text-[var(--fx-muted)]")} />
                  </span>
                  <span className="truncate">{item.label}</span>
                </Link>
                {active && item.children?.length ? (
                  <ul aria-label={`${item.label} pages`} className="mb-1 ml-7 mt-0.5 space-y-0.5 border-l border-[var(--ui-border)] pl-2">
                    {item.children.map((child) => {
                      const childActive = isNavLinkActive(pathname, child, { exact: child.href === item.href });
                      return (
                        <li key={child.href}>
                          <Link
                            href={child.href}
                            aria-current={childActive ? "page" : undefined}
                            className={cn(
                              "block truncate rounded-md px-2 py-1 text-[12px] no-underline transition-colors",
                              childActive
                                ? "bg-[var(--fx-nav-active)] font-medium text-[hsl(var(--foreground))]"
                                : "text-[var(--fx-muted)] hover:bg-[var(--fx-nav-hover)] hover:text-[hsl(var(--foreground))]",
                            )}
                          >
                            {child.label}
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="min-h-0 flex-1 border-t border-[var(--ui-border)]">
        {showSessions ? <UserConsoleSidebar pathname={pathname} selectedSessionId={selectedSessionId} /> : null}
      </div>

      <div className="border-t border-[var(--ui-border)] px-2 py-2.5">
        <PlatformUpdatePanel platformVersion={platformVersion} />
      </div>
    </div>
  );
}
