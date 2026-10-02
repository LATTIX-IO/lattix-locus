"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type ChangeEvent, type FormEvent, type MouseEvent as ReactMouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { StatusChip } from "@/components/status-chip";
import { resolveFloatingMenuPosition } from "@/lib/floating-menu";
import { useKeyboardShortcuts } from "@/lib/keyboard-shortcuts";
import { archiveWorkflowRun, getInbox, getWorkflowRuns, updateWorkflowRunTitle } from "@/lib/api";
import type { InboxItem, PlatformVersionStatus, WorkflowRunSummary } from "@/types/frontier";

type UserConsoleSidebarProps = {
  pathname: string;
  selectedSessionId: string | null;
  expanded: boolean;
  platformVersion?: PlatformVersionStatus | null;
};

type SessionRow = WorkflowRunSummary & {
  inboxCount: number;
  inboxReasons: string[];
};

type SessionMenuState = {
  anchorX: number;
  anchorY: number;
  left: number;
  runId: string;
  top: number;
};

const FALLBACK_MENU_WIDTH = 144;
const FALLBACK_MENU_HEIGHT = 84;

function sessionHref(runId: string): string {
  return `/inbox?session=${encodeURIComponent(runId)}`;
}

function formatSessionSearchText(run: SessionRow): string {
  return [run.title, run.status, run.progressLabel, ...run.inboxReasons].join(" ").toLowerCase();
}

type NavIconName = "home" | "workflow" | "playbooks" | "memory" | "tasks" | "settings";

function NavIcon({ name, active }: { name: NavIconName; active: boolean }) {
  const cls = `h-4 w-4 flex-shrink-0 ${active ? "text-[var(--fx-primary-strong)]" : "text-[var(--fx-muted)]"}`;
  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
        <rect x="3" y="11" width="18" height="10" rx="1" />
        <path d="M3 11L12 3l9 8" />
      </svg>
    );
  }
  if (name === "workflow") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="5" cy="12" r="2" />
        <circle cx="12" cy="6" r="2" />
        <circle cx="19" cy="12" r="2" />
        <circle cx="12" cy="18" r="2" />
        <path d="M7 11l3-3M14 8l3 3M14 16l3-3M10 16l-3-3" />
      </svg>
    );
  }
  if (name === "playbooks") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
        <rect x="4" y="3" width="16" height="5" rx="1" />
        <rect x="4" y="10" width="16" height="5" rx="1" />
        <rect x="4" y="17" width="10" height="4" rx="1" />
      </svg>
    );
  }
  if (name === "memory") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
        <ellipse cx="12" cy="6" rx="8" ry="3" />
        <path d="M4 6v4c0 1.66 3.58 3 8 3s8-1.34 8-3V6M4 10v4c0 1.66 3.58 3 8 3s8-1.34 8-3v-4" />
      </svg>
    );
  }
  if (name === "tasks") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M4 4h16v11H4z" />
        <path d="M4 15h5l2 3h2l2-3h5" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export function UserConsoleSidebar({ pathname, selectedSessionId, expanded, platformVersion }: UserConsoleSidebarProps) {
  const router = useRouter();
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [inboxItems, setInboxItems] = useState<InboxItem[]>([]);
  const [search, setSearch] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editingRunId, setEditingRunId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [archiveBusyRunId, setArchiveBusyRunId] = useState<string | null>(null);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [sessionMenu, setSessionMenu] = useState<SessionMenuState | null>(null);
  const sessionMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getWorkflowRuns(), getInbox()])
      .then(([nextRuns, nextInbox]) => {
        if (cancelled) {
          return;
        }
        setLoadError(null);
        setRuns(nextRuns);
        setInboxItems(nextInbox);
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setRuns([]);
        setInboxItems([]);
        setLoadError(error instanceof Error ? error.message : "Unable to load inbox sessions.");
      });
    return () => {
      cancelled = true;
    };
  }, [pathname, selectedSessionId]);

  useKeyboardShortcuts([
    {
      key: "Escape",
      enabled: sessionMenu !== null,
      handler: () => setSessionMenu(null),
    },
  ]);

  useEffect(() => {
    if (!sessionMenu) {
      return;
    }

    const activeSessionMenu = sessionMenu;

    function updateMenuPosition() {
      const nextPosition = resolveFloatingMenuPosition({
        anchorX: activeSessionMenu.anchorX,
        anchorY: activeSessionMenu.anchorY,
        menuWidth: sessionMenuRef.current?.offsetWidth ?? FALLBACK_MENU_WIDTH,
        menuHeight: sessionMenuRef.current?.offsetHeight ?? FALLBACK_MENU_HEIGHT,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      });
      setSessionMenu((current) => {
        if (!current) {
          return current;
        }
        if (current.left === nextPosition.left && current.top === nextPosition.top) {
          return current;
        }
        return {
          ...current,
          left: nextPosition.left,
          top: nextPosition.top,
        };
      });
    }

    updateMenuPosition();
    const frameId = window.requestAnimationFrame(updateMenuPosition);
    window.addEventListener("resize", updateMenuPosition);
    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener("resize", updateMenuPosition);
    };
  }, [sessionMenu]);

  const sessions = useMemo<SessionRow[]>(() => {
    const inboxByRun = new Map<string, InboxItem[]>();
    for (const item of inboxItems) {
      const current = inboxByRun.get(item.runId) ?? [];
      current.push(item);
      inboxByRun.set(item.runId, current);
    }
    return runs.map((run) => {
      const related = inboxByRun.get(run.id) ?? [];
      return {
        ...run,
        inboxCount: related.length,
        inboxReasons: related.map((i) => i.reason),
      };
    });
  }, [inboxItems, runs]);

  const filteredSessions = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((s) => formatSessionSearchText(s).includes(query));
  }, [search, sessions]);

  const versionLabel = platformVersion?.current_version ? `v${platformVersion.current_version}` : "No version";
  const updateLabel = platformVersion?.status === "update_available" && platformVersion.latest_version
    ? `Update ${platformVersion.latest_version}`
    : platformVersion?.status === "up_to_date"
      ? "Current"
      : "Unchecked";

  if (!expanded) return null;

  async function handleRenameSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingRunId) {
      return;
    }

    const nextTitle = editingTitle.trim();
    if (!nextTitle) {
      setRenameError("Title is required.");
      return;
    }

    try {
      setRenameBusy(true);
      setRenameError(null);
      const updated = await updateWorkflowRunTitle(editingRunId, nextTitle);
      setRuns((current) => current.map((run) => (run.id === updated.id ? updated : run)));
      setEditingRunId(null);
      setEditingTitle("");
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "Unable to rename this session.");
    } finally {
      setRenameBusy(false);
    }
  }

  function openRenameEditor(runId: string, title: string) {
    setEditingRunId(runId);
    setEditingTitle(title);
    setRenameError(null);
    setSessionMenu(null);
  }

  function openSessionMenu(runId: string, x: number, y: number) {
    const position = resolveFloatingMenuPosition({
      anchorX: x,
      anchorY: y,
      menuWidth: FALLBACK_MENU_WIDTH,
      menuHeight: FALLBACK_MENU_HEIGHT,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    });
    setSessionMenu({
      runId,
      anchorX: x,
      anchorY: y,
      left: position.left,
      top: position.top,
    });
  }

  function handleSessionContextMenu(event: ReactMouseEvent<HTMLElement>, runId: string) {
    event.preventDefault();
    openSessionMenu(runId, event.clientX, event.clientY);
  }

  function handleMenuButtonClick(event: ReactMouseEvent<HTMLButtonElement>, runId: string) {
    event.preventDefault();
    event.stopPropagation();
    const bounds = event.currentTarget.getBoundingClientRect();
    openSessionMenu(runId, bounds.right, bounds.bottom + 4);
  }

  async function handleArchiveRun(runId: string) {
    setArchiveBusyRunId(runId);
    setSessionMenu(null);
    try {
      await archiveWorkflowRun(runId);
      setRuns((current) => current.filter((run) => run.id !== runId));
      setInboxItems((current) => current.filter((item) => item.runId !== runId));
      if (editingRunId === runId) {
        setEditingRunId(null);
        setEditingTitle("");
        setRenameError(null);
      }
      if (pathname.startsWith("/inbox") && selectedSessionId === runId) {
        router.replace("/inbox");
      }
      router.refresh();
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "Unable to archive this session.");
    } finally {
      setArchiveBusyRunId(null);
    }
  }

  return (
    <div className="relative flex h-full flex-col bg-[var(--fx-sidebar)]">
      <div className="border-b border-[var(--ui-border)] px-3 py-3">
        <label htmlFor="session-search" className="sr-only">
          Search sessions
        </label>
        <p className="mb-2 px-0.5 text-[0.67rem] font-medium tracking-[0.05em] text-[var(--fx-muted)]">Search</p>
        <div className="rounded-[12px] border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2.5 py-2 shadow-[var(--fx-shadow-soft)]">
          <div className="flex items-center gap-2">
            <span className="text-[0.72rem] text-[var(--fx-muted)]">/</span>
            <input
              id="session-search"
              type="search"
              value={search}
              onChange={(event: ChangeEvent<HTMLInputElement>) => setSearch(event.target.value)}
              placeholder="Search sessions"
              className="w-full bg-transparent text-[0.76rem] text-[var(--foreground)] outline-none placeholder:text-[var(--fx-muted)]"
            />
          </div>
        </div>
      </div>

      <div className="border-b border-[var(--ui-border)] px-3 pb-3 pt-3">
        <p className="text-[0.67rem] font-medium tracking-[0.05em] text-[var(--fx-muted)]">Workspace</p>
        <div className="mt-2 space-y-1">
          <Link href="/inbox" className={pathname.startsWith("/inbox") ? "fx-nav-item fx-nav-item-active min-h-0 justify-start px-2.5 py-2 text-[0.78rem]" : "fx-nav-item min-h-0 justify-start px-2.5 py-2 text-[0.78rem]"}>
            Conversations
          </Link>
          <Link href="/workflows/start" className={pathname.startsWith("/workflows") ? "fx-nav-item fx-nav-item-active min-h-0 justify-start px-2.5 py-2 text-[0.78rem]" : "fx-nav-item min-h-0 justify-start px-2.5 py-2 text-[0.78rem]"}>
            Workflows
          </Link>
          <Link href="/artifacts" className={pathname.startsWith("/artifacts") ? "fx-nav-item fx-nav-item-active min-h-0 justify-start px-2.5 py-2 text-[0.78rem]" : "fx-nav-item min-h-0 justify-start px-2.5 py-2 text-[0.78rem]"}>
            Artifacts
          </Link>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2.5 py-3">
        <div className="mb-2 flex items-center justify-between px-1">
          <p className="text-[0.67rem] font-medium tracking-[0.05em] text-[var(--fx-muted)]">Sessions</p>
          <span className="rounded-full border border-[var(--ui-border)] bg-[color-mix(in_srgb,hsl(var(--card))_90%,hsl(var(--muted))_10%)] px-2 py-0.5 text-[0.68rem] font-medium text-[var(--fx-muted)]">{filteredSessions.length}</span>
        </div>

        <div className="space-y-1">
          {loadError && filteredSessions.length === 0 ? (
            <div className="rounded-[14px] border border-dashed border-[var(--ui-border)] px-3 py-4 text-[0.78rem] text-[var(--fx-muted)]">
              Unable to load sessions right now. {loadError}
            </div>
          ) : filteredSessions.length === 0 ? (
            <div className="rounded-[14px] border border-dashed border-[var(--ui-border)] px-3 py-4 text-[0.78rem] text-[var(--fx-muted)]">
              No sessions match this search.
            </div>
          ) : (
            filteredSessions.map((run: SessionRow) => {
              const active = pathname.startsWith("/inbox") && selectedSessionId === run.id;
              const editing = editingRunId === run.id;
              return (
                <div
                  key={run.id}
                  onContextMenu={(event) => handleSessionContextMenu(event, run.id)}
                  className={active ? "block rounded-[14px] border border-[var(--fx-nav-active-border)] bg-[var(--fx-nav-active)] px-2.5 py-2.5 shadow-[var(--fx-shadow-soft)]" : "block rounded-[14px] border border-transparent px-2.5 py-2.5 transition hover:border-[var(--fx-sidebar-divider)] hover:bg-[var(--fx-nav-hover)]"}
                >
                  <div className="flex items-start gap-2">
                    <Link href={sessionHref(run.id)} className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="min-w-0 flex-1 truncate text-[0.82rem] font-semibold tracking-[-0.01em] leading-5 text-[hsl(var(--foreground))]">{run.title}</p>
                        {run.inboxCount > 0 ? (
                          <span className="rounded-full border border-[var(--ui-border)] bg-[hsl(var(--card))] px-1.5 py-0.5 text-[0.62rem] font-semibold text-[var(--foreground)]">
                            {run.inboxCount}
                          </span>
                        ) : null}
                      </div>
                      <div className="mt-1.5 flex items-center gap-1.5 overflow-hidden">
                        <StatusChip status={run.status} />
                        <span className="truncate text-[0.64rem] text-[var(--fx-muted)]">{run.updatedAt}</span>
                      </div>
                    </Link>
                    <button
                      type="button"
                      aria-label={`More actions for ${run.title}`}
                      onClick={(event) => handleMenuButtonClick(event, run.id)}
                      className="rounded-[10px] px-1.5 py-1 text-[0.85rem] leading-none text-[var(--fx-muted)] transition hover:bg-[var(--fx-nav-hover)] hover:text-[var(--foreground)]"
                    >
                      ...
                    </button>
                  </div>
                  {editing ? (
                    <form className="mt-2 space-y-2" onSubmit={handleRenameSubmit}>
                      <input
                        type="text"
                        value={editingTitle}
                        onChange={(event: ChangeEvent<HTMLInputElement>) => setEditingTitle(event.target.value)}
                        className="w-full rounded-[10px] border border-[var(--ui-border)] bg-[hsl(var(--card))] px-2.5 py-2 text-[0.8rem] text-[hsl(var(--foreground))] outline-none"
                        placeholder="Rename session"
                        maxLength={120}
                        autoFocus
                      />
                      <div className="flex items-center gap-2">
                        <button type="submit" disabled={renameBusy} className="fx-btn-secondary h-8 px-3 text-[0.72rem] font-medium disabled:opacity-60">
                          {renameBusy ? "Saving..." : "Save"}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setEditingRunId(null);
                            setEditingTitle("");
                            setRenameError(null);
                          }}
                          className="fx-btn-secondary h-8 px-3 text-[0.72rem] font-medium"
                        >
                          Cancel
                        </button>
                      </div>
                      {renameError ? <p className="text-[0.72rem] text-[var(--fx-danger)]">{renameError}</p> : null}
                    </form>
                  ) : null}
                </div>
              );
            })
          )}
        </div>
      </div>

      <div className="border-t border-[var(--ui-border)] px-3 py-3">
        <div className="flex items-center justify-between gap-2 text-[0.72rem] text-[var(--fx-muted)]">
          <span>{versionLabel}</span>
          <span>{updateLabel}</span>
        </div>
        <Link href="/settings" className="mt-2 inline-flex text-[0.72rem] font-medium text-[var(--fx-primary-strong)] no-underline hover:underline">
          Preferences
        </Link>
      </div>

      {sessionMenu ? (
        <>
          <button
            type="button"
            aria-label="Dismiss session actions"
            className="fixed inset-0 z-[119] cursor-default bg-transparent"
            onClick={() => setSessionMenu(null)}
          />
          <div
            ref={sessionMenuRef}
            role="menu"
            aria-label="Session actions"
            className="fixed z-[120] min-w-[9rem] rounded-[12px] border border-[var(--ui-border)] bg-[hsl(var(--card))] p-1 shadow-[var(--fx-shadow-panel)]"
            style={{ left: `${sessionMenu.left}px`, top: `${sessionMenu.top}px` }}
            onClick={(event) => event.stopPropagation()}
            onContextMenu={(event) => event.preventDefault()}
          >
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                const run = runs.find((item) => item.id === sessionMenu.runId);
                if (run) {
                  openRenameEditor(run.id, run.title);
                }
              }}
              className="flex w-full items-center justify-start rounded-[10px] px-2.5 py-2 text-left text-[0.78rem] font-medium text-[var(--foreground)] hover:bg-[var(--fx-nav-hover)]"
            >
              Rename
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => void handleArchiveRun(sessionMenu.runId)}
              disabled={archiveBusyRunId === sessionMenu.runId}
              className="flex w-full items-center justify-start rounded-[10px] px-2.5 py-2 text-left text-[0.78rem] font-medium text-[var(--fx-danger)] hover:bg-[var(--fx-nav-hover)] disabled:opacity-60"
            >
              {archiveBusyRunId === sessionMenu.runId ? "Archiving..." : "Archive"}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
