# Lattix xFrontier Quality Score

Quality here means a reviewer can tell what changed, what enforced it, what evidence backs it, and whether the security posture still holds. This document defines the evidence expected on every change. It does not replace `AGENTS.md` — it makes the shared standard measurable for this repo.

## Quality dimensions

| Dimension | Evidence source | Expected signal |
| --- | --- | --- |
| Test correctness | `make test` → `pytest apps/backend/tests tests`, `npm test` in `apps/frontend/` | Behavior changes ship with tests; the suite must at minimum collect |
| Policy enforcement | `make policy-test` → `python scripts/run_opa.py test policies/` (28 tests) | Every `policies/*.rego` change has a matching test in `policies/tests/` |
| Type safety | `make typecheck` → `mypy frontier_tooling/ frontier_runtime/` | Gated scope stays at zero errors under `strict` |
| Lint and format | `ruff check .`, `ruff format --check .`, `eslint` | No new lint findings; new files land already formatted |
| Runtime security posture | `frontier_runtime/security.py`, `apps/backend/app/request_security.py`, `SECURITY.md`, `THREAT-MODEL.md` | Auth, replay, capability, and policy paths fail closed and are tested |
| Isolation correctness | `frontier_runtime/sandbox.py`, `tests/unit/test_sandbox_policy.py`, `test_tool_jail.py`, `docs/SANDBOXING.md` | Strategy selection is explicit per host platform (bwrap / seatbelt / Windows AppContainer / hardened Docker); no silent downgrade to a weaker tier |
| Contract stability | `packages/contracts/templates/*.schema.json`, `tests/unit/test_docs_contract.py`, `test_version_contract.py`, `test_tooling_contract.py` | Schema/API/CLI changes carry compatibility evidence |
| Deployment integrity | `make helm-validate`, `docker compose config`, `tests/unit/test_helm_security_contract.py`, `test_compose_auth_contract.py`, `test_container_image_contract.py` | Compose and Helm stay renderable and keep their security contract |
| Installer safety | `tests/unit/test_installer.py`, `test_public_installer.py`, `test_install_diagnostics.py`, `docs/INSTALLER.md` | Update/remove paths stay non-destructive; state manifest versioning preserved |
| Observability | `structlog`, OpenTelemetry exporters, `/observability/*`, `/audit/*` | New failure modes get structured logs, spans, or audit events |
| Documentation coverage | `README.md`, `docs/`, `.ai-memory/repo-profile.md` | Behavior, config, and operational changes update docs in the same change set |

## Current measured signals

Measured 2026-08-10 against `main`. **Several gates are currently red.** Re-measure before quoting these.

| Gate | Result |
| --- | --- |
| `pytest` | **RED** — collection error: `tests/harness/test_swe_agent_e2e.py` raises `ModuleNotFoundError: No module named 'tests.harness'` (no `tests/__init__.py`) |
| `ruff check .` | **RED** — 24 errors, including a genuine `F821 Undefined name 'platform'` |
| `mypy frontier_tooling/ frontier_runtime/` (the gated scope) | **RED** — 38 errors in 12 files |
| `mypy apps/backend` (not gated) | 491 errors in 26 files |
| OPA policy tests | **28/28 pass** |
| Last desloppify scan (2026-03-23, stale) | overall 77.5, objective 84.7, 355 open items |

## Known quality debt

Track these; do not treat a green gate as full coverage.

- **`apps/backend/app/main.py:1587` calls `platform.system()` without importing `platform`.** A latent `NameError` on that path. Caught by `ruff` as `F821`.
- **`pytest` cannot collect the harness suite.** `tests/harness/` has an `__init__.py` but `tests/` does not, so `tests.harness` is not importable. This aborts the whole run before any test executes.
- **The gated typecheck scope is red.** `make typecheck` covers only `frontier_tooling/` and `frontier_runtime/`, and even that reports 38 errors.
- **`apps/backend/` is not type-checked at all.** 491 `mypy --strict` errors across 26 files. `lattix typecheck` narrows further still, to `frontier_tooling` alone.
- **`ruff format` is not gated.** CI runs `ruff check` only.
- **`apps/backend/app/main.py` is 21,228 LOC** — 139 routes in one file. Desloppify flagged it at 10,112 LOC; it has more than doubled since.
- **Control-plane persistence swallows failures.** `_persist_store_state()` wraps its work in a bare `try`, so a missing or unreachable Postgres can lose definitions on restart without a signal. The A2A nonce path is the one place that correctly returns `503`.
- **`budget_policy.rego` has no test file**; every other policy does.
- **Two isolation strategies are enum-only.** `k8s-gvisor` and `k8s-kata` have no implementing strategy class.
- **Microsoft Agent Framework is code generation, not execution.** `generated_artifacts.py` emits `agent_framework` source; nothing imports or runs it.

## Review cadence

- Re-measure the signals above before any release or posture claim.
- Re-run `make policy-test` whenever `policies/` changes.
- Re-run `make helm-validate` and compose config validation whenever `helm/`, `docker-compose*.yml`, or `envoy/` changes.
- Re-run the installer test set whenever `install/`, `frontier_tooling/installer.py`, or `frontier_runtime/install.py` changes.
- Re-scan with desloppify when the score is older than a release cycle.
