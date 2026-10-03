# Self-improvement loop (native Linear runner)

Locus runs its own Linear → code → PR loop natively (LOCUS-338). It is the
"Symphony generalized" configuration from [11 §8](../product/11-agentic-model.md):
a tracker trigger (Linear issues labelled `agent:eligible`), the verified run
loop (LOCUS-337), the gated model client with the NIM → Ollama chain (D-21,
LOCUS-336) and a delivery step with an auto-merge guard (D-22).

Code: `locus_runtime/loop_runner/` (`linear.py`, `runner.py`, `merge_guard.py`,
`delivery.py`, `state.py`). CLI: `lattix loop …`.

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
| done (criteria verified) | comment with the PR link, link attachment, `In Review` | branch `loop/<issue-key>-<slug>`, push, PR with evidence (envelope, verifier results, judge verdicts, usage, fallback events, trajectory path) |
| done, but no change | blocked (`no_changes`) | none |
| blocked | comment with blocker and what unblocks it, `agent:human-review-required`, `Blocked` (or `Todo`) | none |
| stopped (budget/policy) | comment with the reason, `Todo`; 2nd failure adds `agent:human-review-required` | none |
| stopped by the kill switch | comment, `Todo`; not counted as a failure | none |
| runner error | comment with the error type, `Todo`; 2nd failure adds the label | none |

Every terminal comment carries a `locus-loop:release` marker.

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
