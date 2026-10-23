# LOCUS-470/A — Confined Docker stdin payload contract

Status: **bounded transport only; not a live Codex engine, not native-plus-Codex acceptance**.

The existing `StageDockerBroker.execute` supports an optional supervisor-supplied
`stdin: bytes` payload. Admission requires 1–65536 bytes of valid UTF-8 and
rejects embedded NUL, arbitrary objects and empty prompts **before** any
receipt. No payload can enter Docker command-line arguments, environment,
labels or the persistent action ledger. The frozen Gateway action and launch
digest bind only the SHA-256 and byte count. `docker run --interactive` is
enabled only for this path; `subprocess.PIPE` sends input to the daemon CLI
once, without printing subprocess output. Parent cancellation, timeout,
guardian epoch, revocation, frozen container restrictions and receipt
non-replay rules remain intact. The launch keeps `--network=none` and only
the sealed staging workspace as a bind mount.

**Security caveat:** the Docker daemon necessarily transports plaintext
stdin to the child process. The trusted supervisor, daemon and confined child
see the prompt; it is *not* a channel for provider credentials or secrets.
Use only policy-approved non-secret task content. No output streaming or
provider egress is enabled here. The existing broker does not yet provide
sufficient network/authentication transport for real Codex model inference,
and the old unmediated `run_codex` is not a substitute.

Validation:
`pytest -q tests/unit/test_stage_docker_stdin.py`
and the full Stage-only Docker kernel acceptance workflow on a supported
unprivileged Linux host. Synthetic tests are **not** live Docker or Codex
evidence. Rollback: callers omit `stdin` and the prior non-interactive
behavior is preserved.

Next within LOCUS-470: design and independently verify a narrowly-scoped
local Ollama/provider inference proxy, official version-pinned Codex CLI image,
model input/output attestation, per-child Gateway tool transport, and real
operator environment demonstration before enabling this backend in the UI.
