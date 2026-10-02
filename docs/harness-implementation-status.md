# Harness Implementation Status — 2026-06-12

Tracks what of the [production-harness-plan](production-harness-plan.md) has landed
in code vs. what remains. The goal: stable, production-ready coding harness running
local models (gpt-oss-20b) on long-horizon multi-agent dev workflows, gated at
≥30 % on DeepSWE/SWE-bench.

## Landed this iteration (new, tested, isolated from the monolith)

`frontier_runtime/harness/` — a self-contained, model-agnostic SWE agent scaffold
(mini-SWE-agent + R2E-Gym/DeepSWE + Aider lineage). Does **not** import the
FastAPI monolith, so it runs headless in CI and on remote benchmark runners.

| Module | Plan milestone | Status |
| --- | --- | --- |
| `model_profiles.py` (gpt-oss harmony/native, local-32b, weak, bash-only) | M3.2 | ✅ |
| `executor.py` (LocalDirect / LocalSandbox / DockerContainer, fsync writes) | M2.1 | ✅ |
| `workspace.py` (git diff/reset/run-tests, host-side git) | M2.2 | ✅ |
| `tools.py` (execute_bash/search/str_replace_editor/run_tests/submit, truncation, edit telemetry, auto-downgrade) | M2.3–M2.4 | ✅ |
| `enforcement.py` (envelope validation, bounded re-ask, XGrammar/GBNF kwargs) | M3.3 | ✅ |
| `llm.py` (OpenAI-compatible client for vLLM/llama.cpp/LM Studio; scripted test client) | M3.1 | ✅ |
| `loop.py` (append-only messages, `submit` termination, budgets + submit-or-zero, self-repair) | M2.6, M4 (partial) | ✅ |
| `trajectory.py` (lossless replayable JSONL; SFT/RL-ready) | M4.1 | ✅ |
| `swe_agent.py` (SweAgent.solve → unified-diff prediction) | M2 | ✅ |

`apps/evals/` (`frontier-evals`) — the benchmark harness:
* `synthetic-mini` plumbing dataset (real git repos) + `swe-bench` live dataset.
* execution-only grading (`grade_synthetic`; live defers to official `swebench.harness`).
* per-seed mean ± SEM stats (SWE-rebench protocol), summary.json + report.md.
* remote-runner guardrail (refuses local fleets; `resource-constrained-local-testing`).
* `frontier-evals smoke|run` CLI.

### Tests (all green on this machine; 20 passing, stable across repeated runs)
* `tests/harness/test_harness_core.py` — trajectory, profiles, truncation, telemetry, enforcement.
* `tests/harness/test_swe_agent_e2e.py` — **full agent fixes a real bug, runs real tests, graded by execution**; budget submit-or-zero; malformed-call re-ask; trajectory persistence.
* `tests/evals/test_deepswe_eval.py` — **the 30 % gate** (plumbing in CI; the same assertion enforces gpt-oss-20b ≥ 30 % when pointed at a live runner), reference/no-op solvers, guardrail, SEM math.

## Verified-here vs. hardware-gated

* **Verified on this box (no GPU):** the entire scaffold and eval pipeline — agent
  loop, tool execution, edits, execution grading, stats, report, trajectories,
  CLI. The 30 % gate passes in plumbing mode (reference solver).
* **Gated on a runner box (GPU + Docker), NOT runnable here:** serving gpt-oss-20b
  via vLLM and running real SWE-bench Docker fleets. The live path is wired and
  one command (`frontier-evals run --mode live …`); the measured gpt-oss-20b
  number must be produced there. This box (32 GB, WSL-only shell python) cannot
  serve the model or host instance containers, by hardware and by policy.

## Not yet landed (next milestones, per the plan)

* **M0/M1 monolith decomposition + enforcement wiring** — the harness is isolated;
  wiring `ToolGate` (OPA/Biscuit) around the harness executor and extracting
  `main.py` are still to do. The harness already routes all tool execution through
  one `Executor` seam, so gating is a wrapper away.
* **M3.4 harmony-native gpt-oss serving path** — profile exists; the
  `openai_harmony` encoding/`/completions` client is the next build (biggest live
  lever).
* **M4.2 postgres checkpoint/resume**, **M4.3 `lattix exec`** integration into the
  backend run API.
* **M5 full SWE-bench Verified subset configs** (pinned instance lists) + official
  grader smoke on the runner.
* **M6 best-of-N + hybrid verification**, **M6.2 cache-safe compaction**, **M7 hardening**.

## Shipped agent: Full-Stack SDET (`examples/agents/sdet-swe-agent/`)

A full-stack Software Development Engineer in Test agent ships with the platform —
loaded by the backend's `_load_seeded_agents_from_repo()` as a **published** agent
(verified: appears in the seeded set, status=published, system prompt + `frontier-coding`
toolset + `local-32b-class` profile intact through canonicalization), so it shows in the
agent modeler. The *same* definition drives the benchmark via
`frontier_runtime.harness.agent_library.load_agent_spec` →
`SweAgent(system_prompt_override=spec.system_prompt, profile=spec.profile())`, so the
agent we ship is the agent we score. Select it in the eval with `--agent sdet-swe-agent`.

## Evidence-driven hardening from real gpt-oss:20b runs (via the platform's Ollama)

Driving the real model through the harness surfaced and fixed (each with a regression test):

| Fix | Surfaced by | Effect |
| --- | --- | --- |
| Exclude `__pycache__`/`*.pyc`/caches from the submitted diff | run1 patch had pyc binary noise | apply-clean SWE-bench predictions |
| Editor command inference + `line_start/line_end` alias | run1 wasted a re-ask | fewer wasted steps |
| Clamp `execute_bash` timeout (≤600s) | run1 model passed `timeout:10000` | can't hang a run |
| Provider retry w/ backoff before PROVIDER_UNAVAILABLE | run2 died on a transient Ollama error under load | long runs survive a contended endpoint |
| Tool-name normalization (`view`→editor; `bash`/`edit`/`finish` aliases) | run2 model called `view` as a top-level tool | fewer wasted steps |

Measured improvement on `syn-add-sign` (gpt-oss:20b): well-formed-call rate 0.83 → **1.00**
(0 malformed, 0 re-asks); patch clean; run resilient under stack contention.

A later run added a 6th fix: `diff()` refreshes the git index and `submit` **rejects an
empty patch when edits were applied** (transient git stat-cache miss under load was silently
shipping empty patches — the worst failure: a correct fix graded as a miss).

## Measured DeepSWE-style results (gpt-oss:20b via the shipped SDET agent, your stack's Ollama)

Synthetic-mini set (3 tasks), execution-graded, `--agent sdet-swe-agent`:

| Run | Resolve rate | Notes |
| --- | --- | --- |
| 3 tasks × 2 seeds (before diff fix) | **83.3% ± 16.7%**, pass@2 100% | 1 failure was the lost-diff harness bug, not the model |
| `syn-max-empty` × 3 seeds (after diff fix) | **100% ± 0%** | the previously-failing instance, now stable, non-empty patches |

Well past the 30% gate. This is the *synthetic* suite (real git repos + real test execution);
**SWE-bench Verified** is the next step and needs the per-instance Docker images on a runner.

## Option B (runner) readiness — execution path proven

The SWE-bench live path is fully wired and the riskiest piece is now proven on real Docker:
`tests/harness/test_docker_executor.py` validates `DockerContainerExecutor` (run_shell,
read/write file, exists, exec-imported-module, failing-command exit code) against a real
`python:3.12-slim` container. `frontier-evals list-instances` pulls real Verified ids; the
official `swebench.harness` grader is hooked up. Setup + commands in
`docs/swe-bench-runner-setup.md`. **Blocked only on a runner endpoint** (model URL +
provider/model + Docker host).

## Known issue: SDET agent not visible in Agent Studio (frontend auth, NOT seeding)

The agent is correctly published in the backend store (confirmed in `frontier_state_store`
`section:agent_definitions`, and `GET /agent-definitions` returns all definitions unfiltered).
The UI shows three *mock* agents (`apps/frontend/src/lib/mock-data.ts:150-152`:
Orchestration v5 / Market Intelligence v4 / Outreach Critic v2) because the frontend's `/api`
proxy calls return **401** (builder access requires an authenticated context with a builder
role) and the frontend falls back to mock data even with `NEXT_PUBLIC_ENABLE_MOCK_DATA=false`.
Fix is frontend↔backend auth (casdoor session → backend auth context), tracked separately —
not a harness/seeding problem.

## How to run

```bash
# plumbing gate (here / CI)
python -m pytest tests/harness tests/evals -q
python -c "import sys; sys.path.insert(0,'apps/evals'); from frontier_evals.cli import cli; cli()" smoke

# live gpt-oss-20b on a runner (GPU + Docker)
frontier-evals run --mode live --dataset swe-bench \
  --api-base-url http://runner:8000/v1 --model openai/gpt-oss-20b \
  --provider vllm --docker-host tcp://runner:2376 \
  --seeds 0,1,2,3,4 --threshold 0.30
```
