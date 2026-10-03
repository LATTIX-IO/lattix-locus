# Self-improvement loop (native Linear runner)

Locus runs its own Linear → code → PR loop natively (LOCUS-338). It is the
"Symphony generalized" configuration from [11 §8](../product/11-agentic-model.md):
a tracker trigger (Linear issues labelled `agent:eligible`), the verified run
loop (LOCUS-337), the gated model client with the NIM → Ollama chain (D-21,
LOCUS-336) and a delivery step with an auto-merge guard (D-22).

Code: `locus_runtime/loop_runner/` (`linear.py`, `runner.py`, `merge_guard.py`,
`delivery.py`, `state.py`; LOCUS-339 adds `quality_gates.py`, `perf_budget.py`,
`eval_gate.py`, `feedback.py`, `report.py`). CLI: `lattix loop …`.

## Setup

1. **Secrets** (stored in the OS keychain, Windows DPAPI as a fallback; never
   echoed, never logged):

   ```powershell
   lattix secrets set LINEAR_API_KEY   # Linear personal API key (read + write issues/comments)
   lattix secrets set NVIDIA_API_KEY   # NVIDIA API catalog key for hosted NIM (D-21)
   ```

   The NVIDIA key can also be set from Settings → Models (the settings API stores
   it in the same keychain entry). An environment variable of the same name
   takes precedence.
2. **GitHub**: the `gh` CLI must be logged in (`gh auth login`) with permission to
   push branches and open PRs on the repository's `origin`. The loop never reads
   that credential; `git push` and `gh` use their own.
3. **Policy engine**: OPA must be available (`LOCUS_OPA_BIN` or `.tools/opa/`).
   Without a running engine the loop refuses to run.
4. **Local fallback model** (optional): a running Ollama with `OLLAMA_MODEL`
   pulled. Override the chain with `LOCUS_AGENT_MODEL_CHAIN`.
5. **Linear labels and states** in the project from `WORKFLOW.md`
   (`tracker.provider.project_slug`): labels `agent:eligible`,
   `agent:ineligible`, `agent:human-review-required`; states `Todo`,
   `In Progress`, `In Review` (and optionally `Blocked`).

## Running

```powershell
lattix loop run --once      # or: make loop-once
lattix loop serve           # poll every WORKFLOW.md polling.interval_ms (30 s)
lattix loop status          # or: make loop-status
lattix loop report          # throughput, success rate, cost, gate failures, eval/perf trends
lattix loop report --json --days 7
lattix loop disable         # file kill switch; `lattix loop enable` removes it
```

Useful settings (environment):

| Variable | Default | Meaning |
|---|---|---|
| `LOCUS_LOOP_HOME` | `~/.locus/loop` | ledger, lock, kill-switch file, run dirs, working copies |
| `LOCUS_LOOP_DISABLED` | unset | `1` disables the loop (checked before every step) |
| `LOCUS_LOOP_MAX_RUNS_PER_DAY` | `5` | runs started per UTC day |
| `LOCUS_LOOP_MAX_FAILURES` | `2` | failed runs before `agent:human-review-required` |
| `LOCUS_LOOP_LOCK_TTL_SECONDS` | `7200` | single-run lock and claim lifetime |
| `LOCUS_LOOP_TEST_COMMAND` / `_LINT_COMMAND` / `_TYPECHECK_COMMAND` | detected | replace the command of a detected done check (never removes one) |
| `LOCUS_LOOP_AUTO_MERGE` | off | `1` lets the D-22 guard merge loop PRs |
| `LOCUS_LOOP_REQUIRED_CHECKS` | none | comma-separated CI check names that must pass |
| `LOCUS_RUN_MAX_{STEPS,SECONDS,TOKENS,COST_USD,ACTIONS}` | see `RunBudget` | per-run envelope budget |
| `LOCUS_LOOP_QUALITY_GATES` | `1` | run the pre-PR verifier suite (below) |
| `LOCUS_LOOP_GATE_PYTHON` | `python` | interpreter for the Python gates (runs inside the jail) |
| `LOCUS_LOOP_TYPECHECK_ROOTS` / `_TYPECHECK_ARGS` | `locus_runtime,locus_tooling` / none | packages mypy checks when the diff touches them; extra mypy args (e.g. `--platform linux`) |
| `LOCUS_LOOP_PERF_GATE` | `1` | run the performance budget suite on changes to a measured path |
| `LOCUS_LOOP_PERF_ITERATIONS` | `30` | iterations per metric (the median is compared) |
| `LOCUS_LOOP_PERF_TOLERANCE` / `_PERF_MIN_DELTA_MS` | `0.5` / `2` | a regression is more than +50 % **and** more than +2 ms over the stored baseline |
| `LOCUS_LOOP_PERF_BUDGET_{HEALTH_MS,GATEWAY_DECISION_MS,POLICY_DECISION_MS}` | `100` / `50` / `250` | absolute median ceilings (ms) |
| `LOCUS_LOOP_EVAL_GATE` | `advisory` | `off`, `advisory` (run, record, report) or `required` (the D-22 auto-merge also needs an eval pass) |
| `LOCUS_LOOP_EVAL_THRESHOLD` / `_EVAL_MAX_STEPS` | `0.30` / `20` | eval pass threshold; agent steps per eval task |
| `LOCUS_LOOP_PROPOSE_SKILLS` | `1` | propose a quarantined `SKILL.md` after a done run |
| `LOCUS_LOOP_FILE_FAILURE_ISSUES` | `1` | file Linear issues for recurring failure patterns |
| `LOCUS_LOOP_FAILURE_ISSUE_MIN_OCCURRENCES` / `_MAX_FAILURE_ISSUES_PER_DAY` | `2` / `3` | how often a pattern must recur; filings per UTC day |

This repository's whole-repo `mypy .` and `pytest` are not green today (see
`AGENTS.md`, known gate gaps), so set scoped check commands before the first
real run, for example
`LOCUS_LOOP_TYPECHECK_COMMAND="python -m mypy locus_runtime/loop_runner"` and a
targeted `LOCUS_LOOP_TEST_COMMAND`.

## What a tick does

1. Kill switch, then the single-run lock, then **posture**: a real `Gateway` with
   a running policy engine must be installed, or the tick is `refused`.
2. With auto-merge on, every open loop PR goes through the D-22 guard.
3. A run left behind by a crash resumes from its checkpoint (same run id, same
   working copy), unless the issue was moved, relabelled or re-claimed meanwhile.
4. Otherwise the highest-priority eligible issue is claimed: a comment with a
   `locus-loop:claim` marker and the run id, then `In Progress`. The earliest
   live claim wins; a duplicate claim is released.
5. A working copy of `origin/main` is cloned under `LOCUS_LOOP_HOME/worktrees/`.
   The envelope comes from the issue text plus the repository's checks
   (plan mode required); a gateway session with the envelope's capabilities is
   opened; the model client is `GatedChatClient` over the NIM → Ollama chain.
6. End states:

| Run end | Linear | Git/GitHub |
|---|---|---|
| done (criteria verified, pre-PR gates green) | comment with the PR link, link attachment, `In Review` | branch `loop/<issue-key>-<slug>`, push, PR with evidence (envelope, verifier results, judge verdicts, quality gates, eval gate, usage, fallback events, trajectory path) |
| done, but a pre-PR gate failed | stopped (`quality_gate`), counted as a failure | none |
| done, but a pre-PR gate could not run | blocked (`quality_gate`) | none |
| done, but no change | blocked (`no_changes`) | none |
| blocked | comment with blocker and what unblocks it, `agent:human-review-required`, `Blocked` (or `Todo`) | none |
| stopped (budget/policy) | comment with the reason, `Todo`; 2nd failure adds `agent:human-review-required` | none |
| stopped by the kill switch | comment, `Todo`; not counted as a failure | none |
| runner error | comment with the error type, `Todo`; 2nd failure adds the label | none |

Every terminal comment carries a `locus-loop:release` marker. Every finished run
appends one line to `LOCUS_LOOP_HOME/runs.jsonl` (outcome, kind, reason, usage,
gate failures, eval status).

## Quality gates, eval gate and feedback (LOCUS-339)

### Pre-PR verifier suite

After a run ends `done` with a change, the runner stages the change, lists the
changed paths (`git diff --cached HEAD`, after `git add --renormalize` so a
line-ending difference between the jail's git and the host's git is not a change)
and selects the suite with `quality_gates.select_gate_checks`, a pure function of
the changed paths and the repository's file list:

| Check | Runs when | argv |
|---|---|---|
| `tests` | a changed Python file maps to tests (`test_<module>.py`, or a changed test file) | `python -m pytest -q <mapped tests>` |
| `lint` | Python files changed | `python -m ruff check <changed files>` |
| `typecheck` | the change touches a `LOCUS_LOOP_TYPECHECK_ROOTS` package | `python -m mypy <roots> <args>` |
| `frontend-lint`, `frontend-test` | `apps/frontend/` changed | `npm --prefix apps/frontend run lint` / `run test` |
| `policy`, `policy-tests` | `policies/` changed | `opa test policies`, `python -m pytest -q tests/policy` |
| `perf` | `apps/backend/`, `locus_runtime/` or `policies/` changed | `python -m locus_runtime.loop_runner.perf_budget` |

Commands are argv lists (never a shell). `LOCUS_LOOP_{TEST,LINT,TYPECHECK}_COMMAND`
replace the argv of their check and keep that check in the suite even when the
diff would not select it (an override never removes a check). Unselected checks
are listed in the PR body as skipped, with the reason.

The suite runs the agent-authored code, so it runs through the run's executor
(the jail) under its own gateway session, opened after the agent's last action;
that session may also run the gate executables (`npm`, `opa`). Any failing check
stops the run before a branch is pushed (`stopped`, kind `quality_gate`, counted
as a failure); a check the gateway or sandbox would not run blocks the run
instead. Results are in `runs/<run_id>/quality-gate.json` and the PR body.

### Performance budgets

`perf_budget.py` measures, in-process inside the workspace, the median of N
iterations (after warm-up) for `GET /health` (FastAPI TestClient), one gateway
authorization (an in-process allow engine, i.e. the gateway's own overhead) and
one `agent_policy` decision on the configured policy engine (not measured when no
engine starts there). The runner compares the medians with
`LOCUS_LOOP_HOME/perf-baseline.json`: the first measurement of a metric records
its baseline; later a median fails when it is more than `LOCUS_LOOP_PERF_TOLERANCE`
**and** more than `LOCUS_LOOP_PERF_MIN_DELTA_MS` above the baseline, or above its
absolute budget. A failing measurement never moves the baseline; every
measurement is appended to `perf-history.jsonl`. The baseline is per machine; to
re-baseline (new machine, accepted slowdown) delete `perf-baseline.json`.

### Eval gate

With `LOCUS_LOOP_EVAL_GATE=advisory` (the default for `lattix loop`) or
`required`, the runner runs the `synthetic-mini` DeepSWE tasks from `apps/evals`
with the configured model chain (NIM → Ollama) driving the SWE agent, after a
one-call reachability preflight. The resolve rate goes to `eval-history.jsonl`
and the PR body. No reachable model, or no `apps/evals`, is reported as
`skipped: <reason>`; an eval that crashes is `error`. Neither is ever a pass.
Only `required` makes the D-22 auto-merge hold unless the PR's eval passed.

The eval measures the model chain and the runner's installed harness, not the
PR's code (running the PR's harness would execute agent-authored code on the
host with model egress). The PR's code is covered by the verifier suite.

### Feedback

* **Skill proposals (P24).** After a done run the trajectory (plan steps, tool
  names and counts, changed paths, passed gates) is summarised deterministically
  into one `SKILL.md` with provenance (issue key, run id, PR URL) in
  `metadata.locus-proposal`, and installed in the skill store **quarantined**. It
  carries no scripts and no capability manifest; a blocking static-scan finding
  moves it to `blocked`. The loop never scans it into trust, evaluates or trusts
  it: a human promotes it through the normal skill lifecycle.
* **Failure patterns.** After a blocked, stopped (not by the kill switch) or
  errored run, `runs.jsonl` is clustered by a fingerprint of (outcome/kind,
  normalised reason: ids, hashes, paths, numbers and quoted text removed). A new
  pattern seen `LOCUS_LOOP_FAILURE_ISSUE_MIN_OCCURRENCES` times gets one Linear
  issue in the loop's project, deduplicated by the line
  `locus-loop-failure-fingerprint: <fp>` in its body (the local
  `failure-patterns.json` first, then a Linear search), at most
  `LOCUS_LOOP_MAX_FAILURE_ISSUES_PER_DAY` per UTC day. Filed issues get no
  labels, so the loop never picks up its own filings.
* **Untrusted text (P8).** Trajectory and failure text is sanitized before it
  reaches a skill or an issue: secrets redacted, code blocks and HTML comments
  dropped, `locus-loop` markers and `<!--` neutralised (no forged claim, release
  or fingerprint markers), length capped.

### Report

`lattix loop report [--json] [--days N]` reads `runs.jsonl`, `eval-history.jsonl`,
`perf-history.jsonl`, `perf-baseline.json` and `state.json`: runs and PRs per
day, outcomes, success rate (done over attempted runs; kill-switch stops are not
attempts), cost (sum of usage `cost_usd`; about 0 on the NIM free tier and
Ollama), gate failures by check, the eval resolve-rate trend and the perf trend
per metric.

## Safety model

* **Fail closed.** No gateway with a running engine → no run. No Linear key →
  refused. The kill switch is checked before each tick and each model step.
* **Bounded.** One run at a time on a machine (lock with TTL), a daily run cap,
  per-run envelope budgets, retries with backoff for Linear and push/PR calls,
  and a two-failure limit per issue.
* **Untrusted issue text (P8).** The issue sets the goal and done criteria only.
  Capabilities (workspace root, tools, executables, egress hosts = the model
  tiers) come from the runner.
* **One gateway (P6).** Agent tool calls and model calls go through the run's
  gateway session. Git, `gh` and Linear write-back are the runner's own delivery
  steps (argv only, no shell) and are not reachable by the agent.
* **D-22 auto-merge guard** (`merge_guard.evaluate_auto_merge`, pure). It merges
  only when every check is green (required checks must be `success`; a skipped
  required check holds) **and** no protected path changed. Protected paths are
  the `.github/CODEOWNERS` patterns plus a built-in baseline that also covers the
  loop's own code. It also holds on: changes to gate definitions
  (`.github/workflows/**`, `policies/**`, any `conftest.py`, pytest/ruff/mypy
  config files, Makefile gate targets or variables, `[tool.ruff|mypy|pytest|coverage]`
  in `pyproject.toml`), deleted or moved-out test files, net deletion of test
  functions or assertions, added skip/xfail markers, unsafe paths (traversal,
  absolute, `:` or control characters), and a missing CODEOWNERS. Path matching
  is case-insensitive and covers both sides of a rename. A merge is pinned to
  the evaluated head commit (`--match-head-commit`). A held PR stays open for
  principal review and the issue gets a comment with the reasons.

## Known limits

* Linear write-back is not yet routed through the gateway as an R2 action under
  a standing grant (16 §7). It is runner code, not agent-reachable.
* The GraphQL documents follow Linear's public schema but were exercised only
  against a mock transport in CI. Run `lattix loop run --once` against a test
  issue first.
* On Windows the agent runs in the AppContainer jail. The verify gate needs
  `git` inside that jail to compute the diff.
* A failing pre-PR gate ends the run; the agent does not get another attempt
  with the gate output in the same run (it does on the next pick-up).
* The perf suite and its comparison code live under the protected
  `locus_runtime/loop_runner/`, so a PR that edits the measurement is never
  auto-merged. The suite still executes the PR's code, so its numbers are
  evidence, not proof.
* `issueCreate` is retried on transient Linear errors like every other call; a
  retry after a lost response can create a duplicate issue (the fingerprint
  line makes it easy to spot and the registry stops further filings).
