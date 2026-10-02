# Performance & Resource Tooling

Companion to [../resource-efficiency-plan.md](../resource-efficiency-plan.md). Budgets:
local-first idle ≤ 2 GB total, backend cold start ≤ 3 s, persistence O(changed section).

## Capture a baseline

```
make resource-baseline
```

Writes a timestamped snapshot (host RAM, per-process RSS, one `docker stats` sample)
to `docs/perf/baselines/`. Capture one before and one after any perf-relevant change.

## Hard rules for local machines

- **Never run the full test suites or multiple dev servers locally.** A full
  vitest run previously forked one node worker per CPU core and exhausted a
  32 GB host. Worker caps now live in `apps/frontend/vitest.config.ts` — keep them.
- Verify changes with focused tests only:
  - frontend: `npx vitest run src/lib/api.spec.ts` (single file)
  - backend: `pytest apps/backend/tests/test_generated_artifacts.py::<test_name> -q`
- Operators should run the frontend production bundle: `make frontend-serve`
  (the webpack dev server costs 1–2 GB and is for active UI development only).

## Profiling one-liners

Backend CPU (attach to a running uvicorn, 30 s flame graph):

```
pip install py-spy
py-spy record -o profile.svg --pid <uvicorn-pid> --duration 30
```

Backend memory (allocation tracking for one run):

```
pip install memray
python -m memray run -o backend.bin -m uvicorn app.main:app --app-dir apps/backend
python -m memray flamegraph backend.bin
```

Container memory over time:

```
docker stats          # live
make resource-baseline  # snapshot to file
```

## Local stack profiles

- **Zero-container mode**: leave `POSTGRES_DSN` unset and set
  `FRONTIER_SQLITE_STATE_PATH=.frontier/state.db`, then run uvicorn directly —
  state and audit persist to SQLite with no containers at all.
- `make local-up` — backend + frontend + postgres + redis (Neo4j excluded).
- Neo4j world-graph: `docker compose --profile graph -f docker-compose.local.yml up`
  and set `NEO4J_URI=bolt://neo4j:7687`. The JVM is capped (512 MB heap / 256 MB page cache).
- All local services carry `mem_limit` caps so a leak OOMs one container, not the host.
