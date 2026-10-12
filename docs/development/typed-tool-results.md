# LOCUS-433 — deterministic typed tool-result contract (increment 6)

This is an **offline, deny-first parser and release contract**, not another tool
executor and **not wired to the live command broker**. The current
PodSandboxBroker continues to discard stdout/stderr. A later separate
increment may add a bounded collector upstream of this contract after its
trusted source and transport boundaries are specified and independently tested.

## Trust and data flow

1. A trusted supervisor chooses a frozen ToolResultPolicy describing one
   schema, a finite byte ceiling (1–65536), and optionally an administrator
   approved set of counter names. Policies cannot be provided by a tool or LLM.
2. decode_tool_result receives an immutable binary payload and rejects data
   outside the byte envelope **before** UTF-8 / JSON parsing.
3. JSON must be a single strict UTF-8 object without duplicate keys,
   non-finite values, floats, extra fields, nested arbitrary data, or unknown
   schemas. Booleans are not silently accepted as integer counters.
4. Only two static schemas may be released:
   - status/v1: {schema, ok: bool, code: integer 0–255}; ok means code=0
   - counter/v1: {schema, metric: explicitly pinned name, value: integer
     0–1,000,000,000,000}
5. Results are immutable typed dataclasses. The SHA-256 digest is produced
   from schema-tagged canonical validated values using a domain separator.
   **A digest is not cryptographic provenance or proof of external execution**.
6. Exceptions never interpolate untrusted bytes. Raw stdout/stderr, free text,
   credentials, stack traces, tool arguments and host paths are not exposed by
   the typed result object. No database writes or network calls occur.

## Acceptance and tests

- Positive canonical round trip for both schemas, stable hash under JSON
  key reordering, distinct digests on distinct typed outputs.
- Adversarial duplicate keys, payload enlargement, malformed UTF-8, NaN,
  Infinity, floats, oversized integers, bool-as-int, schema spoofing,
  extra arbitrary fields, unknown metric names and raw secrets denied.
- Frozen policy construction and allowlist validation tested.
- Existing pod reservation, action receipt, OS fencing, gateway, sandbox
  broker and immutable tool registry suites must remain green.
- Latest-head backend pytest, Ruff/mypy, integration, DAST, Windows sandbox,
  release/version, performance and required-gates are mandatory for merge.
  Repository-disabled SAST, SBOM and AppSec are not counted as verified runs.

## Non-goals

This increment does not alter subprocess collection, tool transport, trusted
worker identity, filtering of general free text, model response filtering,
remote provider attestation, distributed fencing, MCP egress, or API serving.
Those require separate trust-boundary and lifecycle specifications. The
contract alone is deliberately insufficient for releasing arbitrary outputs.
