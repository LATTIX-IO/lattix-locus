# Provenance inspection (P28, D-29)

Status: implemented 2026-10-04 (LOCUS-358). Policy: [P28](product/03-product-principles.md)
as amended by [D-29](product/20-roadmap-and-decisions.md). Controls summary:
[SECURITY.md](../SECURITY.md).

Open-source software and **locally run** model weights from China (including Hong Kong
and Macao), Russia, Iran, North Korea, Cuba, Venezuela or Belarus are admitted only after
they pass a recorded provenance inspection, and only at the inspected version. Hosted,
API or web inference and services from those origins stay excluded, whatever an
inspection says. This page describes the process, the tooling and the gates.

## 1. What lives where

| Path | What |
|---|---|
| `provenance/attestations/<ecosystem>/<name>@<version>.json` | One attestation per inspected artifact and exact version, with a generated `.md` summary beside it. |
| `locus_tooling/provenance/attestation.schema.json` | JSON Schema (2020-12) for an attestation. Validated in CI and at runtime by a small built-in validator (`schema_lite.py`); the tests cross-check it with `jsonschema`. |
| `provenance/research/<ecosystem>/<name>@<version>.json` | The inspector's research input: origin, maintainers and funding with evidence links, names to screen, reviewer notes, the exercise script. Kept so an inspection can be re-run. |
| `provenance/sbom/<ecosystem>/<name>@<version>.cdx.json` | CycloneDX SBOM referenced by the attestation (path and SHA-256). |
| `provenance/origins.json` | Reviewed origin record for every declared dependency (and the transitive packages listed in `docs/development/deep-agents-harness.md` section 8). The CI gate reads it; no network. |
| `provenance/unknown_origin_allowlist.json` | Packages of unknown origin that the gate still allows, each with a reason, a reviewer and a date. |
| `provenance/.cache/` | OSV answers and screening downloads (gitignored). |
| `locus_tooling/provenance/` | The tooling: `inspection.py` (orchestration), `static_rules.py`, `dynamic.py` + `egress_harness.py`, `sbom.py`, `vulns.py`, `screening.py`, `models.py`, `gate.py`, `records.py`, `cli.py`. |

## 2. The attestation record

Fields (see the schema for the exact shape):

- `ecosystem`, `name`, `version`, `artifacts` (every distribution file with its SHA-256,
  and whether it was actually inspected);
- `origin`: P28 status (`listed`, `not-listed`, `unknown`), countries, organizations,
  summary, confidence, and evidence (claim, URL, retrieval date);
- `maintainers` (role, affiliation, location and the basis for it) and `funding`;
- `entity_list_checks`: the US Commerce Entity List and the DoD Section 1260H list (both
  required), each with method, source URL, source SHA-256 and date; a third,
  informational check covers the rest of the US Consolidated Screening List;
- `sbom` (generator: `syft` when installed, else the dist-info fallback), `vulnerabilities`
  (OSV), `static_findings`, `dynamic_egress`;
- `model` (weights only): lineage, the weights-only format check, the behavioural/red-team
  eval, inference network;
- `decision`: `pass`, `fail` or `conditional` with conditions and a rationale;
- `review`: `principal`, or `agent-prepared, principal sign-off pending`, and the date.

**Passing** (what the gates accept) is stricter than "the decision says pass": the record
must validate, be internally consistent (no `pass` over a connection attempt, a blocking
finding, an entity-list match or refused weights), have every check complete (dynamic test
passed with the jail positive control blocked, screening run and every hit resolved, no
finding left at `needs-review`, the vulnerability lookup available), carry the
**principal's** sign-off, and, for models, a passing behavioural eval. `lattix provenance
verify` prints the verdict for each record.

## 3. Inspecting a package

1. **Research the origin.** Copy an existing file in `provenance/research/pypi/` and fill
   in the origin, maintainers and funding from primary sources: PyPI metadata and owners
   (`package_roles` over PyPI's XML-RPC API), the source repository and organization,
   maintainer profiles, release commits and the CI run that published the files, funding
   files and sponsor announcements. Say what each piece of evidence shows and how strong it
   is: a GitHub profile location is self-reported. When origin cannot be established, say
   `unknown`; do not infer origin from a person's name. Add `screening_queries` (people and
   organizations), the `modules` to import and an `exercise` script that uses the package
   the way Locus does, and `source` (`repo`, `tag`, `package_dirs`) when the tagged source
   is on GitHub.
2. **Run the inspection** (needs network for PyPI, GitHub, OSV and the screening lists, and
   a confining sandbox for the dynamic test):

   ```bash
   lattix provenance inspect pypi NAME==VERSION \
       --research provenance/research/pypi/NAME@VERSION.json --fetch-screening
   ```

   It downloads every distribution file and checks it against PyPI's published SHA-256;
   unpacks them without executing anything; runs the static ruleset; compares each file
   with the tagged source (`source`); writes the SBOM; queries OSV; screens the names
   against the Entity List, the rest of the Consolidated Screening List and the latest
   1260H notice from the Federal Register; and imports the package and runs the exercise in
   the sandbox with egress denied. The record is written as *agent-prepared, principal
   sign-off pending*; the tooling never sets the reviewer to `principal`.
3. **Review the draft.** Resolve every screening hit in `entity_reviews` (`different-entity`
   with the reason, or `same-entity`), annotate findings in `finding_notes` (a disposition
   and a note; only a person may downgrade `needs-review`), and write the `decision` with
   its conditions and rationale. Re-run the command; it regenerates the record.

The static ruleset (`locus-provenance-static/1`) flags telemetry and analytics SDK imports,
raw sockets and HTTP clients, `exec`/`eval` of decoded data and other obfuscated execution,
embedded code objects, long encoded blobs, process spawning, install-time hooks
(`setup.py` `cmdclass`, overridden setuptools commands, executable `.pth` lines), and
native binaries, which are always flagged for review and scanned for networking imports
and symbols.

The dynamic test runs in the platform's confining tier: AppContainer with no network
capability on Windows (using the Locus-managed toolchain interpreter), bubblewrap with
`--unshare-net` on Linux, seatbelt on macOS. A `sys.addaudithook` hook in the child records
and denies every socket, DNS, HTTP and process event, then waits a moment for background
threads. A positive control (a loopback connection to a listener on the host) must be
blocked, or the test cannot pass. Without a confining tier the test is `not-run`: inspected
code never runs on the host.

## 4. Inspecting local model weights

```bash
lattix provenance inspect model --ollama qwen2.5-coder:7b --lineage qwen \
    --research provenance/research/ollama/qwen2.5-coder@7b.json --fetch-screening
```

(or `--path <weights dir> --name <name> --version <revision>`). Only safetensors and GGUF
pass the format check; pickle-capable files (`.bin`, `.pt`, `.pth`, `.ckpt`, `.pkl`, ...),
files whose bytes look like a pickle or a torch zip, loader code (`*.py`, native
libraries) and configs that request custom code (`auto_map`, `trust_remote_code`) are
refused. The behavioural and red-team eval comes from a hook
(`locus_tooling.provenance.models.install_behavioural_eval`); until LOCUS-351 installs a
suite it records `pending`, so no model attestation can pass yet.

At runtime the model client (`provenance_denial()` in `locus_runtime/model_client.py`)
treats a model of a P28-listed lineage as follows:

- on a non-loopback endpoint (hosted, API or web inference): refused, whatever the
  attestation says;
- on a loopback engine: allowed only with a passing attestation for
  `ollama/<model>@<tag>` whose weights digests match the layers in the local Ollama manifest
  (`OLLAMA_MODELS` or `~/.ollama/models`). Other local engines cannot bind a model to
  attested weights and are refused.

Attestations are looked up in `LOCUS_PROVENANCE_DIR` (if set), `<app home>/provenance`,
then the source checkout's `provenance/`.

## 5. Principal sign-off

The principal, not an agent, signs off. To sign:

1. Read the record and its evidence; resolve every `needs-review` finding (set its
   `disposition` and `note`) and every open condition you are not accepting.
2. Set `review.reviewer` to `principal`, `review.name` and `review.date`, then run
   `lattix provenance render` and `lattix provenance verify`.
3. Merge through a reviewed pull request. Changes under `provenance/` need security review
   (SECURITY.md, review checkpoints).

The same applies to `origins.json` (its `reviewed` block) and to each allowlist entry's
`reviewed_by`.

## 6. The CI dependency gate

`python -m locus_tooling.provenance.gate` (also `lattix provenance gate`) runs in the CI
quality job. It reads `[project].dependencies` in `pyproject.toml`,
`apps/backend/requirements.txt` and the transitive entries of `origins.json`, and fails when:

- a package has no entry in `origins.json` (adding a dependency means adding its origin
  record with evidence);
- a package from a P28-listed origin is not pinned with `==`, or has no passing attestation
  for that exact version;
- a package of unknown origin is not in `unknown_origin_allowlist.json` with a reason, a
  reviewer and a date;
- `pyproject.toml` and `requirements.txt` disagree on a package's specifier.

Allowlist entries still awaiting the principal's sign-off pass with a warning;
`--strict` makes them fail.

## 7. Re-inspection on a version bump

An attestation covers one exact version. When a dependency changes version, or a
transitive package's resolved version moves:

1. Copy the research file to the new version and update anything that changed
   (maintainers, `source.tag`, the exercise if the API changed).
2. Re-run the inspection and the review, and get a new sign-off.
3. Update `origins.json` (the resolved `version` of a transitive entry) in the same change.

For a listed-origin package the gate fails on the bump until the new attestation is signed.
Re-inspect too when a finding is reported against an attested dependency (D-29 trigger).

## 8. Current inspections

| Package | Version | Origin | Draft decision | Status |
|---|---|---|---|---|
| pyasn1 | 0.6.4 | not listed (DE, CA; medium) | conditional | agent-prepared, sign-off pending |
| pyasn1-modules | 0.4.2 | not listed (DE, CA; medium) | conditional | agent-prepared, sign-off pending |
| sqlite-vec | 0.1.9 | not listed (US; medium) | conditional | agent-prepared, sign-off pending; native binaries need principal review |

`origins.json` covers 47 packages: 43 not listed, 4 unknown (httpx, presidio-analyzer,
psycopg, filetype), all on the allowlist pending sign-off. None is P28-listed. The
never-imported nats-py, opa-python-client, structlog and zeroconf were dropped with their
records (LOCUS-352).

## 9. Known limits

- **Coverage.** The gate covers the declared dependencies and the transitive packages
  documented for the Deep Agents stack, not the full transitive closure: Locus has no lock
  file yet. A hash-locked dependency set (D-29 step 5, "pinned version and hash") is a
  follow-up; until then the attested SHA-256 values are the reference.
- **Native code.** Audit hooks do not see native code calling the OS socket API. The jail
  blocks it, but it is not logged; native binaries are scanned for networking imports and
  flagged for review, not reverse-engineered.
- **Screening** is name-based and cannot tell namesakes apart, so every hit needs a
  person's resolution. The 1260H check uses the latest designation notice only.
- **Build provenance.** PEP 740 attestations are recorded when present (none of the first
  three packages has them). Cross-checks against other registries' SLSA provenance were
  done by hand for sqlite-vec; Sigstore signatures were not re-verified.
- **Scanners.** syft is used when installed, otherwise the dist-info fallback (recorded in
  the attestation). osv-scanner, pip-audit, grype and semgrep are not required: the OSV API
  and the built-in ruleset cover the D-29 checks without new dependencies.
