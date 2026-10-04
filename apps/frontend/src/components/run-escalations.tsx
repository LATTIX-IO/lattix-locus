"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { describeSaveError } from "@/components/settings/settings-kit";
import {
  allowSiteInBrowserTier,
  approveRunEscalation,
  denyRunEscalation,
  getRunEscalations,
  type BrowserSiteList,
  type RunEscalation,
} from "@/lib/api";
import { useIsDesktopShell } from "@/lib/desktop-shell";

const ACTION_VERB: Record<string, string> = {
  user_browser_read: "read a page",
  user_browser_navigate: "navigate",
  user_browser_act: "click or type",
};

/** What "Always allow on <site>" does under each tier (policies/user_browser.rego). */
const SITE_LIST_EFFECT: Record<BrowserSiteList, { tier: string; effect: string }> = {
  allowlisted_sites: {
    tier: "Assisted",
    effect: "adds it to the Assisted allowlist: the agent may read and navigate it without asking. Clicks and typing still ask.",
  },
  granted_sites: {
    tier: "Trusted",
    effect: "adds it to the Trusted grant list: the agent may click and type on it without asking. Irreversible actions still ask.",
  },
};

export function describeEscalation(escalation: RunEscalation): string {
  if (escalation.site && escalation.action_kind && ACTION_VERB[escalation.action_kind]) {
    return `The agent wants to ${ACTION_VERB[escalation.action_kind]} on ${escalation.site}.`;
  }
  if (escalation.kind === "gateway") {
    return `The agent wants to use ${escalation.tool || "a tool"}${escalation.path ? ` on ${escalation.path}` : ""}.`;
  }
  return `The agent wants to access ${escalation.path || "a folder outside its working folder"}.`;
}

type Busy = { id: string; kind: "once" | "always" | "deny" } | null;

/**
 * Agent requests waiting on the principal in this run (gateway asks and folder
 * escalations): Allow once, Always allow on <site> (user-browser asks under
 * Assisted or Trusted), or Deny. Allowing widens, so lib/api.ts routes it
 * through the desktop shell's confirmation (approve: confirm_action; the site
 * list: confirm_browser_tier with the new list). Deny grants nothing.
 */
export function RunEscalations({ runId, refreshKey = 0 }: { runId: string; refreshKey?: number }) {
  const isDesktop = useIsDesktopShell();
  const [escalations, setEscalations] = useState<RunEscalation[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const [busy, setBusy] = useState<Busy>(null);
  const [notes, setNotes] = useState<Record<string, { tone: "success" | "error"; text: string }>>({});
  const [confirmingSite, setConfirmingSite] = useState<RunEscalation | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRunEscalations(runId)
      .then((items) => {
        if (cancelled) return;
        setEscalations(items);
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setLoadError(reason instanceof Error ? reason.message : "Could not load the agent's requests.");
      });
    return () => {
      cancelled = true;
    };
  }, [runId, refreshKey, token]);

  const pending = escalations.filter((item) => item.status === "pending");

  function note(id: string, tone: "success" | "error", text: string) {
    setNotes((current) => ({ ...current, [id]: { tone, text } }));
  }

  async function decide(escalation: RunEscalation, kind: "once" | "always" | "deny", acknowledgeRisk = false) {
    setBusy({ id: escalation.id, kind });
    try {
      if (kind === "deny") {
        await denyRunEscalation(runId, escalation.id);
        note(escalation.id, "success", "Denied. The agent was not allowed to do it.");
      } else {
        if (kind === "always" && escalation.site && escalation.site_list) {
          await allowSiteInBrowserTier(escalation.site, escalation.site_list, { acknowledgeRisk });
        }
        await approveRunEscalation(runId, escalation.id);
        note(
          escalation.id,
          "success",
          kind === "always" && escalation.site ? `Allowed, and ${escalation.site} is now always allowed.` : "Allowed once.",
        );
      }
      setToken((value) => value + 1);
    } catch (error) {
      note(escalation.id, "error", describeSaveError(error, "Could not record the decision."));
    } finally {
      setBusy(null);
    }
  }

  function alwaysAllow(escalation: RunEscalation) {
    // The desktop shell shows its own native dialog with the new list; the web
    // profile shows the risk here first and sends the acknowledgement.
    if (isDesktop) {
      void decide(escalation, "always");
    } else {
      setConfirmingSite(escalation);
    }
  }

  if (loadError) {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] border border-destructive/50 bg-destructive/10 px-3 py-2 text-[13px]">
        <span>Could not load the agent&apos;s requests: {loadError}</span>
        <Button variant="secondary" size="sm" onClick={() => setToken((value) => value + 1)}>
          Retry
        </Button>
      </div>
    );
  }

  const resolvedNotes = escalations.filter((item) => item.status !== "pending" && notes[item.id]);
  if (pending.length === 0 && resolvedNotes.length === 0) {
    return null;
  }

  return (
    <section aria-label="Agent requests" className="flex flex-col gap-2">
      {pending.map((escalation) => {
        const offer = escalation.site && escalation.site_list ? SITE_LIST_EFFECT[escalation.site_list] : null;
        const itemNote = notes[escalation.id];
        const itemBusy = busy?.id === escalation.id;
        return (
          <article
            key={escalation.id}
            aria-label={`Request: ${describeEscalation(escalation)}`}
            className="flex flex-col gap-2 rounded-[12px] border border-warning/50 bg-warning/10 px-3 py-2.5 text-[13px]"
          >
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="warning">
                <span aria-hidden="true">◐</span>
                Waiting for you
              </Badge>
              {escalation.risk ? <span className="text-xs text-muted-foreground">Risk {escalation.risk}</span> : null}
            </div>
            <p>{describeEscalation(escalation)}</p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy !== null} onClick={() => void decide(escalation, "once")} aria-busy={itemBusy && busy?.kind === "once"}>
                {itemBusy && busy?.kind === "once" ? "Allowing…" : "Allow once"}
              </Button>
              {offer && escalation.site ? (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={busy !== null}
                  onClick={() => alwaysAllow(escalation)}
                  aria-busy={itemBusy && busy?.kind === "always"}
                  title={`Always allow on ${escalation.site}: ${offer.effect}`}
                >
                  {itemBusy && busy?.kind === "always" ? "Allowing…" : `Always allow on ${escalation.site}`}
                </Button>
              ) : null}
              <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void decide(escalation, "deny")} aria-busy={itemBusy && busy?.kind === "deny"}>
                {itemBusy && busy?.kind === "deny" ? "Denying…" : "Deny"}
              </Button>
            </div>
            {itemNote ? (
              <p role={itemNote.tone === "error" ? "alert" : "status"} className={itemNote.tone === "error" ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
                {itemNote.text}
              </p>
            ) : null}
          </article>
        );
      })}
      {resolvedNotes.map((escalation) => (
        <p key={escalation.id} role="status" className="text-xs text-muted-foreground">
          {notes[escalation.id]?.text}
        </p>
      ))}

      <Dialog open={confirmingSite !== null} onOpenChange={(open) => (!open ? setConfirmingSite(null) : undefined)}>
        <DialogContent>
          {confirmingSite?.site && confirmingSite.site_list ? (
            <>
              <DialogHeader>
                <DialogTitle>Always allow the agent on {confirmingSite.site}?</DialogTitle>
                <DialogDescription>
                  This {SITE_LIST_EFFECT[confirmingSite.site_list].effect} It uses your signed-in session on that site. You can remove it in Settings → Computer use.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="secondary" onClick={() => setConfirmingSite(null)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    const target = confirmingSite;
                    setConfirmingSite(null);
                    void decide(target, "always", true);
                  }}
                >
                  Always allow on {confirmingSite.site}
                </Button>
              </DialogFooter>
            </>
          ) : null}
        </DialogContent>
      </Dialog>
    </section>
  );
}
