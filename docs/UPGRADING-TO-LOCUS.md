# Upgrading from xFrontier to Locus

The product formerly called **xFrontier** is now **Locus**. Every code identifier was renamed: repo `lattix-locus`, Python package `lattix-locus`, modules `locus_runtime` / `locus_tooling` / `locus_evals`, the Helm chart `helm/lattix-locus`, node types `locus/*`, graph schema `locus-graph/1.0`, environment variables `LOCUS_*`, and HTTP headers `X-Locus-*`.

Existing installs upgrade in place. The compatibility shims live in [`locus_runtime/legacy.py`](../locus_runtime/legacy.py) and are covered by `tests/unit/test_legacy_compat.py`.

## Migrated automatically

| Pre-rename | Now | How |
| --- | --- | --- |
| `FRONTIER_*` env vars, `NEXT_PUBLIC_FRONTIER_ACTOR` | `LOCUS_*`, `NEXT_PUBLIC_LOCUS_ACTOR` | Aliased at import of `locus_runtime`, the backend, workers and `next.config.ts`. An explicit `LOCUS_*` value wins |
| Keys in `.env` and `.installer/*.env` | `LOCUS_*` keys | `lattix update` / re-running the bootstrap rewrites keys in place; values are untouched |
| Tables `frontier_state_store`, `frontier_audit_events`, `frontier_long_term_memory`, `frontier_memory_consolidation_queue`, `frontier_kg_nodes`, `frontier_kg_edges` | `locus_*` | Renamed in place on first start (Postgres and SQLite), only when the new table does not exist yet |
| Stored `frontier/*` node types and `frontier-graph/*` schema | `locus/*`, `locus-graph/*` | Normalized when state is loaded and when definitions are imported |
| App home `…/Lattix/xFrontier` (desktop and native) | `…/Lattix/Locus` | The old directory is used when it exists and the new one does not |
| `.frontier/runtime-state.json` | `.locus/runtime-state.json` | Same fallback rule |

## Deliberately unchanged, to keep your data

- **Docker volumes** in `docker-compose.local.yml` keep their `frontier_*` names.
- **Postgres defaults** stay `frontier`, for the database, role and local password. Existing data volumes were initialised with these values.
- **Helm.** Releases installed before the rename must set `nameOverride: lattix-frontier` and `fullnameOverride: lattix-frontier`. That keeps Deployment selectors and StatefulSet PVC names stable. New releases leave both empty.

## Not migrated (one-time effect)

- **Operator session cookie.** It is now `locus_operator_session`, so everyone signs in once more after upgrading.
- **Browser preferences** (theme, classification-banner preset). These now use `locus-*` local-storage keys, so they reset to defaults.
- **Signed A2A and runtime headers.** These are now `X-Locus-*`. Upgrade every agent service together with the backend; a mixed-version fleet rejects each other's requests.
- **Desktop app identifier.** It is now `com.lattix.locus`, so the Locus desktop app installs alongside the old xFrontier app. Uninstall the old app once your data has moved over (see App home above).
