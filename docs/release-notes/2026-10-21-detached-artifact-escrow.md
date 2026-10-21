Release-Impact: minor
Version: 0.10

LOCUS-469 introduces durable, supervisor-private terminal artifact escrow.
A successful authenticated child action may seal one independently verified
regular output into a private 0700 attempt directory (0400 content). The
versioned SQLite/WAL escrow record pins stage/plan/engine/receipt provenance,
original source revision, output digest and inode, principal, and expiry.
The child may then be closed and its disposable staging directory removed.

A distinct authenticated operator can review a bounded, masked diff and
obtain a one-use exact Gateway-human approval after child closure. Escrow
review never creates a writable original Docker mount, replays the child,
or treats a failed/ambiguous execution as a successful artifact.

After separate human approval, a supervisor-only publication path rechecks
the original source revision and uses a durable prepared-inode witness with
atomic Linux create/exchange. Restart recovery rolls back only verified
inodes without replay, reclaims bounded expired escrow payloads and preserves
uncertain/foreign evidence. A reconciliation CLI is provided for native
startup and periodic maintenance.

Multi-file atomicity, remote/fleet deployment, automatic approval, and
Windows/macOS are not enabled by this increment. Native lifecycle wiring,
independent end-user UI acceptance and full exact-head security gates must
pass before default production activation.
