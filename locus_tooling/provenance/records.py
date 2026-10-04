"""Attestation records for the D-29 provenance inspection: paths, validation, verdicts.

An attestation is one JSON file per inspected artifact at one exact version,
``provenance/attestations/<ecosystem>/<name>@<version>.json`` (schema:
``attestation.schema.json`` beside this module), with a short Markdown summary
rendered next to it. This module is pure apart from reading files; the runtime
model gate and the CI dependency gate both use it, so it imports nothing beyond
the standard library.

"Passing" is deliberately stricter than "the record says pass": the record must
validate, be internally consistent, carry the principal's sign-off, and (for
model weights) show a weights-only format and a passing behavioural eval.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schema_lite import validate

#: P28 (as amended by D-29): origins whose software and local model weights need a
#: passing provenance inspection. ISO 3166-1 alpha-2. Hong Kong and Macao are
#: included as part of the People's Republic of China.
P28_LISTED_COUNTRIES: frozenset[str] = frozenset(
    {"CN", "HK", "MO", "RU", "IR", "KP", "CU", "VE", "BY"}
)

REVIEWER_PRINCIPAL = "principal"
REVIEWER_AGENT_PENDING = "agent-prepared, principal sign-off pending"

#: Ecosystems whose names follow PEP 503 normalization.
_PEP503_ECOSYSTEMS = frozenset({"pypi"})

SCHEMA_PATH = Path(__file__).resolve().with_name("attestation.schema.json")
#: The source checkout's provenance directory (absent in an installed wheel).
REPO_PROVENANCE_DIR = Path(__file__).resolve().parents[2] / "provenance"
#: Extra provenance roots (``os.pathsep``-separated); searched before the defaults.
PROVENANCE_DIR_ENV = "LOCUS_PROVENANCE_DIR"


@lru_cache(maxsize=1)
def attestation_schema() -> dict[str, Any]:
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("attestation schema must be a JSON object")
    return data


def normalize_name(ecosystem: str, name: str) -> str:
    """Canonical name: PEP 503 for PyPI; otherwise lower-cased, with a namespace
    separator (``user/model``) written as ``__`` so it fits one path component."""
    text = str(name or "").strip()
    if ecosystem.lower() in _PEP503_ECOSYSTEMS:
        return re.sub(r"[-_.]+", "-", text).lower()
    return text.lower().replace("/", "__")


_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _safe(component: str, what: str) -> str:
    if not _SAFE_COMPONENT.match(component) or ".." in component:
        raise ValueError(f"unsafe {what} for an attestation path: {component!r}")
    return component


def attestation_relpath(ecosystem: str, name: str, version: str) -> Path:
    """``attestations/<ecosystem>/<name>@<version>.json`` (relative to a provenance root)."""
    eco = _safe(str(ecosystem or "").strip().lower(), "ecosystem")
    canonical = _safe(normalize_name(eco, name), "name")
    ver = _safe(str(version or "").strip(), "version")
    return Path("attestations") / eco / f"{canonical}@{ver}.json"


def provenance_roots(extra: Iterable[str | Path] = ()) -> list[Path]:
    """Directories searched for attestations, in order: explicit, env, app home, repo."""
    roots: list[Path] = [Path(p) for p in extra]
    configured = str(os.getenv(PROVENANCE_DIR_ENV) or "").strip()
    if configured:
        roots += [Path(p).expanduser() for p in configured.split(os.pathsep) if p.strip()]
    try:
        from locus_tooling.common import default_app_home

        roots.append(default_app_home() / "provenance")
    except Exception:  # noqa: BLE001 - no resolvable app home: skip it
        pass
    roots.append(REPO_PROVENANCE_DIR)
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def load_record(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: an attestation must be a JSON object")
    return data


def schema_errors(record: Mapping[str, Any]) -> list[str]:
    return validate(dict(record), attestation_schema())


def _findings(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    static = record.get("static_findings") or {}
    return [f for f in static.get("findings") or [] if isinstance(f, Mapping)]


def consistency_errors(record: Mapping[str, Any]) -> list[str]:
    """Contradictions a schema cannot express.

    A failed check (connection attempt, blocking finding, entity-list match,
    refused weights) contradicts a ``pass`` or ``conditional`` decision. An
    *incomplete* check (not run, unavailable, unresolved) may stand under
    ``conditional`` -- it is a condition -- but never under ``pass``; either way
    :func:`evaluate` does not let it through.
    """
    errors: list[str] = []
    decision = record.get("decision") or {}
    outcome = decision.get("outcome")
    origin = record.get("origin") or {}
    countries = {str(c) for c in origin.get("countries") or []}
    if countries & P28_LISTED_COUNTRIES and origin.get("p28_status") != "listed":
        errors.append("origin: a P28-listed country is named but p28_status is not 'listed'")
    if outcome == "conditional" and not decision.get("conditions"):
        errors.append("decision: 'conditional' requires at least one condition")
    is_model = record.get("kind") == "model"
    if is_model and "model" not in record:
        errors.append("model: a model attestation needs the 'model' section")
    if not is_model and (record.get("vulnerabilities") or {}).get("status") == "not-applicable":
        errors.append("vulnerabilities: 'not-applicable' is only for model weights")
    checks = record.get("entity_list_checks") or []
    lists = {c.get("list") for c in checks}
    if not {"us-commerce-entity-list", "dod-1260h"} <= lists:
        errors.append("entity_list_checks: both the Entity List and the 1260H list are required")
    if outcome not in {"pass", "conditional"}:
        return errors
    dynamic = record.get("dynamic_egress") or {}
    model = record.get("model") or {}
    # Failed checks contradict any passing outcome.
    if not is_model and dynamic.get("status") == "fail":
        errors.append("decision: cannot pass after connection attempts in the dynamic test")
    if any(f.get("disposition") == "blocking" for f in _findings(record)):
        errors.append("decision: cannot pass with a blocking static finding")
    if any(c.get("result") == "match" for c in checks):
        errors.append("decision: cannot pass with an entity-list match")
    if is_model and (model.get("format_check") or {}).get("status") != "pass":
        errors.append("model: cannot pass without a weights-only format check")
    if is_model and model.get("inference_network") not in {"none", "loopback-only"}:
        errors.append("model: inference must have no network access (D-29)")
    if outcome == "pass":
        # An unconditional pass needs every check complete.
        errors += [f"decision: 'pass' with {gap}; use 'conditional'" for gap in incomplete(record)]
    return errors


def incomplete(record: Mapping[str, Any]) -> list[str]:
    """Checks that were not run or not resolved (each blocks a passing verdict)."""
    gaps: list[str] = []
    is_model = record.get("kind") == "model"
    dynamic = record.get("dynamic_egress") or {}
    if not is_model and dynamic.get("status") != "pass":
        gaps.append(f"the dynamic egress test '{dynamic.get('status')}'")
    if not is_model and dynamic.get("jail_probe") != "blocked":
        gaps.append("the jail positive control not showing egress blocked")
    checks = record.get("entity_list_checks") or []
    if any(c.get("result") == "not-run" for c in checks):
        gaps.append("an entity-list check not run")
    if any(c.get("result") == "possible-match" for c in checks):
        gaps.append("entity-list possible matches unresolved")
    if any(f.get("disposition") == "needs-review" for f in _findings(record)):
        gaps.append("static findings needing review")
    if (record.get("vulnerabilities") or {}).get("status") == "unavailable":
        gaps.append("the vulnerability lookup unavailable")
    if not any(a.get("inspected") for a in record.get("artifacts") or []):
        gaps.append("no artifact actually inspected")
    if is_model:
        evaluation = (record.get("model") or {}).get("behavioural_eval") or {}
        if evaluation.get("status") != "pass":
            gaps.append(
                f"the behavioural/red-team eval '{evaluation.get('status', 'missing')}' (LOCUS-351)"
            )
    return gaps


@dataclass(frozen=True)
class Verdict:
    """Whether a record lets the gated thing through, and why not."""

    passing: bool
    reasons: tuple[str, ...]

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) or "passing attestation"


def evaluate(record: Mapping[str, Any]) -> Verdict:
    """Pass only with a valid, consistent, complete, principal-signed pass/conditional record."""
    reasons = [*schema_errors(record), *consistency_errors(record)]
    if reasons:
        return Verdict(False, tuple(reasons))
    if (record.get("decision") or {}).get("outcome") not in {"pass", "conditional"}:
        reasons.append(f"decision is '{record['decision']['outcome']}'")
    if (record.get("review") or {}).get("reviewer") != REVIEWER_PRINCIPAL:
        reasons.append("principal sign-off pending")
    reasons += [f"open: {gap}" for gap in incomplete(record)]
    return Verdict(not reasons, tuple(reasons))


def find_attestation(
    ecosystem: str, name: str, version: str, *, roots: Iterable[str | Path] | None = None
) -> Path | None:
    """The first attestation file for that exact artifact under the provenance roots."""
    try:
        relative = attestation_relpath(ecosystem, name, version)
    except ValueError:
        return None
    for root in roots if roots is not None else provenance_roots():
        candidate = Path(root) / relative
        if candidate.is_file():
            return candidate
    return None


def lookup(
    ecosystem: str, name: str, version: str, *, roots: Iterable[str | Path] | None = None
) -> tuple[Verdict, dict[str, Any] | None]:
    """Find and evaluate the attestation for ``name@version`` (fail closed)."""
    path = find_attestation(ecosystem, name, version, roots=roots)
    if path is None:
        return Verdict(False, (f"no attestation for {ecosystem}/{name}@{version}",)), None
    try:
        record = load_record(path)
    except (OSError, ValueError) as exc:
        return Verdict(False, (f"unreadable attestation {path.name}: {exc}",)), None
    if normalize_name(ecosystem, str(record.get("name", ""))) != normalize_name(
        ecosystem, name
    ) or str(record.get("version")) != str(version):
        return Verdict(False, (f"{path.name} does not describe {name}@{version}",)), record
    if str(record.get("ecosystem", "")).lower() != ecosystem.lower():
        return Verdict(
            False, (f"{path.name} is for ecosystem {record.get('ecosystem')!r}",)
        ), record
    return evaluate(record), record


# --------------------------------------------------------------------------- #
# Markdown summary
# --------------------------------------------------------------------------- #
def _bullet(items: Iterable[str]) -> str:
    lines = [f"- {item}" for item in items]
    return "\n".join(lines) if lines else "- none"


def render_markdown(record: Mapping[str, Any]) -> str:
    """The short human summary committed beside the JSON (generated, not hand-edited)."""
    origin = record["origin"]
    decision = record["decision"]
    review = record["review"]
    dynamic = record["dynamic_egress"]
    static = record["static_findings"]
    vulns = record["vulnerabilities"]
    title = f"{record['ecosystem']}/{record['name']}@{record['version']}"
    verdict = evaluate(record)
    lines = [
        f"# Provenance attestation: {title}",
        "",
        "Generated from the JSON record beside this file by `lattix provenance render`; "
        "edit the JSON, not this summary.",
        "",
        f"- **Decision:** {decision['outcome']}"
        + (f" (conditions: {'; '.join(decision['conditions'])})" if decision["conditions"] else ""),
        f"- **Reviewer:** {review['reviewer']}"
        + (f" ({review['name']})" if review.get("name") else "")
        + f", {review['date']}",
        f"- **Gate status:** {'passing' if verdict.passing else 'not passing: ' + verdict.reason}",
        f"- **Origin:** {origin['p28_status']} ({', '.join(origin['countries']) or 'no country established'}; "
        f"confidence {origin['confidence']}). {origin['summary']}",
        "",
        "## Rationale",
        "",
        str(decision["rationale"]),
        "",
        "## Artifacts",
        "",
        _bullet(
            f"`{a['filename']}` sha256 `{a['sha256']}`" + (" (inspected)" if a["inspected"] else "")
            for a in record["artifacts"]
        ),
        "",
        "## Maintainers and funding",
        "",
        _bullet(
            f"{m['name']} ({m['role']}"
            + (f", {m['affiliation']}" if m.get("affiliation") else "")
            + (
                f", {m['location']} [{m.get('location_basis', 'basis not stated')}]"
                if m.get("location")
                else ""
            )
            + ")"
            for m in record["maintainers"]
        ),
        "",
        f"Funding: {record['funding']['summary']}",
        "",
        "## Entity-list checks",
        "",
        _bullet(
            f"{c['list']}: **{c['result']}** ({c['date']}; {c['method']})"
            for c in record["entity_list_checks"]
        ),
        "",
        "## SBOM and vulnerabilities",
        "",
        f"- SBOM: `{record['sbom']['path']}` ({record['sbom']['generator']}, "
        f"{record['sbom']['components']} components)",
        f"- Vulnerabilities ({vulns['source']}, {vulns['mode']}, {vulns['date']}): {vulns['status']}"
        + (": " + ", ".join(v["id"] for v in vulns["results"]) if vulns["results"] else ""),
        "",
        "## Static review",
        "",
        f"{static['files_scanned']} files, ruleset `{static['ruleset']}`."
        + (f" Source comparison: {static['summary']}." if static.get("summary") else ""),
        "",
        _bullet(
            f"`{f['rule']}` ({f['severity']}, {f['disposition']}) {f['path']}"
            + (f":{f['line']}" if f.get("line") else "")
            + f": {f['detail']}"
            + (f" -- {f['note']}" if f.get("note") else "")
            for f in static["findings"]
        ),
        "",
        "## Dynamic egress test",
        "",
        f"- Status: **{dynamic['status']}** (isolation: {dynamic['isolation']}; network {dynamic['network']}; "
        f"{dynamic['date']})",
        f"- Exercise: {dynamic.get('exercise', 'import only')}",
        f"- Jail positive control (loopback connection to the host): {dynamic.get('jail_probe', 'not-run')}",
        f"- Connection or process attempts: {len(dynamic['attempts'])}"
        + "".join(f"; `{a['event']}` {a['target']} ({a['outcome']})" for a in dynamic["attempts"]),
        "- Other recorded events: "
        + (", ".join(f"`{e['event']}`" for e in dynamic.get("other_events") or []) or "none"),
        "- Limitations:",
        *[f"  - {item}" for item in dynamic["limitations"]],
    ]
    model = record.get("model")
    if isinstance(model, Mapping):
        fmt = model["format_check"]
        lines += [
            "",
            "## Model gate",
            "",
            f"- Lineage: {model['lineage']}",
            f"- Weights-only format check: {fmt['status']}"
            + (
                f" (refused: {', '.join(r['path'] for r in fmt['refused'])})"
                if fmt["refused"]
                else ""
            ),
            f"- Behavioural/red-team eval: {model['behavioural_eval']['status']}",
        ]
    lines.append("")
    return "\n".join(lines)
