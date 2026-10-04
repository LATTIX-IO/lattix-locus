"""``lattix provenance`` -- the D-29 inspection, verification and dependency gate."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

from . import gate as gate_module
from .records import (
    REPO_PROVENANCE_DIR,
    consistency_errors,
    evaluate,
    load_record,
    render_markdown,
    schema_errors,
)

DEFAULT_CACHE = REPO_PROVENANCE_DIR / ".cache"


def _load_research(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException(f"{path}: the research file must be a JSON object")
    missing = [key for key in ("origin", "maintainers", "funding") if key not in data]
    if missing:
        raise click.ClickException(f"{path}: missing {', '.join(missing)}")
    return data


def _screening(
    fetch_screening: bool, csl: Path | None, notice: Path | None, cache: Path
) -> dict[str, Any]:
    if fetch_screening:
        from .inspection import fetch_screening_inputs

        return fetch_screening_inputs(cache, log=click.echo)
    return {
        "csl_csv": csl,
        "notice": notice,
        "notice_url": "",
        "notice_id": notice.name if notice else "",
    }


@click.group("provenance")
def provenance() -> None:
    """D-29 provenance inspection: inspect, verify, render and gate."""


@provenance.group("inspect")
def inspect_group() -> None:
    """Inspect one artifact and write an agent-prepared attestation."""


_common_options: list[Callable[[Any], Any]] = [
    click.option(
        "--research",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        required=True,
        help="Origin/maintainer/funding research with evidence (docs/PROVENANCE.md).",
    ),
    click.option(
        "--root",
        type=click.Path(file_okay=False, path_type=Path),
        default=REPO_PROVENANCE_DIR,
        show_default=True,
        help="Provenance directory to write into.",
    ),
    click.option(
        "--fetch-screening",
        is_flag=True,
        help="Download today's Consolidated Screening List and the latest 1260H notice.",
    ),
    click.option(
        "--csl",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="A Consolidated Screening List CSV already downloaded.",
    ),
    click.option(
        "--notice",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        default=None,
        help="A 1260H Federal Register notice (XML) already downloaded.",
    ),
    click.option(
        "--cache",
        type=click.Path(file_okay=False, path_type=Path),
        default=DEFAULT_CACHE,
        show_default=True,
        help="Cache for OSV answers and screening downloads (gitignored).",
    ),
]


_F = TypeVar("_F", bound=Callable[..., Any])


def _apply(options: list[Callable[[_F], _F]]) -> Callable[[_F], _F]:
    def decorator(func: _F) -> _F:
        for option in reversed(options):
            func = option(func)
        return func

    return decorator


@inspect_group.command("pypi")
@click.argument("requirement")
@_apply(_common_options)
@click.option(
    "--no-dynamic", is_flag=True, help="Skip the sandboxed egress test (the record cannot pass)."
)
def inspect_pypi_cmd(
    requirement: str,
    research: Path,
    root: Path,
    fetch_screening: bool,
    csl: Path | None,
    notice: Path | None,
    cache: Path,
    no_dynamic: bool,
) -> None:
    """Inspect NAME==VERSION from PyPI."""
    from .inspection import inspect_pypi

    name, sep, version = requirement.partition("==")
    if not sep or not name.strip() or not version.strip():
        raise click.ClickException("give an exact version: NAME==VERSION")
    inputs = _screening(fetch_screening, csl, notice, cache)
    record, path = inspect_pypi(
        name.strip(),
        version.strip(),
        _load_research(research),
        provenance_root=root,
        cache_dir=cache,
        run_dynamic=not no_dynamic,
        log=click.echo,
        **inputs,
    )
    verdict = evaluate(record)
    click.echo(f"wrote {path} (decision {record['decision']['outcome']}; gate: {verdict.reason})")


@inspect_group.command("model")
@_apply(_common_options)
@click.option("--lineage", required=True, help="The model family, e.g. qwen or deepseek.")
@click.option(
    "--ollama", "ollama_ref", default="", help="An installed Ollama model, e.g. qwen2.5-coder:7b."
)
@click.option(
    "--path",
    "model_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="A local weights directory (e.g. a Hugging Face snapshot).",
)
@click.option("--name", default="", help="Model name for --path.")
@click.option("--version", "model_version", default="", help="Model version/revision for --path.")
def inspect_model_cmd(
    research: Path,
    root: Path,
    fetch_screening: bool,
    csl: Path | None,
    notice: Path | None,
    cache: Path,
    lineage: str,
    ollama_ref: str,
    model_dir: Path | None,
    name: str,
    model_version: str,
) -> None:
    """Inspect local model weights (weights-only format check + eval hook)."""
    from .inspection import inspect_local_model

    inputs = _screening(fetch_screening, csl, notice, cache)
    record, path = inspect_local_model(
        _load_research(research),
        provenance_root=root,
        lineage=lineage,
        ollama=ollama_ref,
        model_dir=model_dir,
        name=name,
        version=model_version,
        **inputs,
    )
    verdict = evaluate(record)
    click.echo(f"wrote {path} (decision {record['decision']['outcome']}; gate: {verdict.reason})")


def _attestation_files(paths: tuple[Path, ...]) -> list[Path]:
    if not paths:
        paths = (REPO_PROVENANCE_DIR / "attestations",)
    files: list[Path] = []
    for path in paths:
        files += sorted(path.rglob("*.json")) if path.is_dir() else [path]
    return files


def _sbom_errors(record: dict[str, Any], path: Path) -> list[str]:
    """The referenced SBOM must exist (relative to the provenance root) and match its hash."""
    root = path.resolve().parents[2]
    sbom_path = (root / str(record["sbom"]["path"])).resolve()
    if root not in sbom_path.parents:
        return [f"SBOM path escapes the provenance directory: {record['sbom']['path']}"]
    if not sbom_path.is_file():
        return [f"SBOM {record['sbom']['path']} is missing"]
    digest = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
    if digest != record["sbom"]["sha256"]:
        return [f"SBOM {record['sbom']['path']} does not match its recorded sha256"]
    return []


@provenance.command("verify")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path))
def verify_cmd(paths: tuple[Path, ...]) -> None:
    """Validate attestations (schema, consistency, SBOM hash, summary) and show verdicts."""
    failed = False
    for path in _attestation_files(paths):
        record = load_record(path)
        errors = schema_errors(record)
        if not errors:
            errors += consistency_errors(record)
            errors += _sbom_errors(record, path)
        summary = path.with_suffix(".md")
        if not errors and (
            not summary.is_file() or summary.read_text(encoding="utf-8") != render_markdown(record)
        ):
            errors.append(f"{summary.name} is missing or stale (run `lattix provenance render`)")
        verdict = evaluate(record)
        if errors:
            failed = True
            click.echo(f"INVALID {path}: " + "; ".join(errors))
        else:
            state = "passing" if verdict.passing else f"not passing ({verdict.reason})"
            click.echo(f"valid   {path}: {state}")
    if failed:
        sys.exit(1)


@provenance.command("render")
@click.argument("paths", nargs=-1, type=click.Path(exists=True, path_type=Path))
def render_cmd(paths: tuple[Path, ...]) -> None:
    """Regenerate the .md summaries from the JSON records."""
    for path in _attestation_files(paths):
        record = load_record(path)
        path.with_suffix(".md").write_text(render_markdown(record), encoding="utf-8", newline="\n")
        click.echo(f"rendered {path.with_suffix('.md')}")


@provenance.command("gate")
@click.option("--strict", is_flag=True, help="Also fail on allowlist entries awaiting sign-off.")
def gate_cmd(strict: bool) -> None:
    """The CI dependency gate (no network)."""
    report = gate_module.run(strict=strict)
    click.echo(report.render())
    if not report.ok:
        sys.exit(1)
