# Deep Agents harness: Locus extensions, hardening, patch ledger (LOCUS-361)

Status: implemented 2026-10-03. Decision: D-27 (LangChain Deep Agents is the base harness),
fork policy LOCUS-363 (pinned upstream, Locus code in an in-repo extension package).
Measured scorecard: [`runtime-bakeoff-2026-10.md`](runtime-bakeoff-2026-10.md), section 9.

`create_runtime()` still defaults to `verified-loop`. `deep-agents` is selectable
(`create_runtime("deep-agents")` or `LOCUS_AGENT_RUNTIME=deep-agents`) and is built for
production use. Flipping the default is a separate decision (D-27: when the extended runtime
matches or beats the verified loop on the scorecard).

## 1. Layout

| Module | Role |
|---|---|
| `locus_runtime/harness/deep_agents/library.py` | The only importer of `deepagents` / `langchain*` / `langgraph` / `langsmith` for the runtime. Forces LangSmith off before and after import; refuses an installed stack that differs from `AUDITED_VERSIONS`. |
| `.../deep_agents/middleware.py` | `GatedChatModel`, `GatedTool` and the three Locus middlewares (gateway, turn, compaction). |
| `.../deep_agents/compaction.py` | The compaction policy (pure). |
| `.../deep_agents/prompts.py` | Trimmed todo / sub-agent prompts and tool descriptions. |
| `.../deep_agents/runtime.py` | `DeepAgentsRuntime` (port `AgentRuntime` 1.0): graph assembly, drive loop, asks, resume. |
| `locus_runtime/harness/runtime_controller.py` | `RunController`: the verified loop's code with the driving taken out (guards, accounting, plan, verify gate, end states), now durable. |
| `locus_runtime/harness/run_store.py` | SQLite run state + action ledger (stdlib only). |
| `locus_runtime/hosted_tracing.py` | `force_langsmith_off()` (stdlib only), called by the backend, the desktop sidecar and the runtime. |

## 2. How the verified loop's guarantees map onto Deep Agents

| Guarantee | Where | Mechanism |
|---|---|---|
| Envelope (goal, done criteria, budget, tier) | `RunController.initial_message()` | The verified loop's envelope message, naming `write_todos` as the plan tool. |
| Budgets: steps, tokens, time, cost, context, user stop | `LocusGatewayMiddleware.before_model` and `RunController.model_turn` | `guard_model_call()` raises the end state (`stopped`, kind `budget`/`user`). Actions: `_guard_before_action` per tool call, `budget_policy` reported to the gateway first. |
| Every model call gated | `GatedChatModel._generate` | `RunController.model_turn` -> `GatedChatClient` -> gateway `model_call`. No `init_chat_model`, no provider SDK client. |
| Every side effect gated | `LocusGatewayMiddleware.wrap_tool_call` | Locus tools run through `RunController.tool_call` -> `CodingToolset.dispatch` on the run's gateway session. |
| Only mediated tools offered | `wrap_model_call` and `GatedChatModel` | Filtered twice: in the middleware and again at the model boundary, whatever an inner middleware adds. |
| Plan | `wrap_tool_call` | `write_todos` is recorded as a versioned run plan (P17). |
| Verify gate + done-criteria judge | `submit` (a Locus tool) | Runs `verification.verify` and the acceptance judge through the controller; only a passing gate ends `done`. |
| Text turn | `LocusTurnMiddleware.after_model` | The loop's nudge (or its end state), then `jump_to: model` inside the graph. |
| End states done / blocked / stopped | `RunController` | Raised as the loop's `_RunEnded` (a `BaseException`), so framework `except Exception` handlers cannot swallow it; a framework crash ends `blocked` (`runtime_error`). |
| Ask -> interrupt -> resume, exactly once | `wrap_tool_call` + `DeepAgentsRuntime._decide` | A gateway ask raises a LangGraph `interrupt` carrying the asks. No approver: `blocked`. Approver: single-use approval in the gateway ledger, `Command(resume=...)`, the call re-runs once (the ledger marks the asked call so the re-run executes and nothing else repeats). |
| Telemetry | `RunController.drive` / `model_turn` / `tool_call` | Same spans as the verified loop (`invoke_agent`, `chat`, `execute_tool`, `gateway`, `sandbox`, `gate`); framework threads re-enter the run's trace. Tested by `tests/harness/test_telemetry_trace.py` for both runtimes. |

## 3. Hardening

- **Built-in file tools neutralized.** `FilesystemMiddleware` is protected scaffolding and must
  keep `read_file`. Locus passes its own instance (`tools=["read_file"]`, in-memory
  `StateBackend`, no eviction), hides `read_file` from the model and refuses it on call, like
  every other unknown tool (`[not executed] ...`). An explicit `general-purpose` sub-agent spec
  replaces the default one, so no sub-agent stack runs without the Locus middleware.
- **LangSmith never on.** `force_langsmith_off()` runs first in `app.main` and in the desktop
  sidecar's `main()`, and again in `LangChain.load()` (before and after import: env scrub,
  `get_env_var.cache_clear()`, `langsmith.configure(enabled=False)`); every run is also wrapped
  in `tracing_context(enabled=False)`. The opt-in OTLP export to a LangSmith endpoint
  (LOCUS-375) is a separate channel and takes its key from native secrets only.
- **Vendor SDKs stay in the runtime module.** `deepagents` hard-depends on
  `langchain-anthropic` / `anthropic` (imported by `import deepagents`) and
  `langchain-google-genai` / `google-genai` (installed, not imported on Locus paths). Nothing
  else imports them; the backend imports no vendor SDK until `create_runtime("deep-agents")`
  (tested in a clean interpreter, with a positive control). They never get credentials or a
  model object: the only model is the gated one.
- **No egress except the gated model client.** Tested with a real loopback endpoint and a
  socket + DNS guard during a run, with LangSmith switched on in the environment: the only
  connections are the gated model client's, to the model endpoint.
- **Audited versions only.** `AUDITED_VERSIONS` must equal the installed versions, else the
  runtime is unavailable. Bumping a pin means re-running the audit in section 6.

## 4. Durable runs

`RuntimeRequest.checkpoint_path` names the run database (production:
`run_store.default_run_db_path()`, i.e. `<app_home>/data/runs/runs.db`, or `LOCUS_RUNS_DB`).
One SQLite file in WAL mode holds the LangGraph `SqliteSaver` tables (`checkpoints`, `writes`),
`locus_run_state` and `locus_action_ledger`. Without a path the run is in memory only
(evals, tests).

- `run()` with a known run id resumes it: a finished run is returned as is; a pending ask is
  presented to the approver again (approvals are never carried across a restart); otherwise
  the graph continues from its last checkpoint.
- Tool calls are written to the ledger as `started` before they run. On resume a `done` call
  is replayed from the ledger; a `started` call (the process died while it ran) is never run
  again and the agent is told its outcome is unknown. Side effects are therefore at most once;
  an approved action runs exactly once unless the process dies inside it.
- A resume with a different envelope ends `blocked` (configuration).
- The checkpoint DB holds transcripts and tool output (secret reads are already masked, P10)
  and LangGraph's serialized state; it is app-home data with the same trust as the verified
  loop's JSON checkpoints. LangGraph's default serializer reads it back (`JsonPlusSerializer`,
  `pickle_fallback=False`, restricted msgpack/JSON module allow-lists).

## 5. Token diet

| Change | Effect per model request |
|---|---|
| Todo system prompt: upstream's ~300-token guide replaced by two lines (it also told the agent to finish with prose; Locus finishes with `submit`) | about -290 tokens |
| `write_todos` description: ~1,000 tokens -> one sentence | about -950 tokens |
| `task` description and parameter descriptions trimmed (harness profile override + the schema shown in `wrap_model_call`) | about -250 tokens |
| `SummarizationMiddleware` excluded (harness profile); replaced by deterministic compaction | no hidden summarization calls |
| Compaction: tool output hard cap 12,000 chars; all but the newest 4 outputs cut to 1,500 chars (400 when the request is over 80,000 chars) | grows with run length |

Contract-fixture first request: verified loop ~1,020 tokens, Deep Agents ~2,740 before and
~1,200 after (1.17x). Measured on the bake-off suite: section 9 of the bake-off page.

## 6. Re-audit on every Deep Agents / LangChain bump (checklist)

1. Diff `create_deep_agent`'s middleware assembly and the protected scaffolding set.
2. Check which tools each middleware adds and that `LocusGatewayMiddleware` still sees every
   tool call (unknown names included).
3. Check new imports at `import deepagents` time (vendor SDKs, network clients) and any new
   environment switches that enable remote tracing or telemetry.
4. Check the harness-profile API (`excluded_middleware`, `tool_description_overrides`).
5. Run the contract suite, `tests/harness/test_deep_agents_harness.py` and the trace test, then
   update `AUDITED_VERSIONS`, `pyproject.toml` and `apps/backend/requirements.txt` together.

## 7. Patch ledger (what extension points cannot do)

Nothing is forked. Cases where upstream's extension points fall short, and what Locus does:

| # | Gap in deepagents 0.7.21 | Locus handling | Fork needed? |
|---|---|---|---|
| 1 | `FilesystemMiddleware` cannot be excluded and must register `read_file`. | Own instance over in-memory state; hidden and refused. | No |
| 2 | `import deepagents` imports `langchain_anthropic` (and so `anthropic`) unconditionally; `langchain-google-genai` is a hard dependency. | Loaded only inside the runtime module; never given credentials or used; tested isolation. Removing them needs an upstream change or a fork. | Not now (upstream issue worth filing) |
| 3 | `tool_description_overrides` cannot change a tool's parameter descriptions. | The `task` schema shown to the model is rewritten in `wrap_model_call`; execution unchanged. | No |
| 4 | Harness profiles are a process-global registry keyed by provider. | The gated model reports provider `locus`; the profile is registered (idempotently) when the runtime is built. | No |
| 5 | A resumed node returns the earlier resume value from `interrupt()` instead of pausing for a second ask in the same node. | The middleware decides such a second ask in place (approve once or end blocked). | No |

## 8. Dependencies (P28 / D-29, P29)

Exact pins in `pyproject.toml`: `deepagents==0.7.21`, `langchain==1.4.3`,
`langchain-core==1.6.6`, `langgraph==1.2.12`, `langgraph-checkpoint==4.2.0`,
`langgraph-checkpoint-sqlite==3.1.1`. Dropped: `langgraph-checkpoint-postgres` (never imported).

New transitive packages (resolved 2026-10-03):

| Package | Version | License | Origin / maintainer of record | Pulled by | Note |
|---|---|---|---|---|---|
| deepagents | 0.7.21 | MIT | LangChain Inc. (US) | platform | the harness |
| langchain | 1.4.3 | MIT | LangChain Inc. (US) | deepagents | agent graph, middleware |
| langchain-protocol | 0.0.19 | MIT | LangChain Inc. (US) | langchain-core, langgraph-sdk | |
| langgraph-checkpoint-sqlite | 3.1.1 | MIT | LangChain Inc. (US) | platform | durable checkpointer |
| aiosqlite | 0.22.1 | MIT | Amethyst Reese (individual; omnilib project) | checkpoint-sqlite | async saver only; unused |
| sqlite-vec | 0.1.9 | MIT OR Apache-2.0 | Alex Garcia (individual, US); wheel metadata says "TODO" | checkpoint-sqlite | **D-29 inspection**: native SQLite extension with placeholder metadata; loaded only by LangGraph's `SqliteStore`, which Locus does not use |
| langchain-anthropic | 1.7.5 | MIT | LangChain Inc. (US) | deepagents | imported, unused |
| anthropic | 1.11.0 | MIT | Anthropic (US) | langchain-anthropic | imported, unused |
| docstring-parser | 0.18.0 | MIT | individual (PL) | anthropic | |
| langchain-google-genai | 4.4.0 | MIT | LangChain Inc. (US) | deepagents | not imported by Locus paths |
| google-genai | 2.28.0 | Apache-2.0 | Google LLC (US) | langchain-google-genai | |
| google-auth | 2.59.1 | Apache-2.0 | Google LLC (US) | google-genai | |
| pyasn1 | 0.6.4 | BSD-2-Clause | Ilya Etingof (individual) per metadata | google-auth | **D-29 inspection** (flagged in LOCUS-348): maintainer origin not established from metadata |
| pyasn1-modules | 0.4.2 | BSD-2-Clause | as pyasn1 | google-auth | **D-29 inspection**, with pyasn1 |
| filetype | 1.2.0 | MIT | Tomas Aparicio (individual, ES) | langchain-google-genai | |
| truststore | 0.10.4 | MIT | Seth Larson, David Glick (individuals, US) | google-genai | |
| httpx2, httpcore2 | 2.13.1 | BSD-3-Clause | encode / Tom Christie (UK) | anthropic, langsmith, openai | |

Required bumps of packages already present: langchain-core 0.3.83 -> 1.6.6, langgraph 0.6.11 ->
1.2.12, langgraph-checkpoint 3.0.1 -> 4.2.0, langgraph-prebuilt 0.6.5 -> 1.1.0, langgraph-sdk
0.2.15 -> 0.4.5, langsmith 0.7.22 -> 0.14.4 (all MIT, LangChain Inc.). All licenses are
permissive and AGPL-compatible. No package is from an excluded jurisdiction as far as the
metadata shows; pyasn1/pyasn1-modules and sqlite-vec need the D-29 inspection.

Upgrading an existing dev venv (the old `langgraph-checkpoint-postgres` and the undeclared
`langchain-openai` 0.3 cannot coexist with langchain-core 1.x):

```bash
python -m pip uninstall -y langgraph-checkpoint-postgres langchain-openai
python -m pip install -e ".[dev]"
```
