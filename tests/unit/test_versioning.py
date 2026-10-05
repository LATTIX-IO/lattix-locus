"""D-31: release versions -- MAJOR.MINOR from VERSION, PATCH = build counter (cap 99999).

Pure functions of ``locus_tooling/versioning.py`` (parse/format/validate, the
PATCH counter, bump classification, the PR check and its "bump required"
signals), plus the repository wiring: the pinned manifests, the workflows that
call the script and the protections around VERSION. Property-style cases use a
seeded ``random.Random`` (no extra test dependency).
"""

from __future__ import annotations

import ast
import random
import subprocess
import sys
from pathlib import Path

import pytest

from locus_tooling import versioning as v

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "locus_tooling" / "versioning.py"
# The tags that existed when D-31 was adopted.
LEGACY_TAGS = ["dev-v0.1.0-dev.15", "dev-v0.1.0-dev.16", "v0.1.0", "v0.1.1", "channel-dev"]


# --------------------------------------------------------------------------- #
# Parse / format / validate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [("0.0.0", (0, 0, 0)), ("0.2.7", (0, 2, 7)), ("1.10.99999", (1, 10, 99999))],
)
def test_parse_and_format_round_trip(text: str, expected: tuple[int, int, int]) -> None:
    version = v.parse_version(text)
    assert (version.major, version.minor, version.patch) == expected
    assert v.format_version(version) == str(version) == text


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "0.2",
        "0.2.",
        "v0.2.1",
        "0.2.1-dev.3",
        "0.2.1+build",
        "00.2.1",
        "0.02.1",
        "0.2.01",
        "-0.2.1",
        "+0.2.1",
        " 0.2.1",
        "0.2.1 ",
        "0.2.1\n",
        "0.2.1.0",
        "0.2.100000",
        "0.2.999999999999999999999",
        "1234567890.0.0",
        "0.٢.1",  # Arabic-Indic digit two
        "０.2.1",  # fullwidth zero
        "a.b.c",
    ],
)
def test_parse_version_is_strict(bad: str) -> None:
    with pytest.raises(v.VersionError):
        v.parse_version(bad)


def test_patch_above_the_cap_names_the_minor_bump() -> None:
    with pytest.raises(v.VersionError, match="MINOR bump"):
        v.parse_version("0.2.100000")


@pytest.mark.parametrize("text", ["0.2", "0.2\n", "0.2\r\n", "10.0\n"])
def test_version_file_accepts_major_minor_and_one_newline(text: str) -> None:
    assert str(v.read_version_file(text)) == text.strip()


@pytest.mark.parametrize(
    "bad", ["", "\n", "0.2\n\n", " 0.2", "0.2 ", "0.2.0", "v0.2", "00.2", "0.02", "0", "0.2\t"]
)
def test_version_file_is_strict(bad: str) -> None:
    with pytest.raises(v.VersionError):
        v.read_version_file(bad)


def test_committed_version_file_is_valid_and_pins_every_manifest() -> None:
    base = v.read_version_file((ROOT / "VERSION").read_text(encoding="utf-8"))

    def read(path: str) -> str | None:
        target = ROOT / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    assert v.manifest_problems(base, read) == []


def test_manifest_drift_is_reported() -> None:
    texts = {
        "apps/desktop-tauri/src-tauri/tauri.conf.json": '{"version": "0.2.0"}',
        "apps/desktop-tauri/src-tauri/Cargo.toml": '[package]\nversion = "0.2.0"\n',
        "apps/frontend/package.json": '{"version": "0.3.0"}',
        "apps/frontend/package-lock.json": '{"version": "0.2.0", "packages": {"": {"version": "0.1.0"}}}',
        "pyproject.toml": '[project]\nversion = "0.2.0"\n',
        "helm/lattix-locus/Chart.yaml": "version: 0.2.0\nappVersion: 0.1.0\n",
    }
    problems = v.manifest_problems(v.Base(0, 2), texts.get)
    assert len(problems) == 4
    assert any("install/manifest.json is missing" in p for p in problems)
    assert any("Chart.yaml says 0.2.0, 0.1.0" in p for p in problems)
    assert any("package.json says 0.3.0" in p for p in problems)
    assert any("package-lock.json" in p for p in problems)


@pytest.mark.parametrize("path", v.PINNED_MANIFESTS)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_set_manifest_version_changes_only_the_version_field(path: str, newline: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", newline)
    current = v.manifest_version(path, text)[0]
    updated = v.set_manifest_version(path, text, "7.8.0")
    assert v.manifest_version(path, updated) == ["7.8.0"] * len(v.manifest_version(path, text))
    assert v.manifest_only_version_changed(path, text, updated, "7.8.0")
    assert updated.count(newline) == text.count(newline)  # formatting and line endings kept
    assert v.set_manifest_version(path, updated, current) == text


def test_manifest_only_version_changed_rejects_anything_else() -> None:
    path = "apps/desktop-tauri/src-tauri/tauri.conf.json"
    text = (ROOT / path).read_text(encoding="utf-8")
    bumped = v.set_manifest_version(path, text, "0.99.0")
    assert not v.manifest_only_version_changed(path, text, bumped, "0.98.0")  # wrong value
    sneaky = bumped.replace('"pubkey": "', '"pubkey": "X', 1)
    assert sneaky != bumped
    assert not v.manifest_only_version_changed(path, text, sneaky, "0.99.0")
    reformatted = bumped.replace('"productName"', ' "productName"', 1)
    assert not v.manifest_only_version_changed(path, text, reformatted, "0.99.0")
    assert not v.manifest_only_version_changed(path, text, "not json", "0.99.0")
    cargo = "apps/desktop-tauri/src-tauri/Cargo.toml"
    toml = (ROOT / cargo).read_text(encoding="utf-8")
    dep = v.set_manifest_version(cargo, toml, "0.99.0") + '\n[dependencies.extra]\nversion = "1"\n'
    assert not v.manifest_only_version_changed(cargo, toml, dep, "0.99.0")


def test_sync_manifests_pins_every_manifest(tmp_path: Path) -> None:
    for path in v.PINNED_MANIFESTS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    changed = v.sync_manifests(tmp_path, v.Base(4, 5))
    assert sorted(changed) == sorted(v.PINNED_MANIFESTS)

    def read(path: str) -> str | None:
        return (tmp_path / path).read_text(encoding="utf-8")

    assert v.manifest_problems(v.Base(4, 5), read) == []
    assert v.sync_manifests(tmp_path, v.Base(4, 5)) == []  # idempotent
    assert v.main(["sync", "--repo", str(tmp_path)]) == 1  # no VERSION file there


# --------------------------------------------------------------------------- #
# The PATCH counter
# --------------------------------------------------------------------------- #
def test_first_build_of_a_new_minor_is_patch_zero() -> None:
    # 0.2.0 sorts above every Dev build and the June Stable that existed before D-31.
    assert str(v.next_version("0.2", LEGACY_TAGS)) == "0.2.0"


def test_counter_takes_one_plus_the_highest_patch_across_channels() -> None:
    tags = [*LEGACY_TAGS, "dev-v0.2.0", "dev-v0.2.1", "stable-v0.2.1", "v0.2.4", "dev-v0.2.3"]
    assert str(v.next_version("0.2", tags)) == "0.2.5"
    # Another base's tags never count.
    assert str(v.next_version("0.3", tags)) == "0.3.0"
    assert str(v.next_version("0.1", tags)) == "0.1.2"


@pytest.mark.parametrize(
    "junk",
    [
        "dev-v0.2.0999",
        "dev-v00.2.9",
        "dev-v0.2.9-dev.1",
        "v0.2.9+meta",
        "V0.2.9",
        "nightly-v0.2.9",
        "dev-0.2.9",
        "dev-v0.2.100000",
        "dev-v0.2",
        "refs/heads/dev-v0.2.9",
        "",
    ],
)
def test_junk_tags_are_ignored(junk: str) -> None:
    assert str(v.next_version("0.2", ["dev-v0.2.3", junk])) == "0.2.4"


def test_ls_remote_output_is_parsed() -> None:
    lines = [
        "aaaa\trefs/tags/dev-v0.2.7",
        "bbbb\trefs/tags/dev-v0.2.7^{}",
        "cccc\trefs/tags/stable-v0.2.8",
        "",
        "v0.2.2",
    ]
    assert v.tag_names(lines) == ["dev-v0.2.7", "stable-v0.2.8", "v0.2.2"]
    assert str(v.next_version("0.2", v.tag_names(lines))) == "0.2.9"


def test_the_cap_fails_the_build_and_never_wraps() -> None:
    assert str(v.next_version("0.2", ["dev-v0.2.99998"])) == "0.2.99999"
    with pytest.raises(v.VersionError, match=r"MINOR bump is required .*VERSION to 0\.3"):
        v.next_version("0.2", ["dev-v0.2.99999"])
    with pytest.raises(v.VersionError, match="MINOR bump"):
        v.next_version("0.2", ["dev-v0.2.7"], cap=7)


@pytest.mark.parametrize("cap", [-1, 100000, True, "5"])
def test_cap_is_validated(cap: object) -> None:
    with pytest.raises(v.VersionError):
        v.next_version("0.2", [], cap=cap)  # type: ignore[arg-type]


def test_counter_properties_hold_for_random_tag_sets() -> None:
    rng = random.Random(31)
    prefixes = ("dev-v", "stable-v", "v")
    for _ in range(400):
        base = v.Base(rng.randint(0, 3), rng.randint(0, 5))
        patches = [rng.randint(0, 99998) for _ in range(rng.randint(0, 8))]
        tags = [f"{rng.choice(prefixes)}{base}.{p}" for p in patches]
        tags += [f"dev-v{base.major}.{base.minor + 1}.{rng.randint(0, 99999)}" for _ in range(2)]
        tags += [
            "channel-dev",
            f"dev-v{base}.0-dev.{rng.randint(1, 50)}",
            f"v{base}.0{rng.randint(1, 9)}",
        ]
        rng.shuffle(tags)
        result = v.next_version(base, tags)
        assert result.base == base
        assert result.patch == (max(patches) + 1 if patches else 0)
        assert v.parse_version(str(result)) == result
        assert all(result > v.Version(base.major, base.minor, p) for p in patches)
        assert v.next_version(base, list(reversed(tags))) == result  # order-free


# --------------------------------------------------------------------------- #
# Bump classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        ("0.2", "0.2", "patch"),
        ("0.2\n", "0.3\n", "minor"),
        ("0.9", "0.10", "minor"),
        ("0.2", "1.0", "major"),
        ("1.7", "2.0", "major"),
    ],
)
def test_classify_bump(old: str, new: str, kind: str) -> None:
    assert v.classify_bump(old, new) == kind


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("0.3", "0.2"),
        ("1.0", "0.9"),
        ("0.2", "0.4"),
        ("0.2", "1.1"),
        ("0.2", "2.0"),
        ("0.2", "0.2.0"),
    ],
)
def test_classify_bump_rejects_anything_but_one_step(old: str, new: str) -> None:
    with pytest.raises(v.VersionError):
        v.classify_bump(old, new)


def test_classify_bump_random_pairs_match_the_rule() -> None:
    rng = random.Random(1031)
    for _ in range(500):
        old = v.Base(rng.randint(0, 4), rng.randint(0, 6))
        new = v.Base(rng.randint(0, 5), rng.randint(0, 7))
        if new == old:
            expected: str | None = "patch"
        elif (new.major, new.minor) == (old.major, old.minor + 1):
            expected = "minor"
        elif (new.major, new.minor) == (old.major + 1, 0):
            expected = "major"
        else:
            expected = None
        if expected is None:
            with pytest.raises(v.VersionError):
                v.classify_bump(old, new)
        else:
            assert v.classify_bump(old, new) == expected


# --------------------------------------------------------------------------- #
# Declarations, release notes, signals
# --------------------------------------------------------------------------- #
def test_parse_declaration() -> None:
    assert v.parse_declaration("Summary\n\nRelease-Impact: minor\n") == ["minor"]
    assert v.parse_declaration("  Release-Impact:  Patch \r\n") == ["patch"]
    assert v.parse_declaration("Release-Impact: patch|minor|major") == []
    assert v.parse_declaration("text Release-Impact: minor") == []
    assert v.parse_declaration("") == []


def test_parse_release_note() -> None:
    note = v.parse_release_note("Release-Impact: minor\nVersion: 0.3\n\nNew connector type.\n")
    assert note.impact == "minor" and note.version == v.Base(0, 3)
    assert note.summary == "New connector type."
    assert v.parse_release_note("Release-Impact: patch\n\nFix.").version is None
    for bad in (
        "Version: 0.3\n\nNo impact.",
        "Release-Impact: huge\n\nx",
        "Release-Impact: minor\n\nNo version.",
        "Release-Impact: minor\nVersion: 0.03\n\nx",
        "Release-Impact: patch\n",
        "just text\n\nbody",
    ):
        with pytest.raises(v.VersionError):
            v.parse_release_note(bad)


def _change(*items: tuple[str, str]) -> list[v.FileChange]:
    return [v.FileChange(status, path) for status, path in items]


def test_port_and_schema_version_changes_are_signals() -> None:
    before = {
        "locus_runtime/harness/runtime_contract.py": 'PORT_VERSION = "1.0"\n',
        "locus_runtime/telemetry/sqlite_store.py": "SCHEMA_VERSION = 1\n",
    }
    after = {
        "locus_runtime/harness/runtime_contract.py": 'PORT_VERSION = "1.1"  # additive\n',
        "locus_runtime/telemetry/sqlite_store.py": "SCHEMA_VERSION: int = 2\n",
        "locus_runtime/new_port/contract.py": 'PORT_VERSION = "1.0"\n',
    }
    changes = _change(
        ("M", "locus_runtime/harness/runtime_contract.py"),
        ("M", "locus_runtime/telemetry/sqlite_store.py"),
        ("A", "locus_runtime/new_port/contract.py"),
    )
    signals = v.bump_signals(changes, before.get, after.get)
    assert len(signals) == 3
    assert any('"1.0" -> "1.1"' in s for s in signals)
    assert any("SCHEMA_VERSION 1 -> 2" in s for s in signals)
    assert any("new_port" in s and "(none)" in s for s in signals)


def test_migrations_and_default_surfaces_are_signals() -> None:
    main_py = "apps/backend/app/main.py"
    base = 'class PlatformSettings(BaseModel):\n    """Doc."""\n    local_only_mode: bool = True\n'
    flipped = base.replace("= True", "= False")
    changes = _change(("A", "apps/backend/migrations/0002_add.sql"), ("M", main_py))
    signals = v.bump_signals(changes, {main_py: base}.get, {main_py: flipped}.get)
    assert any("new migration" in s for s in signals)
    assert any("PlatformSettings changed" in s for s in signals)


def test_comment_docstring_and_formatting_changes_are_not_signals() -> None:
    main_py = "apps/backend/app/main.py"
    base = 'class PlatformSettings(BaseModel):\n    """Doc."""\n    local_only_mode: bool = True\n'
    cosmetic = (
        'class PlatformSettings(BaseModel):\n    """Other words."""\n\n'
        "    # a comment\n    local_only_mode: bool = (True)\n"
    )
    other = "x = 1\n" + base
    changes = _change(("M", main_py), ("M", "locus_runtime/harness/loop.py"))
    reads_before = {main_py: base, "locus_runtime/harness/loop.py": "PORT = 1\n"}
    reads_after = {main_py: cosmetic, "locus_runtime/harness/loop.py": "PORT = 2\n"}
    assert v.bump_signals(changes, reads_before.get, reads_after.get) == []
    assert v.bump_signals(_change(("M", main_py)), {main_py: base}.get, {main_py: other}.get) == []


def test_unparseable_default_surface_is_a_signal() -> None:
    main_py = "apps/backend/app/main.py"
    signals = v.bump_signals(
        _change(("M", main_py)), {main_py: "x = 1\n"}.get, {main_py: "def (:\n"}.get
    )
    assert signals and "cannot be compared" in signals[0]


def test_every_default_surface_exists_in_the_tree() -> None:
    # A renamed or moved surface would silently stop being watched.
    for path, names in v.DEFAULT_SURFACES.items():
        found = v.surface_fingerprints((ROOT / path).read_text(encoding="utf-8"), names)
        assert set(found) == set(names), path


# --------------------------------------------------------------------------- #
# The PR check (pure)
# --------------------------------------------------------------------------- #
MANIFESTS_02 = {
    "apps/desktop-tauri/src-tauri/tauri.conf.json": '{"version": "0.2.0"}',
    "apps/desktop-tauri/src-tauri/Cargo.toml": '[package]\nversion = "0.2.0"\n',
    "apps/frontend/package.json": '{"version": "0.2.0"}',
    "apps/frontend/package-lock.json": '{"version": "0.2.0", "packages": {"": {"version": "0.2.0"}}}',
    "pyproject.toml": '[project]\nversion = "0.2.0"\n',
    "install/manifest.json": '{"version": "0.2.0"}',
    "helm/lattix-locus/Chart.yaml": 'apiVersion: v2\nversion: 0.2.0\nappVersion: "0.2.0"\n',
}


def _manifests(base: str) -> dict[str, str]:
    return {path: text.replace("0.2.0", f"{base}.0") for path, text in MANIFESTS_02.items()}


def _evaluate(
    before_version: str | None,
    after_version: str,
    *,
    body: str | None,
    files: dict[str, str] | None = None,
    changes: list[v.FileChange] | None = None,
    before_files: dict[str, str] | None = None,
) -> v.CheckResult:
    after = {**_manifests(after_version), v.VERSION_FILE: after_version + "\n", **(files or {})}
    before = dict(before_files or {})
    if before_version is not None:
        before.update(_manifests(before_version))
        before[v.VERSION_FILE] = before_version + "\n"
    if changes is None:
        changes = []
        if before_version != after_version:
            changes.append(v.FileChange("M", v.VERSION_FILE))
        changes += [v.FileChange("A", path) for path in (files or {})]
    return v.evaluate_change(
        changes=changes, read_before=before.get, read_after=after.get, pr_body=body
    )


def test_patch_change_with_declaration_passes() -> None:
    result = _evaluate("0.2", "0.2", body="Fix a typo.\n\nRelease-Impact: patch\n")
    assert result.ok, result.problems
    assert result.impact == "patch" and result.version == "0.2"


def test_minor_bump_with_declaration_and_note_passes() -> None:
    note = {"docs/release-notes/new-connector.md": "Release-Impact: minor\nVersion: 0.3\n\nNew.\n"}
    result = _evaluate("0.2", "0.3", body="Release-Impact: minor", files=note)
    assert result.ok, result.problems
    assert result.impact == "minor"


def test_minor_bump_without_a_release_note_fails() -> None:
    result = _evaluate("0.2", "0.3", body="Release-Impact: minor")
    assert any("release-notes" in p for p in result.problems)


def test_release_note_must_match_the_bump() -> None:
    note = {"docs/release-notes/x.md": "Release-Impact: minor\nVersion: 0.4\n\nNew.\n"}
    result = _evaluate("0.2", "0.3", body="Release-Impact: minor", files=note)
    assert any("Version 0.4 but VERSION is 0.3" in p for p in result.problems)
    patch_note = {"docs/release-notes/y.md": "Release-Impact: minor\nVersion: 0.2\n\nNew.\n"}
    result = _evaluate("0.2", "0.2", body="Release-Impact: patch", files=patch_note)
    assert any("Release-Impact minor but the change is patch" in p for p in result.problems)


def test_missing_wrong_or_conflicting_declaration_fails() -> None:
    assert any("no 'Release-Impact" in p for p in _evaluate("0.2", "0.2", body="").problems)
    wrong = _evaluate("0.2", "0.2", body="Release-Impact: minor")
    assert any("edit VERSION (0.2 -> 0.3)" in p for p in wrong.problems)
    under = _evaluate(
        "0.2",
        "0.3",
        body="Release-Impact: patch",
        files={"docs/release-notes/n.md": "Release-Impact: minor\nVersion: 0.3\n\nx\n"},
    )
    assert any("declare 'Release-Impact: minor'" in p for p in under.problems)
    both = _evaluate("0.2", "0.2", body="Release-Impact: patch\nRelease-Impact: minor\n")
    assert any("several impacts" in p for p in both.problems)
    unknown = _evaluate("0.2", "0.2", body="Release-Impact: huge")
    assert any("unknown Release-Impact" in p for p in unknown.problems)


def test_without_a_pr_body_the_declaration_is_not_required() -> None:
    assert _evaluate("0.2", "0.2", body=None).ok


@pytest.mark.parametrize(("old", "new"), [("0.3", "0.2"), ("0.2", "0.4"), ("0.2", "1.1")])
def test_out_of_step_version_changes_fail(old: str, new: str) -> None:
    result = _evaluate(old, new, body=None)
    assert not result.ok and result.impact == ""


def test_major_bump_passes_the_check_but_needs_its_note() -> None:
    note = {
        "docs/release-notes/one.md": "Release-Impact: major\nVersion: 1.0\n\nAction required: x\n"
    }
    assert _evaluate("0.2", "1.0", body="Release-Impact: major", files=note).ok


def test_signal_without_a_bump_fails_and_with_a_bump_passes() -> None:
    port = "locus_runtime/harness/runtime_contract.py"
    before_files = {port: 'PORT_VERSION = "1.0"\n'}
    files = {port: 'PORT_VERSION = "1.1"\n'}
    changes = [v.FileChange("M", port)]
    result = _evaluate(
        "0.2",
        "0.2",
        body="Release-Impact: patch",
        files=files,
        changes=changes,
        before_files=before_files,
    )
    assert any("needs at least a MINOR bump" in p for p in result.problems)
    note = "docs/release-notes/port.md"
    bumped = _evaluate(
        "0.2",
        "0.3",
        body="Release-Impact: minor",
        files={**files, note: "Release-Impact: minor\nVersion: 0.3\n\nPort 1.1.\n"},
        changes=[*changes, v.FileChange("M", v.VERSION_FILE), v.FileChange("A", note)],
        before_files=before_files,
    )
    assert bumped.ok, bumped.problems
    assert any("bump signal" in n for n in bumped.notes)


def test_base_without_version_file_falls_back_to_the_tauri_base() -> None:
    # The PR that introduces VERSION: the base only had tauri.conf.json 0.1.0.
    before_files = {"apps/desktop-tauri/src-tauri/tauri.conf.json": '{"version": "0.1.0"}'}
    note = {"docs/release-notes/d-31.md": "Release-Impact: minor\nVersion: 0.2\n\nNew scheme.\n"}
    result = _evaluate(
        None,
        "0.2",
        body="Release-Impact: minor",
        files=note,
        changes=[
            v.FileChange("A", v.VERSION_FILE),
            v.FileChange("A", "docs/release-notes/d-31.md"),
        ],
        before_files=before_files,
    )
    assert result.ok, result.problems
    assert result.impact == "minor"


def test_malformed_or_missing_head_version_fails() -> None:
    result = v.evaluate_change(
        changes=[], read_before={}.get, read_after={v.VERSION_FILE: "0.2.0\n"}.get, pr_body=None
    )
    assert not result.ok
    assert not v.evaluate_change(changes=[], read_before={}.get, read_after={}.get, pr_body=None).ok


# --------------------------------------------------------------------------- #
# Git adapter and script entry point
# --------------------------------------------------------------------------- #
def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, {**MANIFESTS_02, v.VERSION_FILE: "0.2\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    return repo


def test_run_check_against_a_base_ref(git_repo: Path) -> None:
    _write(
        git_repo,
        {
            **_manifests("0.3"),
            v.VERSION_FILE: "0.3\n",
            "docs/release-notes/feature.md": "Release-Impact: minor\nVersion: 0.3\n\nNew.\n",
        },
    )
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-q", "-m", "bump")
    result = v.run_check(git_repo, base_ref="main", pr_body="Release-Impact: minor")
    assert result.ok, result.problems
    assert v.run_check(git_repo, base_ref="HEAD^1", pr_body="Release-Impact: minor").ok
    wrong = v.run_check(git_repo, base_ref="main", pr_body="Release-Impact: patch")
    assert not wrong.ok


def test_run_check_without_a_base_checks_the_tree(git_repo: Path) -> None:
    assert v.run_check(git_repo, base_ref=None, pr_body=None).ok
    (git_repo / v.VERSION_FILE).write_text("0.3\n", encoding="utf-8")
    result = v.run_check(git_repo, base_ref=None, pr_body=None)
    assert not result.ok and any("requires 0.3.0" in p for p in result.problems)


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "main..HEAD", "a b", "", "x" * 300])
def test_run_check_refuses_odd_refs(git_repo: Path, ref: str) -> None:
    with pytest.raises(v.VersionError):
        v.run_check(git_repo, base_ref=ref, pr_body=None)


def test_script_runs_standalone_without_the_package(tmp_path: Path) -> None:
    # CI calls `python locus_tooling/versioning.py` with no install: -S drops
    # site-packages, -I the environment and the user site.
    tags = tmp_path / "tags.txt"
    tags.write_text("x\trefs/tags/dev-v0.2.4\nx\trefs/tags/channel-dev\n", encoding="utf-8")
    version_file = tmp_path / "VERSION"
    version_file.write_text("0.2\n", encoding="utf-8")
    cmd = [sys.executable, "-I", "-S", str(SCRIPT)]
    done = subprocess.run(
        [*cmd, "next", "--tags-file", str(tags), "--version-file", str(version_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0.2.5"
    bad = subprocess.run([*cmd, "validate", "0.2.100000"], capture_output=True, text=True)
    assert bad.returncode == 1 and "MINOR bump" in bad.stderr
    other_base = subprocess.run(
        [*cmd, "validate", "0.3.1", "--version-file", str(version_file)],
        capture_output=True,
        text=True,
    )
    assert other_base.returncode == 1 and "does not belong" in other_base.stderr


def test_module_imports_only_the_standard_library() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    modules = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert modules <= set(sys.stdlib_module_names) | {"__future__"}, modules


def test_the_check_is_a_gate_definition_but_version_is_not() -> None:
    from locus_runtime import gateway as gw
    from locus_runtime.gate_definitions import (
        GATE_CONFIG_PATHS,
        GATE_WRITE_PATHS,
        RELEASE_VERSION_PATH,
        gate_config_reason,
        gate_write_reason,
    )
    from locus_runtime.loop_runner.merge_guard import BASELINE_PROTECTED
    from tests.gateway_support import FakeEngine

    assert "locus_tooling/versioning.py" in GATE_CONFIG_PATHS
    assert "locus_tooling/versioning.py" in GATE_WRITE_PATHS
    assert {"/locus_tooling/versioning.py", "/docs/VERSIONING.md"} <= set(BASELINE_PROTECTED)
    assert gate_config_reason("locus_tooling/versioning.py")
    # The loop may bump MINOR: a write to VERSION is not asked; the merge guard
    # judges the content (a MAJOR bump holds for the principal).
    assert RELEASE_VERSION_PATH == v.VERSION_FILE.lower()
    assert not gate_write_reason("VERSION") and not gate_config_reason("version")
    root = "/workspace/project"
    gateway = gw.Gateway(FakeEngine(), lambda _r: None)
    caps = gw.Capabilities(
        allowed_tools=frozenset({"read_file", "write_file"}),
        read_roots=(root,),
        write_roots=(root,),
    )
    session = gateway.open_session(run_id="r", principal="p", engine="e", capabilities=caps)
    try:
        bump = session.authorize(kind="file_write", tool="edit", target=f"{root}/VERSION")
        assert bump.outcome == "allow"
        check = session.authorize(
            kind="file_write", tool="edit", target=f"{root}/locus_tooling/versioning.py"
        )
        assert check.outcome == "ask" and check.risk == gw.RiskClass.R3
    finally:
        session.close()


def test_lattix_version_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from locus_tooling import cli as cli_module

    tags = tmp_path / "tags.txt"
    tags.write_text("dev-v0.2.0\nv0.1.1\n", encoding="utf-8")
    monkeypatch.setattr(cli_module, "ROOT", ROOT)
    base = v.read_version_file((ROOT / "VERSION").read_text(encoding="utf-8"))
    runner = CliRunner()
    out = runner.invoke(cli_module.cli, ["version", "next", "--tags-file", str(tags)])
    assert out.exit_code == 0, out.output
    assert out.output.strip() == str(v.next_version(base, ["dev-v0.2.0", "v0.1.1"]))
    checked = runner.invoke(cli_module.cli, ["version", "check"])
    assert checked.exit_code == 0, checked.output
    assert f"VERSION {base}" in checked.output
