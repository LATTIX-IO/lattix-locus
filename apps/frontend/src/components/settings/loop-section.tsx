"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
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
  getLoopBoard,
  getLoopStatus,
  updateLoopIssueStatus,
  updateLoopIssuePriority,
  type LinearBoard,
  type LinearBoardIssue,
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
  const [boardToken, setBoardToken] = useState(0);
  const [repoPath, setRepoPath] = useState("");
  const [busy, setBusy] = useState<Busy>("");
  const [board, setBoard] = useState<LinearBoard | null>(null);
  const [boardError, setBoardError] = useState<string | null>(null);
  const [movingIssue, setMovingIssue] = useState<string | null>(null);
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

  useEffect(() => {
    if (!status?.linear.connected) {
      setBoard(null);
      setBoardError(null);
      return;
    }
    let cancelled = false;
    getLoopBoard()
      .then((next) => {
        if (cancelled) return;
        setBoard(next);
        setBoardError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setBoardError(reason instanceof Error ? reason.message : "Could not load the Linear board.");
          setBoard(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [status?.linear.connected, boardToken]);

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

  async function moveIssue(issue: LinearBoardIssue, nextState: string) {
    if (!nextState || nextState === issue.state) return;
    setMovingIssue(issue.id);
    setNote(null);
    try {
      await updateLoopIssueStatus(issue.id, nextState);
      setNote({ tone: "success", text: `${issue.identifier} moved to ${nextState}.` });
      setBoardToken((value) => value + 1);
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error) });
    } finally {
      setMovingIssue(null);
    }
  }

  async function prioritizeIssue(issue: LinearBoardIssue, priority: number) {
    if (priority === issue.priority) return;
    setMovingIssue(issue.id);
    setNote(null);
    try {
      await updateLoopIssuePriority(issue.id, priority);
      setNote({ tone: "success", text: `${issue.identifier} priority updated.` });
      setBoardToken((value) => value + 1);
    } catch (error) {
      setNote({ tone: "error", text: describeSaveError(error) });
    } finally {
      setMovingIssue(null);
    }
  }

  const header = (
    <SectionHeader title="Self-improvement" description="Locus owns the issue queue and quality gates. Use the native runner or Codex with local Ollama as its coding engine." />
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

      <SettingsGroup title="Self-improvement">
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

      <SettingsGroup title="Linear MCP">
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Badge variant={status.linear.connected ? "success" : "warning"}>
            <span aria-hidden="true">{status.linear.connected ? "●" : "◐"}</span>
            {status.linear.connected ? "Connected" : "Not connected"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {status.linear.connected
              ? `${status.linear.integration_name || "Linear MCP"} is the loop's issue source.`
              : "Connect Linear MCP to use the board and start the loop."}
          </span>
        </div>
        {!status.linear.connected ? (
          <Link href="/settings?section=connections" className="text-[13px] text-primary underline underline-offset-4">
            Open Connections to connect Linear MCP
          </Link>
        ) : null}
      </SettingsGroup>

      <SettingsGroup
        title="Issue board"
        description={status.project_slug ? `Linear issues in ${status.project_slug}. Status changes are written back through Linear MCP.` : "Set tracker.provider.project_slug in WORKFLOW.md to choose the Linear project."}
      >
        {status.linear.connected ? (
          <>
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs text-muted-foreground" role="status">
                {board ? `${board.issues.length} issues · ${board.states.length} Linear statuses` : boardError ? "Board unavailable" : "Loading Linear board…"}
              </p>
              <Button variant="secondary" size="sm" disabled={!status.project_slug} onClick={() => setBoardToken((value) => value + 1)}>
                Refresh board
              </Button>
            </div>
            {boardError ? <p role="alert" className="text-[13px] text-destructive">{boardError}</p> : null}
            {board ? (
              <div className="flex gap-3 overflow-x-auto pb-2" aria-label="Linear kanban board">
                {board.states.map((state) => {
                  const issues = board.issues.filter((issue) => issue.state.toLowerCase() === state.name.toLowerCase());
                  return (
                    <section key={state.id || state.name} aria-label={`${state.name} issues`} className="w-64 shrink-0 rounded-lg border bg-muted/30 p-3">
                      <h3 className="mb-3 flex items-center justify-between text-[13px] font-medium">
                        <span>{state.name}</span>
                        <span className="text-xs tabular-nums text-muted-foreground">{issues.length}</span>
                      </h3>
                      <ul className="flex flex-col gap-2">
                        {issues.map((issue) => (
                          <li key={issue.id} className="rounded-md border bg-background p-3">
                            <div className="flex items-start justify-between gap-2">
                              {(() => {
                                try {
                                  const url = new URL(issue.url);
                                  if (url.protocol === "https:" && url.hostname === "linear.app") {
                                    return <a href={url.toString()} target="_blank" rel="noreferrer" className="text-xs font-medium text-primary underline underline-offset-4">{issue.identifier}</a>;
                                  }
                                } catch {
                                  // An issue without a canonical Linear URL remains readable, without a link.
                                }
                                return <span className="text-xs font-medium">{issue.identifier}</span>;
                              })()}
                              <span className="text-[11px] text-muted-foreground">{issue.priority ? `P${issue.priority}` : "No priority"}</span>
                            </div>
                            <p className="mt-1 text-[13px] leading-5">{issue.title}</p>
                            <label className="mt-3 flex flex-col gap-1 text-[11px] text-muted-foreground">
                              Priority
                              <select
                                aria-label={`Change ${issue.identifier} priority`}
                                className="h-8 rounded-md border bg-background px-2 text-xs text-foreground"
                                value={issue.priority}
                                disabled={movingIssue === issue.id}
                                onChange={(event) => void prioritizeIssue(issue, Number(event.target.value))}
                              >
                                <option value={0}>No priority</option>
                                <option value={1}>Urgent</option>
                                <option value={2}>High</option>
                                <option value={3}>Normal</option>
                                <option value={4}>Low</option>
                              </select>
                            </label>
                            <label className="mt-3 flex flex-col gap-1 text-[11px] text-muted-foreground">
                              Move to status
                              <select
                                aria-label={`Move ${issue.identifier} to status`}
                                className="h-8 rounded-md border bg-background px-2 text-xs text-foreground"
                                value={issue.state}
                                disabled={movingIssue === issue.id}
                                onChange={(event) => void moveIssue(issue, event.target.value)}
                              >
                                {board.states.map((option) => <option key={option.id || option.name} value={option.name}>{option.name}</option>)}
                              </select>
                            </label>
                            {issue.labels.length ? <p className="mt-2 text-[11px] text-muted-foreground">{issue.labels.join(" · ")}</p> : null}
                          </li>
                        ))}
                      </ul>
                    </section>
                  );
                })}
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-[13px] text-muted-foreground">Connect Linear MCP to load and update issues here.</p>
        )}
      </SettingsGroup>

      <SettingsGroup
        title="Empty-queue research"
        description="When no eligible Linear issue remains, Locus drafts testable hypotheses with its local Ollama model."
      >
        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          <Badge variant={status.research.enabled ? "success" : "secondary"}>
            {status.research.enabled ? "Active" : "Off"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {status.research.enabled
              ? `Up to ${status.research.issues_per_day} prioritized Todo issues per day · ${status.research.model} · generated experiments use the same test and RSI gates.`
              : "Set LOCUS_LOOP_RESEARCH_MODE=true to enable."}
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
