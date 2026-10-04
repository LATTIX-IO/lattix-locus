"""Release versions (D-31): ``MAJOR.MINOR`` from ``VERSION``, ``PATCH`` = build counter.

Every published build is ``MAJOR.MINOR.PATCH`` (no pre-release suffixes):

* ``MAJOR.MINOR`` is the repo-root ``VERSION`` file, the single source of the
  first two digits. It changes only through a reviewed PR that follows the bump
  rules in ``docs/VERSIONING.md``.
* ``PATCH`` is never edited by hand. Each published build takes 1 + the highest
  PATCH among the existing release tags of the same ``MAJOR.MINOR``
  (``dev-v*``, ``stable-v*``, ``v*``), or 0 when there are none.
* ``PATCH`` is capped at :data:`PATCH_CAP` (99999). A build that would exceed it
  fails with "a MINOR bump is required"; the counter never wraps or truncates.

The module is pure apart from the small git / file adapters at the bottom, and
uses only the standard library: CI runs it as a script
(``python locus_tooling/versioning.py next|validate|check``) without installing
the package, and ``lattix version next|check`` calls the same functions.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

#: Highest PATCH a build may carry. Higher than Windows Installer's 65535, which
#: is why Windows ships NSIS only (docs/VERSIONING.md, "Windows installers").
PATCH_CAP = 99999
#: MAJOR and MINOR are bounded too, so hostile input cannot become a huge int.
_MAX_COMPONENT_DIGITS = 9

BumpKind = Literal["patch", "minor", "major"]
BUMP_KINDS: tuple[BumpKind, ...] = ("patch", "minor", "major")
_KINDS: dict[str, BumpKind] = {kind: kind for kind in BUMP_KINDS}

#: Version files kept at ``<MAJOR>.<MINOR>.0``; builds set the full version by overlay.
PINNED_MANIFESTS: tuple[str, ...] = (
    "apps/desktop-tauri/src-tauri/tauri.conf.json",
    "apps/desktop-tauri/src-tauri/Cargo.toml",
    "apps/frontend/package.json",
    "apps/frontend/package-lock.json",
    "pyproject.toml",
    # Kept equal to pyproject.toml by tests/unit/test_version_contract.py.
    "install/manifest.json",
    "helm/lattix-locus/Chart.yaml",
)
VERSION_FILE = "VERSION"
RELEASE_NOTES_DIR = "docs/release-notes/"
#: Before ``VERSION`` existed the base version lived here (its MAJOR.MINOR).
_LEGACY_BASE_FILE = "apps/desktop-tauri/src-tauri/tauri.conf.json"

# ASCII digits only ("\d" also matches other scripts' digits).
_INT = r"(0|[1-9][0-9]*)"
_VERSION_RE = re.compile(rf"{_INT}\.{_INT}\.{_INT}")
_BASE_RE = re.compile(rf"{_INT}\.{_INT}")
_VERSION_FILE_RE = re.compile(rf"{_INT}\.{_INT}(?:\r?\n)?")
_TAG_RE = re.compile(rf"(?:dev-|stable-)?v{_INT}\.{_INT}\.{_INT}")
_DECLARATION_RE = re.compile(r"^[ \t]*Release-Impact:[ \t]*([A-Za-z]+)[ \t]*\r?$", re.MULTILINE)
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/^~@{}-]{0,199}")


class VersionError(ValueError):
    """Invalid version input or a build that must not be published."""


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, order=True)
class Base:
    """``MAJOR.MINOR``: the content of ``VERSION``."""

    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


@dataclass(frozen=True, order=True)
class Version:
    """A published build version ``MAJOR.MINOR.PATCH``."""

    major: int
    minor: int
    patch: int

    @property
    def base(self) -> Base:
        return Base(self.major, self.minor)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def _component(text: str, what: str) -> int:
    if len(text) > _MAX_COMPONENT_DIGITS:
        raise VersionError(f"{what} has more than {_MAX_COMPONENT_DIGITS} digits")
    return int(text)


def _check_cap(cap: int) -> int:
    if not isinstance(cap, int) or isinstance(cap, bool) or not 0 <= cap <= PATCH_CAP:
        raise VersionError(f"cap must be an integer between 0 and {PATCH_CAP}")
    return cap


def parse_base(text: str) -> Base:
    """``"0.2"`` -> ``Base(0, 2)``; no leading zeros, signs, spaces or suffixes."""
    match = _BASE_RE.fullmatch(str(text))
    if not match:
        raise VersionError(f"not MAJOR.MINOR (integers, no leading zeros): {text!r}")
    return Base(_component(match[1], "MAJOR"), _component(match[2], "MINOR"))


def read_version_file(text: str) -> Base:
    """The ``VERSION`` file: exactly ``MAJOR.MINOR`` plus an optional newline."""
    match = _VERSION_FILE_RE.fullmatch(str(text))
    if not match:
        raise VersionError(
            f"VERSION must contain exactly MAJOR.MINOR and a newline (e.g. '0.2'), got {text!r}"
        )
    return Base(_component(match[1], "MAJOR"), _component(match[2], "MINOR"))


def parse_version(text: str, *, cap: int = PATCH_CAP) -> Version:
    """``"0.2.7"`` -> ``Version(0, 2, 7)``. Strict: no ``v``, no suffix, PATCH <= cap."""
    _check_cap(cap)
    match = _VERSION_RE.fullmatch(str(text))
    if not match:
        raise VersionError(
            f"not MAJOR.MINOR.PATCH (integers, no leading zeros, no suffix): {text!r}"
        )
    major = _component(match[1], "MAJOR")
    minor = _component(match[2], "MINOR")
    if len(match[3]) > len(str(cap)) or int(match[3]) > cap:
        raise VersionError(f"PATCH {match[3]} exceeds the cap {cap}; a MINOR bump is required")
    return Version(major, minor, int(match[3]))


def format_version(version: Version) -> str:
    return str(version)


def _as_base(value: Base | str) -> Base:
    return value if isinstance(value, Base) else read_version_file(value)


# --------------------------------------------------------------------------- #
# The PATCH counter
# --------------------------------------------------------------------------- #
def tag_names(lines: Iterable[str]) -> list[str]:
    """Tag names from plain names or ``git ls-remote --tags`` lines.

    ``<sha>\\trefs/tags/<name>`` becomes ``<name>``; peeled ``^{}`` entries are
    skipped (the unpeeled line names the same tag).
    """
    names: list[str] = []
    for raw in lines:
        parts = str(raw).split()
        if not parts:
            continue
        name = parts[-1]
        if name.endswith("^{}"):
            continue
        names.append(name.removeprefix("refs/tags/"))
    return names


def tag_version(tag: str, *, cap: int = PATCH_CAP) -> Version | None:
    """The version a release tag names (``dev-v``/``stable-v``/``v`` + X.Y.Z), else ``None``.

    Anything else -- the old ``dev-v0.1.0-dev.16`` pre-release tags, ``channel-dev``,
    leading zeros, a PATCH above the cap -- is not a release tag of this scheme.
    """
    match = _TAG_RE.fullmatch(str(tag))
    if not match:
        return None
    try:
        return parse_version(f"{match[1]}.{match[2]}.{match[3]}", cap=cap)
    except VersionError:
        return None


def next_version(base: Base | str, tags: Iterable[str], *, cap: int = PATCH_CAP) -> Version:
    """The next build of ``base``: 1 + the highest PATCH tagged for it, or 0.

    Raises :class:`VersionError` when that would exceed ``cap`` (a MINOR bump is
    required; the counter never wraps).
    """
    _check_cap(cap)
    target = base if isinstance(base, Base) else parse_base(str(base).strip())
    patches = [v.patch for v in (tag_version(t, cap=cap) for t in tags) if v and v.base == target]
    patch = max(patches) + 1 if patches else 0
    if patch > cap:
        raise VersionError(
            f"{target}.{patch} would exceed the PATCH cap {cap}: a MINOR bump is required "
            f"(edit VERSION to {target.major}.{target.minor + 1}, docs/VERSIONING.md)"
        )
    return Version(target.major, target.minor, patch)


def classify_bump(old: Base | str, new: Base | str) -> BumpKind:
    """What a ``VERSION`` change is: unchanged (``patch``), +1 MINOR, or +1 MAJOR with MINOR 0.

    Anything else -- backwards, a skipped number, a MAJOR bump that keeps MINOR --
    raises :class:`VersionError`.
    """
    before, after = _as_base(old), _as_base(new)
    if after == before:
        return "patch"
    if after.major == before.major and after.minor == before.minor + 1:
        return "minor"
    if after.major == before.major + 1 and after.minor == 0:
        return "major"
    if after < before:
        raise VersionError(f"VERSION moves backwards ({before} -> {after})")
    raise VersionError(
        f"VERSION {before} -> {after} is not one step: a MINOR bump is "
        f"{before.major}.{before.minor + 1}, a MAJOR bump is {before.major + 1}.0"
    )


# --------------------------------------------------------------------------- #
# Declarations and release notes
# --------------------------------------------------------------------------- #
def parse_declaration(body: str) -> list[str]:
    """Every ``Release-Impact:`` value in a PR body (lower-cased, in order)."""
    return [m.lower() for m in _DECLARATION_RE.findall(str(body or ""))]


@dataclass(frozen=True)
class ReleaseNote:
    impact: BumpKind
    version: Base | None
    summary: str


def parse_release_note(text: str) -> ReleaseNote:
    """A ``docs/release-notes/*.md`` fragment: a header block, a blank line, a summary.

    ::

        Release-Impact: minor
        Version: 0.3

        What changed for users (and "Action required: ..." for a breaking change).
    """
    lines = str(text or "").replace("\r\n", "\n").split("\n")
    header: dict[str, str] = {}
    index = 0
    while index < len(lines) and lines[index].strip():
        key, sep, value = lines[index].partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z][A-Za-z-]*", key.strip()):
            raise VersionError(f"header line is not 'Key: value': {lines[index]!r}")
        header[key.strip().lower()] = value.strip()
        index += 1
    impact = _KINDS.get(header.get("release-impact", "").lower())
    if impact is None:
        raise VersionError("needs a 'Release-Impact: patch|minor|major' header line")
    version_text = header.get("version", "")
    version = parse_base(version_text) if version_text else None
    if impact != "patch" and version is None:
        raise VersionError(f"a {impact} note needs a 'Version: MAJOR.MINOR' header line")
    summary = "\n".join(lines[index:]).strip()
    if not summary:
        raise VersionError("needs a user-facing summary after the header")
    return ReleaseNote(impact, version, summary)


# --------------------------------------------------------------------------- #
# "Bump required" signals (deterministic; the rest is a reviewer checklist)
# --------------------------------------------------------------------------- #
#: Where default behaviour is defined, by file and top-level class / function
#: name. Any code change there (comments, formatting and docstrings excluded)
#: is a changed default or a new/removed setting: at least a MINOR bump.
DEFAULT_SURFACES: Mapping[str, tuple[str, ...]] = {
    # Platform settings and their defaults (Settings page, /platform/settings).
    "apps/backend/app/main.py": ("PlatformSettings",),
    # The default agent runtime (D-27).
    "locus_runtime/harness/runtimes.py": ("default_runtime_name",),
}

_PORT_RE = re.compile(r"^PORT_VERSION\s*(?::[^=\n]*)?=\s*(.+?)\s*(?:#.*)?$", re.MULTILINE)
_SCHEMA_RE = re.compile(
    r"^([A-Z0-9_]*SCHEMA_VERSION)\s*(?::[^=\n]*)?=\s*(.+?)\s*(?:#.*)?$", re.MULTILINE
)


@dataclass(frozen=True)
class FileChange:
    """One changed path: ``status`` is git's name-status letter (A, M, D, T)."""

    status: str
    path: str


Reader = Callable[[str], str | None]


def _assignments(pattern: re.Pattern[str], text: str | None) -> dict[str, str]:
    if not text:
        return {}
    out: dict[str, str] = {}
    for match in pattern.finditer(text):
        groups = match.groups()
        name, value = (groups[0], groups[1]) if len(groups) == 2 else ("PORT_VERSION", groups[0])
        out[name] = value
    return out


def _strip_docstrings(node: ast.AST) -> ast.AST:
    for child in ast.walk(node):
        body = getattr(child, "body", None)
        if (
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module)
            and isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            child.body = body[1:] or [ast.Pass()]
    return node


def surface_fingerprints(text: str | None, names: Iterable[str]) -> dict[str, str]:
    """``{name: ast dump}`` of the named top-level classes / functions present in ``text``.

    Raises :class:`VersionError` when ``text`` is not valid Python.
    """
    if text is None:
        return {}
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise VersionError(f"cannot parse ({exc.msg})") from exc
    wanted = set(names)
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in wanted
        ):
            out[node.name] = ast.dump(_strip_docstrings(node))
    return out


def _is_migration(path: str) -> bool:
    return "migrations" in path.lower().split("/")[:-1]


def bump_signals(
    changes: Sequence[FileChange], read_before: Reader, read_after: Reader
) -> list[str]:
    """Reasons this change needs at least a MINOR bump (empty when none were detected).

    Detected: a port contract version (``PORT_VERSION``, D-28) added, removed or
    changed; a persisted schema version (``*SCHEMA_VERSION``) changed, i.e. an
    automatic data migration; a new file under a ``migrations/`` directory; any
    change to a :data:`DEFAULT_SURFACES` default. Everything else in the rules is
    a reviewer checklist item (docs/VERSIONING.md).
    """
    signals: list[str] = []
    for change in changes:
        path = change.path
        status = change.status[:1].upper()
        if status == "A" and _is_migration(path):
            signals.append(f"{path}: new migration (automatic data/config migration)")
        if not path.endswith(".py"):
            continue
        before = None if status == "A" else read_before(path)
        after = None if status == "D" else read_after(path)
        for pattern, label in ((_PORT_RE, "port contract version"), (_SCHEMA_RE, "schema version")):
            old, new = _assignments(pattern, before), _assignments(pattern, after)
            for name in sorted(set(old) | set(new)):
                if old.get(name) != new.get(name):
                    signals.append(
                        f"{path}: {label} {name} {old.get(name, '(none)')} -> "
                        f"{new.get(name, '(none)')}"
                    )
        names = DEFAULT_SURFACES.get(path)
        if names:
            try:
                old_fp = surface_fingerprints(before, names)
                new_fp = surface_fingerprints(after, names)
            except VersionError as exc:
                signals.append(f"{path}: {exc}; default surfaces cannot be compared")
                continue
            for name in names:
                if old_fp.get(name) != new_fp.get(name):
                    signals.append(f"{path}: default surface {name} changed")
    return signals


# --------------------------------------------------------------------------- #
# Pinned manifests
# --------------------------------------------------------------------------- #
_JSON_VERSION_LINE = re.compile(r'^([ \t]*"version"[ \t]*:[ \t]*")([^"\r\n]*)(")', re.MULTILINE)
_TOML_VERSION_LINE = re.compile(r'^(version[ \t]*=[ \t]*")([^"\r\n]*)(")', re.MULTILINE)
#: Helm ``Chart.yaml``: the top-level ``version`` and ``appVersion`` scalars.
_YAML_VERSION_LINE = re.compile(
    r"""^((?:version|appVersion):[ \t]*["']?)([^"'\r\n]*?)(["']?[ \t]*)(?=\r?$)""",
    re.MULTILINE,
)


def _manifest_kind(path: str) -> tuple[str, int]:
    """``(format, number of version fields)`` of a pinned manifest path."""
    name = path.replace("\\", "/").lower().rsplit("/", 1)[-1]
    if name == "package-lock.json":
        return "json", 2
    if name.endswith(".json"):
        return "json", 1
    if name in {"cargo.toml", "pyproject.toml"}:
        return name, 1
    if name == "chart.yaml":
        return "yaml", 2
    raise VersionError(f"not a pinned manifest: {path}")


def _version_line(kind: str) -> re.Pattern[str]:
    if kind == "json":
        return _JSON_VERSION_LINE
    return _YAML_VERSION_LINE if kind == "yaml" else _TOML_VERSION_LINE


def _parsed_without_version(path: str, text: str) -> tuple[object, list[str]]:
    """``(document minus its version field(s), the removed values)``."""
    kind, count = _manifest_kind(path)
    if kind == "yaml":
        # No YAML parser in the standard library: the two top-level scalars are
        # read line-wise; the rest of the document is compared as text.
        values: list[str] = []

        def take(match: re.Match[str]) -> str:
            values.append(match[2])
            return match[1] + match[3]

        rest = _YAML_VERSION_LINE.sub(take, text.replace("\r\n", "\n"))
        if len(values) != count:
            raise VersionError(f"{path} needs one top-level version and appVersion")
        return rest, values
    if kind == "json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise VersionError(f"{path} is not a JSON object")
        values = [str(data.pop("version", None))]
        if path.lower().endswith("package-lock.json"):
            root = (data.get("packages") or {}).get("")
            if not isinstance(root, dict):
                raise VersionError(f"{path} has no root package entry")
            values.append(str(root.pop("version", None)))
        return data, values
    toml = tomllib.loads(text)
    table = toml.get("package" if kind == "cargo.toml" else "project")
    if not isinstance(table, dict):
        raise VersionError(f"{path} has no [{'package' if kind == 'cargo.toml' else 'project'}]")
    return toml, [str(table.pop("version", None))]


def manifest_version(path: str, text: str) -> list[str]:
    """The version(s) a pinned manifest declares."""
    return _parsed_without_version(path, text)[1]


def manifest_only_version_changed(path: str, before: str, after: str, expected: str) -> bool:
    """True iff ``after`` is ``before`` with only its version field(s) set to ``expected``.

    Checked twice: the parsed documents (minus the version fields) are equal, and
    the text differs only on version lines. Used by the D-22 merge guard to let a
    MINOR bump touch pinned manifests that sit inside protected paths.
    """
    kind, count = _manifest_kind(path)
    line = _version_line(kind)
    try:
        old_doc, _old = _parsed_without_version(path, before)
        new_doc, new_values = _parsed_without_version(path, after)
    except (ValueError, AttributeError, TypeError):
        return False
    if new_values != [expected] * count or old_doc != new_doc:
        return False
    old_lines, new_lines = before.splitlines(), after.splitlines()
    if len(old_lines) != len(new_lines):
        return False
    changed = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    return len(changed) <= count and all(line.match(a) and line.match(b) for a, b in changed)


def set_manifest_version(path: str, text: str, version: str) -> str:
    """``text`` with its version field(s) set to ``version``, formatting preserved."""
    kind, count = _manifest_kind(path)
    line = _version_line(kind)
    updated, replaced = line.subn(lambda m: f"{m[1]}{version}{m[3]}", text, count=count)
    if replaced != count or not manifest_only_version_changed(path, text, updated, version):
        raise VersionError(f"{path}: the version field is not where it is expected")
    return updated


def sync_manifests(root: Path, base: Base) -> list[str]:
    """Set every pinned manifest under ``root`` to ``<base>.0``; returns the changed paths.

    Line endings and formatting are preserved; a missing manifest is skipped
    (``check`` reports it). ``lattix version sync`` runs it after a ``VERSION``
    edit; the loop runner runs it host-side for a MINOR bump.
    """
    expected = f"{base}.0"
    changed: list[str] = []
    for path in PINNED_MANIFESTS:
        target = Path(root) / path
        if not target.is_file():
            continue
        with target.open(encoding="utf-8", newline="") as fh:
            text = fh.read()
        if manifest_version(path, text) == [expected] * _manifest_kind(path)[1]:
            continue
        updated = set_manifest_version(path, text, expected)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(updated)
        changed.append(path)
    return changed


def manifest_problems(base: Base, read: Reader) -> list[str]:
    """Each pinned manifest must say ``<MAJOR>.<MINOR>.0`` (the build overlay sets the rest)."""
    expected = f"{base}.0"
    problems: list[str] = []
    for path in PINNED_MANIFESTS:
        text = read(path)
        if text is None:
            problems.append(f"{path} is missing")
            continue
        try:
            found = manifest_version(path, text)
        except (ValueError, AttributeError, TypeError) as exc:
            problems.append(f"{path} cannot be parsed ({type(exc).__name__})")
            continue
        if any(value != expected for value in found):
            problems.append(f"{path} says {', '.join(found)}; VERSION {base} requires {expected}")
    return problems


# --------------------------------------------------------------------------- #
# The PR check
# --------------------------------------------------------------------------- #
@dataclass
class CheckResult:
    """Outcome of :func:`evaluate_change` / :func:`run_check`."""

    version: str = ""
    impact: str = ""
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        lines = [f"VERSION {self.version or '?'}; release impact: {self.impact or 'n/a'}"]
        lines += [f"note: {note}" for note in self.notes]
        lines += [f"error: {problem}" for problem in self.problems]
        lines.append("ok" if self.ok else "see docs/VERSIONING.md")
        return "\n".join(lines)


def _legacy_base(read_before: Reader) -> Base | None:
    text = read_before(_LEGACY_BASE_FILE)
    if text is None:
        return None
    try:
        legacy = parse_version(str(json.loads(text).get("version") or ""))
    except (ValueError, AttributeError):
        return None
    return legacy.base


def evaluate_change(
    *,
    changes: Sequence[FileChange],
    read_before: Reader,
    read_after: Reader,
    pr_body: str | None,
) -> CheckResult:
    """Validate one change against its base (pure; the readers supply file contents).

    * ``VERSION`` is well-formed; the pinned manifests say ``<MAJOR>.<MINOR>.0``.
    * A ``VERSION`` change is exactly +1 MINOR or +1 MAJOR with MINOR 0, never backwards.
    * With ``pr_body``: exactly one ``Release-Impact`` declaration, equal to the actual bump.
    * A detected bump signal needs at least a MINOR bump.
    * A MINOR / MAJOR bump adds a release-notes fragment with the same impact and version.
    """
    result = CheckResult()
    after_text = read_after(VERSION_FILE)
    if after_text is None:
        result.problems.append("VERSION is missing")
        return result
    try:
        new = read_version_file(after_text)
    except VersionError as exc:
        result.problems.append(str(exc))
        return result
    result.version = str(new)
    result.problems += manifest_problems(new, read_after)

    before_text = read_before(VERSION_FILE)
    old: Base | None
    if before_text is None:
        old = _legacy_base(read_before)
        if old is not None:
            result.notes.append(f"the base has no VERSION; compared with its tauri.conf.json {old}")
    else:
        try:
            old = read_version_file(before_text)
        except VersionError:
            old = None
            result.notes.append("the base VERSION is malformed; only the new one is checked")
    actual: BumpKind | None = None
    if old is None:
        result.problems.append("the base version cannot be determined")
    else:
        try:
            actual = classify_bump(old, new)
        except VersionError as exc:
            result.problems.append(str(exc))
    result.impact = actual or ""

    if pr_body is not None:
        declared = list(dict.fromkeys(parse_declaration(pr_body)))
        if not declared:
            result.problems.append("the PR body has no 'Release-Impact: patch|minor|major' line")
        elif len(declared) > 1:
            result.problems.append(f"the PR body declares several impacts: {declared}")
        elif declared[0] not in BUMP_KINDS:
            result.problems.append(f"unknown Release-Impact {declared[0]!r}")
        elif actual is not None and declared[0] != actual:
            hint = (
                f"edit VERSION ({old} -> {_target(old, declared[0])}) in this PR"
                if actual == "patch" and old is not None
                else f"declare 'Release-Impact: {actual}'"
            )
            result.problems.append(
                f"declared Release-Impact {declared[0]} but VERSION says {actual}: {hint}"
            )

    signals = bump_signals(changes, read_before, read_after)
    if signals and actual == "patch":
        result.problems.append(
            "this change needs at least a MINOR bump: " + "; ".join(signals[:10])
        )
    elif signals:
        result.notes += [f"bump signal: {signal}" for signal in signals[:10]]

    notes_ok = False
    for change in changes:
        path = change.path
        if not path.startswith(RELEASE_NOTES_DIR) or not path.endswith(".md"):
            continue
        if path.rsplit("/", 1)[-1].lower() == "readme.md" or change.status[:1] not in "AM":
            continue
        try:
            note = parse_release_note(read_after(path) or "")
        except VersionError as exc:
            result.problems.append(f"{path}: {exc}")
            continue
        if change.status[:1] != "A" or actual is None:
            continue
        if note.impact != actual:
            result.problems.append(
                f"{path}: Release-Impact {note.impact} but the change is {actual}"
            )
        elif actual != "patch":
            if note.version == new:
                notes_ok = True
            else:
                result.problems.append(f"{path}: Version {note.version} but VERSION is {new}")
    if actual in ("minor", "major") and not notes_ok:
        result.problems.append(
            f"a {actual} bump needs a new {RELEASE_NOTES_DIR}<name>.md with "
            f"'Release-Impact: {actual}' and 'Version: {new}'"
        )
    return result


def _target(old: Base, kind: str) -> str:
    return f"{old.major + 1}.0" if kind == "major" else f"{old.major}.{old.minor + 1}"


# --------------------------------------------------------------------------- #
# IO adapters (git, files)
# --------------------------------------------------------------------------- #
GitRunner = Callable[[Sequence[str]], str]


def git_runner(repo: Path) -> GitRunner:
    """Run ``git -C repo <args>`` (argument list, no shell); raises on failure."""

    def run(args: Sequence[str]) -> str:
        try:
            done = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VersionError(f"git is not available ({type(exc).__name__})") from exc
        if done.returncode != 0:
            detail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
            raise VersionError(f"git {args[0]} failed: {detail[0][:300]}")
        return done.stdout

    return run


def _resolve(git: GitRunner, ref: str) -> str:
    if not _REF_RE.fullmatch(ref) or ".." in ref:
        raise VersionError(f"unsupported git ref {ref!r}")
    return git(["rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"]).strip()


def _show(git: GitRunner, sha: str, path: str) -> str | None:
    try:
        return git(["show", f"{sha}:{path}"])
    except VersionError:
        return None


def collect_changes(git: GitRunner, base_ref: str) -> tuple[str, str, list[FileChange]]:
    """``(merge_base_sha, head_sha, changes)`` of ``HEAD`` against ``base_ref``."""
    base = _resolve(git, base_ref)
    head = _resolve(git, "HEAD")
    merge_base = git(["merge-base", base, head]).strip()
    raw = git(["diff", "--name-status", "--no-renames", "-z", merge_base, head])
    fields = [f for f in raw.split("\0") if f]
    changes = [
        FileChange(fields[i], fields[i + 1].replace("\\", "/"))
        for i in range(0, len(fields) - 1, 2)
    ]
    return merge_base, head, changes


def run_check(repo: Path, *, base_ref: str | None, pr_body: str | None) -> CheckResult:
    """``check``: against ``base_ref`` (committed ``HEAD``), or the working tree alone."""
    repo = Path(repo)
    if base_ref is None:

        def read_tree(path: str) -> str | None:
            target = repo / path
            return target.read_text(encoding="utf-8") if target.is_file() else None

        result = CheckResult()
        text = read_tree(VERSION_FILE)
        if text is None:
            result.problems.append("VERSION is missing")
            return result
        try:
            base = read_version_file(text)
        except VersionError as exc:
            result.problems.append(str(exc))
            return result
        result.version = str(base)
        result.problems += manifest_problems(base, read_tree)
        result.notes.append("no --base-ref: VERSION and the pinned manifests only")
        return result
    git = git_runner(repo)
    merge_base, head, changes = collect_changes(git, base_ref)
    return evaluate_change(
        changes=changes,
        read_before=lambda path: _show(git, merge_base, path),
        read_after=lambda path: _show(git, head, path),
        pr_body=pr_body,
    )


def next_from_files(version_file: Path, tags_file: Path, *, cap: int = PATCH_CAP) -> Version:
    """``next``: ``VERSION`` + a tag listing (names or ``git ls-remote --tags`` output)."""
    base = read_version_file(Path(version_file).read_text(encoding="utf-8"))
    tags = tag_names(Path(tags_file).read_text(encoding="utf-8").splitlines())
    return next_version(base, tags, cap=cap)


# --------------------------------------------------------------------------- #
# CLI (script entry point; ``lattix version`` wraps the same functions)
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="versioning", description="Release versions (D-31), docs/VERSIONING.md"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("next", help="print the next build version")
    p.add_argument("--tags-file", required=True)
    p.add_argument("--version-file", default=VERSION_FILE)

    p = sub.add_parser("validate", help="validate a build version (and optionally its base)")
    p.add_argument("version")
    p.add_argument("--version-file", default=None, help="require MAJOR.MINOR to match it")

    p = sub.add_parser("sync", help="set the pinned manifests to <VERSION>.0")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("check", help="validate VERSION and a PR's release impact")
    p.add_argument("--base-ref", default=None)
    p.add_argument("--pr-body-file", default=None)
    p.add_argument("--repo", default=".")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "next":
            print(next_from_files(Path(args.version_file), Path(args.tags_file)))
        elif args.cmd == "validate":
            version = parse_version(args.version)
            if args.version_file:
                base = read_version_file(Path(args.version_file).read_text(encoding="utf-8"))
                if version.base != base:
                    raise VersionError(f"{version} does not belong to VERSION {base}")
            print(version)
        elif args.cmd == "sync":
            repo = Path(args.repo)
            base = read_version_file((repo / VERSION_FILE).read_text(encoding="utf-8"))
            for path in sync_manifests(repo, base):
                print(f"updated {path}")
        else:
            body = (
                Path(args.pr_body_file).read_text(encoding="utf-8") if args.pr_body_file else None
            )
            result = run_check(Path(args.repo), base_ref=args.base_ref, pr_body=body)
            print(result.render())
            return 0 if result.ok else 1
    except (VersionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
