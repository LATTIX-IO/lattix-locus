"""Run a D-29 provenance inspection of one PyPI package version and draft its attestation.

Inputs: the package and version, and a *research file* with what cannot be
derived mechanically -- origin, maintainers and funding with evidence links, the
names to screen, reviewer notes on findings, and the exercise script for the
dynamic test (``provenance/research/<ecosystem>/<name>@<version>.json``; see
docs/PROVENANCE.md). The run:

1. reads the release from the PyPI JSON API and downloads every distribution file,
   checking each against its published SHA-256 (nothing downloaded is executed on
   the host);
2. unpacks each file and runs the static ruleset (:mod:`.static_rules`);
3. writes an SBOM (syft if installed, else the dist-info fallback);
4. looks up known vulnerabilities (OSV, cached);
5. screens the maintainers and organizations against the Entity List and the
   latest DoD 1260H notice (files downloaded with their hashes recorded);
6. imports the package and runs the exercise inside the sandbox with egress
   denied (:mod:`.dynamic`), using the wheel that runs on the inspection host;
7. proposes a decision and writes the attestation as *agent-prepared, principal
   sign-off pending*. Only the principal changes the reviewer.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import platform
import re
import shutil
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import dynamic, fetch, sbom, screening, static_rules, vulns
from .records import (
    REVIEWER_AGENT_PENDING,
    attestation_relpath,
    normalize_name,
    render_markdown,
    schema_errors,
)

Log = Callable[[str], None]


def _today() -> str:
    return _dt.date.today().isoformat()


def host_wheel_tags() -> tuple[str, ...]:
    """Substrings that all appear in a wheel name that runs on this inspection host."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    x86 = machine in {"amd64", "x86_64"}
    if system == "windows":
        return ("win_amd64",) if x86 else ("win_arm64",)
    if system == "darwin":
        return ("macosx", "x86_64") if x86 else ("macosx", "arm64")
    return ("linux", "x86_64") if x86 else ("linux", "aarch64")


def pick_dynamic_wheel(filenames: Sequence[str], tags: Sequence[str] | None = None) -> str:
    """A pure-Python wheel if there is one, else the wheel built for this host."""
    wheels = [f for f in filenames if f.endswith(".whl")]
    for name in wheels:
        if name.endswith("-none-any.whl"):
            return name
    wanted = tuple(tags) if tags is not None else host_wheel_tags()
    return next((name for name in wheels if all(tag in name for tag in wanted)), "")


def top_level_modules(unpacked: Path) -> list[str]:
    for dist_info in sorted(unpacked.glob("*.dist-info")):
        top = dist_info / "top_level.txt"
        if top.is_file():
            names = [
                line.strip()
                for line in top.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if names:
                return names
    found = sorted(
        p.name
        for p in unpacked.iterdir()
        if p.is_dir()
        and (p / "__init__.py").is_file()
        and not p.name.endswith((".dist-info", ".data"))
    )
    return found


def _apply_finding_notes(
    findings: list[dict[str, Any]], notes: Sequence[Mapping[str, Any]]
) -> None:
    for note in notes:
        rule = str(note.get("rule", ""))
        pattern = re.compile(str(note.get("path_regex", ".*")))
        for finding in findings:
            if finding["rule"] == rule and pattern.search(finding["path"]):
                finding["disposition"] = str(note.get("disposition", finding["disposition"]))
                finding["note"] = str(note.get("note", ""))


def resolve_entity_reviews(
    check: dict[str, Any], reviews: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Apply reviewer resolutions to a ``possible-match`` check.

    Each hit needs a review with the same ``list``, ``query`` and ``listed_name``
    and a ``resolution`` of ``different-entity`` (with a ``reason``) or
    ``same-entity``. All different: ``reviewed-no-match``. Any same: ``match``.
    Any hit left unreviewed keeps ``possible-match``."""
    hits = list(check.get("matches") or [])
    if check.get("result") != "possible-match" or not hits:
        return check
    lines: list[str] = []
    confirmed = False
    for hit in hits:
        review = next(
            (
                r
                for r in reviews
                if r.get("list") == check["list"]
                and str(r.get("listed_name", "")).lower() == str(hit.get("listed_name", "")).lower()
                and str(r.get("query", "")).lower() == str(hit.get("query", "")).lower()
            ),
            None,
        )
        resolution = str((review or {}).get("resolution", ""))
        if resolution not in {"different-entity", "same-entity"}:
            return check
        hit["resolution"] = resolution
        hit["reason"] = str((review or {}).get("reason", ""))
        confirmed = confirmed or resolution == "same-entity"
        lines.append(f"{hit['query']} vs '{hit['listed_name']}': {resolution} ({hit['reason']})")
    check["result"] = "match" if confirmed else "reviewed-no-match"
    check["notes"] = "Reviewed hits: " + " | ".join(lines)
    return check


def screen(
    queries: Sequence[screening.Query],
    *,
    csl_csv: Path | None,
    notice: Path | None,
    notice_url: str,
    notice_id: str,
    reviews: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    names = [q.name for q in queries] or ["(none)"]
    if csl_csv is not None and csl_csv.is_file():
        checks.append(screening.entity_list_check(csl_csv, queries).as_record())
        checks.append(screening.consolidated_list_check(csl_csv, queries).as_record())
    else:
        checks.append(
            {
                "list": "us-commerce-entity-list",
                "method": "not run: the Consolidated Screening List was not available",
                "source": screening.CSL_URL,
                "date": _today(),
                "queries": names,
                "result": "not-run",
            }
        )
    if notice is not None and notice.is_file():
        checks.append(
            screening.dod_1260h_check(
                notice, queries, source_url=notice_url, notice=notice_id
            ).as_record()
        )
    else:
        checks.append(
            {
                "list": "dod-1260h",
                "method": "not run: the 1260H notice was not available",
                "source": screening.FEDERAL_REGISTER_1260H_SEARCH,
                "date": _today(),
                "queries": names,
                "result": "not-run",
            }
        )
    return [resolve_entity_reviews(c, reviews) for c in checks]


def propose_decision(
    record: Mapping[str, Any], override: Mapping[str, Any] | None
) -> dict[str, Any]:
    """A proposed decision; the research file may override it (with its own rationale)."""
    blockers: list[str] = []
    conditions: list[str] = []
    dynamic_status = record["dynamic_egress"]["status"]
    if dynamic_status == "fail":
        blockers.append("connection attempts during the dynamic test")
    elif dynamic_status != "pass":
        conditions.append(f"re-run the dynamic egress test (status '{dynamic_status}')")
    findings = record["static_findings"]["findings"]
    if any(f["disposition"] == "blocking" for f in findings):
        blockers.append("blocking static findings")
    pending = [f for f in findings if f["disposition"] == "needs-review"]
    if pending:
        conditions.append(
            f"principal review of {len(pending)} static finding(s) marked needs-review"
        )
    checks = record["entity_list_checks"]
    if any(c["result"] == "match" for c in checks):
        blockers.append("entity-list match")
    if any(c["result"] in {"not-run", "possible-match"} for c in checks):
        conditions.append("complete the entity-list screening")
    if record["vulnerabilities"]["status"] == "found":
        conditions.append("assess the known vulnerabilities listed")
    if record["vulnerabilities"]["status"] == "unavailable":
        conditions.append("re-run the vulnerability lookup")
    if override:
        return {
            "outcome": str(override["outcome"]),
            "conditions": [str(c) for c in override.get("conditions") or []],
            "rationale": str(override["rationale"]),
        }
    if blockers:
        return {
            "outcome": "fail",
            "conditions": [],
            "rationale": "Fails: " + "; ".join(blockers) + ".",
        }
    if conditions:
        return {
            "outcome": "conditional",
            "conditions": conditions,
            "rationale": "No blocking result; open items are listed as conditions.",
        }
    return {"outcome": "pass", "conditions": [], "rationale": "All automated checks passed."}


def inspect_pypi(
    name: str,
    version: str,
    research: Mapping[str, Any],
    *,
    provenance_root: Path,
    csl_csv: Path | None = None,
    notice: Path | None = None,
    notice_url: str = "",
    notice_id: str = "",
    cache_dir: Path | None = None,
    run_dynamic: bool = True,
    log: Log = print,
) -> tuple[dict[str, Any], Path]:
    """Inspect ``name==version`` and write its attestation; returns (record, json path)."""
    release = fetch.pypi_release(name, version)
    canonical = normalize_name("pypi", release["info"]["name"])
    files = [f for f in release.get("urls") or [] if not f.get("yanked")]
    if not files:
        raise RuntimeError(f"{name}=={version}: no distribution files on PyPI")
    dynamic_wheel = pick_dynamic_wheel([f["filename"] for f in files])
    work = Path(tempfile.mkdtemp(prefix="locus-inspect-"))
    try:
        artifacts: list[dict[str, Any]] = []
        scans: list[static_rules.ScanResult] = []
        unpacked_dynamic: Path | None = None
        for item in files:
            filename = str(item["filename"])
            sha = str(item["digests"]["sha256"])
            log(f"download {filename}")
            local = fetch.download(str(item["url"]), work / "dl" / filename, sha256=sha)
            target = work / "src" / filename
            if filename.endswith(".whl"):
                fetch.unpack_wheel(local, target)
            elif filename.endswith((".tar.gz", ".tgz")):
                fetch.unpack_sdist(local, target)
            else:
                log(f"not unpacked (unsupported archive): {filename}")
                artifacts.append(
                    {
                        "filename": filename,
                        "sha256": sha,
                        "url": str(item["url"]),
                        "inspected": False,
                    }
                )
                continue
            scans.append(static_rules.scan_tree(target, label=filename))
            if filename == dynamic_wheel:
                unpacked_dynamic = target
            artifacts.append(
                {
                    "filename": filename,
                    "sha256": sha,
                    "url": str(item["url"]),
                    "size": int(item.get("size") or 0),
                    "uploaded": str(item.get("upload_time_iso_8601") or ""),
                    "inspected": True,
                    "source": "PyPI JSON API; sha256 verified on download",
                }
            )
        summary = ""
        source = research.get("source")
        if isinstance(source, Mapping):
            match = source_match(
                str(source["repo"]),
                str(source["tag"]),
                [str(d) for d in source.get("package_dirs") or []],
                {a["filename"]: work / "src" / a["filename"] for a in artifacts if a["inspected"]},
            )
            scans.append(match.scan)
            summary = match.summary
        merged = static_rules.merge(scans)
        static_record = merged.as_record()
        static_record["scanned_artifacts"] = [a["filename"] for a in artifacts]
        if summary:
            static_record["summary"] = summary
        _apply_finding_notes(static_record["findings"], research.get("finding_notes") or [])

        sbom_source = unpacked_dynamic or work / "src" / artifacts[0]["filename"]
        artifact_sha = next(
            (a["sha256"] for a in artifacts if sbom_source.name == a["filename"]), ""
        )
        sbom_result = sbom.generate(
            sbom_source, name=canonical, version=version, artifact_sha256=artifact_sha
        )
        sbom_rel = Path("sbom") / "pypi" / f"{canonical}@{version}.cdx.json"
        sbom_sha = sbom.write(sbom_result, provenance_root / sbom_rel)

        vuln_result = vulns.lookup("pypi", canonical, version, cache_dir=cache_dir)

        queries = [
            screening.Query(str(q["name"]), str(q.get("kind", "person")))
            for q in research.get("screening_queries") or []
        ]
        checks = screen(
            queries,
            csl_csv=csl_csv,
            notice=notice,
            notice_url=notice_url or screening.FEDERAL_REGISTER_1260H_SEARCH,
            notice_id=notice_id or "unspecified",
            reviews=research.get("entity_reviews") or [],
        )

        exercise = str(research.get("exercise") or "")
        if run_dynamic and unpacked_dynamic is not None:
            modules = list(research.get("modules") or top_level_modules(unpacked_dynamic))
            log(f"dynamic egress test: import {', '.join(modules)} in {unpacked_dynamic.name}")
            paths = [unpacked_dynamic]
            for dependency in research.get("dynamic_dependencies") or []:
                # Runtime dependencies of the package, at their pinned version and hash.
                paths.append(
                    _fetch_wheel(str(dependency["name"]), str(dependency["version"]), work, log)
                )
            egress = dynamic.run_egress_test(paths, modules, exercise=exercise)
            dynamic_record = egress.as_record(
                exercise=str(research.get("exercise_summary") or ("import " + ", ".join(modules))),
                artifact=unpacked_dynamic.name,
            )
        else:
            dynamic_record = dynamic.EgressResult(
                status="not-run",
                isolation="none",
                detail="no wheel for this inspection host"
                if run_dynamic
                else "dynamic test skipped",
            ).as_record(exercise="")
        if dynamic_record["status"] == "pass":
            for artifact in artifacts:
                if artifact["filename"] != dynamic_record.get("artifact"):
                    artifact["source"] += "; static checks only (not run on this host)"

        record: dict[str, Any] = {
            "schema_version": "1",
            "kind": "package",
            "ecosystem": "pypi",
            "name": canonical,
            "version": version,
            "artifacts": artifacts,
            "origin": research["origin"],
            "maintainers": research["maintainers"],
            "funding": research["funding"],
            "entity_list_checks": checks,
            "sbom": {
                "generator": sbom_result.generator,
                "generator_kind": sbom_result.generator_kind,
                "format": "CycloneDX JSON",
                "path": sbom_rel.as_posix(),
                "sha256": sbom_sha,
                "components": sbom_result.components,
            },
            "vulnerabilities": vuln_result.as_record(),
            "static_findings": static_record,
            "dynamic_egress": dynamic_record,
        }
        record["decision"] = propose_decision(record, research.get("decision"))
        record["review"] = {
            "reviewer": REVIEWER_AGENT_PENDING,
            "date": _today(),
            "prepared_by": str(research.get("prepared_by") or "lattix provenance inspect"),
        }
        if research.get("notes"):
            record["notes"] = [str(n) for n in research["notes"]]
        errors = schema_errors(record)
        if errors:
            raise ValueError("attestation does not validate: " + "; ".join(errors[:10]))
        path = write_record(record, provenance_root)
        return record, path
    finally:
        shutil.rmtree(work, ignore_errors=True)


#: sdist files a build adds that are not in the source tree.
_SDIST_BUILD_FILES = re.compile(r"(^|/)(PKG-INFO|setup\.cfg)$|\.egg-info/")


@dataclass(frozen=True)
class SourceMatch:
    scan: static_rules.ScanResult
    summary: str


def _tree_hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}


def source_match(
    repo: str,
    tag: str,
    package_dirs: Sequence[str],
    unpacked: Mapping[str, Path],
    *,
    tarball: bytes | None = None,
) -> SourceMatch:
    """Compare each distribution's files with the tagged GitHub source tree.

    Wheels: every file under ``package_dirs`` must be byte-identical to the tag.
    Sdists: every file must be, except build metadata (``PKG-INFO``, ``*.egg-info``,
    ``setup.cfg``), which is reported as info. ``tarball`` is injectable for tests.
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(
        r"[A-Za-z0-9_.+-]+", tag
    ):
        raise ValueError(f"unexpected source repo/tag: {repo}@{tag}")
    data = (
        tarball
        if tarball is not None
        else fetch.http_get(
            f"https://codeload.github.com/{repo}/tar.gz/refs/tags/{tag}", timeout=300
        )
    )
    tree: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            handle = archive.extractfile(member) if member.isfile() else None
            if handle is not None:
                tree["/".join(member.name.split("/")[1:])] = hashlib.sha256(
                    handle.read()
                ).hexdigest()
    findings: list[static_rules.Finding] = []
    parts: list[str] = []
    for filename, root in sorted(unpacked.items()):
        is_wheel = filename.endswith(".whl")
        base = root
        if not is_wheel:
            children = [p for p in root.iterdir() if p.is_dir()]
            base = children[0] if len(children) == 1 else root
        files = {
            p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()
        }
        if is_wheel:
            files = {
                n: b
                for n, b in files.items()
                if any(n.startswith(d.rstrip("/") + "/") for d in package_dirs)
            }
        identical = 0
        for name, digest in sorted(_tree_hashes(files).items()):
            label = f"{filename}/{name}"
            if tree.get(name) == digest:
                identical += 1
            elif not is_wheel and _SDIST_BUILD_FILES.search(name):
                findings.append(
                    static_rules.Finding(
                        "source-build-metadata",
                        "info",
                        label,
                        0,
                        f"build metadata not in {repo}@{tag}",
                    )
                )
            elif name in tree:
                findings.append(
                    static_rules.Finding(
                        "source-mismatch", "high", label, 0, f"differs from {repo}@{tag}"
                    )
                )
            else:
                findings.append(
                    static_rules.Finding(
                        "file-not-in-source", "high", label, 0, f"absent from {repo}@{tag}"
                    )
                )
        parts.append(f"{filename}: {identical}/{len(files)} files identical to {repo}@{tag}")
    return SourceMatch(static_rules.ScanResult(0, tuple(findings)), "; ".join(parts))


def _fetch_wheel(name: str, version: str, work: Path, log: Log) -> Path:
    """Download (hash-checked) and unpack the host-compatible wheel of a dependency."""
    release = fetch.pypi_release(name, version)
    files = {str(f["filename"]): f for f in release.get("urls") or [] if not f.get("yanked")}
    chosen = pick_dynamic_wheel(list(files))
    if not chosen:
        raise RuntimeError(f"no wheel of {name}=={version} runs on this host")
    item = files[chosen]
    log(f"download dependency {chosen}")
    local = fetch.download(
        str(item["url"]), work / "deps" / chosen, sha256=str(item["digests"]["sha256"])
    )
    return fetch.unpack_wheel(local, work / "deps-src" / chosen)


def write_record(record: Mapping[str, Any], provenance_root: Path) -> Path:
    """Write ``<name>@<version>.json`` and its rendered ``.md`` summary."""
    path = provenance_root / attestation_relpath(
        str(record["ecosystem"]), str(record["name"]), str(record["version"])
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    path.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8", newline="\n")
    return path


# --------------------------------------------------------------------------- #
# Local model weights
# --------------------------------------------------------------------------- #
def inspect_local_model(
    research: Mapping[str, Any],
    *,
    provenance_root: Path,
    lineage: str,
    ollama: str = "",
    model_dir: Path | None = None,
    name: str = "",
    version: str = "",
    csl_csv: Path | None = None,
    notice: Path | None = None,
    notice_url: str = "",
    notice_id: str = "",
    models_dir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    """Draft the attestation for local weights: an Ollama model or a weights directory.

    The weights-only format check runs on the exact files (Ollama blobs are
    content-addressed, so their digests bind the attestation to what the engine
    loads); the behavioural/red-team eval comes from the LOCUS-351 hook (pending
    until it exists, which keeps the model from passing).
    """
    from . import models

    served_as: list[dict[str, str]] = []
    if ollama:
        ref = models.parse_ollama_ref(ollama)
        if ref is None:
            raise ValueError(f"unrecognized Ollama model reference {ollama!r}")
        digests = models.ollama_weight_digests(ref, models_dir)
        if not digests:
            raise FileNotFoundError(f"no Ollama manifest with weights for {ollama}")
        blobs = [models.ollama_blob_path(d, models_dir) for d in digests]
        check = models.check_weights(blobs)
        ecosystem, name, version = "ollama", ref.attestation_name, ref.tag
        served_as.append({"engine": "ollama", "model": ollama})
    elif model_dir is not None:
        if not name or not version:
            raise ValueError("a weights directory needs a name and a version (e.g. the revision)")
        check = models.check_model_dir(model_dir)
        ecosystem = "huggingface"
    else:
        raise ValueError("give an Ollama model reference or a weights directory")
    artifacts: list[dict[str, Any]] = [
        {
            "filename": w["path"],
            "sha256": w["sha256"],
            "inspected": True,
            "source": "local weights file",
        }
        for w in check.weights
    ] or [{"filename": "(none)", "sha256": "0" * 64, "inspected": False}]
    findings = [
        {
            "rule": "model-weights-format",
            "severity": "high",
            "path": item["path"],
            "detail": item["reason"],
            "disposition": "blocking",
        }
        for item in check.refused
    ]
    model_ref = f"{ecosystem}/{name}@{version}"
    sbom_doc = sbom.SbomResult(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "component": {"type": "machine-learning-model", "name": name, "version": version}
            },
            "components": [
                {
                    "type": "machine-learning-model",
                    "name": w["path"],
                    "hashes": [{"alg": "SHA-256", "content": w["sha256"]}],
                    "properties": [{"name": "locus:format", "value": w["format"]}],
                }
                for w in check.weights
            ],
        },
        "locus-provenance model sbom (weights files, CycloneDX 1.5)",
        "locus-fallback",
    )
    canonical = normalize_name(ecosystem, name)
    sbom_rel = Path("sbom") / ecosystem / f"{canonical}@{version}.cdx.json"
    sbom_sha = sbom.write(sbom_doc, provenance_root / sbom_rel)
    queries = [
        screening.Query(str(q["name"]), str(q.get("kind", "org")))
        for q in research.get("screening_queries") or []
    ]
    record: dict[str, Any] = {
        "schema_version": "1",
        "kind": "model",
        "ecosystem": ecosystem,
        "name": canonical,
        "version": version,
        "artifacts": artifacts,
        "origin": research["origin"],
        "maintainers": research["maintainers"],
        "funding": research["funding"],
        "entity_list_checks": screen(
            queries,
            csl_csv=csl_csv,
            notice=notice,
            notice_url=notice_url or screening.FEDERAL_REGISTER_1260H_SEARCH,
            notice_id=notice_id or "unspecified",
            reviews=research.get("entity_reviews") or [],
        ),
        "sbom": {
            "generator": sbom_doc.generator,
            "generator_kind": sbom_doc.generator_kind,
            "format": "CycloneDX JSON",
            "path": sbom_rel.as_posix(),
            "sha256": sbom_sha,
            "components": sbom_doc.components,
        },
        "vulnerabilities": {
            "source": "not applicable: model weights have no OSV ecosystem",
            "mode": "not-applicable",
            "date": _today(),
            "status": "not-applicable",
            "results": [],
        },
        "static_findings": {
            "ruleset": "locus-model-weights/1",
            "files_scanned": len(check.weights) + len(check.refused),
            "findings": findings,
        },
        "dynamic_egress": {
            "status": "not-run",
            "isolation": "not applicable to weights; the engine serves on loopback only",
            "network": "denied",
            "date": _today(),
            "attempts": [],
            "limitations": ["Weights are data; network behaviour belongs to the inference engine."],
        },
        "model": {
            "lineage": lineage,
            "served_as": served_as,
            "format_check": check.as_record(),
            "behavioural_eval": models.run_behavioural_eval(model_ref),
            "inference_network": "loopback-only",
        },
    }
    record["decision"] = propose_decision(
        {**record, "dynamic_egress": {"status": "pass"}}, research.get("decision")
    )
    if check.status != "pass":
        record["decision"] = {
            "outcome": "fail",
            "conditions": [],
            "rationale": "Weights-only format check failed: "
            + "; ".join(r["reason"] for r in check.refused or [{"reason": "no weights found"}]),
        }
    record["review"] = {"reviewer": REVIEWER_AGENT_PENDING, "date": _today()}
    errors = schema_errors(record)
    if errors:
        raise ValueError("attestation does not validate: " + "; ".join(errors[:10]))
    return record, write_record(record, provenance_root)


def fetch_screening_inputs(cache: Path, *, log: Log = print) -> dict[str, Any]:
    """Download today's Consolidated Screening List and the latest 1260H notice."""
    day = cache / "screening" / _today()
    day.mkdir(parents=True, exist_ok=True)
    csl = day / "consolidated.csv"
    if not csl.is_file():
        log(f"download {screening.CSL_URL}")
        csl.write_bytes(fetch.http_get(screening.CSL_URL, timeout=300))
    search = json.loads(fetch.http_get(screening.FEDERAL_REGISTER_1260H_SEARCH).decode("utf-8"))
    notices = [
        r
        for r in search.get("results") or []
        if "designation of chinese military companies" in str(r.get("title", "")).lower()
        and "removal" not in str(r.get("title", "")).lower()
    ]
    if not notices:
        raise RuntimeError("no 1260H designation notice found in the Federal Register")
    latest = notices[0]
    number = str(latest["document_number"])
    detail = json.loads(
        fetch.http_get(f"https://www.federalregister.gov/api/v1/documents/{number}.json").decode(
            "utf-8"
        )
    )
    xml_url = str(detail["full_text_xml_url"])
    notice = day / f"1260h-{number}.xml"
    if not notice.is_file():
        log(f"download {xml_url}")
        notice.write_bytes(fetch.http_get(xml_url))
    return {
        "csl_csv": csl,
        "notice": notice,
        "notice_url": str(latest.get("html_url") or xml_url),
        "notice_id": f"Federal Register {number}, {latest.get('publication_date')}",
    }
