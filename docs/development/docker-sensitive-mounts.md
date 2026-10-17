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


## 2026-10-12 subsequent P1/P2 security review

1. **Writable symlink roots:** The authoritative policy may name a symlinked
   directory `.git` whose canonical target no longer contains the sensitive
   component. The planner and independent broker now inspect the *original
   lexical path* and canonical target before resolving and admitting Docker
   writable binds. Direct symlinked control roots and casefolded aliases deny
   prior to the Gateway, receipts, or Docker subprocess.
2. **Sensitive destinations initially absent:** A Docker process could create
   a nonexistent `.git`, `.locus`, `.aws` or `.config/gcloud` inside an
   approved writable parent. Existing protected paths still receive
   `:ro` bind overlays. Missing protected destinations now receive an empty
   `--tmpfs <destination>:ro,nosuid,nodev,noexec,size=4k,mode=0555` mask;
   for missing nested ancestors (for example absent `.config`), mask the
   first missing ancestor. Docker supports `ro` tmpfs options:
   https://docs.docker.com/engine/storage/tmpfs/
3. **Independent contract:** Both the planner and broker derive these masks
   from the policy's original untrusted write-path strings before launch. A
   planner that strips a required mask is rejected before any durable receipt.
4. **Regression evidence:** Added wrong-root symlink, multiple absent
   protected paths, nested `.config`, unchanged normal writable workspace,
   overlay-mismatch and no-side-effect tests.

**Important limitation:** CI unit tests verify the generated Docker CLI
contract, not an actual daemon mount in production. A Linux Docker smoke
against the supported Docker engine versions must verify write refusal to
both existing read-only bind and missing tmpfs masked destinations before
claiming this control fully runtime-attested. Host-side pathname TOCTOU
under privileged writers remains a separate deployment threat.

5. **Missing read-only host bind sources:** Docker `-v` creates absent host
   paths automatically. Both the broker and planner now deny any explicitly
   readable-but-missing source rather than allowing Docker to manufacture
   credential/control directories. This is independently tested alongside
   existing legitimate read/write mount compatibility.


## Security review: non-creating recursive bind mounts

Docker `-v` creates missing host paths in a rename race even after an existence check, and ordinary `:ro` on older kernels can leave nested submounts writable. All workspace binds now use `--mount type=bind,source=...,target=...`, which fails rather than creating absent source paths; read-only and protected overlays additionally require `readonly,bind-recursive=readonly`. Docker Engine 25+ and kernel 5.12+ support the explicitly recursive read-only bind mode; incompatible runtimes fail closed and **must not** be marked production-ready. Absent writable roots are denied before Gateway or receipts. Tests cover noncreating sources, recursive flag retention, and planner/broker argument identity. A real supported Docker daemon with a nested submount remains necessary for release attestation.
