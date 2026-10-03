# Locus coding harness (`locus_runtime.harness`)

A self-contained, model-agnostic SWE agent scaffold for extracting high-quality
long-horizon coding behaviour from **local open-weight models** (gpt-oss,
Qwen3-Coder, Devstral, …) and benchmarking it against DeepSWE / SWE-bench.

Design lineage: **mini-SWE-agent** (minimal, replayable, linear trajectory) +
**R2E-Gym/DeepSWE** (fixed tool set, execution-graded, submit-or-zero) +
**Aider** (edit-format discipline with weak-model fallback).

## Pieces

| Module | Role |
| --- | --- |
| `model_profiles.py` | Per-model capability profile (edit format, tool protocol, context, sampler, structured-output backend). gpt-oss → harmony/apply_patch; local 32B → search-replace + json_schema; weak → whole-file. |
| `executor.py` | Where tools run: `default_executor()` → `LocalSandboxExecutor` on the platform's confining tier (AppContainer / seatbelt / bwrap / hardened docker via `locus_runtime.sandbox`), `DockerContainerExecutor` (SWE-bench instance container on a remote `DOCKER_HOST`), `LocalDirectExecutor` (tests / explicit dev opt-out; exec denied by `tool_jail`). |
| `workspace.py` | Repo root + git diff/reset/run-tests helpers (host-side git keeps the sandbox `.git` invariant). |
| `tools.py` | The fixed tool set: `execute_bash`, `search`, `str_replace_editor`, `run_tests`, `submit`. Exact-match edits, well-formed-edit telemetry, auto-downgrade to whole-file. 50KB/2000-line output truncation. |
| `enforcement.py` | Tool-call validation (envelope), bounded re-ask, grammar-constrained decoding kwargs (XGrammar/GBNF). |
| `llm.py` | `ChatClient` protocol; `OpenAIChatClient` (any OpenAI-compatible endpoint); `ScriptedChatClient` (tests). |
| `loop.py` | The agent loop: append-only messages, `submit` termination, hard budgets with submit-or-zero, self-repair, trajectory recording. |
| `trajectory.py` | Lossless JSONL trajectory (header + verbatim messages + annotations + outcome); replayable / SFT-ready. |
| `run_envelope.py` | `RunEnvelope` (goal, done criteria, capabilities, budget, autonomy tier) and `build_envelope()` from task text + detected repo checks (pytest/ruff/mypy/npm). |
| `verification.py` | The verify gate: command and file checks through the gated executor, plus the acceptance judge for free-text criteria. |
| `verified_loop.py` | `VerifiedLoop` (LOCUS-337): plan → act → verify → `done` / `blocked` / `stopped`, budgets reported to the gateway, checkpoint/resume. |
| `swe_agent.py` | Assembles the above into `SweAgent.solve(SweTask) -> SweAgentResult` (produces the unified-diff prediction). Runs the verified loop when the task has a test command. |

## Why this should make local models perform (ranked levers)

1. **Native-format fidelity** — harmony channels + in-distribution tools for
   gpt-oss; the `gpt-oss-harmony` profile encodes this (worth up to ~30 pts).
2. **Execution feedback** — `run_tests` returns verbatim output; the agent
   iterates against real test results, not plausibility.
3. **Grammar-constrained tool calls** — `enforcement.constraint_kwargs` removes
   the malformed-call tax on weak models; envelope validation + bounded re-ask
   catch the rest.
4. **Edit-format matching** — search-replace for capable models, auto-downgrade
   to whole-file after repeated failures; well-formed-edit rate is telemetry.
5. **Budgets + submit-or-zero** — bounded steps/time/context, no unbounded loops,
   no credit without an explicit submission (DeepSWE compact filtering).

## Quick use

```python
from locus_runtime.harness import (
    SweAgent, SweTask, resolve_profile,
)
from locus_runtime.harness.executor import default_executor
from locus_runtime.harness.llm import OpenAIChatClient

client = OpenAIChatClient(model="gpt-oss-20b",
                          base_url="http://runner:8000/v1", provider="vllm")
agent = SweAgent(client=client, profile=resolve_profile("vllm", "gpt-oss-20b"))
task = SweTask(instance_id="demo", problem_statement="...",
               # pass gateway_session=... (a run's gateway session); unbound
               # executors are denied by a real gateway
               executor=default_executor("/path/to/repo"),
               test_command="python -m pytest -q")
result = agent.solve(task)
print(result.outcome, result.has_patch)
print(result.patch)          # unified diff = the graded prediction
print(result.telemetry)      # well_formed_edit_rate, reasks, …
```

Benchmarking (DeepSWE/SWE-bench) is driven by `apps/evals` — see its README.

## Gateway (policy enforcement, LOCUS-332)

Every executor side effect asks `locus_runtime.gateway` first: `run`/`run_shell`
are `process_exec`, `write_file` is `file_write`, `read_file` is `file_read`.
A blocked command returns exit code 126 with the decision attached; a blocked
file operation raises `GatewayBlocked`; `CodingToolset` turns both into a
`[denied by policy]` / `[permission required]` tool result, so the run continues.

- Executors need a run `gateway_session` (the backend opens one per run via
  `WorkspaceManager.provision(..., session_factory=...)`). Without one they are
  unbound callers and a real gateway denies them; with no gateway installed in
  the process everything is denied.
- `tool_jail` sees the executor's real jail facts, derived from the strategy it
  launches with (principal decision 2026-10-03, "Accept real OS jails"):
  - `kernel-bwrap` / `kernel-seatbelt` / `hardened-docker`: read-only root and a
    numeric non-root uid → allowed.
  - `windows-appcontainer`: AppContainer + Job Object launched with
    `--require-appcontainer` (fails closed, never degrades to Job-Object-only) →
    allowed; without the require flag → denied.
  - `docker-exec` (SWE-bench container): allowed only for an `evals` session
    (`apps/evals`; the backend gateway refuses evals sessions) and a container
    whose network mode, read by `docker inspect`, is `none`.
  - `local-direct`, `restricted-process`, `unavailable` → denied.
- Sandboxed execution is the default: `default_executor()` picks AppContainer
  (Windows), seatbelt (macOS), bubblewrap (Linux), else hardened Docker. With no
  confining tier, every exec is denied with an actionable reason
  (`tool_jail.no_confining_sandbox`, "install bubblewrap ..."). `LOCUS_SANDBOX_AGENTS=0`
  is the explicit dev opt-out to `LocalDirectExecutor`, still denied by `tool_jail`.
- Agent commands get a minimal environment (`sandbox.minimal_agent_env`: PATH,
  HOME/USERPROFILE, TEMP/TMP, LANG, SystemRoot, LOCALAPPDATA, ...) plus explicit
  per-run variables; LOCUS_* and secret-like names (key/token/secret/password)
  are always dropped. The docker CLI additionally gets DOCKER_* only.
- On Windows the AppContainer is default-deny: only native tools readable by
  ALL APPLICATION PACKAGES (System32, Program Files, e.g. `cmd`, `git`) run;
  WSL `bash` and user-profile Python installs do not.
- Executables allowed by `tool_jail`: `LOCUS_GATEWAY_ALLOWED_EXECUTABLES`
  (comma separated; default `bash,sh,git,python,python3,pytest,rg,grep,codex`).
- `tests/harness/test_gateway_bypass.py` fails if a new spawn/write/network
  call or executor class bypasses the gateway.

## Verified run loop (LOCUS-337)

`VerifiedLoop` runs one envelope to exactly one end state (P3):

- **done** -- every done criterion passed at `submit`; `RunResult.evidence` holds
  the commands, exit codes, output tails, judge verdicts, diff and plan version.
- **blocked** -- `RunResult.blocker` names the blocker and what would unblock it:
  a gateway deny/ask on a verifier command, a missing tool (exit 127), the agent's
  `report_blocker` (e.g. ambiguous criteria), an unavailable model or judge, or
  the same verification failure `max_identical_failures` times.
- **stopped** -- `RunResult.stop`: budget (steps, seconds, tokens, cost, actions,
  context), user (`request_stop()` / `should_stop`) or policy (a `budget_policy`
  deny at the gateway).

The first model call asks for a plan through `update_plan` (steps + how each
criterion is verified); `plan_mode="required"` refuses other actions until a
plan exists. A failing `submit` is rejected with the findings as its tool
result. Free-text criteria are judged by `judge_client` (default: the worker's
client with a separate prompt) only after the command and file checks pass.

Before every action the loop reports `BudgetFigures` to the executor's gateway
session (`GatewaySession.report_budget`, monotonic: usage never decreases and
limits never rise), so `budget_policy` is evaluated on each action.

With `checkpoint_path` set, the loop state is written atomically after every
step (`kind: locus.run_checkpoint`, `version: 1`). `VerifiedLoop.resume(path,
client=..., toolset=..., profile=...)` continues after a restart; resuming a
finished run returns its result without calling the model. Budget defaults:
`LOCUS_RUN_MAX_{STEPS,SECONDS,TOKENS,COST_USD,ACTIONS}`; tier: `LOCUS_AUTONOMY_TIER`.
