# Provenance attestation: pypi/sqlite-vec@0.1.9

Generated from the JSON record beside this file by `lattix provenance render`; edit the JSON, not this summary.

- **Decision:** conditional (conditions: Principal accepts the native vec0 binaries at the review level recorded in the static findings (provenance-linked CI build, networking-import scan, sandboxed load; no reverse engineering or reproducible build).; Locus does not load sqlite-vec today (only langgraph.store.sqlite / SqliteStore imports it, and Locus uses SqliteSaver). If Locus starts using SqliteStore or loads the extension, re-inspect and exercise that code path.; Install pinned by version and hash once Locus has a hash-locked dependency set (D-29 step 5); langgraph-checkpoint-sqlite only requires sqlite-vec>=0.1.6.; Re-inspect on any version change.)
- **Reviewer:** agent-prepared, principal sign-off pending, 2026-10-04
- **Gate status:** not passing: principal sign-off pending; open: static findings needing review
- **Origin:** not-listed (US; confidence medium). Written and released by Alex Garcia (GitHub asg017, profile: Los Angeles, California; account since 2015), who is the only PyPI owner and made 441 of the repository's commits. The main sponsor is Mozilla through Mozilla Builders; Fly.io, Turso, SQLite Cloud and Shinkai are named as additional sponsors. The PyPI files' native binaries are byte-identical to the npm packages of the same release, whose SLSA provenance names the v0.1.9 tag and release workflow run 23786847732. The location is self-reported. No evidence ties the author or a sponsor to a P28-listed country; the sponsors' own origins were not individually established.

## Rationale

No P28-listed origin found: the sole author and PyPI owner self-reports Los Angeles, and the main sponsor is Mozilla. The Entity List and the 1260H notice show no match; four informational SDN name hits are resolved as different people (reasons in the 'other' check). The five native binaries in the PyPI wheels are byte-identical to the npm packages of the same release, whose SLSA provenance names the v0.1.9 tag and release workflow run, and no networking imports were found in them. The Python wrapper only computes the extension path and serializes vectors. The win_amd64 wheel loaded vec0 and ran a KNN query in the AppContainer with egress denied and made no connection attempt; OSV lists no vulnerabilities for 0.1.9. The wheel metadata carries placeholder values ('TODO') from the packaging tool, which is cosmetic.

## Artifacts

- `sqlite_vec-0.1.9-py3-none-macosx_10_6_x86_64.whl` sha256 `1b62a7f0a060d9475575d4e599bbf94a13d85af896bc1ce86ee80d1b5b48e5fb` (inspected)
- `sqlite_vec-0.1.9-py3-none-macosx_11_0_arm64.whl` sha256 `1d52e30513bae4cc9778ddbf6145610434081be4c3afe57cd877893bad9f6b6c` (inspected)
- `sqlite_vec-0.1.9-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl` sha256 `4e921e592f24a5f9a18f590b6ddd530eb637e2d474e3b1972f9bbeb773aa3cb9` (inspected)
- `sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux1_x86_64.whl` sha256 `1515727990b49e79bcaf75fdee2ffc7d461f8b66905013231251f1c8938e7786` (inspected)
- `sqlite_vec-0.1.9-py3-none-win_amd64.whl` sha256 `4a28dc12fa4b53d7b1dced22da2488fade444e96b5d16fd2d698cd670675cf32` (inspected)

## Maintainers and funding

- Alex Garcia (author, sole PyPI owner, release manager, independent; Mozilla Builders project, Los Angeles, California, US [self-reported GitHub profile])

Funding: Sponsored mainly by Mozilla (Mozilla Builders), with additional sponsorship from Fly.io, Turso, SQLite Cloud and Shinkai, as stated in the README. The sponsors' countries of origin were not individually established in this inspection; Mozilla's sponsorship is confirmed by Mozilla's own announcement.

## Entity-list checks

- us-commerce-entity-list: **no-match** (2026-10-04; Name screening of 3419 Entity List (EL) rows in the US Consolidated Screening List CSV (primary and alternate names): persons by full-name token match, organizations by normalized phrase; surname plus a given-name variant (same first three letters) is reported too. Every hit is a possible match for a reviewer.)
- other: **reviewed-no-match** (2026-10-04; Informational: the same screening over all 26185 rows of the US Consolidated Screening List (SDN, MEU, CMIC, DPL, UVL, ISN, ...).)
- dod-1260h: **no-match** (2026-10-04; Phrase screening of the full text of the latest Federal Register Section 1260H notice (Federal Register 2026-11571, 2026-06-10); the list names companies, so a person can only hit by surname.)

## SBOM and vulnerabilities

- SBOM: `sbom/pypi/sqlite-vec@0.1.9.cdx.json` (locus-provenance sbom fallback (dist-info metadata, CycloneDX 1.5), 2 components)
- Vulnerabilities (OSV API (https://api.osv.dev/v1/query), online, 2026-10-04): none-known

## Static review

30 files, ruleset `locus-provenance-static/1`.

- `native-extension` (review, needs-review) sqlite_vec-0.1.9-py3-none-macosx_10_6_x86_64.whl/sqlite_vec/vec0.dylib: native binary: needs review (opaque to the AST rules) -- Agent review: built in GitHub Actions from tag v0.1.9 (byte-identical to the npm packages with SLSA provenance for run 23786847732); the networking-import scan found nothing; the win_amd64 build was loaded and exercised in the AppContainer with no connection attempt. Not reverse-engineered and the build was not reproduced, so the principal decides whether this level of review is enough.
- `native-extension` (review, needs-review) sqlite_vec-0.1.9-py3-none-macosx_11_0_arm64.whl/sqlite_vec/vec0.dylib: native binary: needs review (opaque to the AST rules) -- Agent review: built in GitHub Actions from tag v0.1.9 (byte-identical to the npm packages with SLSA provenance for run 23786847732); the networking-import scan found nothing; the win_amd64 build was loaded and exercised in the AppContainer with no connection attempt. Not reverse-engineered and the build was not reproduced, so the principal decides whether this level of review is enough.
- `native-extension` (review, needs-review) sqlite_vec-0.1.9-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl/sqlite_vec/vec0.so: native binary: needs review (opaque to the AST rules) -- Agent review: built in GitHub Actions from tag v0.1.9 (byte-identical to the npm packages with SLSA provenance for run 23786847732); the networking-import scan found nothing; the win_amd64 build was loaded and exercised in the AppContainer with no connection attempt. Not reverse-engineered and the build was not reproduced, so the principal decides whether this level of review is enough.
- `native-extension` (review, needs-review) sqlite_vec-0.1.9-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux1_x86_64.whl/sqlite_vec/vec0.so: native binary: needs review (opaque to the AST rules) -- Agent review: built in GitHub Actions from tag v0.1.9 (byte-identical to the npm packages with SLSA provenance for run 23786847732); the networking-import scan found nothing; the win_amd64 build was loaded and exercised in the AppContainer with no connection attempt. Not reverse-engineered and the build was not reproduced, so the principal decides whether this level of review is enough.
- `native-extension` (review, needs-review) sqlite_vec-0.1.9-py3-none-win_amd64.whl/sqlite_vec/vec0.dll: native binary: needs review (opaque to the AST rules) -- Agent review: built in GitHub Actions from tag v0.1.9 (byte-identical to the npm packages with SLSA provenance for run 23786847732); the networking-import scan found nothing; the win_amd64 build was loaded and exercised in the AppContainer with no connection attempt. Not reverse-engineered and the build was not reproduced, so the principal decides whether this level of review is enough.

## Dynamic egress test

- Status: **pass** (isolation: windows-appcontainer, network denied, audit hook; network denied; 2026-10-04)
- Exercise: import sqlite_vec; load the vec0 native extension into SQLite, check vec_version(), create a vec0 table, insert two vectors and run a KNN query
- Jail positive control (loopback connection to the host): blocked
- Connection or process attempts: 0
- Other recorded events: `sqlite3.enable_load_extension`, `sqlite3.load_extension`, `sqlite3.enable_load_extension`
- Limitations:
  - Python audit hooks see socket, DNS, urllib/http.client and process events; native code calling the OS socket API directly raises no audit event. The jail blocks such calls but they are not logged, so native binaries are also scanned statically for networking imports.
  - Only the import and the stated exercise were run; code paths they do not reach were not observed.
