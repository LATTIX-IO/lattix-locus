# Provenance attestation: pypi/pyasn1-modules@0.4.2

Generated from the JSON record beside this file by `lattix provenance render`; edit the JSON, not this summary.

- **Decision:** conditional (conditions: Principal accepts that PyPI owner account 'ilya' (upload rights) could not be tied to a person.; Principal accepts that 0.4.2 was uploaded outside CI (no workflow run found), so the PyPI files are tied to the tag only by the byte-for-byte source comparison.; Install pinned by version and hash once Locus has a hash-locked dependency set (D-29 step 5).; Re-inspect on any version change (google-auth pulls pyasn1-modules unpinned).)
- **Reviewer:** principal (jpbooth@lattix.io (principal)), 2026-10-04
- **Gate status:** passing
- **Origin:** not-listed (DE, CA; confidence medium). Same maintainers and PyPI owners as pyasn1: maintained in the pyasn1 GitHub organization by Christian Heimes (GitHub profile: Hamburg, Germany; Red Hat) and Simon Pichugin (GitHub profile: Vancouver, Canada; Red Hat), who made the v0.4.2 release commit. Original author Ilya Etingof (profile 'Central Europe'); the second-largest historical contributor is Russ Housley (RFC module definitions). Locations are self-reported and not independently verified. No evidence ties a maintainer, the organization or a funder to a P28-listed country.

## Rationale

No P28-listed origin found for maintainers, organization or funding; the Entity List, the rest of the Consolidated Screening List and the 1260H notice show no hit. All 132 wheel files are byte-identical to the v0.4.2 git tag; the sdist differs only in build metadata (setup.cfg, PKG-INFO, egg-info). The only other static findings are two sdist-only example scripts under tools/ (an OCSP client and an SNMP GET example) that use the network when run by hand and are not in the wheel. OSV lists no vulnerability for 0.4.2; the sandboxed import plus an X.509 decode/re-encode with rfc5280 and rfc2459 made no connection attempt.

## Artifacts

- `pyasn1_modules-0.4.2-py3-none-any.whl` sha256 `29253a9207ce32b64c3ac6600edc75368f98473906e8fd1043bd6b5b1de2c14a` (inspected)
- `pyasn1_modules-0.4.2.tar.gz` sha256 `677091de870a80aae844b1ca6134f54652fa2c8c5a52aa396440ac3106e941e6` (inspected)

## Maintainers and funding

- Simon Pichugin (maintainer; PyPI owner; release commit for 0.4.2, Red Hat, Vancouver, Canada [self-reported GitHub profile])
- Christian Heimes (maintainer; PyPI owner; maintainer of record in package metadata, Red Hat, Hamburg, Germany [self-reported GitHub profile])
- Ilya Etingof (original author; PyPI owner; inactive, Red Hat, Central Europe [self-reported GitHub profile (no country given)])
- Russ Housley (historical contributor (RFC ASN.1 module definitions); not a PyPI owner)
- unidentified PyPI account 'ilya' (PyPI owner (upload rights))

Funding: No current funding source declared. .github/FUNDING.yml has a 'custom' link to http://snmplabs.com/sponsorship.html, the original author's legacy site (not checked; plain http). No GitHub Sponsors, Open Collective or corporate sponsorship found.

## Entity-list checks

- us-commerce-entity-list: **no-match** (2026-10-04; Name screening of 3419 Entity List (EL) rows in the US Consolidated Screening List CSV (primary and alternate names): persons by full-name token match, organizations by normalized phrase; surname plus a given-name variant (same first three letters) is reported too. Every hit is a possible match for a reviewer.)
- other: **no-match** (2026-10-04; Informational: the same screening over all 26185 rows of the US Consolidated Screening List (SDN, MEU, CMIC, DPL, UVL, ISN, ...).)
- dod-1260h: **no-match** (2026-10-04; Phrase screening of the full text of the latest Federal Register Section 1260H notice (Federal Register 2026-11571, 2026-06-10); the list names companies, so a person can only hit by surname.)

## SBOM and vulnerabilities

- SBOM: `sbom/pypi/pyasn1-modules@0.4.2.cdx.json` (locus-provenance sbom fallback (dist-info metadata, CycloneDX 1.5), 1 components)
- Vulnerabilities (OSV API (https://api.osv.dev/v1/query), online, 2026-10-04): none-known

## Static review

424 files, ruleset `locus-provenance-static/1`. Source comparison: pyasn1_modules-0.4.2-py3-none-any.whl: 132/132 files identical to pyasn1/pyasn1-modules@v0.4.2; pyasn1_modules-0.4.2.tar.gz: 278/286 files identical to pyasn1/pyasn1-modules@v0.4.2.

- `network-client-import` (review, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules-0.4.2/tools/ocspclient.py:15: imports network module 'urllib.request' -- sdist-only example script (tools/), not in the wheel and never installed or imported; it contacts the network only when a person runs it by hand
- `network-call` (review, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules-0.4.2/tools/ocspclient.py:159: calls urllib2.urlopen() -- sdist-only example script (tools/), not in the wheel and never installed or imported; it contacts the network only when a person runs it by hand
- `network-client-import` (review, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules-0.4.2/tools/snmpget.py:10: imports network module 'socket' -- sdist-only example script (tools/), not in the wheel and never installed or imported; it contacts the network only when a person runs it by hand
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/PKG-INFO: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/PKG-INFO: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/SOURCES.txt: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/dependency_links.txt: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/requires.txt: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/top_level.txt: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/pyasn1_modules.egg-info/zip-safe: build metadata not in pyasn1/pyasn1-modules@v0.4.2
- `source-build-metadata` (info, benign) pyasn1_modules-0.4.2.tar.gz/setup.cfg: build metadata not in pyasn1/pyasn1-modules@v0.4.2

## Dynamic egress test

- Status: **pass** (isolation: windows-appcontainer, network denied, audit hook; network denied; 2026-10-04)
- Exercise: import pyasn1_modules (rfc2459, rfc5208, rfc5280, pem, as google-auth does); decode and re-encode a self-signed X.509 certificate with rfc5280 and rfc2459 (pyasn1 0.6.4 on the path)
- Jail positive control (loopback connection to the host): blocked
- Connection or process attempts: 0
- Other recorded events: none
- Limitations:
  - Python audit hooks see socket, DNS, urllib/http.client and process events; native code calling the OS socket API directly raises no audit event. The jail blocks such calls but they are not logged, so native binaries are also scanned statically for networking imports.
  - Only the import and the stated exercise were run; code paths they do not reach were not observed.
