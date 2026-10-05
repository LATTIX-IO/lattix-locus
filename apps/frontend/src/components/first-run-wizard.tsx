"use client";

import { CheckIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { UpdatesPanel } from "@/components/navigation/platform-update-panel";
import { BrowserTierControl } from "@/components/settings/computer-use-section";
import { describeSaveError } from "@/components/settings/settings-kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getModelsOverview, setProviderKey, type ModelsOverview } from "@/lib/api";
import { isDesktopShell } from "@/lib/desktop-shell";
import { cn } from "@/lib/utils";

const COMPLETE_KEY = "locus-first-run-complete";
const OPEN_EVENT = "locus:open-first-run";

export const FIRST_RUN_STEPS = [
  { id: "welcome", label: "Welcome" },
  { id: "engines", label: "Engines" },
  { id: "browser", label: "Browser control" },
  { id: "updates", label: "Updates" },
  { id: "done", label: "Done" },
] as const;

export function isFirstRunComplete(): boolean {
  try {
    return window.localStorage?.getItem(COMPLETE_KEY) === "1";
  } catch {
    return false;
  }
}

function markFirstRunComplete(): void {
  try {
    window.localStorage?.setItem(COMPLETE_KEY, "1");
  } catch {
    // Without storage the wizard may show again next launch; nothing else breaks.
  }
}

/** Re-open the setup wizard (Settings → Run setup again). */
export function openFirstRunWizard(): void {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

function EnginesStep() {
  const [overview, setOverview] = useState<ModelsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nimKey, setNimKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getModelsOverview()
      .then((next) => {
        if (!cancelled) {
          setOverview(next);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not check for local models.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const ollama = overview?.providers.ollama;
  const nimConfigured = overview?.external.find((entry) => entry.id === "nim")?.configured ?? false;

  async function saveNimKey() {
    setSaving(true);
    setNote(null);
    try {
      await setProviderKey("nim", nimKey.trim());
      setNimKey("");
      setNote({ tone: "success", text: "NVIDIA NIM key stored in the OS keychain." });
      setToken((value) => value + 1);
    } catch (reason) {
      setNote({ tone: "error", text: describeSaveError(reason) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 text-[13px]">
      <section aria-label="Local models" className="flex flex-col gap-2">
        <h3 className="font-semibold">Local models</h3>
        {error ? (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-2 text-destructive">
            Could not check for Ollama: {error}
            <Button variant="secondary" size="sm" onClick={() => setToken((value) => value + 1)}>
              Retry
            </Button>
          </div>
        ) : !overview ? (
          <p role="status" className="text-muted-foreground">Looking for Ollama…</p>
        ) : ollama?.available ? (
          <p role="status">
            <Badge variant="success">● Ollama found</Badge>{" "}
            {ollama.installed_models.length
              ? `${ollama.installed_models.length} model${ollama.installed_models.length === 1 ? "" : "s"} ready.`
              : "No models yet."}{" "}
            <Link href="/settings?section=engines" className="underline underline-offset-2">
              Manage models
            </Link>
          </p>
        ) : (
          <p role="status">
            <Badge variant="warning">◐ Ollama not found</Badge> Install Ollama to run models on this machine. Nothing is sent off-device until you add a hosted engine.
          </p>
        )}
      </section>
      <section aria-label="NVIDIA NIM" className="flex flex-col gap-2">
        <h3 className="font-semibold">NVIDIA NIM (optional)</h3>
        {nimConfigured ? <p role="status">● A NIM key is already stored.</p> : null}
        <Label htmlFor="first-run-nim-key">NIM API key</Label>
        <div className="flex gap-2">
          <Input
            id="first-run-nim-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="nvapi-…"
            value={nimKey}
            onChange={(event) => setNimKey(event.target.value)}
          />
          <Button variant="secondary" className="shrink-0" disabled={!nimKey.trim() || saving} onClick={() => void saveNimKey()}>
            {saving ? "Saving…" : "Save key"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Kept in the OS keychain. The desktop app asks you to confirm before storing it.</p>
        {note ? (
          <p role={note.tone === "error" ? "alert" : "status"} className={note.tone === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
            {note.text}
          </p>
        ) : null}
      </section>
    </div>
  );
}

/**
 * First-run setup (LOCUS-353 skeleton): welcome, engines, browser control,
 * updates, done. Opens on the desktop app's first launch; every step can be
 * skipped, and Settings → Run setup again re-opens it.
 */
export function FirstRunWizard() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    const show = () => {
      setStep(0);
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, show);
    if (isDesktopShell() && !isFirstRunComplete()) {
      // Deferred so the shell paints first.
      const timer = window.setTimeout(show, 0);
      return () => {
        window.clearTimeout(timer);
        window.removeEventListener(OPEN_EVENT, show);
      };
    }
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, []);

  function finish() {
    markFirstRunComplete();
    setOpen(false);
  }

  const current = FIRST_RUN_STEPS[step];
  const last = step === FIRST_RUN_STEPS.length - 1;

  return (
    <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : finish())}>
      <DialogContent className="max-w-2xl" aria-describedby="first-run-description">
        <DialogHeader>
          <DialogTitle>Set up Locus</DialogTitle>
          <DialogDescription id="first-run-description">
            Step {step + 1} of {FIRST_RUN_STEPS.length}: {current.label}
          </DialogDescription>
        </DialogHeader>

        <ol aria-label="Setup steps" className="flex flex-wrap gap-1.5">
          {FIRST_RUN_STEPS.map((item, index) => (
            <li
              key={item.id}
              aria-current={index === step ? "step" : undefined}
              className={cn(
                "flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px]",
                index === step ? "border-primary bg-primary/10 font-medium" : "border-border text-muted-foreground",
              )}
            >
              {index < step ? <CheckIcon aria-hidden="true" className="size-3" /> : <span aria-hidden="true">{index + 1}</span>}
              {item.label}
            </li>
          ))}
        </ol>

        <div className="min-h-48">
          {current.id === "welcome" ? (
            <div className="flex flex-col gap-2 text-[13px] leading-6">
              <p>Locus runs agents on this computer. A few choices get you going; you can change all of them later in Settings.</p>
              <p className="text-muted-foreground">Anything that lets the agents do more asks you to confirm in a Locus dialog first.</p>
            </div>
          ) : null}
          {current.id === "engines" ? <EnginesStep /> : null}
          {current.id === "browser" ? (
            <div className="flex flex-col gap-2 text-[13px]">
              <p className="text-muted-foreground">Strict is the default and the recommended start: the agent only reads tabs you share and asks before anything else.</p>
              <BrowserTierControl />
            </div>
          ) : null}
          {current.id === "updates" ? <UpdatesPanel /> : null}
          {current.id === "done" ? (
            <div className="flex flex-col gap-2 text-[13px] leading-6">
              <p>You are set. Start a task from Home; runs show up in Activity.</p>
              <p className="text-muted-foreground">Settings → Run setup again brings this back.</p>
            </div>
          ) : null}
        </div>

        <DialogFooter className="justify-between sm:justify-between">
          {!last ? (
            <Button variant="ghost" onClick={finish}>
              Skip setup
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            {step > 0 ? (
              <Button variant="secondary" onClick={() => setStep((value) => value - 1)}>
                Back
              </Button>
            ) : null}
            {last ? (
              <Button onClick={finish}>Finish</Button>
            ) : (
              <Button onClick={() => setStep((value) => value + 1)}>{step === 0 ? "Get started" : "Next"}</Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
