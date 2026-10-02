# Lattix xFrontier Reliability

xFrontier reliability depends on three things: control-plane state surviving restart, runtime isolation never degrading silently, and long-running graph execution remaining observable and replayable.

## Dependencies

| Dependency | Role | Degradation behavior |
| --- | --- | --- |
| PostgreSQL | Control-plane state snapshot, long-term memory (`pgvector`), LangGraph checkpoints | **Currently silent** — snapshot write failures are swallowed; see failure modes |
| Redis | Short-term/session memory, A2A replay-nonce cache | Falls back to the Postgres state snapshot for nonces; **fails closed** with `503` if neither is available |
| Neo4j | Internal world graph for consolidated memory | Feature-flagged off by default; projection is skipped when disabled |
| Vault | Secret storage and installer-managed config mirroring | Backed by the durable `vault-data` volume |
| OPA | Policy decisions for agents, tools, egress, filesystem, classification | Must fail closed — an unavailable PDP is a deny, never an allow |
| NATS | Event transport for worker runtime | Event bus is rate-limited; local fallback store exists |
| Envoy + local gateway | Ingress and `/api/*` proxying in the secure stack | Full stack only; the lightweight stack talks to the backend directly |
| Sandbox egress gateway | Controlled outbound path for sandboxed tools | Full stack only |
| Jaeger / OTel | Tracing | Optional; absence must not change request behavior |
| OpenAI (or configured provider) | Model execution for `frontier/agent` and framework engines | Provider readiness is reported through `/runtime/providers`; missing key degrades to a reported-unavailable state, not a crash |

## Failure modes

- **Control-plane state loss on restart.** `_persist_store_state()` catches and discards every exception. With `POSTGRES_DSN` unset or Postgres unreachable, the platform runs normally and loses all workflow, agent, and guardrail definitions on restart, with no log line. This is the highest-severity known reliability gap.
- **Replay-state unavailability.** Correctly handled: the A2A nonce path raises `503 A2A replay state persistence unavailable` rather than accepting unverifiable traffic.
- **Silent isolation downgrade.** A host where the intended strategy is unavailable must not fall back to a weaker tier without an explicit, logged, opt-in control. `restricted-process` is gated behind `FRONTIER_ALLOW_RESTRICTED_PROCESS_SANDBOX`, and Windows AppContainer confinement is fail-closed rather than downgrading to a bare Job Object.
- **Unimplemented Kubernetes isolation.** `k8s-gvisor` and `k8s-kata` exist as `IsolationStrategy` values with no implementing strategy. Selecting them must not resolve to something weaker.
- **Stream interruption.** Run progress streams over SSE (`text/event-stream`). A dropped stream must not be indistinguishable from a completed run; clients need an explicit terminal event or a reconnect path.
- **Non-native engine absence.** LangGraph, LangChain, Semantic Kernel, and AutoGen are optional imports. A missing module must produce a clear provider-status result, not a partially executed graph.
- **Memory consolidation drift.** Consolidation, decay, dedup, and world-graph projection are all feature-flagged off. Enabling them mid-run changes retrieval behavior for in-flight sessions.
- **The test suite does not currently collect.** `tests/harness` is unimportable (`tests/` lacks `__init__.py`), so `pytest` aborts before running anything. Until fixed, there is no automated regression signal at all.

## Reliability expectations

- State-changing endpoints are idempotent, or document their compensation path.
- Every external call has a timeout and a stable error code. No unbounded retries, queues, or concurrency.
- Auth, policy, parsing, replay, and isolation decisions **fail closed**. An unavailable dependency is a deny.
- Persistence failures on the control-plane path must be surfaced — logged at minimum, and returned to the caller when the write was part of the request contract.
- Isolation strategy selection is explicit and logged per execution. Never downgrade tiers implicitly.
- Feature flags default to the safe value. Enabling a memory or engine flag is an operator decision, not a runtime inference.
- Runs are replayable from persisted events. The hash-chained event log (`frontier_runtime/events.py`) is the audit spine — do not bypass it.

## Runtime profiles and posture

| Profile | Auth required | Signed A2A headers | Intended use |
| --- | --- | --- | --- |
| `local-lightweight` | no | no | Quick local iteration only. **This is the default when `FRONTIER_RUNTIME_PROFILE` is unset.** |
| `local-secure` | yes | no | Secure local/full stack; pinned by `docker-compose.yml` |
| `hosted` | yes | yes | Non-local deployment; pinned by the Helm chart |

Any deployment that is not a developer laptop must pin `local-secure` or `hosted` explicitly. Relying on the default is a misconfiguration.

## Recovery

- Postgres, Redis, Neo4j, NATS, and Vault each use their own named volume; do not delete volumes as part of routine teardown.
- `lattix update` / `make update` refresh in place and preserve `.installer/` env files and volumes.
- `lattix remove` / `make remove` tear down stacks and delete installer-managed env files. It leaves the checkout and `.env` intact. Treat it as destructive to local platform state.
