"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  ControlRow,
  ListField,
  LoadState,
  SectionHeader,
  SettingsGroup,
  describeSaveError,
  findControl,
  parseList,
  toListText,
  usePlatformResource,
} from "@/components/settings/settings-kit";
import {
  BROWSER_TIERS,
  getComputerUseStatus,
  getUserBrowserStatus,
  getUserBrowserTier,
  isBrowserTierWidening,
  pairUserBrowser,
  resetComputerUse,
  unpairUserBrowser,
  setUserBrowserTier,
  triggerComputerUsePanic,
  type BrowserTier,
  type BrowserTierChange,
  type ComputerUseStatus,
  type UserBrowserStatus,
  type UserBrowserTierSettings,
} from "@/lib/api";
import { defaultPanicHotkeyLabel, useIsDesktopShell } from "@/lib/desktop-shell";
import type { ControlStatusItem, SecurityPolicyResponse } from "@/types/locus";

const TIER_LABEL: Record<BrowserTier, string> = {
  strict: "Strict",
  assisted: "Assisted",
  trusted: "Trusted",
  open: "Open",
};

/** Shown when the backend does not send its own risk text. */
const FALLBACK_TIER_RISK: Record<BrowserTier, string> = {
  strict: "Every action asks; the agent only reads tabs you share.",
  assisted: "On allowlisted sites the agent reads pages and navigates without asking. Clicks and typing still ask.",
  trusted: "On granted sites the agent clicks and types without asking. Irreversible actions still ask.",
  open: "The agent acts in every tab and site without asking, including irreversible actions.",
};

/**
 * Browser control tier (LOCUS-350). Strict is the default. Widening is
 * confirmed in the desktop shell's native dialog; on the web profile the UI
 * shows the risk text and asks for an explicit acknowledgement first.
 */
export function BrowserTierControl({ onSaved }: { onSaved?: (settings: UserBrowserTierSettings) => void }) {
  const isDesktop = useIsDesktopShell();
  const [current, setCurrent] = useState<UserBrowserTierSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const [tier, setTier] = useState<BrowserTier>("strict");
  const [allowlisted, setAllowlisted] = useState("");
  const [granted, setGranted] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [confirming, setConfirming] = useState<BrowserTierChange | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUserBrowserTier()
      .then((settings) => {
        if (cancelled) return;
        setCurrent(settings);
        setTier(settings.tier);
        setAllowlisted(toListText(settings.allowlisted_sites));
        setGranted(toListText(settings.granted_sites));
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLoadError(reason instanceof Error ? reason.message : "Could not load the browser tier.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const change: BrowserTierChange = useMemo(
    () => ({ tier, allowlisted_sites: parseList(allowlisted), granted_sites: parseList(granted) }),
    [allowlisted, granted, tier],
  );
  const dirty = Boolean(
    current &&
      (tier !== current.tier ||
        JSON.stringify(change.allowlisted_sites) !== JSON.stringify(current.allowlisted_sites) ||
        JSON.stringify(change.granted_sites) !== JSON.stringify(current.granted_sites)),
  );
  const widening = current ? isBrowserTierWidening(current, change) : false;
  const riskText = (value: BrowserTier) => current?.tier_risks?.[value] ?? FALLBACK_TIER_RISK[value];

  const apply = useCallback(
    async (next: BrowserTierChange, acknowledgeRisk: boolean) => {
      if (!current) return;
      setSaving(true);
      setMessage(null);
      try {
        const saved = await setUserBrowserTier(current, next, { acknowledgeRisk });
        const merged = { ...current, ...saved };
        setCurrent(merged);
        setTier(merged.tier);
        setAllowlisted(toListText(merged.allowlisted_sites));
        setGranted(toListText(merged.granted_sites));
        setMessage({ tone: "success", text: `Browser tier is ${TIER_LABEL[merged.tier] ?? merged.tier}.` });
        onSaved?.(merged);
      } catch (error) {
        setMessage({ tone: "error", text: describeSaveError(error) });
      } finally {
        setSaving(false);
      }
    },
    [current, onSaved],
  );

  function save() {
    // Desktop: the shell shows its own native dialog with the risk text.
    if (widening && !isDesktop) {
      setConfirming(change);
      return;
    }
    void apply(change, false);
  }

  if (!current) {
    return <LoadState loading={!loadError} error={loadError} onRetry={() => setToken((value) => value + 1)} />;
  }

  return (
    <div className="flex flex-col gap-3">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-[13px] font-medium">Tier</legend>
        {BROWSER_TIERS.map((value) => (
          <label
            key={value}
            className="flex cursor-pointer items-start gap-3 rounded-[10px] border border-border px-3 py-2.5 has-[:checked]:border-primary has-[:checked]:bg-primary/5"
          >
            <input
              type="radio"
              name="browser-tier"
              value={value}
              checked={tier === value}
              onChange={() => {
                setTier(value);
                setMessage(null);
              }}
              className="mt-1"
            />
            <span className="min-w-0">
              <span className="text-[13px] font-medium">
                {TIER_LABEL[value]}
                {value === "strict" ? " (default)" : ""}
                {current.tier === value ? " · current" : ""}
              </span>
              <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">{riskText(value)}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {tier === "assisted" || tier === "trusted" ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <ListField
            id="browser-allowlisted-sites"
            label="Allowlisted sites (read and navigate)"
            description="May stay empty: approve sites one by one with “Always allow” when the agent asks."
            value={allowlisted}
            onChange={setAllowlisted}
            placeholder={"docs.example.com"}
          />
          <ListField
            id="browser-granted-sites"
            label="Granted sites (act, Trusted)"
            description="May stay empty: approve sites one by one with “Always allow” when the agent asks."
            value={granted}
            onChange={setGranted}
            placeholder={"app.example.com"}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-end gap-2">
        {message ? (
          <p role={message.tone === "error" ? "alert" : "status"} className={message.tone === "error" ? "text-[13px] text-destructive" : "text-[13px] text-muted-foreground"}>
            {message.text}
          </p>
        ) : null}
        <Button size="sm" disabled={!dirty || saving} onClick={save} aria-busy={saving}>
          {saving ? "Saving…" : widening ? (isDesktop ? "Confirm in Locus…" : "Review and save") : "Save tier"}
        </Button>
      </div>

      <Dialog open={confirming !== null} onOpenChange={(open) => (!open ? setConfirming(null) : undefined)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Widen browser access to {confirming ? TIER_LABEL[confirming.tier] : ""}?</DialogTitle>
            <DialogDescription>{confirming ? riskText(confirming.tier) : ""}</DialogDescription>
          </DialogHeader>
          {confirming ? (
            <dl className="grid gap-1 text-xs">
              <dt className="font-medium">Allowlisted sites</dt>
              <dd className="text-muted-foreground">{confirming.allowlisted_sites.join(", ") || "(none)"}</dd>
              <dt className="mt-1 font-medium">Granted sites</dt>
              <dd className="text-muted-foreground">{confirming.granted_sites.join(", ") || "(none)"}</dd>
            </dl>
          ) : null}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                const next = confirming;
                setConfirming(null);
                if (next) void apply(next, true);
              }}
            >
              I understand, widen access
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Every computer-use and browser action is a gateway decision, and the gateway
 * denies everything while its policy engine is not running (fail closed). Say
 * so plainly instead of leaving two "Unverified" badges unexplained.
 */
function PolicyEngineNotice({ control }: { control: ControlStatusItem | undefined }) {
  if (!control || control.state === "enforced") return null;
  const text =
    control.state === "off"
      ? "Policy engine missing: reinstall Lattix Locus. Until it runs, the gateway denies every model call, computer-use action and browser action."
      : "Policy engine not running: restart Lattix Locus, and reinstall if this persists. Until it runs, the gateway denies every model call, computer-use action and browser action.";
  return (
    <p role="alert" className="rounded-[10px] border border-destructive/40 px-3 py-2.5 text-[13px] text-destructive">
      {text}
    </p>
  );
}

export function ComputerUseSection() {
  const isDesktop = useIsDesktopShell();
  const platform = usePlatformResource({ withPolicy: true });
  const [status, setStatus] = useState<ComputerUseStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "panic" | "reset">("");
  const [note, setNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getComputerUseStatus()
      .then((next) => {
        if (!cancelled) {
          setStatus(next);
          setStatusError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setStatusError(reason instanceof Error ? reason.message : "Could not load computer-use status.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function act(kind: "panic" | "reset") {
    setBusy(kind);
    setNote(null);
    try {
      if (kind === "panic") {
        await triggerComputerUsePanic();
        setNote({ tone: "success", text: "Stopped. Every computer-use action was cancelled." });
      } else {
        await resetComputerUse();
        setNote({ tone: "success", text: "Computer use may run again." });
      }
      setToken((value) => value + 1);
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error) });
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <SectionHeader title="Computer use" description="How the agent may use this computer and your signed-in browser." />

      <SettingsGroup title="Status and stop">
        {statusError ? (
          <LoadState loading={false} error={statusError} onRetry={() => setToken((value) => value + 1)} />
        ) : !status ? (
          <p role="status" className="text-[13px] text-muted-foreground">Loading…</p>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <Badge variant={status.panicked ? "destructive" : "secondary"}>
                <span aria-hidden="true">{status.panicked ? "■" : "●"}</span>
                {status.panicked ? "Stopped (panic)" : `Mode: ${status.mode}`}
              </Badge>
              <span className="text-muted-foreground tabular-nums">{status.inflight_actions} actions in flight</span>
            </div>
            <div className="flex gap-2">
              {status.panicked ? (
                <Button size="sm" variant="secondary" disabled={busy !== ""} onClick={() => void act("reset")}>
                  {busy === "reset" ? "Resuming…" : "Resume computer use"}
                </Button>
              ) : (
                <Button size="sm" variant="destructive" disabled={busy !== ""} onClick={() => void act("panic")}>
                  {busy === "panic" ? "Stopping…" : "Stop all now"}
                </Button>
              )}
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-medium">Panic key</span>
          <kbd className="rounded-md border border-border bg-muted px-2 py-0.5 font-mono text-xs">{defaultPanicHotkeyLabel()}</kbd>
          <span className="text-xs text-muted-foreground">
            {isDesktop
              ? "Stops every run and releases input, even when this window is frozen. LOCUS_PANIC_HOTKEY sets another chord."
              : "Available in the desktop app."}
          </span>
        </div>
        {note ? (
          <p role={note.tone === "error" ? "alert" : "status"} className={note.tone === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {note.text}
          </p>
        ) : null}
      </SettingsGroup>

      <SettingsGroup title="Operating system access" description="As the backend observes it: the desktop driver and OS permissions.">
        {/* A recheck keeps the rows mounted (and their notes) while it reloads. */}
        {(platform.loading && !platform.policy) || platform.error ? (
          <LoadState loading={platform.loading} error={platform.error} onRetry={platform.reload} />
        ) : (
          <div className="flex flex-col gap-3">
            <PolicyEngineNotice control={findControl(platform.policy, "policy_engine_rego")} />
            <OperatingSystemAccess policy={platform.policy} onChanged={platform.reload} />
          </div>
        )}
      </SettingsGroup>

      <SettingsGroup title="Browser control" description="How much the agent may do in your signed-in browser.">
        <BrowserTierControl />
      </SettingsGroup>
    </div>
  );
}

/**
 * Operating system access: what the backend reports for desktop control and
 * the principal's own browser, with the actions that change it. Status is
 * never upgraded by the UI (P9); pairing widens access, so on the desktop it is
 * confirmed in the shell's native dialog (confirm_browser_pairing, LOCUS-350).
 */
export function OperatingSystemAccess({
  policy,
  onChanged,
}: {
  policy: SecurityPolicyResponse | null;
  onChanged: () => void;
}) {
  const isDesktop = useIsDesktopShell();
  const computerUse = findControl(policy, "computer_use");
  const [browser, setBrowser] = useState<UserBrowserStatus | null>(null);
  const [browserError, setBrowserError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const [busy, setBusy] = useState<"" | "pair" | "unpair">("");
  const [note, setNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getUserBrowserStatus()
      .then((next) => {
        if (!cancelled) {
          setBrowser(next);
          setBrowserError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setBrowserError(reason instanceof Error ? reason.message : "Could not load the browser pairing.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function changePairing(kind: "pair" | "unpair") {
    setBusy(kind);
    setNote(null);
    try {
      const next = kind === "pair" ? await pairUserBrowser() : await unpairUserBrowser();
      setBrowser((current) => ({ ...(current ?? { paired: false, connected: false }), ...next }));
      setNote({
        tone: "success",
        text:
          kind === "pair"
            ? "Browser paired. Open the Locus extension in your browser to connect it."
            : "Browser unpaired. The agent can no longer use your browser.",
      });
      onChanged();
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error, kind === "pair" ? "Could not pair the browser." : "Could not unpair the browser.") });
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="grid gap-2 lg:grid-cols-2">
      <div className="flex flex-col gap-2">
        <ControlRow control={computerUse} fallbackLabel="Computer use" />
        {computerUse?.state !== "enforced" ? (
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-xs text-muted-foreground">
            <span>Desktop control turns on by itself once the policy engine is running; there is no switch to force it.</span>
            <Button size="sm" variant="secondary" onClick={onChanged}>
              Check again
            </Button>
          </div>
        ) : null}
      </div>
      <div className="flex flex-col gap-2">
        <ControlRow control={findControl(policy, "user_browser")} fallbackLabel="Your browser" />
        {browserError ? (
          <LoadState loading={false} error={browserError} onRetry={() => setToken((value) => value + 1)} />
        ) : !browser ? (
          <p role="status" className="px-1 text-xs text-muted-foreground">Checking the browser pairing…</p>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            <span className="text-xs text-muted-foreground">
              {browser.paired ? (browser.connected ? "Paired and connected." : "Paired, not connected.") : "Not paired."}
            </span>
            <div className="flex gap-2">
              <Button size="sm" disabled={busy !== ""} onClick={() => void changePairing("pair")} aria-busy={busy === "pair"}>
                {busy === "pair" ? "Pairing…" : browser.paired ? (isDesktop ? "Re-pair…" : "Re-pair") : isDesktop ? "Pair browser…" : "Pair browser"}
              </Button>
              {browser.paired ? (
                <Button size="sm" variant="secondary" disabled={busy !== ""} onClick={() => void changePairing("unpair")} aria-busy={busy === "unpair"}>
                  {busy === "unpair" ? "Unpairing…" : "Unpair"}
                </Button>
              ) : null}
            </div>
          </div>
        )}
        {note ? (
          <p role={note.tone === "error" ? "alert" : "status"} className={note.tone === "error" ? "px-1 text-xs text-destructive" : "px-1 text-xs text-muted-foreground"}>
            {note.text}
          </p>
        ) : null}
      </div>
    </div>
  );
}
