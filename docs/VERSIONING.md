# Release versioning (D-31)

Status: adopted 2026-10-04 (decision D-31, principal: "Roll the third digit as
just an integer + 1 with a cap of 99999"). This page defines what each digit
means, so people and the self-improvement loop (RSI) bump them the same way.
CODEOWNERS-protected: changing these rules is a principal-reviewed change.

## 1. The scheme

Every published build is `MAJOR.MINOR.PATCH`, three plain integers with no
leading zeros and no pre-release suffix (`0.2.0`, `0.2.17`, `1.0.3`).

| Digit | Source | Who changes it |
|---|---|---|
| MAJOR, MINOR | the repo-root `VERSION` file (`0.2` plus a newline) | a PR that follows section 3 |
| PATCH | a build counter: 1 + the highest PATCH among the release tags of that `MAJOR.MINOR` (`dev-v*`, `stable-v*`, `v*`), or 0 for the first build | nobody: CI computes it for every published build |

* **Cap.** PATCH is at most **99999**. When the next build would pass it, the
  build fails with "a MINOR bump is required". The counter never wraps or
  truncates. At one build per merge that is years of merges per MINOR.
* **Immutable releases.** A version is published once. The Dev workflow refuses
  to publish a version that already has a `dev-v`, `stable-v` or `v` tag or a
  `dev-v` release, and never uploads into an existing release (`--clobber` is
  used only for the rolling `channel-dev` / `channel-stable` pointers). A Stable
  promotion keeps the Dev build's version and bytes (`dev-v0.2.17` becomes
  `stable-v0.2.17`).
* **Pinned manifests.** `apps/desktop-tauri/src-tauri/tauri.conf.json`,
  `apps/desktop-tauri/src-tauri/Cargo.toml`, `apps/frontend/package.json` (and
  its lock file), `pyproject.toml`, `install/manifest.json` and
  `helm/lattix-locus/Chart.yaml` (`version`, `appVersion`) say
  `<MAJOR>.<MINOR>.0` (`PINNED_MANIFESTS` in `locus_tooling/versioning.py`). Builds set the
  full version with a config overlay and stamp the backend
  (`python -m locus_tooling.build_info`). `lattix version sync` updates them
  after a `VERSION` edit; CI fails when they drift.
* **Why 0.2.** The unified single-user desktop app, Deep Agents as the default
  runtime and the new update channels are new capabilities, which is a MINOR
  bump under section 3. `0.2.N` also ranks above the old `0.1.0-dev.N` Dev
  builds and the June `v0.1.1` Stable, so existing installs update forward.

### Windows installers

Windows ships the NSIS installer only (`bundle.targets` has no `msi`). Windows
Installer (MSI/WiX) caps the third version field at 65535, below the PATCH cap,
so an MSI build would start failing at PATCH 65536. The updater already uses the
NSIS artifacts and the app installs per user, so nothing else needs MSI. Do not
lower the cap to make MSI fit; add MSI back only with a separate version mapping.

## 2. How a build gets its version

| Workflow | Version |
|---|---|
| `desktop-dev.yml` (every merge to `main`) | `git ls-remote --tags origin` (complete, no pagination) piped to `python locus_tooling/versioning.py next`; release `dev-v<version>`, title "Lattix Locus `<version>` (Dev)". The `desktop-dev-channel` concurrency group keeps two main builds from taking the same PATCH, and the publish job re-checks before it creates anything. `channel-dev` only ever moves to a newer version. |
| `desktop-promote.yml` (manual) | input `version`, validated as `^(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]{0,4})$` (PATCH <= 99999); copies `dev-v<version>` to `stable-v<version>` |
| `desktop-release.yml` (manual or `v*` tag) | the tag without `v`, or the input; same rules, and MAJOR.MINOR must equal the commit's `VERSION`. A build that creates a release refuses a version the channels already published. |

A re-run of a failed Dev publish refuses the version it already published:
re-run the whole workflow instead, which takes the next PATCH.

Locally: `lattix version next --tags-file tags.txt` (the same code as CI).

## 3. Bump rules

Decide by the most significant change in the PR. When in doubt between two
levels, take the higher one and say why in the release note.

### PATCH (third digit): never edited by hand

Every build. Fixes, refactors, performance work, docs, tests, dependency patch
or minor upgrades, a policy tightening that does not change a default, UI
polish, new eval tasks.

### MINOR (second digit; PATCH restarts at 0)

* a new user-visible capability, setting, connector type, surface or skill type;
* a changed default behaviour: the default runtime, memory on by default, a
  policy default that widens **or** narrows what is allowed;
* an additive new version of a D-28 port (`docs/ARCHITECTURE-MODULES.md`, the
  `PORT_VERSION` constants);
* an automatic, forward-only data or config migration;
* a major-version upgrade of a core dependency (LangGraph / LangChain / Deep
  Agents, Tauri, Next.js, Python);
* while MAJOR is 0, a breaking change also goes here, with an
  "Action required" paragraph in its release note.

### MAJOR (first digit; MINOR and PATCH restart at 0)

From 1.0 on:

* removal or incompatible change of a public contract: a D-28 port's major
  version, the REST API, the A2A / MCP server surface, CLI semantics, the skill
  manifest / policy / grant schema;
* user data or config that needs a manual export or re-setup (no automatic
  migration);
* a security-model change that invalidates existing grants or consents
  (re-consent);
* dropping a supported OS or architecture.

The 0.x to 1.0 transition is a principal decision.

## 4. Making a bump

1. **Declare the impact.** Every PR body has one line
   `Release-Impact: patch|minor|major` (the PR template starts it at `patch`;
   the loop runner writes it from the run's `VERSION` diff).
2. **MINOR or MAJOR:** in the same PR, edit `VERSION` (`0.2` to `0.3`; `0.9` to
   `1.0`), run `lattix version sync` (or
   `python locus_tooling/versioning.py sync`), and add a release-notes fragment
   (section 5).
3. **CI checks it** (`ci.yml` job "Release version (D-31)",
   `python locus_tooling/versioning.py check`, the code behind
   `lattix version check`):
   * `VERSION` is well-formed and the pinned manifests say `<VERSION>.0`;
   * a `VERSION` change is exactly +1 MINOR (same MAJOR) or +1 MAJOR with MINOR
     0, never backwards or skipping;
   * the PR body declares exactly one impact and it equals the `VERSION` change;
   * a MINOR or MAJOR bump adds a release-notes fragment with the same impact
     and version;
   * a detected "bump required" signal is not shipped as a PATCH.

   The check reads the PR body from the event payload: after editing the
   declaration, push a commit (a re-run reuses the old body).

Locally: `lattix version check --base-ref origin/main --pr-body-file body.md`
(commit first; the check compares committed `HEAD`).

### Deterministic "bump required" signals

The check fails a PATCH change that has any of these:

| Signal | Detected from |
|---|---|
| a port contract version added, removed or changed (D-28) | a module-level `PORT_VERSION = ...` in any `.py` file |
| a persisted schema version changed (automatic migration) | a module-level `*SCHEMA_VERSION = ...` (for example `locus_runtime/telemetry/sqlite_store.py`, `harness/run_store.py`, `harness/trajectory.py`) |
| a new migration file | an added file under any `migrations/` directory |
| a changed default or a new / removed setting | any code change (comments, formatting and docstrings ignored) in `PlatformSettings` (`apps/backend/app/main.py`) or `default_runtime_name` (`locus_runtime/harness/runtimes.py`); the list is `DEFAULT_SURFACES` in `locus_tooling/versioning.py`, and a test fails if a listed name disappears |

### Reviewer checklist (not detectable reliably)

* a new user-visible capability, connector type, surface or skill type;
* a policy default (`policies/*.rego`) that widens or narrows what is allowed
  (policy files are principal-reviewed anyway);
* a core dependency's major version (LangGraph / LangChain / Deep Agents, Tauri,
  Next.js, Python);
* a breaking change while MAJOR is 0 has an "Action required" release note;
* from 1.0: REST API, A2A / MCP surface, CLI semantics or skill / policy /
  grant schema changes; manual re-setup; re-consent; dropped OS or arch.

## 5. Release-notes fragments

One Markdown file per PR that bumps MINOR or MAJOR (optional for a PATCH), in
`docs/release-notes/`, named after the change (`2026-10-04-patch-counter.md`):

```markdown
Release-Impact: minor
Version: 0.3

What changed for users, in one or two sentences.
Action required: ... (only for a breaking change)
```

The header block ends at the first blank line. `Version` (the new
`MAJOR.MINOR`) is required for minor and major.

## 6. Who may bump (D-22)

* **PATCH:** CI, every build.
* **MINOR:** anyone, including the RSI loop. `VERSION` is not a gate-write path
  (the agent may edit it without an approval prompt). The loop runner then sets
  the pinned manifests host-side (the agent may not write `pyproject.toml`) and
  writes `Release-Impact: minor` into the PR. The D-22 merge guard lets the PR
  auto-merge when `VERSION` rose by exactly one MINOR and the protected
  manifests (`tauri.conf.json`, `Cargo.toml`) changed only their version field.
* **MAJOR:** the principal. `VERSION` is a CODEOWNERS path, and the merge guard
  (`locus_runtime/loop_runner/merge_guard.py`, rule 5) holds any PR whose
  `VERSION` change increments MAJOR, or is malformed, out of step, added,
  removed or renamed, for principal approval.
* **The rules and their check** (`docs/VERSIONING.md`,
  `locus_tooling/versioning.py`) are protected paths: CODEOWNERS, the merge
  guard's built-in baseline, and (for the check) a gate definition the gateway
  asks about before an agent writes it (`locus_runtime/gate_definitions.py`,
  mirrored in `policies/filesystem_access.rego`).
