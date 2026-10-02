# Production Harness Plan — xFrontier Coding & Multi-Agent Platform

> Date: 2026-06-12 · Companion to `docs/competitive-gap-analysis-2026-06.md`
> Goal: a stable, production-ready harness running locally hosted models (gpt-oss et al.)
> through long-running multi-agent dev workflows, measured against DeepSWE-class SWE benchmarks.
> Branch context: `feat/cognitive-mvp-foundation`. All line numbers verified against the
> working tree on 2026-06-12.

---

## 0. Definition of "production ready"

The plan is done when all of the following hold:

1. **A coding agent run works end-to-end**: provision workspace → sandboxed bash/search/edit
   → test feedback → `submit` with a unified diff — against both a frontier model and a
   local model served by vLLM or llama.cpp.
2. **Every tool call is gated**: capability token verified + policy evaluated + audited,
   fail-closed, on every loop path (kickoff, collaboration, follow-up, graph node).
3. **No run can be lost or loop forever**: checkpointed + resumable after crash/restart;
   hard step/time/context budgets with submit-or-zero semantics.
4. **Every run yields a lossless, replayable trajectory** (JSONL, SFT/RL-compatible).
5. **`lattix exec` headless mode** drives runs from CI with proper exit codes.
6. **The eval harness reports SWE-bench Verified subset results** with ≥5 seeds, mean±SEM,
   graded only by test execution, on a remote runner (never the local dev machine —
   memory: `resource-constrained-local-testing`).
7. **Secure by default**: auth required out of the box, no plaintext webhook tokens at rest
   or in URLs, per-action approval records, constant-time token compares.
8. **The monolith is shrinking, enforced by a ratchet test**; all new code lands in new modules.

---

## 1. Verified baseline corrections (read before implementing)

Findings from code verification that change the work, vs. what the gap analysis assumed:

| # | Finding | Consequence |
|---|---|---|
| V1 | `ToolJailService`/`SandboxManager` only **plan** execution (`frontier_runtime/sandbox.py:518–530` returns `executed=False`); nothing in `frontier_runtime/` or `apps/backend/` calls `subprocess` | The execution layer must be **built**, not reused (M2/S1) |
| V2 | `OPAClient` (`frontier_runtime/security.py:386–711`) never makes HTTP calls — it is an in-process evaluator mirroring the real Rego in `policies/*.rego` | "Local-eval fallback" already exists and is the only path; wire it into the gate + add optional HTTP OPA with Rego parity tests |
| V3 | Capability tokens are **HMAC-SHA256 JSON**, not Biscuit (no attenuation/caveats) | Adequate for per-run scoping; true Biscuit deferred to sub-agent delegation work; fix marketing language |
| V4 | The actual tool gate is a substring blacklist applied at tool-*gathering* time (`_gather_mcp_run_tools`, `main.py:13486–13555`), silently dropping tools | Replace silent drop with gate decisions incl. `requires_approval` |
| V5 | Collaboration turns (`main.py:13963`) and follow-up `_respond` (`main.py:14926`) run **without tools** | Gate wiring must add tools to these paths, per-agent tokens |
| V6 | Each loop iteration **rebuilds a synthetic prompt** with only the last 4 interim notes (`main.py:13781–13786`); the in-call tool conversation is discarded | Lossless trajectories are a build; iterations must continue on one append-only message list |
| V7 | `ConversationManager.compact()` mutates old turns and emits the summary **before** the system message (`frontier_runtime/conversation.py:93–210`) | Confirmed cache-destroying; fork-mode redesign (M6) |
| V8 | Provider plumbing is already general (`_PROVIDER_REGISTRY` `main.py:2746` + `ai_providers` settings) | vLLM/llama.cpp/LM Studio are registry entries, not architecture |
| V9 | Malformed tool-call JSON is silently coerced to `{}` (`main.py:4543–4547`) | Exact hook point for validation + bounded re-ask |
| V10 | `langgraph-checkpoint-postgres` declared (`pyproject.toml:23`), unused; backend store persists to Postgres JSONB via `PostgresStateStore` (`platform_services.py:109`); append-only `PostgresAuditLog` (`:203`) is the pattern to copy for trajectories | Checkpointing integrates with existing persistence idioms |
| V11 | `main.py` = 19,427 lines / 127 routes / 496 functions; tests monkeypatch `app.main` module globals | Decomposition must keep `app.main` a re-exporting facade |
| V12 | Sandbox `ALWAYS_READONLY_SUBPATHS` force-remounts `.git` read-only in writable mounts (`sandbox.py:38–47`) | Git operations are host-side (`WorkspaceManager`); agent never commits; `submit` diffs host-side |
| V13 | A run CLI stub exists: `lattix workflow run` (`frontier_tooling/cli.py:249`), fire-and-forget; console script is `lattix` | `exec` extends it; add `frontier` script alias (naming sign-off needed) |
| V14 | `require_authenticated_requests` defaults `False` (`main.py:374`); webhook trigger tokens plaintext at rest and in URLs (`main.py:15363, 15406`); non-constant-time token compare (`main.py:9499`) | M0/M7 hardening items |

---

## 2. Consolidated architecture decisions

- **D1 — Coding tools are native frontier tools, not MCP tools.** A `CodingToolset` produces
  OpenAI-function schemas + a dispatch callable merged with MCP dispatch. In-process binding to
  workspace, sandbox, telemetry; no HTTP hop. Registered through `agent_tools.py` so the
  ToolGate covers them by construction.
- **D2 — Execution layer**: new `SandboxExecutor` wraps `SandboxManager.plan()` and actually
  runs `plan.command` via `subprocess.run` (timeout, capture). File edits run in-process with
  strict workspace-root path validation; bash/search/tests go through the sandbox. K8s
  strategies raise `NotImplementedError` (workflow-engine scope).
- **D3 — Git is host-side** (`WorkspaceManager`): clone/worktree/diff/cleanup in trusted code;
  preserves the `.git` read-only sandbox invariant (V12).
- **D4 — Checkpoint abstraction over the existing loop, not a LangGraph graph rewrite.**
  Extract the loop into `agent_loop.py`; persist `LoopState` via `PostgresSaver`
  (`thread_id = run_id`) behind a `RunCheckpointStore` protocol with a file fallback. The
  checkpoint shape keeps a later true-StateGraph migration cheap. Rationale: the loop is
  linear; a graph rewrite of the monolith's hottest path buys nothing now.
- **D5 — One `submit` tool is the termination mechanism** (replaces `<CONTINUE>`/`<DONE>`).
  Synthetic tool injected into every loop; when a coding workspace is active, the loop
  enriches the submission with the host-side workspace diff. Markers remain behind
  `legacy_markers=True` as an encoding for non-tool-calling paths until constrained decoding
  lands.
- **D6 — Gate placement: wrap the tool executor**, built in one module (`agent_tools.py`),
  so every present/future call site is gated by construction; `_run_openai_chat` refuses
  ungated executors in enforcement mode (defense in depth).
- **D7 — Policy evaluation**: in-process evaluator default (microseconds, logic exists),
  optional `HttpOpaEvaluator` when `FRONTIER_OPA_URL` set, fail-closed fallback, CI-only
  Rego parity tests (`opa eval` vs Python over shared fixtures).
- **D8 — Harmony sits beside `_run_openai_chat`**, dispatched by model capability profile;
  never round-trips through Chat Completions templating; raw `/completions` endpoint.
- **D9 — Eval harness is API-only and remote-first** (`apps/evals` drives `lattix exec`
  against a runner box; refuses non-smoke configs on localhost without `--allow-local`).
- **D10 — Best-of-N is a dedicated orchestration path** (`rollouts.py`), not the mention
  loop; the LLM patch judge *is* an agent (`patch-judge` AgentDefinition); selection is
  deterministic: submitted → exec-verified → judge-ranked → smallest patch.
- **D11 — All new code in new modules**; `main.py` gets only thin wiring + facade re-exports;
  a ratchet test enforces monotone shrinkage.

---

## 3. Milestones

Sizing: S < 1 day · M = 1–3 days · L = 1–2 weeks (single engineer, focused).

### M0 — Foundation & safety rails (parallel-friendly, start immediately)

| Step | Content | Size |
|---|---|---|
| M0.1 | **Decomposition P1**: extract `models.py` (main.py:88–608), `runtime_config.py` (:611–763), `store.py` (`InMemoryStore` + `get_store()`; main keeps rebindable `store` alias for test monkeypatching). Verbatim moves, facade re-exports. | M |
| M0.2 | **Monolith guard** `test_monolith_guard.py`: line-count ratchet (start 19,500), route-decorator count ≤127 in main.py (new endpoints must use routers), no-cycle assertions for new modules. | S |
| M0.3 | **Policy evaluator extraction**: `frontier_runtime/policy.py` — `PermissionTier`, `GateDecision`, `LocalPolicyEvaluator` (sync extraction of `OPAClient.evaluate_request` body; existing async tests keep passing), `HttpOpaEvaluator` w/ fail-closed fallback, `build_policy_evaluator()`. | M |
| M0.4 | **Quick hardening**: constant-time compare at main.py:9499 (`hmac.compare_digest`); flip `require_authenticated_requests` default to `True` with `local-lightweight` profile as explicit opt-out; startup refusal when auth off × non-loopback bind. | S |

**Gate to proceed**: full existing test suite green after each PR; `import app.main` smoke; ratchet in place.

### M1 — Gated execution core

| Step | Content | Size |
|---|---|---|
| M1.1 | **Loop extraction** → `apps/backend/app/agent_loop.py`: `LoopState` (JSON-serializable, append-only `messages`), `LoopBudgets`, `LoopOutcome` (`SUBMITTED / BUDGET_EXHAUSTED / PROVIDER_UNAVAILABLE / ERROR`), `run_agent_loop(state, *, chat_fn, tools, tool_executor, budgets, emit_progress, recorder, checkpointer, legacy_markers)`. `_run_agent_iterations` becomes a thin wrapper resolving `chat_fn` from module globals at call time (keeps `test_agent_collaboration.py` monkeypatch pattern working). Add `on_message` tap to `_run_openai_chat` (main.py:4419) — the lossless hook for messages + usage. | M |
| M1.2 | **Tool surface extraction + gate** → `apps/backend/app/agent_tools.py`: move `_gather_mcp_run_tools` et al. (main.py:13443–13555); add `RunCapabilityContext`, `resolve_permission_tier` (most-restrictive-wins: payload/agent/platform), `mint_run_capability` (per-run HMAC token scoped to workspace root, tier-filtered tools, max_tool_calls, TTL), `ToolGate.authorize` (token verify → counter → path classification → policy eval → tier overlay → audit EVERY decision), `build_gated_tool_executor` (deny → structured `[POLICY DENIED: reason]` tool result + `policy_rejected` event). High-risk pattern match becomes `requires_approval`, not silent drop (V4). | L |
| M1.3 | **Wiring at all four call sites**: `_execute_run` (main.py:14359, replace raw `_execute_mcp_tool`), `_run_agent_collaboration` (:13963, per-agent tokens), `_respond` (:14926), graph `frontier/tool` handler (:7111–7126). Record `permission_tier` + capability metadata in `run_details[run_id]["access"]`; expose tier in agent security-policy endpoint (:18262) and run detail (:15024). Ungated-executor guard in `_run_openai_chat`. | M |

**Gate**: `test_tool_gate.py` matrix green (bad sig/expired/wrong agent/tool-not-allowed/counter exhaustion/tier matrix/approval path); gate latency <1 ms without OPA server; every decision audited.

### M2 — Coding agent v1

| Step | Content | Size |
|---|---|---|
| M2.1 | `apps/backend/app/coding/executor.py`: `SandboxExecutor.run(spec, policy) -> ToolExecutionResult` (plan → `subprocess.run`, timeout, capture). | M |
| M2.2 | `coding/workspace.py`: `WorkspaceSpec`/`Workspace`/`WorkspaceManager` (clone `--depth=50` / worktree-for-local-path / empty; host-side `diff()`; `cleanup()` + `sweep_expired(retention_hours)`; run_id path-injection rejection; egress-allowlist check on clone). | M |
| M2.3 | `coding/truncation.py` (50 KB / 2,000-line head-60/tail-40 elision) + `coding/telemetry.py` (`CodingTelemetry`: tool_calls_total/malformed, edits attempted/well_formed/applied, reasks, downgrades; emitted as a final `telemetry` run event + merged into model_meta). | S |
| M2.4 | `coding/toolset.py`: fixed R2E-Gym-shaped toolset — `execute_bash`, `search` (rg via sandbox, python fallback), `str_replace_editor` (in-process; exact-once `old_str` with not-found/ambiguous hints; workspace-root confinement incl. symlink resolution), `run_tests` (verbatim output, truncation caps only), `submit` (host-side diff → artifact). Schemas stable/frozen ordering (cache discipline). | L |
| M2.5 | **Run wiring**: `_gather_coding_run_tools` (activation: agent tool type `frontier-coding` or payload `workspace` block); merge schemas before MCP (frozen prefix); compose dispatcher; cleanup honoring `workspace_retention_hours`. New `PlatformSettings` (all default-off). Contract: `agent.config.schema.json` gains `workspace` block + `frontier-coding` tool type (NB top-level `additionalProperties:false` — schema must change before configs use it). Coding dispatch goes through M1's gated executor. | M |
| M2.6 | **Structured termination**: synthetic `submit` tool in every loop (D5); iterations continue on the same append-only `messages` list (delete the synthetic-prompt rebuild, V6); coding runs enrich submission with workspace diff; `legacy_markers` flag for compat. | M |
| M2.7 | **Budgets + bounded self-repair**: step/time/context budgets (estimator from `conversation.py`, real `usage` when present); ≤2 verbatim-error retries per step then forced-submit turn (`tool_choice` pinned to `submit`); breach ⇒ `BUDGET_EXHAUSTED`, zero credit (DeepSWE compact filtering). Budget resolution: payload > agent config > platform ceiling. | M |

**Gate**: monkeypatched end-to-end test — agent declares `frontier-coding`, payload points at a tmp git repo, fake chat emits edit + submit → tool events present, diff artifact exists, telemetry event present, workspace cleaned; no schema leak when disabled; no run can loop unboundedly.

### M3 — Local-model fidelity

| Step | Content | Size |
|---|---|---|
| M3.1 | **Provider registry entries** for `vllm` / `llamacpp` / `lmstudio` (main.py:2746; `key_required: False`, default base URLs); `local_models.py` gains `serving_overview()`/`backend_models()`; `/models/overview` includes new backends. Everything else (routing, `ai_providers` base_url overrides, model listing) falls out of existing plumbing (V8). | S |
| M3.2 | `apps/backend/app/model_profiles.py`: `ModelCapabilityProfile` (edit_format, tool_protocol native-fc/harmony/xml/bash-only, max_effective_context, sampler, structured_output none/json_schema/xgrammar/gbnf, tool_defs_in_system) + builtin profiles (`gpt-oss-harmony`, `local-32b-class`, `local-weak`) + fnmatch pattern resolution + override precedence (agent > platform settings > builtin). `_resolve_agent_model_profile` beside `_resolve_agent_chat_model` (:3139). | M |
| M3.3 | `apps/backend/app/tool_call_enforcement.py`: `constraint_kwargs(provider, profile, tools)` (vLLM xgrammar extra_body / llama.cpp json_schema→GBNF / plain json_schema; version-drift isolated in one function with per-provider settings escape hatch); `validate_tool_call` (jsonschema envelope validation — replaces the silent `{}` coercion at main.py:4543–4547); `ReaskPolicy` (≤2 re-asks not consuming tool budget, global `max_reasks_per_run≈8`); edit-format auto-downgrade after 2 consecutive editor failures (behavioral, never mutates schemas mid-conversation). New dep: `jsonschema` (core). | M |
| M3.4 | **Harmony-native path**: `apps/backend/app/harmony_chat.py` (optional dep `openai-harmony`, extra `local-models`): render via `openai_harmony` (tool namespaces in **system** message), call raw `/completions` (httpx, temp 1.0/top_p 1.0, harmony stop tokens), parse analysis/commentary/final channels; `HARMONY_TOOL_ALIASES` (`container.exec`→`execute_bash`); **`apply_patch` exposed as a first-class tool** applied host-side in the workspace (resolves the riskiest translation — see Sign-offs). Dispatcher head in `_run_openai_chat`; graceful fallback when package absent. | L |

**Gate**: profile resolution tests; malformed-args fake client yields exactly 2 re-asks + correct well-formed-call rate; harmony fixture round-trip (render with 2 tools → system contains namespaces; parse canned completion → alias mapping + reasoning extraction); `vllm/gpt-oss-20b` routes to harmony when available.

### M4 — Durability & headless operation

| Step | Content | Size |
|---|---|---|
| M4.1 | **Trajectories**: `frontier_runtime/trajectory.py` — versioned JSONL schema (meta header w/ model/sampler/budgets/system-prompt hash/task incl. eval instance_id+seed; verbatim `message` lines w/ usage; `annotation` lines for repairs; `outcome` line w/ submission + budgets used). `PostgresTrajectoryStore` (append-only `frontier_trajectory_records(run_id, seq, payload JSONB)`, copying `PostgresAuditLog` pattern) + `FileTrajectoryStore` (`.frontier/trajectories/{run_id}.jsonl`). `GET /workflow-runs/{id}/trajectory` (NDJSON, `_enforce_run_access`). Feature flag `FRONTIER_TRAJECTORIES_ENABLED`. | M |
| M4.2 | **Checkpointing + resume**: `frontier_runtime/checkpoints.py` — `RunCheckpointStore` protocol; `LangGraphPostgresCheckpointStore` (sync `PostgresSaver`, `thread_id=run_id`, channel `{"loop_state": ...}`); `FileCheckpointStore` fallback (no new dep — decision over `langgraph-checkpoint-sqlite`). Cadence: every step boundary + outcome. `RunExecutionContext` (pydantic) replaces the captured closure so runs are resumable; startup recovery for orphaned `Running` runs; `POST /workflow-runs/{id}/resume`; `resume_epoch` fence against racing threads. | L |
| M4.3 | **`lattix exec`** (+ `frontier` script alias): POST `/workflow-runs` → SSE follow with `?after=` reconnect across the 300 s window (fallback to plain event polling on connection failure) → `--output text|json|stream-json`, `--trajectory-out`, `--best-of`. Exit codes: 0 done · 1 failed · 2 blocked · 3 budget-exhausted · 4 timeout · 5 connection/auth. stdout machine-clean, diagnostics to stderr. | M |

**Gate**: kill-mid-step → restore → identical message prefix → completes; orphaned runs never silently stuck; `lattix exec "task" --output json; echo $?` scriptable; trajectory replays byte-identical to provider I/O.

### M5 — Eval harness

| Step | Content | Size |
|---|---|---|
| M5.1 | `apps/evals` package (`frontier-evals`; deps httpx/click/pydantic/datasets/swebench; not in backend image): `instances.py` (pinned SWE-bench Verified subsets vendored in `configs/`), `runner.py` (per instance×seed: provision env → `lattix exec` → collect submission+trajectory), `docker_env.py` (official SWE-bench instance images via remote `DOCKER_HOST`), `grading.py` (predictions.jsonl → `swebench.harness.run_evaluation`; **test execution is the only verdict**), `stats.py` (per-seed resolve rates, mean ± SEM), `report.py`. Results layout: `results/{eval_id}/instances/{id}/seed-{k}/{trajectory.jsonl,result.json}` + `summary.json`. | L |
| M5.2 | **Resource guardrail**: refuse non-smoke configs when API URL or DOCKER_HOST resolve to localhost unless `--allow-local` (encodes memory `resource-constrained-local-testing` as code). | S |
| M5.3 | **Smoke evals**: `--mode plumbing` (3–5 instances, fake exec backend + golden patches, <2 min, CI on PRs touching evals/loop) and `--mode live` (1 seed, real backend, nightly on runner). | M |

**Gate**: plumbing smoke green in CI; live smoke produces non-degenerate mean±SEM + one trajectory per instance×seed; reruns reproducible up to sampler nondeterminism.

### M6 — Quality scaling & cache discipline

| Step | Content | Size |
|---|---|---|
| M6.1 | **Best-of-N + hybrid verification**: `apps/backend/app/rollouts.py` — N independent rollouts (own workspace clone, own trajectory `run_id-r{k}`, own checkpoint; `_RUN_EXECUTOR` w/ `best_of_n_max_parallel` cap); `submit` gains optional `regression_tests`; `execution_verify` (agent regression tests + workspace test command), `judge_patches` (seeded read-only `patch-judge` AgentDefinition, order-randomized, 0–1 normalized), deterministic `select` (D10). Default **off**; `payload.best_of` / `--best-of N`. Parent-run events per rollout/verification; selected candidate is the final `agent_message`. | L |
| M6.2 | **Cache-safe compaction**: `ConversationManager` v2 `fork` mode (env `FRONTIER_CONVERSATION_COMPACTION_MODE`, default `legacy` first) — `set_prefix(system, tool_defs)` (frozen, idempotent; also fixes the duplicate-system-turn bug at main.py:6838), append-only segments, compaction closes a segment and opens a new one with summary + last-K turns deep-copied; v1→v2 serialization migration. Flip default to `fork` only after one local-model soak run. | M |

**Gate**: `--best-of 4` ≥ single-rollout resolve rate on smoke set; N=1 byte-identical to normal run; 100-turn fork-mode simulation shows zero retroactive message mutations (hash assertion).

### M7 — Hardening completion

| Step | Content | Size |
|---|---|---|
| M7.1 | Webhook trigger tokens: re-key store by `sha256(token)` (startup migration), header transport `X-Frontier-Trigger-Token` + new canonical route, legacy path deprecation audit event. | S |
| M7.2 | Per-action approval records (`store.approval_requests`: id/run_id/tool/args_digest/status/decided_by); ToolGate `requires_approval` creates one; `submit_approval` accepts `approval_id`; inbox links. v1 semantics: approve-then-resend (gate honors decided approval for matching `(run_id, tool, args_digest)` within TTL); pause/resume-on-approve lands on top of M4.2 checkpoints later. | M |
| M7.3 | Token-bucket rate limit on `PUBLIC_MINIMAL` routes (login/register/webhook), env-tunable, default 30/min. | S |

---

## 4. Sequencing & dependency graph

```
M0.1 store/models/config ─┬─► M1.1 agent_loop ─┬─► M2.6 submit termination ─► M2.7 budgets
M0.3 policy.py ───────────┴─► M1.2 agent_tools/gate ─► M1.3 wiring ─► (M2 coding tools gated for free)
M2.1 executor ─► M2.4 toolset ─► M2.5 wiring          M2.2 workspace ─► M2.4
M3.1 providers ─► M3.2 profiles ─► M3.3 constraints ─► M3.4 harmony (last: biggest payoff, most novel)
M1.1 ─► M4.1 trajectories ─► M4.2 checkpoints         M2.6 ─► M4.3 exec CLI
M4.3 ─► M5 evals (plumbing mode does NOT block on M2; live mode does)
M2 + M4 + M5 ─► M6.1 best-of-N        M6.2 compaction: parallel anytime (tiny touchpoint)
M0.4 ─► M7 (anytime after store.py)
```

Recommended order of PRs: M0.1→M0.2→M0.3→M0.4 → M1.1→M1.2→M1.3 → M2.1→M2.2→M2.3→M2.4→M2.5→M2.6→M2.7
→ M3.1→M3.2→M3.3 → M4.1→M4.3→M4.2 → M5 → M3.4 → M6 → M7 (M6.2 and M7 interleave opportunistically).

**First measurable checkpoint** (after M2+M3.1–3.3+M4.3+M5): run the 50-instance Verified
subset with `vllm/gpt-oss-20b` (constrained native-FC, no harmony yet) — this is the baseline
that M3.4 harmony and M6.1 best-of-N improvements are measured against. Per the eval
discipline: no harness change is "better" without ≥5-seed mean±SEM evidence.

---

## 5. Ways of working (process tightening)

1. **Eval-before-tune**: after M5 lands, any change claiming benchmark impact requires a
   smoke-eval delta in the PR description; full-subset runs for the milestone gates.
2. **Telemetry-first**: well-formed-call rate, well-formed-edit rate, reasks, downgrades are
   first-class run metadata from M2.3 onward — collected *before* M3 so constraints have a baseline.
3. **Monolith ratchet**: each PR touching `main.py` must hold or lower the line ratchet;
   new endpoints go in routers; `get_store()` per use, never bound at import (code-review rule).
4. **Feature flags default-off, soak before flip**: coding tools, trajectories, fork
   compaction, harmony, best-of-N all ship behind flags; defaults flip only after a soak run.
5. **Additive contracts only**: `agent.config.schema.json` changes are additive; persisted
   state must load across versions (defaults for missing fields); JSONL/trajectory schemas
   are version-tagged.
6. **No benchmark fleets locally** (memory `resource-constrained-local-testing`) — enforced
   in code by M5.2, not by convention.
7. **Security claims must match code**: "HMAC capability tokens" until Biscuit lands;
   enforcement statements in docs/SECURITY.md update in the same PR as M1.3.

---

## 6. Open decisions requiring sign-off

| # | Decision | Recommendation |
|---|---|---|
| O1 | **DLP vs verbatim tool output**: coding tool outputs bypass DLP redaction inside the loop (workspace trust boundary); event summaries keep masking | Accept — needs explicit security-posture statement |
| O2 | **`frontier` vs `lattix` CLI naming** | Add `frontier` as script alias; pick canonical name before docs |
| O3 | **`apply_patch` handling in harmony mode** | First-class host-side tool (not conversion to editor ops) — lower risk, in-distribution for gpt-oss |
| O4 | **Checkpoint fallback store** | File-based (zero new deps) over `langgraph-checkpoint-sqlite`; Postgres saver is the production path |
| O5 | **True Biscuit adoption** (attenuable delegation tokens) | Defer to sub-agent-spawning workstream; HMAC suffices for per-run scoping |
| O6 | **READ_ONLY tier semantics for opaque MCP tools** | Readonly-pattern allowlist now; adopt MCP `readOnlyHint` annotations when integrations carry them |
| O7 | **K8s/gVisor execution path** | Out of scope for the harness (plan-only, explicit `NotImplementedError`); revisit for the hosted platform |

## 7. Top risks

1. **Windows dev host has no sandbox backend** (no bwrap/seatbelt) — tests use injected fake
   executors; Linux CI is the real target; toolset surfaces a clear "no sandbox backend" error.
2. **vLLM structured-output API drift** — isolated in `constraint_kwargs` with per-provider
   capability probe + settings escape hatch; pin vLLM versions on the runner.
3. **Thread-based execution racing resume** — `resume_epoch` fence enforced on every
   checkpoint write; explicit race test required.
4. **Trajectory volume** (untruncated outputs in JSONB) — storage cap knob, cap recorded in
   header so SFT filtering can exclude capped runs.
5. **Hidden coupling via `store` globals during resume** — `RunExecutionContext`
   serialization is the mitigation; treat any closure capture in `_execute_run` as a bug.
6. **Marker-compat window**: weak models may emit malformed `submit` before M3.3 lands —
   fallback ladder + bounded self-repair covers it; record the failure rate now as M3.3's baseline.
