"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

type SettingsSection = {
  id: string;
  label: string;
  description?: string;
};

type SettingsShellProps = {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
  sections: readonly SettingsSection[];
  navLabel?: string;
  rightRail?: ReactNode;
  children: ReactNode;
};

export function SettingsShell({
  eyebrow,
  title,
  description,
  action,
  sections,
  navLabel = "Sections",
  rightRail,
  children,
}: SettingsShellProps) {
  const [activeSection, setActiveSection] = useState<string>(sections[0]?.id ?? "");
  const observerRef = useRef<IntersectionObserver | null>(null);

  useEffect(() => {
    if (!sections.length) {
      return;
    }

    observerRef.current?.disconnect();
    observerRef.current = new IntersectionObserver(
      (entries) => {
        const visible = entries.find((entry) => entry.isIntersecting);
        if (visible) {
          setActiveSection(visible.target.id);
        }
      },
      { rootMargin: "-24% 0px -62% 0px", threshold: 0 },
    );

    for (const { id } of sections) {
      const element = document.getElementById(id);
      if (element) {
        observerRef.current.observe(element);
      }
    }

    return () => observerRef.current?.disconnect();
  }, [sections]);

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--ui-border)] pb-4">
        <div className="max-w-3xl space-y-2">
          <p className="text-[11px] font-medium tracking-[0.05em] fx-muted">{eyebrow}</p>
          <div className="space-y-1">
            <h1 className="text-[1.45rem] font-semibold tracking-[-0.03em] text-[var(--foreground)]">{title}</h1>
            <p className="max-w-2xl text-[0.95rem] leading-6 text-[var(--fx-muted)]">{description}</p>
          </div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>

      <div className={`grid gap-4 ${rightRail ? "xl:grid-cols-[220px_minmax(0,1fr)_320px]" : "xl:grid-cols-[220px_minmax(0,1fr)]"}`}>
        <aside className="h-fit rounded-[16px] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] p-2.5 shadow-[var(--fx-shadow-soft)] xl:sticky xl:top-24">
          <div className="px-2 pb-2 text-[11px] font-medium tracking-[0.05em] fx-muted">{navLabel}</div>
          <nav className="space-y-1">
            {sections.map((item) => {
              const isActive = activeSection === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => document.getElementById(item.id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  className={`block w-full rounded-[12px] border px-2.5 py-2.5 text-left transition ${
                    isActive
                      ? "border-[color-mix(in_srgb,var(--fx-primary)_32%,var(--ui-border))] bg-[color-mix(in_srgb,var(--fx-primary)_8%,hsl(var(--card)))] text-[hsl(var(--foreground))]"
                      : "border-transparent text-[var(--fx-muted)] hover:border-[var(--ui-border)] hover:bg-[hsl(var(--card)/0.82)] hover:text-[var(--foreground)]"
                  }`}
                >
                  <div className="text-[0.8rem] font-semibold tracking-[-0.01em]">{item.label}</div>
                  {item.description ? <div className="mt-1 text-[0.72rem] leading-5 text-inherit opacity-80">{item.description}</div> : null}
                </button>
              );
            })}
          </nav>
        </aside>

        <div className="space-y-4">{children}</div>

        {rightRail ? <aside className="space-y-4 xl:sticky xl:top-24 xl:h-fit">{rightRail}</aside> : null}
      </div>
    </section>
  );
}

export function SettingsRailCard({
  title,
  description,
  badge,
  children,
}: {
  title: string;
  description?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <article className="rounded-[16px] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_96%,hsl(var(--background))_4%)] p-4 shadow-[var(--fx-shadow-soft)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--foreground)]">{title}</h2>
          {description ? <p className="mt-1 text-xs leading-5 text-[var(--fx-muted)]">{description}</p> : null}
        </div>
        {badge ? <div className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2.5 py-1 text-[0.68rem] font-semibold tracking-[0.03em] text-[var(--foreground)]">{badge}</div> : null}
      </div>
      <div className="mt-3">{children}</div>
    </article>
  );
}