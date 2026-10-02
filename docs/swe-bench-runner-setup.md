# SWE-bench Verified on a runner (Option B)

Run the shipped **Full-Stack SDET agent** against real SWE-bench Verified
instances on a dedicated runner (Docker + a model endpoint), keeping the load
off the 32 GB dev box. The benchmark is a direct agent↔model loop; it does not
route through the deployed xFrontier control plane.

## Runner prerequisites

- **Docker** (for per-instance SWE-bench images). The runner pulls
  `swebench/sweb.eval.x86_64.<instance>:latest` per instance.
- **A model endpoint** serving gpt-oss-20b over an OpenAI-compatible API:
  - **vLLM (recommended)** — faster, batches best-of-N, harmony-capable:
    ```bash
    vllm serve openai/gpt-oss-20b --port 8000 --max-model-len 131072
    ```
  - or **Ollama** — `ollama serve` with `gpt-oss:20b` pulled (works; slower).
- **Python 3.12** with the harness + evals + swebench extra:
  ```bash
  pip install -e .                       # repo root: frontier_runtime + harness
  pip install -e "apps/evals[swebench]"  # frontier-evals + datasets + swebench
  ```

## Run it

```bash
# 1) pick a reproducible subset from the dataset (prints real instance ids)
frontier-evals list-instances --limit 20 > /tmp/ids.txt

# 2) drive the SHIPPED SDET agent against SWE-bench Verified
frontier-evals run \
  --mode live --dataset swe-bench \
  --agent sdet-swe-agent \
  --api-base-url http://localhost:8000/v1 \
  --model openai/gpt-oss-20b --provider vllm \
  --docker-host unix:///var/run/docker.sock \
  --instance-ids "$(paste -sd, /tmp/ids.txt)" \
  --seeds 0,1,2 --max-steps 40 --threshold 0.30 \
  --output-dir eval-results/gpt-oss-20b-swebench
```

- `--agent sdet-swe-agent` makes the shipped agent's system prompt + capability
  profile (`local-32b-class`: native tool-calling, search-replace edits, temp
  0.2) drive every instance — the agent you see in Agent Studio is the agent
  scored.
- `--model` / `--api-base-url` / `--provider` point at the runner's endpoint
  (the agent config's `gpt-oss:20b` Ollama tag is just a default; override here).
- Grading is the official `swebench.harness` (FAIL_TO_PASS / PASS_TO_PASS by
  test execution). Exit code is `0` iff mean resolve rate ≥ `--threshold`.

## Output

```
eval-results/gpt-oss-20b-swebench/
  summary.json     # mean ± SEM, pass@k, per-instance, per-seed
  report.md
  instances/<id>/seed-<k>/{trajectory_<id>.jsonl, result.json}
```

The trajectories are the iteration fuel: read failures, harden the harness,
re-run — the same loop that took the synthetic suite from one flaky miss to
100%.

## Multi-agent workflow path (the cross-functional team)

The reactflow canvas now compiles a workflow's `graph_json` into a real LangGraph
`StateGraph` (`apps/backend/app/graph_compiler.py`). The shipped
**cross-functional-development** workflow (`215ee3c5-496d-5a7e-bd0d-72addb33c42f`)
runs the facilitated round-table: Tech Lead → backend/frontend/SDET/security/
devops/perf debate to a **bounded consensus loop**, then the **build node delegates
to the same `SweAgent`** the SWE-bench gate scores, then a verify/gate loop hands
back a completed feature. Every agent node uses its Agent-Studio system prompt and
`gpt-oss:20b` on the configured endpoint.

Two ways to score the team (both belong on the runner — model + git + the repo
under test):

```bash
# A) the team as a unit, via the collaborate CLI (one spec -> a built+tested feature)
frontier-evals collaborate --repo /path/to/target-repo --spec @spec.md \
  --api-base-url http://localhost:8000/v1 --model openai/gpt-oss-20b --provider vllm \
  --base-ref main --isolation worktree --allow-outside deny

# B) the deployed workflow end to end: POST /graph/runs with the workflow graph_json
#    and input.workspace = {repo_path, base_ref, test_command, allow_outside}. The
#    backend compiles + runs it; the build node provisions a git worktree and edits
#    real files. Requires the backend image to include git (now baked in Dockerfile)
#    and the backend pointed at the model endpoint (set the ollama provider base_url
#    in /builder/settings to the host/runner, or pull gpt-oss:20b into the ollama
#    sidecar). The single-agent SWE-bench gate above remains the headline score; the
#    workflow path is graded on the build node's produced patch the same way.
```

The headline ≥30% gate is `tests/evals/test_deepswe_eval.py` — it drives the
implementer `SweAgent` directly (FAIL_TO_PASS/PASS_TO_PASS grading). In
`plumbing` mode it runs anywhere (reference solver, no GPU); set
`FRONTIER_EVALS_MODE=live` + `FRONTIER_EVALS_API_BASE_URL` + `FRONTIER_EVALS_MODEL`
+ `DOCKER_HOST` on the runner to enforce gpt-oss-20b ≥ 30% on SWE-bench Verified.

## Tuning toward a higher score (after a baseline lands)

1. Serve with **vLLM** and enable **harmony** fidelity for gpt-oss (biggest
   single lever; the `gpt-oss-harmony` profile encodes it — drop the
   `capability_profile` override on the agent or pass `--profile gpt-oss-harmony`).
2. Enable **grammar-constrained** tool calls (XGrammar) once on vLLM.
3. Add **best-of-N + execution verification** (planned) — DeepSWE's +17-pt lever.
