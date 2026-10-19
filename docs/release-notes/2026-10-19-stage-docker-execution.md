Release-Impact: minor
Version: 0.8

Adds an optional, Linux-only stage-bound Docker execution path that preserves
the original project as read-only host state, with durable Gateway action
receipts, a kernel-attested stage-generation witness and bounded process
timeout/cancellation. It remains separate from vendor CLI adapters and
explicit artifact promotion.

Action required: Operators using the new worker must supply a local Unix
Docker Engine, immutable OCI image digest, unprivileged Linux supervisor,
private staging root, and a kernel supporting statx birth/mount identity.
Windows/macOS and unsupported Docker/FS combinations fail closed. No
existing deployment is automatically switched to the new worker.
