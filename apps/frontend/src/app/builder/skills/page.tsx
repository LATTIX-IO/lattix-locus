"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  deleteSkill,
  getSkills,
  importSkill,
  saveSkill,
  scanSkill,
  type SkillDefinition,
} from "@/lib/api";

function quarantineBadge(status: SkillDefinition["quarantine_status"]): {
  label: string;
  className: string;
} | null {
  switch (status) {
    case "pending":
      return {
        label: "Quarantined",
        className: "border-[hsl(var(--state-warning)/0.5)] text-[hsl(var(--state-warning))]",
      };
    case "blocked":
      return {
        label: "Blocked",
        className: "border-[hsl(var(--state-critical)/0.5)] text-[hsl(var(--state-critical))]",
      };
    case "cleared":
      return {
        label: "Cleared",
        className: "border-[hsl(var(--state-success)/0.5)] text-[hsl(var(--state-success))]",
      };
    default:
      return null;
  }
}

export default function SkillsInventoryPage() {
  const router = useRouter();
  const [skills, setSkills] = useState<SkillDefinition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [showImport, setShowImport] = useState(false);
  const [importUrl, setImportUrl] = useState("");
  const [importName, setImportName] = useState("");
  const [importContent, setImportContent] = useState("");

  const refresh = useCallback(async () => {
    try {
      setSkills(await getSkills());
      setError(null);
    } catch {
      setError("Unable to load skills. Check that the backend is reachable.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function toggleSkill(skill: SkillDefinition) {
    setBusy(skill.id);
    setNotice(null);
    try {
      await saveSkill({ id: skill.id, status: skill.status === "enabled" ? "disabled" : "enabled" });
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : `Unable to update ${skill.name}.`);
    } finally {
      setBusy(null);
    }
  }

  async function handleImportFile(file: File | null) {
    if (!file) {
      return;
    }
    if (file.size > 500_000) {
      setNotice("File is too large — skills must be under 500 KB.");
      return;
    }
    try {
      const text = await file.text();
      setImportContent(text);
      setImportName((current) => current || file.name.replace(/\.[^.]+$/, ""));
      setImportUrl("");
      setNotice(`Loaded "${file.name}" — review and click Import & scan.`);
    } catch {
      setNotice("Unable to read the selected file.");
    }
  }

  async function runImport() {
    if (!importUrl.trim() && !importContent.trim()) {
      setNotice("Provide a skill URL, upload a file, or paste skill content to import.");
      return;
    }
    setBusy("import");
    setNotice(null);
    try {
      const imported = await importSkill({
        url: importUrl.trim() || undefined,
        content: importContent.trim() || undefined,
        name: importName.trim() || undefined,
      });
      const scan = imported.security_scan;
      setNotice(
        imported.quarantine_status === "cleared"
          ? `Imported "${imported.name}" — blast chamber cleared. Enable it when ready.`
          : `Imported "${imported.name}" — BLOCKED by the blast chamber: ${scan?.summary ?? "high-severity findings"}.`,
      );
      setImportUrl("");
      setImportName("");
      setImportContent("");
      setShowImport(false);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Unable to import skill.");
    } finally {
      setBusy(null);
    }
  }

  async function rescanSkill(skill: SkillDefinition) {
    setBusy(skill.id);
    setNotice(null);
    try {
      const result = await scanSkill(skill.id);
      setNotice(
        result.quarantine_status === "cleared"
          ? `${skill.name} cleared the blast chamber.`
          : `${skill.name} is still blocked: ${result.security_scan.summary}`,
      );
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : `Unable to scan ${skill.name}.`);
    } finally {
      setBusy(null);
    }
  }

  async function removeSkill(skill: SkillDefinition) {
    setBusy(skill.id);
    setNotice(null);
    try {
      await deleteSkill(skill.id);
      setNotice(`Deleted ${skill.name}.`);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : `Unable to delete ${skill.name}.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Skills</h1>
          <p className="fx-muted">
            Operating procedures injected into agent context. Open a skill to edit, version, and test it.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="fx-btn-secondary px-3 py-2 text-sm font-medium"
            onClick={() => setShowImport((v) => !v)}
          >
            Import Skill
          </button>
          <button
            type="button"
            className="fx-btn-primary px-3 py-2 text-sm font-medium"
            onClick={() => router.push(`/builder/skills/${crypto.randomUUID()}`)}
          >
            New Skill
          </button>
        </div>
      </header>

      {showImport ? (
        <article className="fx-panel space-y-2 p-3 text-xs">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Import skill from an online source</h2>
            <span className="fx-muted">
              Imported skills are quarantined and run through a security blast chamber
              (static scan → guarded dry-run) before they can be enabled.
            </span>
          </div>
          <input
            className="fx-field h-8 w-full px-2"
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            placeholder="https://example.com/skill.md (https only, no internal hosts)"
          />
          <input
            className="fx-field h-8 w-full px-2"
            value={importName}
            onChange={(e) => setImportName(e.target.value)}
            placeholder="Skill name (optional — derived from the URL or file when blank)"
          />
          <label className="flex flex-wrap items-center gap-2">
            <span className="fx-muted">…or upload a file:</span>
            <input
              type="file"
              accept=".md,.markdown,.txt,.skill,text/markdown,text/plain"
              className="text-[11px] file:mr-2 file:rounded file:border-0 file:bg-[var(--fx-surface-elevated)] file:px-2 file:py-1 file:text-[var(--foreground)]"
              onChange={(e) => void handleImportFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <textarea
            className="fx-field min-h-24 w-full p-2"
            value={importContent}
            onChange={(e) => setImportContent(e.target.value)}
            placeholder="…or paste the skill content directly instead of a URL."
          />
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy === "import"}
              onClick={() => void runImport()}
              className="fx-btn-primary px-3 py-1.5 font-medium disabled:opacity-60"
            >
              {busy === "import" ? "Scanning…" : "Import & scan"}
            </button>
            <button
              type="button"
              onClick={() => setShowImport(false)}
              className="fx-btn-secondary px-3 py-1.5 font-medium"
            >
              Cancel
            </button>
          </div>
        </article>
      ) : null}

      {error ? <div className="fx-panel border-[hsl(var(--state-critical)/0.4)] p-3 text-sm">{error}</div> : null}

      <div className="fx-panel overflow-hidden">
        <table className="w-full text-sm">
          <thead className="fx-table-head">
            <tr>
              <th className="px-3 py-2 text-left">Skill</th>
              <th className="px-3 py-2 text-left">Tier</th>
              <th className="px-3 py-2 text-left">Maturity</th>
              <th className="px-3 py-2 text-left">Eval</th>
              <th className="px-3 py-2 text-left">Security</th>
              <th className="px-3 py-2 text-left">Status</th>
              <th className="px-3 py-2 text-left">Uses</th>
              <th className="px-3 py-2 text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {skills.map((skill) => (
              <tr key={skill.id} className="border-t border-[var(--fx-border)]">
                <td className="px-3 py-2">
                  <p className="font-mono font-medium text-[var(--foreground)]">{skill.name}</p>
                  <p className="fx-muted text-xs">{skill.description}</p>
                </td>
                <td className="px-3 py-2">
                  <span className="rounded-full border border-[var(--ui-border)] px-2 py-0.5 text-[10px] uppercase">
                    {{ tier1: "T1 · Standard", tier2: "T2 · Method", tier3: "T3 · Personal" }[skill.tier] ?? skill.tier}
                  </span>
                </td>
                <td className="px-3 py-2">
                  <span
                    className={`text-xs ${
                      skill.maturity === "standard" || skill.maturity === "validated"
                        ? "text-[hsl(var(--state-success))]"
                        : "fx-muted"
                    }`}
                  >
                    {skill.maturity}
                  </span>
                </td>
                <td className="px-3 py-2 text-xs">
                  {skill.last_eval ? (
                    <span className={skill.last_eval.passed ? "text-[hsl(var(--state-success))]" : "text-[hsl(var(--state-warning))]"}>
                      {Math.round(skill.last_eval.score * 100)}%
                    </span>
                  ) : (
                    <span className="fx-muted">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-xs">
                  {(() => {
                    const badge = quarantineBadge(skill.quarantine_status);
                    if (!badge) {
                      return <span className="fx-muted">—</span>;
                    }
                    const highCount =
                      skill.security_scan?.findings.filter((f) => f.severity === "high").length ??
                      0;
                    return (
                      <span
                        className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${badge.className}`}
                        title={skill.security_scan?.summary ?? ""}
                      >
                        {badge.label}
                        {highCount > 0 ? ` · ${highCount}` : ""}
                      </span>
                    );
                  })()}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={
                      skill.status === "enabled"
                        ? "text-[hsl(var(--state-success))]"
                        : "fx-muted"
                    }
                  >
                    {skill.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-[var(--foreground)]">{skill.usage_count}</td>
                <td className="px-3 py-2 text-right">
                  <div className="flex justify-end gap-2">
                    <Link className="fx-btn-primary px-2.5 py-1 text-xs font-medium" href={`/builder/skills/${skill.id}`}>
                      Open
                    </Link>
                    {skill.quarantine_status === "pending" || skill.quarantine_status === "blocked" ? (
                      <button
                        type="button"
                        disabled={busy === skill.id}
                        onClick={() => void rescanSkill(skill)}
                        className="fx-btn-secondary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                      >
                        {busy === skill.id ? "Scanning…" : "Scan"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={busy === skill.id}
                        onClick={() => void toggleSkill(skill)}
                        className="fx-btn-secondary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                      >
                        {skill.status === "enabled" ? "Disable" : "Enable"}
                      </button>
                    )}
                    {skill.source === "custom" ? (
                      <button
                        type="button"
                        disabled={busy === skill.id}
                        onClick={() => void removeSkill(skill)}
                        className="fx-btn-secondary px-2.5 py-1 text-xs font-medium disabled:opacity-60"
                      >
                        Delete
                      </button>
                    ) : null}
                  </div>
                </td>
              </tr>
            ))}
            {skills.length === 0 && !error ? (
              <tr>
                <td colSpan={8} className="fx-muted px-3 py-4 text-sm">
                  Loading skills...
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {notice ? <p className="fx-muted text-xs">{notice}</p> : null}
    </section>
  );
}
