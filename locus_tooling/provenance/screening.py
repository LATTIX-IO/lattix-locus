"""Entity-list screening for the D-29 ownership-and-ties check.

Two official sources, both downloaded at inspection time and recorded with their
SHA-256 so the check is reproducible:

* the **US Consolidated Screening List** CSV (trade.gov), whose ``Entity List (EL)
  - Bureau of Industry and Security`` rows are the Commerce Entity List (15 CFR 744
  Supp. 4); the other CSL sources (SDN, MEU, CMIC, ...) are screened as a third,
  informational check;
* the latest **DoD Section 1260H** "Chinese military companies" notice, as
  published in the Federal Register (full-text XML).

Matching is deliberately simple and explainable. A person is a hit when
every token of the queried name appears in one listed name (strength ``name``) or
when a listed name has the person's surname and a given name with the same first
three letters, a transliteration variant (strength ``variant``); an
organization is a hit when its normalized name appears as a phrase. Any hit makes
the result ``possible-match``: name screening cannot tell two people apart, so a
reviewer resolves each hit (``different-entity`` with the reason, or
``same-entity``), which turns the result into ``reviewed-no-match`` or ``match``.
Pure apart from reading the downloaded files.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CSL_URL = "https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv"
ENTITY_LIST_SOURCE = "Entity List (EL) - Bureau of Industry and Security"
FEDERAL_REGISTER_1260H_SEARCH = (
    "https://www.federalregister.gov/api/v1/documents.json?conditions%5Bterm%5D=%22Section+1260H%22"
    "&conditions%5Bagencies%5D%5B%5D=defense-department&order=newest"
)

_TOKEN = re.compile(r"[a-z0-9]+")
_ORG_SUFFIXES = frozenset(
    {
        "inc",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "ag",
        "sa",
        "plc",
    }
)


@dataclass(frozen=True)
class Query:
    name: str
    kind: str  # person | org


@dataclass
class CheckResult:
    list_name: str
    method: str
    source: str
    source_sha256: str
    source_version: str
    queries: list[str]
    matches: list[dict[str, Any]] = field(default_factory=list)
    date: str = field(default_factory=lambda: _dt.date.today().isoformat())

    @property
    def result(self) -> str:
        return "possible-match" if self.matches else "no-match"

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "list": self.list_name,
            "method": self.method,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "source_version": self.source_version,
            "date": self.date,
            "queries": self.queries,
            "result": self.result,
            "matches": list(self.matches),
        }
        return record


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(str(text or "").lower())


def org_phrase(name: str) -> str:
    words = [w for w in tokens(name) if w not in _ORG_SUFFIXES]
    return " ".join(words)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _variant_hit(person: str, listed: set[str]) -> bool:
    """Surname present and a given name sharing its first three letters (Alex/Alejandro,
    Yuri/Yuriy): catches transliteration variants without flagging every namesake."""
    parts = tokens(person)
    if len(parts) < 2 or parts[-1] not in listed:
        return False
    prefixes = {given[:3] for given in parts[:-1] if len(given) >= 3}
    return any(token[:3] in prefixes for token in listed - {parts[-1]})


def _match_names(
    queries: Sequence[Query], entries: Iterable[tuple[str, list[str], dict[str, str]]]
) -> list[dict[str, Any]]:
    """``entries``: (primary name, all names, context). Returns the hits."""
    matches: list[dict[str, Any]] = []
    prepared = [(q, set(tokens(q.name)), org_phrase(q.name)) for q in queries]
    for primary, names, context in entries:
        name_tokens = [set(tokens(n)) for n in names]
        phrases = [" " + " ".join(tokens(n)) + " " for n in names]
        for query, qtokens, phrase in prepared:
            if not qtokens:
                continue
            if query.kind == "person":
                if len(qtokens) >= 2 and any(qtokens <= nt for nt in name_tokens):
                    matches.append(
                        {"strength": "name", "query": query.name, "listed_name": primary, **context}
                    )
                    continue
                if any(_variant_hit(query.name, nt) for nt in name_tokens):
                    matches.append(
                        {
                            "strength": "variant",
                            "query": query.name,
                            "listed_name": primary,
                            **context,
                        }
                    )
            elif phrase and any(f" {phrase} " in p for p in phrases):
                matches.append(
                    {"strength": "name", "query": query.name, "listed_name": primary, **context}
                )
    return matches


def screen_csl(
    csv_path: Path,
    queries: Sequence[Query],
    *,
    sources: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Screen against CSL rows (optionally only the given ``source`` values)."""
    rows = 0

    def entries() -> Iterable[tuple[str, list[str], dict[str, str]]]:
        nonlocal rows
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if sources is not None and row.get("source") not in sources:
                    continue
                rows += 1
                primary = str(row.get("name") or "")
                alts = [a.strip() for a in str(row.get("alt_names") or "").split(";") if a.strip()]
                details = [
                    f"{key}={str(row.get(key) or '')[:80]}"
                    for key in ("programs", "nationalities", "dates_of_birth", "addresses")
                    if row.get(key)
                ]
                yield (
                    primary,
                    [primary, *alts],
                    {
                        "source_list": str(row.get("source") or ""),
                        "listed_details": "; ".join(details),
                    },
                )

    matches = _match_names(queries, entries())
    return matches, rows


def entity_list_check(csv_path: Path, queries: Sequence[Query]) -> CheckResult:
    matches, rows = screen_csl(csv_path, queries, sources=frozenset({ENTITY_LIST_SOURCE}))
    return CheckResult(
        list_name="us-commerce-entity-list",
        method=(
            f"Name screening of {rows} Entity List (EL) rows in the US Consolidated Screening "
            "List CSV (primary and alternate names): persons by full-name token match, "
            "organizations by normalized phrase; surname plus a given-name variant (same first "
            "three letters) is reported too. Every hit is a possible match for a reviewer."
        ),
        source=CSL_URL,
        source_sha256=file_sha256(csv_path),
        source_version=f"{rows} EL rows",
        queries=[q.name for q in queries],
        matches=matches,
    )


def consolidated_list_check(csv_path: Path, queries: Sequence[Query]) -> CheckResult:
    matches, rows = screen_csl(csv_path, queries)
    return CheckResult(
        list_name="other",
        method=(
            f"Informational: the same screening over all {rows} rows of the US Consolidated "
            "Screening List (SDN, MEU, CMIC, DPL, UVL, ISN, ...)."
        ),
        source=CSL_URL,
        source_sha256=file_sha256(csv_path),
        source_version=f"{rows} CSL rows",
        queries=[q.name for q in queries],
        matches=matches,
    )


def strip_markup(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def dod_1260h_check(
    notice_path: Path, queries: Sequence[Query], *, source_url: str, notice: str
) -> CheckResult:
    """Screen against the full text of a Federal Register 1260H notice."""
    text = strip_markup(notice_path.read_text(encoding="utf-8", errors="replace"))
    padded = " " + " ".join(tokens(text)) + " "
    matches: list[dict[str, Any]] = []
    for query in queries:
        phrase = org_phrase(query.name) if query.kind == "org" else " ".join(tokens(query.name))
        hit = {"query": query.name, "source_list": notice}
        if phrase and f" {phrase} " in padded:
            matches.append({"strength": "name", "listed_name": phrase, **hit})
        elif query.kind == "person":
            surname = tokens(query.name)[-1] if tokens(query.name) else ""
            if len(surname) >= 4 and f" {surname} " in padded:
                matches.append({"strength": "surname", "listed_name": surname, **hit})
    return CheckResult(
        list_name="dod-1260h",
        method=(
            "Phrase screening of the full text of the latest Federal Register Section 1260H "
            f"notice ({notice}); the list names companies, so a person can only hit by surname."
        ),
        source=source_url,
        source_sha256=file_sha256(notice_path),
        source_version=notice,
        queries=[q.name for q in queries],
        matches=matches,
    )
