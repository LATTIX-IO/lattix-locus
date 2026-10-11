# LOCUS-433 — bounded sandboxed command broker (increment 4)

The supervisor-side broker connects **admitted child gateway sessions** and
**durable fenced action receipts** to existing Locus sandbox strategies. No new
policy engine or direct tool sink is introduced. The broker is not yet exposed
to model-generated tasks or arbitrary third-party workers.

## Control flow

`BrokerCommand` validates a bounded argv and an explicit working directory,
idempotency key, tool name and <=60-second timeout. The broker checks trusted
child capabilities (exact tool and executable allowlists and contained workdir)
and picks an actual confinement tier. It plans with `SandboxManager`,
derives non-model `JailFacts`, then passes the authorization request to
`PodActionReceipts.execute_once`. Only an allowed signed gateway decision
can reach the single `subprocess.run` sink, and that sink is within the
persisted `prepared → fence_guarded/dispatching → result` lifecycle.

Shell wrappers/interpreters and inline `-c` command formats are excluded.
There is no caller-provided environment, network or arbitrary mount request.
The child receives only `minimal_agent_env`; the Docker CLI receives an
empty-host-derived environment, excluding `DOCKER_HOST`/`DOCKER_CONTEXT` so
there is no implicit remote Docker daemon selection. Output streams are sent to DEVNULL and only
exit status + backend + elapsed milliseconds are returned and digested. This
is deliberately narrow: a later bounded redacted output adapter will support
assistant/research and richer noncoding tool results.

### Verified platform choices

* Linux: prefer hardened Docker with no network, read-only root filesystem,
  capability drop, seccomp, non-root UID, resource ceilings, explicit mounts;
  **deny** bwrap's current all-files read-only root mount (data confidentiality).
* macOS: Seatbelt deny-default profile (if supported).
* Windows: AppContainer + Job Object with `require_appcontainer=True`.
* Missing confinement / hosted K8s pod-template / restricted-process:
  **deny, never fall back**.
* The broker never claims to impose an additional sandbox around raw Python
  callbacks running in its own trusted supervisor process.

## Acceptance tests

`tests/unit/test_pod_sandbox_broker.py` has negative first-class cases for bad
argv, shells, undeclared tools/executables, forbidden workdir, missing
sandbox tier, Linux bwrap all-files leakage, denied gateway decision,
network-disabled launch configuration, no inherited secret environment,
no raw output persistence, exactly one callback on retry, and sandbox
timeout → uncertain non-replayable receipt. CI must include full backend,
gateway, sandbox, action receipts and fenced recovery suites, Linux and
Windows build/sandbox tests and policy validation.

## Important non-goals

No model-facing arbitrary process execution, output streaming, computer-use
dispatch, external provider receipt attestation, independent worker process
identity, sandbox-escape certification, network-enabled tools, or distributed
orchestration. A sandboxed command can complete externally before timing out;
its durable action remains `uncertain` and is never automatically repeated.
Docker `run` is named with a broker-generated identifier and a timed-out Docker client triggers a bounded `docker kill`; a failed cleanup remains an uncertain, non-replayable action. Canonical read/write mount overlaps are mounted once with the already-authorized write scope. Docker daemon or image trust is an operational prerequisite to be attested
before deployment. Release as a **trusted-supervisor local tool primitive**,
not a general production-safe agent execution surface.
