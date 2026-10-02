# xFrontier coding harness (`frontier_runtime.harness`)

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
| `executor.py` | Where tools run: `LocalDirectExecutor` (dev/CI), `LocalSandboxExecutor` (bwrap/seatbelt/docker via `frontier_runtime.sandbox`), `DockerContainerExecutor` (SWE-bench instance container on a remote `DOCKER_HOST`). |
| `workspace.py` | Repo root + git diff/reset/run-tests helpers (host-side git keeps the sandbox `.git` invariant). |
| `tools.py` | The fixed tool set: `execute_bash`, `search`, `str_replace_editor`, `run_tests`, `submit`. Exact-match edits, well-formed-edit telemetry, auto-downgrade to whole-file. 50KB/2000-line output truncation. |
| `enforcement.py` | Tool-call validation (envelope), bounded re-ask, grammar-constrained decoding kwargs (XGrammar/GBNF). |
| `llm.py` | `ChatClient` protocol; `OpenAIChatClient` (any OpenAI-compatible endpoint); `ScriptedChatClient` (tests). |
| `loop.py` | The agent loop: append-only messages, `submit` termination, hard budgets with submit-or-zero, self-repair, trajectory recording. |
| `trajectory.py` | Lossless JSONL trajectory (header + verbatim messages + annotations + outcome); replayable / SFT-ready. |
| `swe_agent.py` | Assembles the above into `SweAgent.solve(SweTask) -> SweAgentResult` (produces the unified-diff prediction). |

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
from frontier_runtime.harness import (
    SweAgent, SweTask, LocalDirectExecutor, resolve_profile,
)
from frontier_runtime.harness.llm import OpenAIChatClient

client = OpenAIChatClient(model="gpt-oss-20b",
                          base_url="http://runner:8000/v1", provider="vllm")
agent = SweAgent(client=client, profile=resolve_profile("vllm", "gpt-oss-20b"))
task = SweTask(instance_id="demo", problem_statement="...",
               executor=LocalDirectExecutor("/path/to/repo"),
               test_command="python -m pytest -q")
result = agent.solve(task)
print(result.outcome, result.has_patch)
print(result.patch)          # unified diff = the graded prediction
print(result.telemetry)      # well_formed_edit_rate, reasks, …
```

Benchmarking (DeepSWE/SWE-bench) is driven by `apps/evals` — see its README.
