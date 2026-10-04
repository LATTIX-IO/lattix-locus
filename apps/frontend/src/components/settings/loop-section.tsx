"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  LoadState,
  SectionHeader,
  SettingsGroup,
  TextField,
  describeSaveError,
} from "@/components/settings/settings-kit";
import {
  disableLoop,
  disableLoopAutostart,
  enableLoop,
  enableLoopAutostart,
  getLoopStatus,
  type LoopStatus,
} from "@/lib/api";

type Busy = "" | "enable" | "disable" | "autostart-on" | "autostart-off";

/**
 * The self-improvement loop (Linear intake → verified run → PR). Turning it on
 * and autostarting it widen what runs unattended, so both are confirmed in the
 * desktop shell; turning it off never needs a confirmation.
 */
export function LoopSection() {
  const [status, setStatus] = useState<LoopStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const [repoPath, setRepoPath] = useState("");
  const [busy, setBusy] = useState<Busy>("");
  const [note, setNote] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLoopStatus()
      .then((next) => {
        if (cancelled) return;
        setStatus(next);
        setRepoPath(next.autostart.repo_path);
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLoadError(reason instanceof Error ? reason.message : "Could not load the loop status.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function act(kind: Exclude<Busy, "">, action: () => Promise<LoopStatus>, done: string) {
    setBusy(kind);
    setNote(null);
    try {
      const next = await action();
      setStatus(next);
      setNote({ tone: "success", text: done });
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error) });
    } finally {
      setBusy("");
    }
  }

  const header = (
    <SectionHeader title="Loop & Linear" description="The self-improvement loop picks eligible Linear issues, runs them, and opens pull requests." />
  );

  if (!status) {
    return (
      <div className="flex flex-col gap-4">
        {header}
        <LoadState loading={!loadError} error={loadError} onRetry={() => setToken((value) => value + 1)} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {header}

      <SettingsGroup title="Loop">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <Badge variant={status.enabled ? "success" : "secondary"}>
              <span aria-hidden="true">{status.enabled ? "●" : "○"}</span>
              {status.enabled ? "On" : "Off"}
            </Badge>
            <span className="tabular-nums text-muted-foreground">
              {status.runs_today}/{status.max_runs_per_day} runs today
            </span>
            {status.active_run ? <Badge variant="warning">Run in progress</Badge> : null}
          </div>
          {status.enabled ? (
            <Button variant="secondary" size="sm" disabled={busy !== ""} onClick={() => void act("disable", disableLoop, "The loop stops before its next step.")}>
              {busy === "disable" ? "Turning off…" : "Turn off"}
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={busy !== "" || status.disabled_by_environment}
              onClick={() => void act("enable", enableLoop, "The loop is on.")}
            >
              {busy === "enable" ? "Turning on…" : "Turn on"}
            </Button>
          )}
        </div>
        {!status.enabled && status.disabled_reason ? (
          <p className="text-xs text-muted-foreground">
            Off because {status.disabled_reason}.{status.disabled_by_environment ? " Clear that variable to turn it on." : ""}
          </p>
        ) : null}
        <p className="text-xs text-muted-foreground">
          {status.last_run?.run_id
            ? `Last run ${status.last_run.run_id} on ${status.last_run.issue ?? "an issue"}: ${status.last_run.outcome ?? "unknown"}.`
            : "No run recorded yet."}{" "}
          {status.open_prs.length} open loop pull request{status.open_prs.length === 1 ? "" : "s"}.
        </p>
      </SettingsGroup>

      <SettingsGroup title="Start with Locus" description="Run the loop whenever the desktop app starts, also after updates. Turning the loop off still wins.">
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-64 flex-1">
            <TextField
              id="loop-autostart-repo"
              label="Repository checkout"
              description="A git checkout that contains WORKFLOW.md, inside your projects folder (your home folder unless LOCUS_PROJECTS_ROOT is set)."
              value={repoPath}
              onChange={setRepoPath}
              placeholder="C:\\src\\lattix-locus"
            />
          </div>
          {status.autostart.enabled ? (
            <Button variant="secondary" size="sm" className="mb-6" disabled={busy !== ""} onClick={() => void act("autostart-off", disableLoopAutostart, "The loop no longer starts with Locus.")}>
              Stop autostart
            </Button>
          ) : null}
          <Button
            size="sm"
            className="mb-6"
            disabled={busy !== "" || !repoPath.trim() || (status.autostart.enabled && repoPath.trim() === status.autostart.repo_path)}
            onClick={() => void act("autostart-on", () => enableLoopAutostart(repoPath.trim()), "The loop starts with Locus.")}
          >
            {status.autostart.enabled ? "Change checkout" : "Start with Locus"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground" role="status">
          {status.autostart.enabled ? `Autostart is on for ${status.autostart.repo_path}.` : "Autostart is off."}
        </p>
      </SettingsGroup>

      <SettingsGroup title="Linear">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Badge variant={status.linear.api_key_configured ? "success" : "warning"}>
            <span aria-hidden="true">{status.linear.api_key_configured ? "●" : "◐"}</span>
            {status.linear.api_key_configured ? "API key stored" : "No API key"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {status.linear.api_key_configured
              ? "LINEAR_API_KEY resolves from the environment or the OS keychain."
              : "Store it in the OS keychain with: lattix secrets set LINEAR_API_KEY"}
          </span>
        </div>
      </SettingsGroup>

      {note ? (
        <p role={note.tone === "error" ? "alert" : "status"} className={note.tone === "error" ? "text-[13px] text-destructive" : "text-[13px] text-muted-foreground"}>
          {note.text}
        </p>
      ) : null}
    </div>
  );
}
