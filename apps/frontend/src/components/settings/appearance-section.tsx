"use client";

import { useMemo } from "react";
import {
  LoadState,
  SaveBar,
  SectionHeader,
  SettingsGroup,
  TextField,
  ToggleRow,
  useDraft,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import { useIsDesktopShell } from "@/lib/desktop-shell";
import { setTheme, useTheme, type Theme } from "@/lib/theme";
import type { PlatformSettings } from "@/types/locus";

function normalizeHexColor(value: string, fallback: string): string {
  const trimmed = value.trim();
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(trimmed) ? trimmed : fallback;
}

type BannerDraft = { enabled: boolean; text: string; background: string; foreground: string };

function toBannerDraft(settings: PlatformSettings): BannerDraft {
  return {
    enabled: settings.console_classification_banner_enabled ?? true,
    text: settings.console_classification_banner_text ?? "Internal • Operational Console",
    background: normalizeHexColor(settings.console_classification_banner_background_color ?? "", "#2e2a28"),
    foreground: normalizeHexColor(settings.console_classification_banner_text_color ?? "", "#e7dcc0"),
  };
}

/** Hosted installs only: the desktop app shows no classification banner. */
function ClassificationBannerGroup() {
  const platform = usePlatformResource();
  const initial = useMemo(() => (platform.settings ? toBannerDraft(platform.settings) : null), [platform.settings]);
  const { draft, dirty, saving, message, update, commit, reset } = useDraft<BannerDraft>(initial);

  return (
    <SettingsGroup title="Classification banner" description="The strip at the top of the hosted console.">
      {!draft ? (
        <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
      ) : (
        <>
          <ToggleRow id="banner-enabled" label="Show the banner" checked={draft.enabled} onCheckedChange={(next) => update("enabled", next)} />
          <TextField id="banner-text" label="Banner text" value={draft.text} onChange={(value) => update("text", value)} />
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField id="banner-background" label="Background colour" type="color" value={draft.background} onChange={(value) => update("background", value)} />
            <TextField id="banner-foreground" label="Text colour" type="color" value={draft.foreground} onChange={(value) => update("foreground", value)} />
          </div>
          <div
            aria-label="Banner preview"
            className="rounded-md border border-border px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.08em]"
            style={{ background: draft.background, color: draft.foreground }}
          >
            {draft.text.trim() || "Internal • Operational Console"}
          </div>
          <SaveBar
            dirty={dirty}
            saving={saving}
            message={message}
            onReset={reset}
            onSave={() =>
              void commit((next) =>
                platform.save({
                  console_classification_banner_enabled: next.enabled,
                  console_classification_banner_text: next.text.trim() || "Internal • Operational Console",
                  console_classification_banner_background_color: normalizeHexColor(next.background, "#2e2a28"),
                  console_classification_banner_text_color: normalizeHexColor(next.foreground, "#e7dcc0"),
                }),
              )
            }
          />
        </>
      )}
    </SettingsGroup>
  );
}

export function AppearanceSection() {
  const theme = useTheme();
  const isDesktop = useIsDesktopShell();

  return (
    <div className="flex flex-col gap-4">
      <SectionHeader title="Appearance" />
      <SettingsGroup title="Theme" description="Saved on this device.">
        <fieldset className="flex flex-wrap gap-2">
          <legend className="sr-only">Theme</legend>
          {(["light", "dark"] as Theme[]).map((value) => (
            <label
              key={value}
              className="flex cursor-pointer items-center gap-2 rounded-[10px] border border-border px-3 py-2 text-[13px] has-[:checked]:border-primary has-[:checked]:bg-primary/5"
            >
              <input type="radio" name="theme" value={value} checked={theme === value} onChange={() => setTheme(value)} />
              {value === "light" ? "Light" : "Dark"}
            </label>
          ))}
        </fieldset>
      </SettingsGroup>
      {!isDesktop ? <ClassificationBannerGroup /> : null}
    </div>
  );
}
