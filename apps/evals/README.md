# frontier-evals — DeepSWE / SWE-bench evaluation harness

Drives `frontier_runtime.harness.SweAgent` over a dataset, grades each produced
patch **by test execution only**, runs multiple seeds, and reports the mean
resolve rate ± SEM (SWE-rebench protocol). The headline gate is **gpt-oss-20b ≥
30 % on DeepSWE/SWE-bench Verified**.

## Modes

### Plumbing (CI / any machine — no GPU, no Docker)
Synthetic in-repo bug-fix tasks materialized as real git repos, solved by a
deterministic reference solver. Validates the whole pipeline (materialize →
agent loop → execution grading → stats → report) and clears the 30 % gate end
to end.

```bash
# from the repo root
python -c "import sys; sys.path.insert(0,'apps/evals'); from frontier_evals.cli import cli; cli()" smoke
# or, installed:  frontier-evals smoke
```

The automated gate test runs this in CI: `tests/evals/test_deepswe_eval.py`.

### Live (remote runner with a GPU + Docker)
The model under test (gpt-oss-20b) served by vLLM, real SWE-bench instances in
Docker. **Never run a fleet on the local dev box** — the config refuses a
>2-instance live run against localhost (the `resource-constrained-local-testing`
rule, enforced in code). Point it at a runner.

```bash
# on the runner box (GPU): serve the model
vllm serve openai/gpt-oss-20b --port 8000 --max-model-len 131072
#   (gpt-oss needs harmony-aware serving + its in-distribution tools; see
#    frontier_runtime/harness/model_profiles.py gpt-oss profiles)

# drive the benchmark (from anywhere that can reach the runner)
frontier-evals run \
  --mode live --dataset swe-bench \
  --api-base-url http://runner:8000/v1 --model openai/gpt-oss-20b \
  --provider vllm --docker-host tcp://runner:2376 \
  --seeds 0,1,2,3,4 --threshold 0.30 \
  --output-dir eval-results/gpt-oss-20b-deepswe
```

`--instance-ids` (or `FRONTIER_EVALS` ids) selects the SWE-bench subset. Exit
code is `0` iff the mean resolve rate meets `--threshold`.

## What each module does

| Module | Role |
| --- | --- |
| `config.py` | `EvalConfig` + the remote-runner guardrail. |
| `datasets.py` | `synthetic-mini` (materialized repos) + `swe-bench` (Docker-backed instances, statements from `princeton-nlp/SWE-bench_Verified`). |
| `model_client.py` | live `OpenAIChatClient`; plumbing reference/no-op solvers. |
| `docker_env.py` | boot/clean per-instance SWE-bench containers on a remote `DOCKER_HOST`. |
| `grading.py` / `swebench_grader.py` | execution grading; live grading defers to the official `swebench.harness`. |
| `stats.py` | per-seed resolve rate, mean ± SEM, pass@k. |
| `runner.py` | orchestrates instance × seed → agent → grade → stats → report. |
| `report.py` | `summary.json` + `report.md`. |

## Results layout

```
<output-dir>/
  summary.json            # config + summary + every InstanceResult
  report.md
  instances/<id>/seed-<k>/trajectory_<id>.jsonl   # replayable trajectory
  instances/<id>/seed-<k>/result.json
```

## Improving the gpt-oss-20b score toward / past 30 %

The harness already implements the highest-impact levers (see
`frontier_runtime/harness/README.md`). To push the live number:

1. Serve gpt-oss with **harmony** fidelity (the `gpt-oss-harmony` profile) — the
   single biggest lever; generic Chat-Completions serving leaves ~30 pts on the
   table.
2. Turn on **grammar-constrained** tool calls (vLLM XGrammar) via the profile's
   `structured_output`.
3. Tune budgets (`--max-steps`, context) — 32B-class models plateau ~32K context.
4. Add **best-of-N + execution verification** (planned M6) — DeepSWE's +17-pt
   lever; nearly free under vLLM batching.
