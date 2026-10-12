# LOCUS-433 increment 9 — canonical registered tool paths

The administrator-configured local tool registry already rejects symlinked executable and workspace **leaf entries**, but those checks do not reject symbolic-link parent directories. A path like `/workspace/alias/tool` can resolve to an executable outside its apparent directory even when `tool` itself is not a symlink. This increment adds a strict, deny-first canonical-path check at invocation.

## Security contract

Before calling the single `PodSandboxBroker`, the `SandboxedPodToolBroker` resolves the pinned executable and workspace paths with `strict=True`. It permits dispatch only if each resolved path equals the exact registered lexical `Path`, the executable is a regular file, and the workspace is a directory. Unavailable paths, parent aliases and lexical `..` traversal are rejected with a fixed error before creating an action receipt or launching any process.

The check operates on administrator-authored paths; the LLM still cannot select an executable, mutate registered argv, choose network settings, or add host mounts. It does not replace existing child lease capability checks, gateway PDP authorization, sandbox isolation, OS-fenced durable receipts or replay restrictions.

## Tests and acceptance

- Parent-directory symlink of executable is denied even when its leaf is a regular file.
- Parent-directory symlink of workspace is denied even when the leaf is a directory.
- Lexically noncanonical `..` path is denied.
- All denied cases create zero receipt rows and invoke no subprocess.
- A canonical normal tool still produces exactly one authorized subprocess and the original receipt audit sequence.
- Full existing broker, registry, typed results, Linux/Windows sandbox, DAST, integration, replay and gateway regression suites remain green under exact-head required CI.

## Limits

This is a best-effort canonical-path preflight; it does **not** eliminate races between filesystem checks and process invocation, enforce stable file identity, provide signature verification or attest remote binaries. Do not describe it as race-free secure file opening. Those threats require operating-system file-descriptor binding and sandbox strategy-specific execution identity in a separate increment.
