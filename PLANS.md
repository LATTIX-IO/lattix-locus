# Lattix xFrontier Plans

Active plan areas and the evidence each must produce before it counts as done. Issues are tracked in Linear under `FRONT-*`; `TODO.md` is the generated sync view.

## Active plan areas

| Area | Status | Notes |
| --- | --- | --- |
| Cognitive MVP foundation | Landed | Goal/evidence/assembly/commitment columns in `frontier_runtime/cognitive.py`, wired into the graph executor |
| Restore green gates | **Urgent** | `pytest` does not collect (`tests/` lacks `__init__.py`), `ruff` reports 24 errors including `F821 Undefined name 'platform'` at `main.py:1587`, gated `mypy` reports 38 |
| Columnar cognitive expansion | Planned | Evaluation, Uncertainty, State, Decomposition, Prediction, Adaptation columns per `docs/COLOUMN_LAYER_IMPLEMENTATION_PLAN.md` |
| Backend type safety | Planned | Bring `apps/backend/` into `make typecheck`; 491 `mypy --strict` errors across 26 files today |
| Control-plane persistence | Planned | Replace the in-memory store + swallowed snapshot failures with a real repository boundary that surfaces write errors |
| `main.py` decomposition | Planned | Extract routers/services from a 21,228-LOC, 139-route module; extract opportunistically when substantially touching a domain area |
| Run streaming | Landed | SSE (`text/event-stream`); clients still need explicit stream-drop handling |
| Windows isolation | Landed | `_WindowsAppContainerStrategy` / `windows-appcontainer` tier, fail-closed |
| Kubernetes isolation tiers | Planned | `k8s-gvisor` and `k8s-kata` are enum values with no implementing strategy |
| MAF integration | Undecided | Currently a code emitter in `generated_artifacts.py`, not an execution engine — either wire it or restate the architecture |
| Memory feature rollout | Feature-flagged | Consolidation, hybrid retrieval, decay, vector/file dedup, WAL, and world-graph projection all default off; needs a staged enablement plan |
| Policy coverage | Planned | `budget_policy.rego` has no test file; every other policy does |
| Format gating | Planned | Add `ruff format --check` to CI |
| Agent standards adoption | Landed | Shared Lattix bundle `2026.05.05` installed; see `.github/agent-standards/README.md`. Not yet auto-synced — this repo is absent from the monorepo `.gitmodules` |

## Linear backlog shape

48 open `FRONT-*` issues; only 4 are leaf items (`FRONT-3` multi-framework runtime, `FRONT-4` approval/HITL, `FRONT-5` memory and context, `FRONT-6` event bus and async). The other 44 are unrefined Epics with no Story/Task/Bug children.

All four leaf issues describe capabilities that already have substantial working implementations in this repository. The backlog is out of sync with the code and needs a refinement pass before it can drive autonomous work.

## Completion evidence

Every plan area must record, before it is closed:

- API contract changes and their compatibility evidence
- schema or migration changes
- test evidence — which suites ran, in which order, and their results
- policy test results when `policies/` changed
- Helm and compose validation results when deployment surfaces changed
- observability changes: new logs, spans, metrics, or audit events
- documentation updated in the same change set
- rollout and rollback notes for anything touching security posture, isolation, persistence, or the installer
