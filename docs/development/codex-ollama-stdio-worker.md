# LOCUS-470/B — confined Codex CLI and local Ollama

**Status: experimental, disabled unless explicitly instantiated by a trusted supervisor.** No default UI, auto-dispatch, Claude/OpenCode, remote fleet, arbitrary browser/skills, or native+Codex team orchestration. The existing legacy `run_codex` host path is **not** permitted for this feature.

## Trust and execution boundary

1. The operator approves a `TaskPlan` node and pins `codex-cli/0.163.0`. The normal durable scheduler admits a distinct child `PodLease`, resource hold and `StageLease`.
2. The administrator builds `docker/codex/Dockerfile` from a **resolved base image manifest** using `--build-arg NODE_BASE=node@sha256:...`. The image installs official `@openai/codex@0.163.0` and build-time checks `codex --version`. The final OCI image must be referenced by its **registry content digest**, not an image tag.
3. The trusted `CodexCliWorker` chooses the fixed `/usr/local/bin/locus-codex-stdio` entrypoint, allowed output filename and task content. `StageDockerBroker` verifies the child engine, its existing Gateway identity and action receipt, stage provenance, OS guardian, trusted local daemon and exact digest image before launching.
4. Docker remains on `--network=none`, with **exactly one writable bind**, the disposable stage. Root filesystem is read-only; non-root UID, dropped capabilities, seccomp and resource limits remain mandatory. Docker receives no Codex home, Git home, CLI user config, Locus token, model token, mounted socket, arbitrary tool or original workspace.
5. The official CLI runs `codex exec --oss --local-provider ollama --json --ephemeral --ignore-user-config --sandbox read-only --disable shell_tool` through the vetted container runner. The runner's **in-container loopback** Ollama-compatible HTTP server forwards model requests via bounded JSONL **stdio**. The child has no external TCP route.
6. The supervisor `CodexOllamaBridge` authenticates *each* request using the original child `GatewaySession`, enforces the pinned model, fixed API route, strict byte limits and bounded request count, settles conservative **worst-case** token usage in the durable ledger before egress, and connects to **only** `127.0.0.1:11434` with no HTTP proxies, redirects or provider keys. Gateway refusal kills the execution; none of the raw payload enters the durable audit.
7. The runner hashes official CLI JSONL events but never relays them or raw task/model output as telemetry. It returns a terminal digest of a newly-created, bounded `answer.txt`-style file. A successful Docker status or runner-provided digest is **not** independent artifact verification. `DetachedArtifactEscrow.seal` is still mandatory before approval, and the existing one-file operator approval is mandatory before publication.

## Supported and unsupported scope

- A **single** ephemeral Codex CLI specialist performs text reasoning and writes a new plain-text terminal artifact into its stage. Model inference is to a locally installed Ollama model with explicit Gateway grant and admission budget. Up to 16 serialized model calls, at most 512 requested output tokens each, at most 1 MiB request and 4 MiB reply, 60-second container deadline. These limits are safety ceilings, not a general performance configuration.
- All child-originated MCP, browser, remote web, shell, process and host file operations are **unavailable**. The CLI's own read-only sandbox and feature flags are secondary controls, **not** Gateway mediation. The Docker kernel boundary protects original host files; stage files remain untrusted until independent escrow inspection.
- Ollama does not provide a universally trustworthy advance estimate of inference usage. The bridge conservatively reserves bytes-as-token upper bound plus output cap; refusals retain charges. The container memory/time limit and reply byte ceiling provide independent resource bounds, but a nonconformant model server could consume more inference tokens than the requested cap before abort. Do not interpret this as a provider-enforced billing guarantee.
- The Linux recovery guardian must be operating on the same ledger and local Docker engine. Cancellation, timeout or interrupted transport remain **uncertain / non-replayable** until reconciled. No fallback to host execution.
- Test-only locally bound Ollama needs no credential. Hosted OpenAI/Anthropic credentials and endpoint forwarding are explicitly prohibited in B.

## Exact-host acceptance

A dedicated `Real Codex CLI and Ollama kernel acceptance` PR workflow must build the image from a resolved digest, run an actual Ollama server with `llama3.2:1b`, run the official pinned CLI inside the guarded Docker stage, observe at least one real Gateway-authorized inference, independently escrow the resulting new single-file artifact, and prove source unchanged and replay/cancel denial.

Command on a supported **unprivileged** Linux host:
```sh
LOCUS_REAL_CODEX_ACCEPTANCE=1 \
LOCUS_CODEX_ACCEPTANCE_IMAGE='localhost:5001/locus-codex@sha256:<published digest>' \
python -m pytest -q -rs tests/unit/test_stage_codex_worker.py \
  tests/integration/test_codex_ollama_kernel_acceptance.py
```

**No skip is acceptance.** CI must include real kernel Docker, a compatible supported Codex CLI version, a genuine Ollama model call, and passing normal backend/quality/DAST/CodeQL jobs. Skipped standalone AppSec/SBOM/SAST jobs remain not executed.

## Rollback and subsequent increments

The implementation is opt-in. Disable instantiation of `CodexCliWorker` or revert its new modules and optional broker channel without a migration; preserve existing uncertain receipts, ledgers and escorted artifact records. Stage-only Docker without a Codex bridge remains unchanged.

**LOCUS-470/C** must add two real independent specialists (native + Codex) with verified artifact escrow between them, revision-pinned immutable handoff, post-child-closure human approval, negative/repair and supervisor-crash acceptance, and the front-end journey later under LOCUS-459.
