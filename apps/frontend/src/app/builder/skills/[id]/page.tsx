"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  getSkills,
  promoteSkill,
  runSkillEval,
  saveSkill,
  testSkill,
  type SkillDefinition,
  type SkillEvalRunResult,
  type SkillTestResult,
} from "@/lib/api";

type Draft = {
  name: string;
  description: string;
  content: string;
  tags: string;
  auto_inject: boolean;
  status: "enabled" | "disabled";
  tier: "tier1" | "tier2" | "tier3";
  maturity: "draft" | "incubating" | "validated" | "standard";
  owner: string;
  dependencies: string;
  evalRubric: string;
  evalDataset: string;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  description: "",
  content: "## Goal\n\n## Steps\n1. \n\n## Output\n",
  tags: "",
  auto_inject: true,
  status: "enabled",
  tier: "tier3",
  maturity: "draft",
  owner: "",
  dependencies: "",
  evalRubric: "",
  evalDataset: "",
};

export default function SkillBuilderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [existing, setExisting] = useState<SkillDefinition | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testPrompt, setTestPrompt] = useState("");
  const [testModel, setTestModel] = useState("");
  const [testResult, setTestResult] = useState<SkillTestResult | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalResult, setEvalResult] = useState<SkillEvalRunResult | null>(null);

  const load = useCallback(async () => {
    try {
      const skills = await getSkills();
      const match = skills.find((skill) => skill.id === id) ?? null;
      setExisting(match);
      if (match) {
        setDraft({
          name: match.name,
          description: match.description,
          content: match.content,
          tags: match.tags.join(", "),
          auto_inject: match.auto_inject,
          status: match.status,
          tier: match.tier,
          maturity: match.maturity,
          owner: match.owner,
          dependencies: match.dependencies.join(", "),
          evalRubric: match.eval_rubric,
          evalDataset: match.eval_dataset.map((c) => c.prompt).join("\n"),
        });
        setSaved(true);
      }
    } catch {
      setNotice("Unable to load the skill registry.");
    } finally {
      setLoaded(true);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSave() {
    if (!draft.name.trim()) {
      setNotice("Skill name is required.");
      return;
    }
    setBusy("save");
    setNotice(null);
    try {
      await saveSkill({
        id,
        name: draft.name.trim(),
        description: draft.description.trim(),
        content: draft.content,
        status: draft.status,
        auto_inject: draft.auto_inject,
        tags: draft.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        tier: draft.tier,
        maturity: draft.maturity,
        owner: draft.owner.trim(),
        dependencies: draft.dependencies
          .split(",")
          .map((d) => d.trim())
          .filter(Boolean),
        eval_rubric: draft.evalRubric,
        eval_dataset: draft.evalDataset
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .map((prompt) => ({ prompt, expectation: "" })),
      });
      setSaved(true);
      setNotice("Skill saved.");
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Unable to save the skill.");
    } finally {
      setBusy(null);
    }
  }

  async function handleTest() {
    if (!testPrompt.trim()) {
      setNotice("Enter a sample task to test the skill against.");
      return;
    }
    setBusy("test");
    setNotice(null);
    setTestResult(null);
    try {
      const result = await testSkill(id, {
        prompt: testPrompt.trim(),
        model: testModel.trim() || undefined,
      });
      setTestResult(result);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Skill test failed.");
    } finally {
      setBusy(null);
    }
  }

  async function handleRunEval() {
    setEvalBusy(true);
    setNotice(null);
    setEvalResult(null);
    try {
      const result = await runSkillEval(id, { model: testModel.trim() || undefined });
      setEvalResult(result);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Eval failed. Save an eval dataset first.");
    } finally {
      setEvalBusy(false);
    }
  }

  async function handlePromote() {
    setNotice(null);
    try {
      await promoteSkill(id);
      setNotice("Skill promoted.");
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Promotion requires a passing eval.");
    }
  }

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] uppercase tracking-wide fx-muted">
            <Link href="/builder/skills" className="underline decoration-dotted underline-offset-2">
              Skills
            </Link>{" "}
            / {existing ? existing.name : "new"}
          </p>
          <h1 className="text-xl font-semibold">
            {existing ? `Edit skill: ${existing.name}` : loaded ? "New skill" : "Loading skill..."}
          </h1>
          <p className="fx-muted text-sm">
            SKILL.md-style operating procedure. Save it, then dry-run it against a sample task before
            enabling it platform-wide.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {existing ? (
            <>
              <span className="rounded-full border border-[var(--ui-border)] px-2 py-1">v{existing.version}</span>
              <span className="rounded-full border border-[var(--ui-border)] px-2 py-1">{existing.source}</span>
              <span className="rounded-full border border-[var(--ui-border)] px-2 py-1">
                used {existing.usage_count}×
              </span>
            </>
          ) : null}
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void handleSave()}
            className="fx-btn-primary px-3 py-2 text-sm font-medium disabled:opacity-60"
          >
            {busy === "save" ? "Saving..." : "Save skill"}
          </button>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <article className="fx-panel space-y-2.5 p-3 text-xs">
          <h2 className="text-sm font-semibold">Definition</h2>
          <div className="grid gap-2.5 md:grid-cols-2">
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Name</span>
              <input
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="release-notes"
                className="fx-field h-8 w-full px-2"
              />
            </label>
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Tags (comma separated)</span>
              <input
                value={draft.tags}
                onChange={(event) => setDraft({ ...draft, tags: event.target.value })}
                placeholder="delivery, git"
                className="fx-field h-8 w-full px-2"
              />
            </label>
          </div>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Description</span>
            <input
              value={draft.description}
              onChange={(event) => setDraft({ ...draft, description: event.target.value })}
              placeholder="What this procedure achieves"
              className="fx-field h-8 w-full px-2"
            />
          </label>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Procedure (markdown)</span>
            <textarea
              value={draft.content}
              onChange={(event) => setDraft({ ...draft, content: event.target.value })}
              className="fx-field min-h-72 w-full p-2 font-mono"
            />
          </label>
          <div className="flex flex-wrap gap-3">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={draft.status === "enabled"}
                onChange={(event) =>
                  setDraft({ ...draft, status: event.target.checked ? "enabled" : "disabled" })
                }
              />
              Enabled
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={draft.auto_inject}
                onChange={(event) => setDraft({ ...draft, auto_inject: event.target.checked })}
              />
              Auto-inject into agent prompts
            </label>
          </div>
        </article>

        <article className="fx-panel space-y-2.5 p-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Maturity &amp; evaluation</h2>
            {existing?.last_eval ? (
              <span
                className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${
                  existing.last_eval.passed
                    ? "border-[hsl(var(--state-success)/0.45)] text-[hsl(var(--state-success))]"
                    : "border-[hsl(var(--state-warning)/0.45)] text-[hsl(var(--state-warning))]"
                }`}
              >
                eval {Math.round(existing.last_eval.score * 100)}% · {existing.last_eval.summary}
              </span>
            ) : null}
          </div>
          <p className="fx-muted leading-5">
            Tiers and a rubric-graded eval govern skill maturity (Savant model): score the skill
            against its dataset, then promote it through tiers once it passes.
          </p>
          <div className="grid gap-2.5 md:grid-cols-2">
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Tier</span>
              <select
                className="fx-field h-8 w-full px-2 text-sm"
                value={draft.tier}
                onChange={(e) => setDraft({ ...draft, tier: e.target.value as Draft["tier"] })}
              >
                <option value="tier3">Tier 3 · Personal/Workflow</option>
                <option value="tier2">Tier 2 · Methodology</option>
                <option value="tier1">Tier 1 · Standard</option>
              </select>
            </label>
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Maturity</span>
              <select
                className="fx-field h-8 w-full px-2 text-sm"
                value={draft.maturity}
                onChange={(e) => setDraft({ ...draft, maturity: e.target.value as Draft["maturity"] })}
              >
                <option value="draft">Draft</option>
                <option value="incubating">Incubating</option>
                <option value="validated">Validated</option>
                <option value="standard">Standard</option>
              </select>
            </label>
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Owner</span>
              <input
                className="fx-field h-8 w-full px-2"
                value={draft.owner}
                onChange={(e) => setDraft({ ...draft, owner: e.target.value })}
                placeholder="team or person"
              />
            </label>
            <label className="block">
              <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Dependencies</span>
              <input
                className="fx-field h-8 w-full px-2"
                value={draft.dependencies}
                onChange={(e) => setDraft({ ...draft, dependencies: e.target.value })}
                placeholder="comma-separated skill names"
              />
            </label>
          </div>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Eval rubric</span>
            <textarea
              value={draft.evalRubric}
              onChange={(e) => setDraft({ ...draft, evalRubric: e.target.value })}
              placeholder="How should a graded response be judged?"
              className="fx-field min-h-16 w-full p-2"
            />
          </label>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Eval dataset (one task per line)</span>
            <textarea
              value={draft.evalDataset}
              onChange={(e) => setDraft({ ...draft, evalDataset: e.target.value })}
              placeholder={"Commit the staged changes.\nDraft release notes for v1.2."}
              className="fx-field min-h-16 w-full p-2"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={evalBusy || !saved}
              onClick={() => void handleRunEval()}
              className="fx-btn-primary px-3 py-1.5 font-medium disabled:opacity-60"
              title={saved ? "" : "Save the skill first"}
            >
              {evalBusy ? "Running eval…" : "Run eval"}
            </button>
            <button
              type="button"
              disabled={!existing?.last_eval?.passed}
              onClick={() => void handlePromote()}
              className="fx-btn-secondary px-3 py-1.5 font-medium disabled:opacity-60"
              title={existing?.last_eval?.passed ? "" : "Promotion requires a passing eval"}
            >
              Promote tier
            </button>
          </div>
          {evalResult ? (
            <div className="space-y-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
              <p className={evalResult.passed ? "text-[hsl(var(--state-success))]" : "text-[hsl(var(--state-warning))]"}>
                Score {Math.round(evalResult.score * 100)}% — {evalResult.passed ? "passed" : "below threshold"}
                {evalResult.mode !== "live" ? " (simulated — configure a provider)" : ""}
              </p>
              {evalResult.cases.map((c, i) => (
                <div key={i} className="border-t border-[var(--fx-border)] pt-1">
                  <span className="fx-muted">{Math.round(c.score * 100)}% · {c.prompt}</span>
                  {c.reason ? <p className="text-[var(--foreground)]">{c.reason}</p> : null}
                </div>
              ))}
            </div>
          ) : null}
        </article>

        <article className="fx-panel space-y-2.5 p-3 text-xs">
          <h2 className="text-sm font-semibold">Test bench</h2>
          <p className="fx-muted leading-5">
            Runs a sample task with <em>only this skill</em> injected, using the configured inference
            providers. Save the skill before testing.
          </p>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">Sample task</span>
            <textarea
              value={testPrompt}
              onChange={(event) => setTestPrompt(event.target.value)}
              placeholder="Commit the current changes with an appropriate message."
              className="fx-field min-h-20 w-full p-2"
            />
          </label>
          <label className="block">
            <span className="fx-muted mb-1 block text-[11px] uppercase tracking-wide">
              Model (optional — e.g. ollama/llama3.2:3b, nim/&lt;model&gt;, anthropic/&lt;model&gt;)
            </span>
            <input
              value={testModel}
              onChange={(event) => setTestModel(event.target.value)}
              placeholder="platform default"
              className="fx-field h-8 w-full px-2 font-mono"
            />
          </label>
          <button
            type="button"
            disabled={busy !== null || !saved}
            onClick={() => void handleTest()}
            className="fx-btn-secondary px-3 py-2 text-sm font-medium disabled:opacity-60"
            title={saved ? "" : "Save the skill before testing"}
          >
            {busy === "test" ? "Running..." : "Run test"}
          </button>

          {testResult ? (
            <div className="space-y-1.5 rounded border border-[var(--fx-border)] bg-[var(--fx-surface-elevated)] p-2">
              <div className="flex flex-wrap gap-2 text-[10px] uppercase tracking-wide fx-muted">
                <span>provider: {testResult.provider}</span>
                <span>model: {testResult.model}</span>
                <span>mode: {testResult.mode}</span>
              </div>
              {testResult.mode !== "live" && testResult.reason ? (
                <p className="text-[hsl(var(--state-warning))]">{testResult.reason}</p>
              ) : null}
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-xs text-[var(--foreground)]">{testResult.output}</pre>
            </div>
          ) : null}
        </article>
      </div>

      {notice ? <p className="fx-muted text-xs">{notice}</p> : null}
    </section>
  );
}
