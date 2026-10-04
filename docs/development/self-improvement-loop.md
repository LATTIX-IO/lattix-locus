# Self-improvement loop (native Linear runner)

Locus runs its own Linear → code → PR loop natively (LOCUS-338). It is the
"Symphony generalized" configuration from [11 §8](../product/11-agentic-model.md):
a tracker trigger (Linear issues labelled `agent:eligible`), the verified run
loop (LOCUS-337), the gated model client with the NIM → Ollama chain (D-21,
LOCUS-336) and a delivery step with an auto-merge guard (D-22).

Code: `locus_runtime/loop_runner/` (`linear.py`, `runner.py`, `merge_guard.py`,
`delivery.py`, `state.py`; LOCUS-339 adds `quality_gates.py`, `perf_budget.py`,
`eval_gate.py`, `feedback.py`, `report.py`; LOCUS-351 adds `scorecard_gate.py`).
CLI: `lattix loop …`. What "better" means for the loop (the RSI scorecard, its
suite, held-out split and promotion rule) is in [rsi-scorecard.md](rsi-scorecard.md).

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
lattix loop report          # throughput, success rate, cost, gate failures, eval/perf/scorecard trends
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
| `LOCUS_LOOP_SCORECARD` | `advisory` where the candidate can be jailed, else `off` | RSI scorecard (LOCUS-351): `off`, `advisory` (run, archive, report) or `required` (the D-22 auto-merge also needs `promote`). The default follows the host (LOCUS-379): `advisory` when the candidate instance runs in an OS jail here (AppContainer, seatbelt, bubblewrap), `off` otherwise; `lattix loop status` shows which and why |
| `LOCUS_RSI_CANDIDATE_UNJAILED` | unset | `1` runs the candidate without an OS jail (loud warning; the scorecard records `isolation: none` and never promotes) |
| `LOCUS_LOOP_SCORECARD_{TRIALS,SPLITS,MODEL,PYTHON}` | `1` / `dev,heldout` / `gpt-oss:20b-ctx32k` / runner's | trials per task, splits, the keyless (Ollama) model, the candidate interpreter |
| `LOCUS_LOOP_TAG_VARIANTS` | off | `1` tags each evaluated commit `variant/<sha12>` in the runner's repository (local, never pushed) |
| `LOCUS_EVAL_HELDOUT_DIR` | unset | a private held-out task directory replacing the committed one |

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
| done (criteria verified, pre-PR gates green) | comment with the PR link, link attachment, `In Review` | branch `loop/<issue-key>-<slug>`, RSI scorecard on the run's tree, commit, variant archived under the commit sha, push, PR with evidence (envelope, verifier results, judge verdicts, quality gates, eval gate, RSI scorecard + comparison, usage, fallback events, trajectory path) |
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
host with model egress). The PR's code is covered by the verifier suite and,
since LOCUS-351, by the RSI scorecard below.

### RSI scorecard (LOCUS-351)

With `LOCUS_LOOP_SCORECARD=advisory` (the default where the candidate can be
jailed, LOCUS-379) or `required`, the runner evaluates the run's tree after the verifier suite passed
(then commits it and archives the variant under the commit sha) with the RSI
suite: 12 dev and 8 held-out tasks from
`apps/evals/locus_evals/suite/`, loaded from the runner's own checkout (never
from the run's working copy), sealed read-only and hash-verified before and
after every sample. The candidate commit runs in a separate, secret-free
candidate instance (own temp app home, scrubbed environment, no keychain, its
own telemetry DB, the evaluator's metering proxy as its only model endpoint, the
trusted policy bundle on real OPA) inside an OS jail with no network: it can read
only its interpreter, a read-only copy of the candidate's code and the trusted
entry script, and write only its temp home and the sample's workspace. Model
calls, policy decisions, the agent's commands and host git reach the trusted
parent over a stdio bridge. Before scoring, an isolation probe proves the jail
from inside; the scorecard records the tier (`isolation`). The scorecard (pass rates with Wilson CIs per
split, tokens / cost / time, gate regressions, injection attack success rate,
mediation coverage) is compared with the base branch's latest scorecard in
`LOCUS_LOOP_HOME/variants/`: `promote` only when nothing regresses beyond noise
and at least one held-out dimension improves; otherwise `hold` with reasons. The
variant is archived, the PR body gets the scorecard and the decision, and with
`required` anything but `promote` holds the D-22 auto-merge. Without a reachable
keyless model endpoint or OPA the scorecard is `skipped`, never a promote.

Where no jail exists (no AppContainer APIs, no `sandbox-exec`, no `bwrap`) the
default stays `off` and `lattix loop status` / `report` say why. An explicit
`LOCUS_LOOP_SCORECARD` still applies, but the candidate then refuses to run (the
scorecard is `skipped`) unless `LOCUS_RSI_CANDIDATE_UNJAILED=1` is set, which runs
it as your OS user and records `isolation: none` (never promoted). The guarantees
and residual risks are in [rsi-scorecard.md §4](rsi-scorecard.md#4-the-candidate-instance);
read them before switching to `required` for Dev publishing.

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
`perf-history.jsonl`, `perf-baseline.json`, `scorecard-history.jsonl` and
`state.json`: runs and PRs per day, outcomes, success rate (done over attempted
runs; kill-switch stops are not attempts), cost (sum of usage `cost_usd`; about 0
on the NIM free tier and Ollama), gate failures by check, the eval resolve-rate
trend, the perf trend per metric and the RSI scorecard trend (held-out pass rate,
decisions and isolation tiers), plus the scorecard mode in effect and why.

## Delivery to the desktop: update channels (D-26, LOCUS-349)

A merged loop PR reaches the principal's desktop app through the Dev update
channel; details and the signing-key setup are in
[INSTALLER.md, "Desktop app: update channels"](../INSTALLER.md#desktop-app-update-channels-d-26-locus-349).

### The full cycle

1. **Merge.** A loop PR merges to `main` (D-22: green required checks and no
   protected path; the update trust chain is protected, see below).
2. **Dev release.** `desktop-dev.yml` builds Windows x64 (NSIS) and macOS arm64
   as `<VERSION>.<PATCH>` (PATCH = the next build number of that MAJOR.MINOR,
   [VERSIONING.md](../VERSIONING.md), D-31), stamps that version into the
   backend sidecar, signs the updater bundles with `TAURI_SIGNING_PRIVATE_KEY`,
   verifies each signature against the app's committed public key, publishes the
   prerelease `dev-v<version>` (never over an existing one) and moves
   `channel-dev/latest.json` forward. Runs are serialized; without the key it
   publishes installers only and no metadata. A loop PR declares its
   `Release-Impact`; it may bump MINOR, never MAJOR (the D-22 guard holds that
   for the principal).
3. **Auto-update.** Apps on the Dev channel check on start and every 4 hours,
   download the update, then poll `POST /system/update/prepare` until no agent
   run is in progress and the update holds the loop's single-run lock
   (`loop.lock`, owner `desktop-update-…`). Holding the lock means a running loop
   run finishes first and no new one starts; `lattix loop serve` ticks return
   `busy` meanwhile. The app then stops the backend through its normal teardown,
   installs (signature verified by the updater) and restarts.
4. **Version check.** After the restart the app compares the backend's stamped
   `build_version` with its own version and refuses to load a stale backend
   (tauri#15134).
5. **Loop resume.** On every start the desktop supervisor releases an update
   hold on the loop and, when loop autostart is on and the kill switch is off,
   starts `lattix loop serve` again, now on the new code (`--loop-serve` mode of
   the bundled backend, a supervised child that quit and updates stop). A run
   interrupted by a crash resumes from its checkpoint as usual.
6. **Stable promotion.** After testing a Dev build, run `desktop-promote` with its
   version. The same files become `stable-v<version>` (GitHub "latest") and
   `channel-stable/latest.json` moves to it; Stable apps show an "Update
   available" banner and install on click.

Loop autostart (persisted in `LOCUS_LOOP_HOME/desktop-autostart.json`):

```powershell
lattix loop autostart --repo E:\lattix\lattix-locus   # start the loop with the app, also after updates
lattix loop autostart --off
lattix loop disable                                   # the kill switch still wins: the app will not start it
```

The update never sets or clears the kill switch (`DISABLED` / `LOCUS_LOOP_DISABLED`).

### Trust and gates

* The updater signing key in the repository secrets is the root of trust for
  every Dev and Stable install. A holder of the key and the repository's
  release write access can ship code to all installs. Signature verification
  in the app is mandatory and cannot be turned off from the UI or settings; the
  app accepts only the two compiled-in channel URLs.
* Dev installs run code the loop wrote. Today the gates in front of that are
  the CI required checks and D-22 (green gates, no protected path changed). The
  update trust chain is a protected path, both in `.github/CODEOWNERS` and in the
  merge guard's built-in baseline: `apps/desktop-tauri/src-tauri/` (pubkey,
  endpoints, updater code), `scripts/desktop_channel.py`,
  `locus_tooling/{update_contract,desktop_update,build_info}.py` and
  `.github/workflows/`. The RSI scorecard (LOCUS-351) gates the loop's
  auto-merge in `required` mode, which is what reaches the Dev channel; a check
  of the merged commit's scorecard inside `desktop-dev.yml` itself is a
  follow-up ([rsi-scorecard.md §11](rsi-scorecard.md#11-known-limits-follow-ups-and-decisions)).
  The suite, graders, held-out split and scorecard code are protected paths too.

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
* **Host git never trusts the working copy's `.git`.** The working copy's `.git`
  is inside the agent's write root, so a run could plant hooks or config
  (filter/diff drivers, `core.fsmonitor`, `sshCommand`, credential helpers,
  `insteadOf`, remote URL, object alternates) that host git would execute or
  follow. Every host git call runs with `core.hooksPath` set to an empty
  runner-owned directory and `core.fsmonitor=false`, and commit/push pass
  `--no-verify`. After provisioning, the runner seals a digest of `.git/config`,
  `HEAD`, `hooks/`, `info/` and the alternates files in `<worktree>.gitseal`
  (outside the write root). Any later host git call on that copy first checks
  the seal and fails with `workspace_git_tampered` on a mismatch. The push uses
  no `-u`, so the runner never rewrites the sealed config itself.
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
