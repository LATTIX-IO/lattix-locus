# Provenance attestation: pypi/pyasn1@0.6.4

Generated from the JSON record beside this file by `lattix provenance render`; edit the JSON, not this summary.

- **Decision:** conditional (conditions: Principal accepts that PyPI owner account 'ilya' (upload rights) could not be tied to a person.; Install pinned by version and hash once Locus has a hash-locked dependency set (D-29 step 5); until then the attested sha256 values are the reference.; Re-inspect on any version change (google-auth pulls pyasn1 unpinned).)
- **Reviewer:** principal (jpbooth@lattix.io (principal)), 2026-10-04
- **Gate status:** passing
- **Origin:** not-listed (DE, CA; confidence medium). Maintained since 2022 by Christian Heimes (GitHub profile: Hamburg, Germany; Red Hat) and Simon Pichugin (GitHub profile: Vancouver, Canada; Red Hat) in the pyasn1 GitHub organization. Simon Pichugin made the v0.6.4 release commit and ran the publish workflow. The original author, Ilya Etingof (GitHub profile: 'Central Europe'; Red Hat), still holds PyPI owner rights but has no commits in the organization's repository since 2020-03-21. Locations are self-reported profile fields and were not independently verified. No evidence ties a maintainer, the organization or a funder to a P28-listed country.

## Rationale

No P28-listed origin found for maintainers, organization or funding; the Entity List, the rest of the Consolidated Screening List and the 1260H notice show no hit. All 32 wheel files are byte-identical to the v0.6.4 git tag, and the sdist differs from it only in build metadata (PKG-INFO, egg-info, setup.cfg). The release commit and the upload were made by a current maintainer from the tagged commit. The static ruleset found no telemetry, network, obfuscated-execution, install-hook or native-code finding; OSV lists no vulnerability for 0.6.4; the sandboxed import plus a DER/BER/CER round trip made no connection attempt. The conditions cover the unidentified PyPI owner, token-based uploads without PEP 740 provenance, and hash pinning.

## Artifacts

- `pyasn1-0.6.4-py3-none-any.whl` sha256 `deda9277cfd454080ec40b207fb6df82206a3a2688735233cdcd8d3d565f088b` (inspected)
- `pyasn1-0.6.4.tar.gz` sha256 `9c447d8431c947fe4c8febc4ed9e760bc29011a5b01e5c74b67025bd9fb8ce81` (inspected)

## Maintainers and funding

- Simon Pichugin (maintainer; PyPI owner; release manager for 0.6.4, Red Hat, Vancouver, Canada [self-reported GitHub profile])
- Christian Heimes (maintainer; PyPI owner; maintainer of record in package metadata, Red Hat; CPython core developer (python.org e-mail), Hamburg, Germany [self-reported GitHub profile])
- Ilya Etingof (original author (2005-2020); PyPI owner; inactive in the org repository, Red Hat, Central Europe [self-reported GitHub profile (no country given)])
- unidentified PyPI account 'ilya' (PyPI owner (upload rights))

Funding: No funding source declared: .github/FUNDING.yml has only a 'custom' link to the documentation site. No GitHub Sponsors, Open Collective or corporate sponsorship found. The maintainers' employer (Red Hat) is named on their profiles; whether the work is employer-funded was not established.

## Entity-list checks

- us-commerce-entity-list: **no-match** (2026-10-04; Name screening of 3419 Entity List (EL) rows in the US Consolidated Screening List CSV (primary and alternate names): persons by full-name token match, organizations by normalized phrase; surname plus a given-name variant (same first three letters) is reported too. Every hit is a possible match for a reviewer.)
- other: **no-match** (2026-10-04; Informational: the same screening over all 26185 rows of the US Consolidated Screening List (SDN, MEU, CMIC, DPL, UVL, ISN, ...).)
- dod-1260h: **no-match** (2026-10-04; Phrase screening of the full text of the latest Federal Register Section 1260H notice (Federal Register 2026-11571, 2026-06-10); the list names companies, so a person can only hit by surname.)

## SBOM and vulnerabilities

- SBOM: `sbom/pypi/pyasn1@0.6.4.cdx.json` (locus-provenance sbom fallback (dist-info metadata, CycloneDX 1.5), 1 components)
- Vulnerabilities (OSV API (https://api.osv.dev/v1/query), online, 2026-10-04): none-known

## Static review

193 files, ruleset `locus-provenance-static/1`. Source comparison: pyasn1-0.6.4-py3-none-any.whl: 32/32 files identical to pyasn1/pyasn1@v0.6.4; pyasn1-0.6.4.tar.gz: 148/155 files identical to pyasn1/pyasn1@v0.6.4.

- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/PKG-INFO: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/pyasn1.egg-info/PKG-INFO: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/pyasn1.egg-info/SOURCES.txt: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/pyasn1.egg-info/dependency_links.txt: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/pyasn1.egg-info/top_level.txt: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/pyasn1.egg-info/zip-safe: build metadata not in pyasn1/pyasn1@v0.6.4
- `source-build-metadata` (info, benign) pyasn1-0.6.4.tar.gz/setup.cfg: build metadata not in pyasn1/pyasn1@v0.6.4

## Dynamic egress test

- Status: **pass** (isolation: windows-appcontainer, network denied, audit hook; network denied; 2026-10-04)
- Exercise: import pyasn1; DER/CER encode and DER/BER decode a SEQUENCE (INTEGER, UTF8String, OID, GeneralizedTime)
- Jail positive control (loopback connection to the host): blocked
- Connection or process attempts: 0
- Other recorded events: none
- Limitations:
  - Python audit hooks see socket, DNS, urllib/http.client and process events; native code calling the OS socket API directly raises no audit event. The jail blocks such calls but they are not logged, so native binaries are also scanned statically for networking imports.
  - Only the import and the stated exercise were run; code paths they do not reach were not observed.
