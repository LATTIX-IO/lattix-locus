"use client";

import { useEffect, useState } from "react";
import { ListField, LoadState, SaveBar, SettingsGroup, parseList, toListText, useDraft } from "@/components/settings/settings-kit";
import { getUserSkills, saveUserSkills } from "@/lib/api";

/**
 * Your personal /skills (Library → Skills → Personal): the slash skills loaded
 * into your agents. Adding one widens what the agents are given, so the save
 * goes through lib/api.ts and, on the desktop, the shell's confirmation when
 * the backend says it widens (skills.user.write, LOCUS-357).
 */
export function PersonalSkillsPanel() {
  const [loaded, setLoaded] = useState<{ skills: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getUserSkills()
      .then((response) => {
        if (cancelled) return;
        setLoaded({ skills: toListText(response.skills) });
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not load your skills.");
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const { draft, dirty, saving, message, update, commit, reset } = useDraft(loaded);

  return (
    <SettingsGroup title="Personal skills" description="Slash skills loaded into your agents, for you only.">
      {!draft ? (
        <LoadState loading={!error} error={error} onRetry={() => setToken((value) => value + 1)} />
      ) : (
        <>
          <ListField
            id="personal-skills"
            label="Skills"
            value={draft.skills}
            onChange={(value) => update("skills", value)}
            placeholder={"/incident-triage\n/research-brief"}
          />
          <SaveBar
            dirty={dirty}
            saving={saving}
            message={message}
            onReset={reset}
            onSave={() =>
              void commit(async (next) => {
                await saveUserSkills({ skills: parseList(next.skills) });
              })
            }
          />
        </>
      )}
    </SettingsGroup>
  );
}
