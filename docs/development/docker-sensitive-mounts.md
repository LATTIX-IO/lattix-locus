# LOCUS-433 increment 22 — protected sensitive subpaths inside Docker writable workspaces

## Security problem

The hardened Docker planner mounts administrator-approved writable workspace roots via Docker bind mounts. Unlike Locus's bubblewrap strategy, its Docker launcher did not re-protect nested control and credential directories, including `.git`, `.locus`, `.ssh`, `.gnupg`, `.aws`, `.azure`, `.kube`, and `.config/gcloud`. Thus a bounded tool admitted to write the workspace could also modify repository control metadata or mounted credential/configuration material inside a writable parent.

This increment closes that gap **without disabling normal workspace writes**. Existing sensitive subpaths are over-mounted as **read-only binds after** the writable parent binds. The same deterministic protection list is used by the sandbox Docker planner and the broker's independently constructed pre-planner Docker launch contract.

## Enforcement

For each canonical writable root:

1. Reject roots that are themselves inside a protected control directory. No `.git` or credential directory may be directly admitted as writable.
2. Reject any symlinked protected leaf or its sensitive-path ancestors, including `.config` as a symlink before `.config/gcloud` exists. Do not follow an external symlink and mistake it for protection of an in-workspace path.
3. For existing sensitive paths below that root, add `-v <canonical-path>:<canonical-path>:ro` after parent `:rw` mounts, deduplicated and sorted.
4. If validation of a protected path fails, deny before Gateway authorization, action receipt creation or subprocess launch.
5. Independently reconstruct the expected Docker argv in the broker before the planner runs. A planner that omits, reorders or weakens a required read-only overlay is denied by the already-existing complete-launch-contract check.

This intentionally uses the existing supervisor-approved Docker invocation and fixed `--network=none`, read-only root filesystem, no-new-privileges, dropped capabilities, seccomp and resource bounds. It does **not** add an additional process executor, output collector, network permission or LLM-authorized mount.

## TDD acceptance

- Each of the eight existing protected path classes nested inside a writable workspace is mounted read-only after the writable parent; normal workspace files remain writable.
- Removing a required overlay in a compromised planner fails before any durable action receipt or process launch.
- Directly admitted writable `.git`, `.aws` and `.config/gcloud` paths are denied before Gateway or subprocess dispatch.
- Symlinked protected paths (`.git`, `.ssh`) and sensitive ancestors (`.config`) fail closed, including when a target leaf does not yet exist.
- An ordinary workspace without protected subpaths uses its original Docker mounts, executes once with suppressed stdout/stderr, and remains subject to the existing at-most-once receipt/replay barrier.
- All preceding tool catalog, protected endpoint, approved image, Docker launch, typed receipt, Windows sandbox, integration and security regression suites remain required.

## Limitations

Docker bind mounts are resolved by the host daemon; this increment does not independently attest container mount state or protect against a privileged host process/daemon modifying mounted paths between validation and execution. Read-only container mounts do not guarantee that a sensitive directory is absent from the tool's *read* authority. Tenants should avoid exposing unnecessary credentials through read scopes and enforce privilege isolation at the OS and container runtime.

Merge sequence: PR #106 must merge first. Then reconcile this increment onto latest `main`, confirm scope is limited to the sandbox Docker planner, broker launch validation, tests and docs, and pass exact-head backend-unit, Ruff/mypy quality, integration, Windows sandbox, DAST, performance, frontend, release/version and final required-gates. CodeQL, SBOM/SCA and AppSec jobs skipped in CI are not treated as verified security gates.
