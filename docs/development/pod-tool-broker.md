# LOCUS-433 — Sandboxed, gateway-mediated tool broker: first increment

Implements **only** a trusted-supervisor broker for immutable, administratively
registered local command descriptors. LLM text cannot provide an executable,
argv, network host, environment, or writable path. A child must already be
admitted to the Zero Trust gateway, and its allowed-tools capability must
include the registered tool. The receipt ledger then authorizes this exact
operation through Gateway and writes a durable action/dispatch record before
the subprocess can run.

A confining SandboxManager strategy (bubblewrap, Seatbelt, Windows
AppContainer or hardened Docker) is required. Restricted process and K8s
plans are never run by this *local subprocess* broker; a missing sandbox
fails closed. No network, inherited secrets, shell, tool-managed writes
or dynamically supplied argv are supported. Subprocess runtime and retained output
are bounded with separate draining threads for stdout/stderr (64 KiB per stream), including
when the sandbox emits substantially more data. The trusted broker still
handles finite data throughput for the duration of the bounded process. Tool results are returned to the trusted caller; only hashed
outcome evidence enters the receipt database. A timeout results in an
uncertain receipt and cannot automatically replay.

## Test-driven definition of done

- Unknown tools, unapproved capabilities, malformed command descriptors and
  weak/mismatched isolation deny before external work
- Gateway permission and pre-dispatch receipt are required for execution
- A duplicate action key cannot invoke the runner again; timeout fails closed
- Explicit no-network and minimal environment, fixed argv, bounded output
- Linux/Windows/macOS CI and sandbox regression gates pass on exact commit

## Non-goals and next hardening

This **does not** execute arbitrary scripts or untrusted worker content.
Administrative tool descriptors and broker code are trusted. This does
not attest arbitrary external tools, perform provider-signed outcome
verification or rehydrate Gateway sessions across process deaths. Broader
sandboxed worker IPC and tenant-specific tool distribution require separate
trust protocols and verification suites. No use of Savant is required.
