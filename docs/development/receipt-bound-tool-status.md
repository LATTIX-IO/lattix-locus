# LOCUS-433 — receipt-bound status projection (increment 7)

Increment 7 is the first **live, status-only** consumer of the strict
`status/v1` result contract introduced by increment 6. This code does not
open a new sandbox, start a second subprocess, capture stdout/stderr, infer
provider success, or extend the underlying zero-trust capabilities.

## Control path

The only entry point is `SandboxedPodToolBroker.execute_status(...)` on the
trusted administrator registry. It delegates exactly once to the existing
`execute(...)` method, which enforces the admitted `PodLease`, exact named tool
and executable capabilities, canonical workspace, confining sandbox, native
gateway authorization, durable prepare/dispatch, and kernel OS fencing.

After the broker returns a **succeeded** `ActionRecord` and its status-only
`BrokerOutcome`, the adapter creates a minimal JSON record:
`{"schema":"status/v1","ok":boolean,"code":integer}`. It passes that record
through `decode_tool_result` with the fixed trusted `status/v1` policy
and returns immutable `TypedToolStatus(receipt, status)`.

The projection exposes only the terminal receipt and bounded schema data:
there is no raw stdout, stderr, argv, environment, stack trace or content in
the public result. A zero process exit code is transport success; it **does
not prove an external domain action took effect**.

Nonrepresentable codes (negative signal exits and values above 255) deny the
typed projection. Since the actual subprocess may already have executed, its
action receipt remains terminal, and a repeat with the same key is refused.
A timeout leaves the original action `uncertain`, not a typed success;
kernel fencing and Docker timeout cleanup remain unchanged.

## Required tests

- Successful and nonzero exits through the real gateway/receipt-backed broker.
- Exactly one side effect across the raw and typed broker methods.
- No stdout/stderr or unrecognized data in the projected result.
- Native gateway denial fails before subprocess invocation.
- Negative/out-of-range exit codes rejected and cannot be replayed.
- Docker timeout cleanup and uncertain receipt non-replayability retained.
- Prior policy, action receipt, tool registry, typed parser, Linux/Windows
  sandbox, DAST, integration and release gates all must remain passing.

## Explicit exclusions

No output collection, typed counter extraction from processes, arbitrary
agent-visible free text, dynamic tool registry, network access, MCP or A2A
protocol transport, multi-host execution, provider-signed attestation, or
exactly-once guarantees. External tool results remain status-only.
