# LOCUS-433 increment 23 — mandatory Docker seccomp deny baseline

## Security gap

The broker previously validated the path and `.json` extension of the selected Docker seccomp profile, but did not inspect its rules. A file located inside the trusted `docker/sandbox` directory could have a weakened or empty policy while the launcher still appeared to include `--security-opt=seccomp=...`. A permissive rule or architecture/argument-limited block could allow privileged tracing, kernel modification, namespace escape, keyring access, and other operations the packaged security profile expressly denies.

This increment adds **content-level validation before Gateway authorization or receipt preparation**. It does not add a new executor, modify the existing sandbox resources or permit broader tool actions.

## Policy invariant

The broker checks the profile selected by the existing trusted, directory-contained seccomp path resolver:

- The profile is a regular file of at most 64 KiB and valid UTF-8 JSON with no duplicate keys. Root must be an object with a syscall-rule list and recognized default action.
- Every syscall in the packaged critical deny baseline must appear in an **unconditional** deny rule. Critical areas include ptrace and process memory access, io_uring, kernel modules, namespace/mount operations, keyrings, BPF/perf, credential and time manipulation, and raw device access.
- For any rule that mentions a protected syscall, only a recognized `SCMP_ACT_ERRNO` or kill action is accepted. A protected-name rule cannot have an `includes` or `excludes` architecture filter, syscall argument filter, permissive action or zero errno. An extra permissive rule for the same protected syscall is also rejected.
- Stronger, unrelated unconditional syscall denials are permitted. The stock `docker/sandbox/seccomp-strict.json` remains valid without configuration changes.
- Failure is a fixed `AdmissionDenied("Docker seccomp baseline denied")` before the existing full Docker CLI check, Gateway PEP decision, durable receipt and any subprocess invocation.

Validation is integrated into the broker's **independently reconstructed pre-planner Docker command**, so an untrusted planner cannot redefine the minimum accepted seccomp policy after authorization inputs are captured.

## Unit, regression and security acceptance

- Nine weakened policies (removed required syscall, permissive override, conditional architecture/argument restrictions, invalid errno, invalid default and malformed names) must deny with no process invocation or receipt.
- Invalid JSON, non-object JSON, duplicate keys and oversized payloads must also deny with no effects.
- The packaged baseline must still pass under an explicitly configured contained seccomp file, then execute exactly one no-network, cap-dropped, status-only Docker process.
- Adding a stricter unconditional denial not in the baseline remains valid.
- All prior catalog, trusted image, frozen launch, protected Docker mounts, Gateway receipt, Windows sandbox and performance regressions must remain green.

## Limitations

This check verifies the seccomp profile **at broker admission time**. Docker's daemon still reads the profile by file path during launch; a privileged writer replacing it between validation and daemon consumption is a remaining host-side TOCTOU threat. The check does not attest effective kernel seccomp installation or container daemon trust. Protect `docker/sandbox` with deployment-level immutability and filesystem permissions. Skipped CodeQL, SBOM/SCA and separate AppSec gates are unverified, not passing.

## Release gates

This increment is stacked on PR #107. After upstream integration, reconcile onto `main` and require current-head backend pytest, Ruff/mypy quality, integration, Windows sandbox, DAST, frontend, performance, release/version, final required-gates, review closure and a SHA-protected merge. Incremental diff should contain only the sandbox policy validator, broker launch check, new tests and this document.
