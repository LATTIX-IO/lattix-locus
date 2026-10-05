"use client";

import {
  BrainIcon,
  CpuIcon,
  EyeIcon,
  MonitorSmartphoneIcon,
  PaletteIcon,
  PlugIcon,
  RefreshCwIcon,
  RepeatIcon,
  ShieldCheckIcon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { openFirstRunWizard } from "@/components/first-run-wizard";
import { UpdatesPanel } from "@/components/navigation/platform-update-panel";
import { AppearanceSection } from "@/components/settings/appearance-section";
import { ComputerUseSection } from "@/components/settings/computer-use-section";
import { ConnectionsSection } from "@/components/settings/connections-section";
import { EnginesSection } from "@/components/settings/engines-section";
import { LoopSection } from "@/components/settings/loop-section";
import { MemorySection } from "@/components/settings/memory-section";
import { ObservabilitySection } from "@/components/settings/observability-section";
import { PoliciesSection } from "@/components/settings/policies-section";
import { SectionHeader, SettingsGroup } from "@/components/settings/settings-kit";
import { Button } from "@/components/ui/button";
import { getPlatformVersionStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { PlatformVersionStatus } from "@/types/locus";

export const SETTINGS_SECTIONS = [
  { id: "engines", label: "Engines", icon: CpuIcon },
  { id: "connections", label: "Connections", icon: PlugIcon },
  { id: "computer-use", label: "Computer use", icon: MonitorSmartphoneIcon },
  { id: "policies", label: "Policies & autonomy", icon: ShieldCheckIcon },
  { id: "memory", label: "Memory & knowledge", icon: BrainIcon },
  { id: "self-improvement", label: "Self-improvement", icon: RepeatIcon },
  { id: "updates", label: "Updates", icon: RefreshCwIcon },
  { id: "observability", label: "Observability", icon: EyeIcon },
  { id: "appearance", label: "Appearance", icon: PaletteIcon },
] as const satisfies ReadonlyArray<{ id: string; label: string; icon: LucideIcon }>;

export type SettingsSectionId = (typeof SETTINGS_SECTIONS)[number]["id"];

export function resolveSettingsSection(value: string | null | undefined): SettingsSectionId {
  if (value === "loop") return "self-improvement";
  return SETTINGS_SECTIONS.some((section) => section.id === value) ? (value as SettingsSectionId) : "engines";
}

function UpdatesSection() {
  const [version, setVersion] = useState<PlatformVersionStatus | null>(null);
  useEffect(() => {
    let cancelled = false;
    getPlatformVersionStatus()
      .then((next) => {
        if (!cancelled) setVersion(next);
      })
      .catch(() => {
        /* The panel shows "Version unavailable"; the desktop shell has its own version. */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="flex flex-col gap-4">
      <SectionHeader title="Updates" description="How Locus updates itself." />
      <SettingsGroup title="Channel and status">
        <UpdatesPanel platformVersion={version} />
      </SettingsGroup>
    </div>
  );
}

function SectionBody({ section }: { section: SettingsSectionId }) {
  switch (section) {
    case "engines":
      return <EnginesSection />;
    case "connections":
      return <ConnectionsSection />;
    case "computer-use":
      return <ComputerUseSection />;
    case "policies":
      return <PoliciesSection />;
    case "memory":
      return <MemorySection />;
    case "self-improvement":
      return <LoopSection />;
    case "updates":
      return <UpdatesSection />;
    case "observability":
      return <ObservabilitySection />;
    case "appearance":
      return <AppearanceSection />;
  }
}

/** The one Settings page (LOCUS-353): a section list on the left, one section at a time. */
export function SettingsWorkspace() {
  const searchParams = useSearchParams();
  const section = resolveSettingsSection(searchParams?.get("section"));

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <Button variant="secondary" size="sm" onClick={() => openFirstRunWizard()}>
          Run setup again
        </Button>
      </div>
      <div className="grid gap-5 md:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="md:sticky md:top-[calc(var(--fx-content-top,48px)+16px)] md:self-start">
          <ul className="flex gap-1 overflow-x-auto md:flex-col md:overflow-visible">
            {SETTINGS_SECTIONS.map((item) => {
              const active = item.id === section;
              const Icon = item.icon;
              return (
                <li key={item.id} className="shrink-0">
                  <Link
                    href={`/settings?section=${item.id}`}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center gap-2 rounded-[10px] px-2.5 py-1.5 text-[13px] no-underline transition-colors",
                      active ? "bg-[var(--fx-nav-active)] font-medium text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                  >
                    <Icon aria-hidden="true" className="size-4 shrink-0" />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="min-w-0" key={section}>
          <SectionBody section={section} />
        </div>
      </div>
    </div>
  );
}
