# Installer and Public Distribution

Lattix xFrontier now includes a public-facing installer flow intended to be published from a public repository.

When run interactively, the installer now presents a terminal UI (TUI) style setup flow with boxed sections, numbered choices, secure-default prompts, and a review screen before it writes secrets or starts the stack.

## Bootstrap commands

### Public raw bootstrap URLs

#### POSIX shells

`curl -fsSL https://raw.githubusercontent.com/LATTIX-IO/lattix-xfrontier/main/install/bootstrap.sh | sh`

#### PowerShell

`powershell -ExecutionPolicy Bypass -c "iwr https://raw.githubusercontent.com/LATTIX-IO/lattix-xfrontier/main/install/bootstrap.ps1 -UseBasicParsing | iex"`

### From a local source checkout

#### PowerShell

`pwsh -File .\install\bootstrap.ps1`

#### POSIX shells

`sh ./install/bootstrap.sh`

The PowerShell variant includes `-UseBasicParsing` so it works cleanly in Windows PowerShell 5.1 without the legacy web-parsing prompt.

Both bootstrap variants now try to prepare a clean machine automatically: they detect the host OS, install Python 3.12+ and Docker when those prerequisites are missing, refresh the current-process `PATH`, and only then launch the interactive installer. On Windows, that also avoids the common Microsoft Store placeholder-alias failure mode by rechecking for a real supported interpreter after any bootstrap install attempt.

The public bootstrap is intentionally pinned to the vetted `main` branch installer and archive so published installs only deploy tested content.

When you run the bootstrap scripts from a local source checkout, they use the checkout's bundled `install/frontier-installer.py` instead of fetching `main` again. That makes branch-local bootstrap testing exercise the code you actually have checked out, while the public raw bootstrap URLs remain pinned to the vetted `main` branch.

A future vanity URL such as `https://install.lattix.io/xfrontier.sh` can safely redirect or proxy to the same bootstrap script.

## What the installer does

1. Detects the host platform and recommended sandbox prerequisites.
2. Validates core commands like Docker, offers best-effort prerequisite installation when something is missing, and re-checks after any install attempt.
3. Prompts the user for preferred local and/or enterprise configuration.
4. Checks writable install location, vanity hostname safety, port availability, and enterprise tools (`helm`, `kubectl`) when needed.
5. Prompts for secure local authentication mode, a platform bootstrap admin identity, and — when using the bundled Casdoor preset — a separate bootstrap login user that is created automatically for first sign-in. The Casdoor login username, email, display name, and password must be entered explicitly by the operator; they are not auto-generated.
6. Writes the installer-managed env file for the secure local stack at `.installer/local-secure.env`, generated Helm values, and a versioned installer state manifest at `.installer/state-manifest.json`.
7. Bootstraps the local Vault instance when needed, stores installer-generated passwords/secrets there, and writes a durable installer-state snapshot so env/file-backed setup data is recoverable from Vault-backed storage.
8. Creates or refreshes a managed virtual environment under the install root and installs the CLI/runtime into that environment instead of mutating the system Python environment.
9. Applies best-effort owner-only file permissions to the local env file before launching Docker Compose.
10. Ensures the managed script directory is on `PATH` for the host OS.
11. Automatically runs `lattix up` for the secure local stack.
12. Prints portal URLs including `http://xfrontier.local`, `http://127.0.0.1`, and the detected LAN IP.

When the published installer is rerun over an existing installed app home, it preserves the installer-managed state before replacing the package contents. That keeps the existing `.installer/` env files, top-level `.env`, Docker volumes, and previously configured secure-local credentials/settings compatible with the refreshed build instead of rotating them underneath persisted data.

The durable local secret store for that flow is HashiCorp Vault running in the secure Compose stack with file storage mounted on the persistent `vault-data` Docker volume. PostgreSQL and Neo4j likewise keep their user/platform data on persistent named volumes, so installer reruns do not wipe passwords, graph state, or relational state unless the operator explicitly removes volumes.

The reinstall preservation logic also keeps operator-owned agent assets when they live inside the installed app home. Explicit in-app `FRONTIER_AGENT_ASSETS_ROOT` directories are carried forward, and user-added agent folders under `examples/agents/` are retained while package-owned sample agents still refresh from the new build.

The public installer provisions the **secure local** profile by default. That profile enables fail-closed auth, signed A2A messaging, and replay protection, but it still runs on a single local Docker host. It is not the same as a hosted or enterprise deployment with separate workload, cluster, or network isolation boundaries per agent.

For installer-managed secure-local deployments, the first authenticated operator session from the local OIDC identity plane is also treated as admin/builder-capable by default. This avoids a dead-end where the installer user signs in successfully but cannot enter builder mode because their local IAM claims do not exactly match the seeded bootstrap identifiers.

If a prerequisite is missing and automatic installation is not available, is declined, or fails, the installer exits cleanly with a list of missing tools and the next steps to finish setup.

## Removing a local install

For repeated install-testing, the canonical removal path is:

`lattix remove`

Equivalent repo-local helpers:

- `make remove`
- `./scripts/frontier.ps1 remove`

The remove flow:

1. Stops the secure and lightweight Docker Compose stacks when installer-managed env files are present.
2. Removes the Compose volumes and orphaned containers for those stacks.
3. Deletes installer-managed artifacts under `.installer/`, including the secure/lightweight env files, the installer state manifest, plus generated installer leftovers such as `generated-values.yaml` and legacy `local.env`.
4. Removes the installer-managed Vault bootstrap metadata file (`.installer/vault-bootstrap.json`) along with the rest of the installer artifact set.

It intentionally does **not** delete the repository checkout, your top-level `.env` file, editable installs, virtual environments, or PATH entries.

## Updating a local install in place

When you have already installed xFrontier locally and want the latest published build without wiping operator-created data, use:

`lattix update`

Equivalent repo-local helpers:

- `make update`
- `./scripts/frontier.ps1 update`

The update flow is intentionally non-destructive:

1. Preserves installer-managed env files under `.installer/`.
2. Preserves the top-level `.env` file when present.
3. Reinstalls the current package in place.
4. Restarts the active local stack(s) with `docker compose up -d --build --remove-orphans`.
5. Leaves Docker volumes intact, so local workflows, agents, settings, and other persisted operator data survive the refresh.

The published bootstrap installer now uses the same preservation model when it detects an existing installed app home, and the interactive prompts are seeded from the current secure-local env values. Operators can still change passwords or identity settings intentionally, but pressing Enter now keeps the current values instead of forcing a secret reset during an in-place upgrade.

The installer also records a small versioned manifest in `.installer/state-manifest.json` so future upgrade logic can reason about installer-managed state explicitly instead of inferring everything from ad hoc file discovery.

That manifest now also tracks the local installation identifier plus the Vault secret/state paths used for the secure-local install. Those references let upgrade logic keep the env compatibility files in sync with the durable Vault-backed copy instead of treating plaintext files as the only source of truth.

Legacy installs that predate the manifest are normalized into that contract automatically during install/update, so upgrade logic can migrate forward from a stable metadata shape instead of relying on one-off heuristics forever.

For editable/source-checkout installs, `lattix update` requires a clean Git working tree and then performs a fast-forward pull before refreshing the local stack.

## Local vanity URL

The Docker Compose stack now includes `local-gateway` powered by Caddy, which routes `LOCAL_STACK_HOST` to the Frontier frontend over plain HTTP for local development and proxies `/api/*` requests to the canonical backend service. The bundled Casdoor service is also exposed directly on a loopback-only port (default `http://127.0.0.1:8081`) so operator sign-in does not depend on `*.localhost` hostname resolution.

If loopback port `80` is already in use on the machine, the packaged installer automatically retries the secure gateway on a fallback loopback port and prints the adjusted URLs in the install summary.

## Local secret handling

For local deployments, the installer now handles secrets such as `A2A_JWT_SECRET` during setup:

- Operators can paste a value explicitly.
- If they leave it blank, the installer generates a strong random secret.
- In explicit shared-token mode, the installer also generates `FRONTIER_API_BEARER_TOKEN`, `POSTGRES_PASSWORD`, and `NEO4J_PASSWORD` for the local stack.
- The generated secret is written into the installer-managed secure env file (`.installer/local-secure.env`) rather than into `answers.json`.
- The installer applies best-effort owner-only permissions to the generated env file before launching the local stack.

This keeps required runtime secrets out of the interactive answer manifest and out of the normal repo workflow.

For the secure local profile, those same secrets are also mirrored into Vault under installer-managed KV paths. The `.installer/local-secure.env` file remains a compatibility surface for Compose and local tooling, but Vault is the durable local secret store and also holds a serialized snapshot of non-secret installer state that would otherwise live only in env/files.

## Local authentication options

The installer now supports two secure-local operator authentication modes:

- **OIDC** (default) — the installer writes issuer, audience, JWKS, client, and endpoint metadata for an operator identity provider. Casdoor is the default preset, but operators can instead point the install at an external OIDC-compliant IAM solution.
- **Shared bearer token** (fallback) — generates `FRONTIER_API_BEARER_TOKEN` for backend/API use when an operator does not want to wire up OIDC yet. The installer no longer injects this token into browser-visible `NEXT_PUBLIC_*` frontend configuration.

OIDC setup is intentionally flexible:

- **Casdoor preset** — uses Casdoor-oriented defaults and naming for a turnkey local IAM option.
- **External OIDC** — accepts arbitrary issuer, audience, JWKS, authorization URL, token URL, sign-in URL, sign-up URL, client ID, and scopes so the same Frontier install can sit behind another IAM solution.

That keeps operator login flexible while still allowing agents, workflows, and internal services to trust a single verified operator identity plane.

The frontend now includes a generic auth portal at `/auth`. It reads the installer-emitted OIDC environment values and presents provider-hosted **Sign in** and **Create account** actions that can point at Casdoor or another IAM solution without changing the UI flow.

The installer emits a platform bootstrap admin identity contract for secure-local installs:

- `FRONTIER_BOOTSTRAP_ADMIN_USERNAME`
- `FRONTIER_BOOTSTRAP_ADMIN_EMAIL`
- `FRONTIER_BOOTSTRAP_ADMIN_SUBJECT`
- `FRONTIER_ADMIN_ACTORS`
- `FRONTIER_BUILDER_ACTORS`

By default, the installer now proposes a unique per-install bootstrap admin username/email/subject. Operators can accept those generated defaults or override them, and the resulting identifiers are treated as both **admin** and **builder-capable** when the authenticated claims match.

When the bundled Casdoor preset is selected, the installer also collects a separate bootstrap login contract and provisions that user automatically after the stack starts:

- `CASDOOR_BOOTSTRAP_LOGIN_USERNAME`
- `CASDOOR_BOOTSTRAP_LOGIN_EMAIL`
- `CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME`
- `CASDOOR_BOOTSTRAP_LOGIN_PASSWORD`

That login user is the human-facing account you can use from the `/auth` screen immediately after install, while the existing Frontier bootstrap admin identifiers remain the backend/operator allowlist contract. In secure-local mode, the installer-managed login user still lands with builder/admin capability via the local authenticated-operator bootstrap path. Because this account is intended to be a deliberate human login, the installer now requires explicit interactive input for all Casdoor bootstrap login fields rather than offering generated defaults.

In both modes, the generated secure-local profile sets:

- `FRONTIER_RUNTIME_PROFILE=local-secure`
- `FRONTIER_REQUIRE_AUTHENTICATED_REQUESTS=true`
- `FRONTIER_ALLOW_HEADER_ACTOR_AUTH=false`
- `FRONTIER_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR=true`

That means secure local installs fail closed by default, rather than trusting unsigned identity headers.

## Secure defaults for Casdoor and external OIDC

When the installer is configured for Casdoor or another OIDC provider, it keeps the same platform security baseline:

- signed internal A2A messages stay enabled
- replay protection stays enabled
- operator requests must present verified bearer tokens
- header-only actor spoofing remains disabled
- generated local database credentials are still unique per install

The backend already validates OIDC bearer tokens generically through issuer, audience, and JWKS settings, so the installer-generated contract works for Casdoor and for other standards-compliant IAM providers.
For local secure deployments, the default Casdoor endpoints intentionally use a direct loopback URL (`http://127.0.0.1:8081` by default) so local login works even on systems that do not resolve `*.localhost` aliases consistently. The Caddy-hosted `http://casdoor.localhost` route remains available for environments where that hostname resolves. Non-local OIDC providers are still expected to use HTTPS.

## Public repository strategy

To publish this cleanly as a public installer repository, use a slim distribution layout that contains:

- `install/bootstrap.sh`
- `install/bootstrap.ps1`
- `install/frontier-installer.py`
- packaged installer/runtime assets or a release bundle reference

The current monorepo can remain the source of truth while a public repo publishes installer artifacts and release bundles.

## Enterprise federation metadata

The installer can now collect federation cluster name, region, and peer endpoints, and writes those values into generated Helm overrides for enterprise/self-hosted deployments.
