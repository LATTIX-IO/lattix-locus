# LOCUS-433 — strict Docker backend admission (increment 18)

## Problem and security invariant

An administrator can require a specific content-addressed Docker image through `PodSandboxBroker(require_immutable_docker_image=True, pinned_docker_image=...)`. In the previous implementation the image requirement was only evaluated **if** strategy selection already chose `HARDENED_DOCKER`. On hosts where the selection resolved to kernel Seatbelt (macOS) or Windows AppContainer, the broker could dispatch through a native sandbox without using the approved Docker image. That would silently weaken the explicitly selected supervisor policy.

**In strict-image mode, the selected concrete execution backend must be hardened Docker.** Immediately after choosing a permitted strategy, the broker now rejects any other selection with a fixed admission error *before* policy planning, Gateway authorization, receipt creation, or subprocess invocation.

This intentionally restricts portability when a supervisor mandates the image pin; a native confinement backend is not equivalent to that exact approved image. Operators choosing native macOS/Windows tools must select the non-strict image mode under their separately configured admission controls. This is not a downgrade or a fallback carried out automatically by the broker.

## Bwrap compatibility

`_choose_strategy` may still explicitly promote a supported Linux bubblewrap selection to `HARDENED_DOCKER` when permitted and Docker is present. Strict-image admission is applied to that **resulting concrete strategy**. It therefore permits only an actual Docker execution with the approved digest and complete launch-policy checks. When Docker is unavailable or native-only mode is selected, existing bwrap fail-closed behavior remains.

## Tests / definition of done

- Simulated macOS Seatbelt and Windows AppContainer selections must deny strict Docker mode **without calling the planner**, writing an action receipt, or executing a subprocess.
- Bwrap selection promoted to actual Docker must use the pinned image, no network, frozen argv, suppressed stdout/stderr, and one durable action receipt; replay is denied.
- Bwrap selection without Docker must remain denied without side effects.
- Legacy non-strict native selection must retain the existing planner dispatch pathway; the new guard may not silently disable unrelated native operation.
- Full preexisting gateway, catalog, action receipt, Docker policy, Linux/Windows sandbox, typed-tool result and integration suites must pass on the latest PR head. Required CI includes backend unit tests, Ruff/mypy, frontend, integration, Windows sandbox, DAST, performance, release/version, and final required-gates.

## Boundaries

This guard enforces **backend selection**, not image signature verification, Docker engine provenance, attested runtime identity or a new isolation implementation. In particular, it does not make native Seatbelt/AppContainer isolation unsafe or unsuitable in general; it refuses to misrepresent them as execution using the specifically configured Docker image. Optional CodeQL, SBOM/SCA and AppSec jobs remain unverified when skipped.
