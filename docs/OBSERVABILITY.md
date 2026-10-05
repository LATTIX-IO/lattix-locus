# AI observability (LOCUS-375)

Status: Phase 1 implemented. Module: `locus_runtime/telemetry/` (D-28 observability module, port `TelemetrySink`, `PORT_VERSION = "1.0"`).

Locus traces every agent run with **OpenTelemetry**, using the OTel **GenAI semantic conventions**, and stores the traces in a **local SQLite file** in the app home. Nothing leaves the machine unless the principal turns on an external exporter. The design takes the useful parts of Langfuse and LangSmith (run traces, cost and token accounting, scores on traces) without depending on either.

## 1. What is traced

One run is one trace. The `invoke_agent` span is its root; everything the run causes is a descendant, and every span carries `locus.run.id`.

| Span | Name | Where | Attributes (no content) |
|---|---|---|---|
| Run | `invoke_agent {agent}` | `VerifiedLoop.run` (verified-loop runtime) and `RunController.drive` (Deep Agents runtime) | `gen_ai.agent.name`, `gen_ai.conversation.id` (run id), `locus.runtime`, `locus.run.end_state`, `locus.run.verified`, steps, actions, tokens, cost |
| Model call | `chat {model}` (CLIENT) | `ModelClient.create_chat_completion` / `create_response` (every gated model call, backend chat included) | `gen_ai.provider.name` (+ deprecated `gen_ai.system`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens` / `output_tokens`, `locus.usage.cost_usd`, `server.address`, finish reasons, temperature / top_p / max_tokens, `error.type` (`provider_call_failed`, `model_call_denied`, ...), `locus.model.fallback_hop` / `fallback_from`; latency is the span duration |
| Fallback | event `locus.model.fallback` | `ModelRouter` | from tier, to tier, reason code (never the reason text) |
| Tool call | `execute_tool {tool}` | `VerifiedLoop._run_tool`, the bash-only protocol, `submit` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `locus.tool.status` (ok / error / blocked), the worst gateway outcome and highest risk class of the actions it caused, reason codes |
| Gateway decision | `gateway {action_kind}` | `Gateway.authorize` (and the not-installed deny) | outcome, risk class, policy reason **codes** only, policy version, audit id. Never the target, arguments or command |
| Sandbox exec | `sandbox exec {executable}` | every executor's `run()` after the gateway allowed it | jail tier, backend, network flag, exit code, timed out, duration. Never the command text or output |
| Verify gate | `gate verify` | `VerifiedLoop._submit` | attempt, failed criterion ids; score `verification` |
| Loop tick | `locus.loop.tick` | `LoopRunner.run_once` | status, issue, run id |
| Quality / eval gates | `gate quality`, `gate eval` | `LoopRunner._quality_gate`, `_eval_gate` | status; scores `quality_gate`, `quality_gate.<check>`, `eval_gate` (value = resolve rate) |
| RSI scorecard (LOCUS-351) | `gate scorecard` | `LoopRunner._scorecard_gate`; the suite evaluator (`locus_evals.suite.runner`) | status (`promote` / `hold` / `skipped` / `error`), held-out pass rate; scores `rsi_scorecard` (loop run), `rsi.sample`, `rsi.injection`, `rsi.budget_adherence` (per eval sample, on the sample's run id), `rsi.pass_rate.<split>`. The candidate instance writes its own run traces to its own SQLite (`<output>/candidate/telemetry.db`). The scorecard records the candidate's OS jail tier (`isolation`, LOCUS-379) and a note with the isolation probe result and the jail setup time |

**Scores** are `gen_ai.evaluation.result` events (`gen_ai.evaluation.name`, `score.value`, `score.label`) on the run trace; the local store lifts them into a `scores` table. The loop runner's tick span is the root of the loop's trace, so the agent run, its gates and their scores land in one trace.

The attribute names are pinned in `locus_runtime/telemetry/semconv.py`. The GenAI conventions are still "Development" status upstream; a rename is a one-file change.

Locus owns its `TracerProvider` and does **not** install it as the OTel global, so third-party libraries cannot write into Locus sinks and Locus spans never go to an exporter a library configured. Until a composition root calls `telemetry.configure(...)` (backend startup, `loop_runner.build_runner`), every helper is a no-op.

## 2. Privacy defaults (P10, P14)

- **Content capture is off.** Prompts, completions, tool arguments and tool output are never put on a span by default.
- **When the principal turns it on** (`telemetry_capture_content`), content is serialized, redacted with the platform helpers (`gateway.redact_text`: key/value and token patterns, private keys, URL credentials, provider key shapes; plus Presidio when `LOCUS_ENABLE_PRESIDIO_PII_ANALYZER` is on), then truncated (4000 characters by default). It is stored as a separate payload that is pruned after the payload retention.
- **Every exported string is redacted again** on the batch worker, for every sink (SQLite, OTLP, LangSmith), so a secret that reached an attribute by any other path is masked before it lands anywhere. Exporter auth values are also masked wherever they appear.
- Gateway spans carry reason codes only; anything that is not a short code is shown as `[withheld]`.
- Test: `tests/unit/test_telemetry.py::test_a_secret_never_reaches_any_exporter` (content capture on, every sink attached).

## 3. Local store

- File: `<app_home>/data/telemetry/telemetry.db` (`LOCUS_TELEMETRY_DB` overrides; `LOCUS_TELEMETRY_LOCAL=0` turns it off).
- SQLite in WAL mode, written only from OTel's `BatchSpanProcessor` worker thread: bounded queue (2048 spans), spans dropped on overflow, so the agent never waits on telemetry. An unwritable file or a schema from a newer Locus is reported in Posture as `blocked`; the agent runs on.
- Schema versioned with `PRAGMA user_version`; `sqlite_store.MIGRATIONS[n]` takes a file from version n-1 to n (forward only, one transaction per step).
- Retention (O-09): payloads are cleared after **90 days** (`telemetry_payload_retention_days`); spans, events and scores are deleted after 365 days. Pruning runs on write, at most once an hour. **Audit stays in the audit log** (P11); telemetry is diagnostic and prunable.

Read API (authenticated reads, `request_security.py`):

| Route | Returns |
|---|---|
| `GET /telemetry/runs?limit&offset&since&until&end_state&runtime&status` | runs, newest first, with model / tool call counts, tokens, cost, errors and scores |
| `GET /telemetry/runs/{run_id}/trace` | every span of the run's trace as a tree, with events and scores |
| `GET /telemetry/summary?window_hours&since&until` | cost, tokens, p50 / p90 / p99 latency (runs, model calls, tool calls), errors by operation, gate outcomes, end states |

`since` / `until` take ISO-8601 or epoch seconds.

## 4. External exporters (off by default)

Both are platform settings (`POST /platform/settings`). Credentials are **names of native secrets** (`lattix secrets set NAME`), resolved at configuration time and never stored in settings, returned by an API or logged.

| Setting | Meaning |
|---|---|
| `telemetry_otlp_enabled`, `telemetry_otlp_endpoint`, `telemetry_otlp_auth_secret_ref` | Generic OTLP/HTTP exporter. The secret's value is sent as the `Authorization` header |
| `telemetry_langsmith_enabled`, `telemetry_langsmith_endpoint`, `telemetry_langsmith_project`, `telemetry_langsmith_api_key_ref` (default `LANGSMITH_API_KEY`) | LangSmith OTLP preset: **hosted, proprietary; data leaves the machine** |
| `telemetry_capture_content`, `telemetry_payload_retention_days` | Content capture and payload retention |

Rules (fail closed):

- The exporter's host must be on the platform egress allowlist (`allowed_egress_hosts`), whether or not `enforce_egress_allowlist` is on. The check runs at configuration and again on every export, so removing the host stops export at once.
- https is required, except for a collector on loopback; credentials in the URL are refused; a missing or unreadable secret means the exporter is not installed.
- **Capability widening:** turning content capture on, enabling an exporter, setting a new endpoint or credential, or lengthening the payload retention widens what leaves the run. It needs `confirm_security_change: true` on every profile and, on the desktop, the shell proof (`capability_widening.py`: `PERMISSIVE_FLAGS`, `DESTINATIONS`, `LIMITS`). Disabling an exporter, clearing an endpoint or shortening retention narrows and needs neither.
- Posture (`control_status`, control `telemetry_local`) reports the local store on/off and each external exporter with its destination class (`loopback`, `remote`, `hosted_proprietary`) and why it is blocked. `enforced` means local only with content capture off; any active external exporter or content capture is `degraded`.

### Self-hosted Langfuse (recommended optional backend, MIT)

1. Run Langfuse yourself (Docker Compose or Helm, per Langfuse's self-hosting guide).
2. Create a project and API key pair. Store the header value: `lattix secrets set LANGFUSE_OTLP_AUTH`, entering `Basic <base64 of public_key:secret_key>`.
3. Add the Langfuse host to `allowed_egress_hosts`.
4. Save platform settings with `telemetry_otlp_enabled: true`, `telemetry_otlp_endpoint: https://<your-langfuse-host>/api/public/otel/v1/traces`, `telemetry_otlp_auth_secret_ref: LANGFUSE_OTLP_AUTH`, `confirm_security_change: true`.

Langfuse reads the GenAI attributes (model, token usage) from the spans; Locus's own cost estimate is in `locus.usage.cost_usd`. Any other OTLP/HTTP collector (OpenTelemetry Collector, Jaeger, Phoenix) works the same way; a collector on `localhost` may use http.

### LangSmith caveats

LangSmith is a hosted, proprietary service: enabling it sends run traces (and, with content capture on, redacted content) to LangSmith's servers. It is opt-in egress only. Store the key with `lattix secrets set LANGSMITH_API_KEY`, add `api.smith.langchain.com` (or your regional host) to the egress allowlist, then enable it. The Deep Agents runtime keeps LangChain's own LangSmith tracing forced off; this preset is the only path to LangSmith.

## 5. Phase 2 plan

- **PromptRegistry** port: versioned prompts (system prompts, skill prompts, judge prompts) with labels (production / staging), linked from `chat` spans by prompt name and version.
- **Datasets and experiments**: save a run's inputs and expected outcome as a dataset item from a trace; run an experiment (runtime, model or prompt variant) over a dataset and compare scores side by side.
- **Inspect AI scores**: import Inspect AI eval logs (the eval gate's suites, LOCUS-339/351) as scores on the run trace.
- **Judges and annotations**: LLM-as-judge scorers over traces (sampled, budgeted, gateway-mediated) and human annotations (thumbs, labels, comments) stored as scores with `source` `judge` / `human`.
- **UI views** (FRONTEND.md): run list, trace waterfall, cost and latency dashboard, score trends, built on the read API above.
- Metrics (OTel metrics for token and cost counters) and an OTLP/gRPC option if a collector needs it.
