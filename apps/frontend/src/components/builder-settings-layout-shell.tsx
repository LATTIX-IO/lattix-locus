"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getBuilderSettingsNavBadge, getVisibleBuilderSettingsSections } from "@/lib/builder-settings";
import { getOperatorSession, getPlatformSecurityPolicy, getPlatformSettings } from "@/lib/api";
import type { OperatorSession, PlatformSettings, SecurityPolicyResponse } from "@/types/frontier";

export function BuilderSettingsLayoutShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [operatorSession, setOperatorSession] = useState<OperatorSession | null>(null);
  const [settings, setSettings] = useState<PlatformSettings | null>(null);
  const [policy, setPolicy] = useState<SecurityPolicyResponse | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadNavState() {
      try {
        const [sessionResponse, settingsResponse, policyResponse] = await Promise.all([
          getOperatorSession(),
          getPlatformSettings(),
          getPlatformSecurityPolicy(),
        ]);
        if (cancelled) {
          return;
        }
        setOperatorSession(sessionResponse);
        setSettings(settingsResponse);
        setPolicy(policyResponse);
      } catch {
        if (!cancelled) {
          setOperatorSession(null);
          setSettings(null);
          setPolicy(null);
        }
      }
    }

    void loadNavState();
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleSections = getVisibleBuilderSettingsSections(operatorSession);

  return (
    <section className="space-y-5">
      <div className="border-b border-[var(--ui-border)] pb-4">
        <p className="text-[11px] font-medium tracking-[0.05em] fx-muted">Builder / Settings</p>
        <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-3xl">
            <h1 className="text-[1.45rem] font-semibold tracking-[-0.03em] text-[var(--foreground)]">Builder Configuration</h1>
            <p className="mt-1 text-[0.95rem] leading-6 text-[var(--fx-muted)]">Direct access to the runtime, policy, and integration controls builders use most.</p>
          </div>
          <div className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card)/0.9)] px-3 py-2 text-[11px] font-medium text-[var(--foreground)] shadow-[var(--fx-shadow-soft)]">
            Builder settings
          </div>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[212px_minmax(0,1fr)]">
        <aside className="h-fit rounded-[16px] border border-[var(--ui-border)] bg-[var(--fx-sidebar)] shadow-[var(--fx-shadow-soft)] xl:sticky xl:top-24">
          <div className="border-b border-[var(--ui-border)] px-3 pb-3 pt-3">
            <p className="text-[0.67rem] font-medium tracking-[0.05em] text-[var(--fx-muted)]">Builder settings</p>
            <p className="mt-2 text-[0.82rem] leading-5 text-[hsl(var(--foreground))]">Direct access to runtime, integrations, governance, and policy controls.</p>
          </div>

          <div className="px-2.5 py-3">
            <section className="fx-nav-section mb-0">
              <h2 className="fx-nav-section-title">Configuration</h2>
              <nav className="space-y-1" aria-label="Builder settings">
            <Link
              href="/builder/settings"
              className={pathname === "/builder/settings" ? "fx-nav-item fx-nav-item-active justify-between" : "fx-nav-item justify-between"}
            >
              <span className="truncate">Overview</span>
            </Link>

            {visibleSections.map((section) => {
              const active = pathname === section.href;
              const badge = getBuilderSettingsNavBadge(section.key, settings, policy);
              return (
                <Link
                  key={section.key}
                  href={section.href}
                  className={active ? "fx-nav-item fx-nav-item-active justify-between" : "fx-nav-item justify-between"}
                >
                  <span className="truncate">{section.navLabel}</span>
                  {badge ? (
                    <span className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-1.5 py-0.5 text-[0.62rem] font-semibold tracking-[0.04em] text-[var(--foreground)]">
                      {badge}
                    </span>
                  ) : null}
                </Link>
              );
            })}
              </nav>
            </section>
          </div>
        </aside>

        <div className="space-y-4">{children}</div>
      </div>
    </section>
  );
}