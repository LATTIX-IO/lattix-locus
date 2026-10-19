Release-Impact: minor
Version: 0.8

Adds an optional, Linux-only stage-bound Docker execution path that preserves
the original project as read-only host state, with durable Gateway action
receipts, a kernel-attested stage-generation witness and bounded process
timeout/cancellation and OS-supervised orphan reconciliation. Each child
launch is denied unless the private local Docker recovery guardian has a
fresh, systemd-notified heartbeat; a changed guardian epoch or failed daemon
stops execution. A separate watch process can clean up SIGKILL orphaned
containers without touching original files or releasing held budgets. It
remains separate from vendor CLI adapters and
explicit artifact promotion.

Action required: Operators using the new worker must supply a local Unix
Docker Engine, immutable OCI image digest, unprivileged Linux supervisor,
private staging root, an installed `systemctl --user` Type=notify guardian
sharing the exact supervisor ledger and Docker socket, and a kernel supporting
statx birth/mount identity. Install using
`python scripts/install-stage-guardian.py --ledger /absolute/private/runs.db`
and verify live host service watchdog/restart qualification before enabling
staged Docker on production machines.
Windows/macOS and unsupported Docker/FS combinations fail closed. No
existing deployment is automatically switched to the new worker.
