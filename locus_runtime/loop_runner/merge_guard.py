"""Auto-merge guard for self-improvement PRs (docs/product/20 §2 D-22).

D-22: a PR opened by the self-improvement loop auto-merges **only** when every
gate is green **and** no protected path changed. The loop can never weaken or
skip a gate. :func:`evaluate_auto_merge` is the pure decision function; the
runner gathers its inputs (changed files, check results, CODEOWNERS text, the
before/after text of gate-config files) and merges through the injected GitHub
client only when the decision is ``merge``.

Every rule fails closed: anything the guard cannot interpret (an unparseable
CODEOWNERS, a changed ``Makefile`` without its contents, a path that escapes
the repository, an unknown check state) is a ``hold`` with a reason. A hold is
not a failure -- the PR stays open for principal review (P4, P32).

Rules (each produces a reason on hold):

1. **Gates green.** At least one check ran; every check completed with
   ``success`` (``skipped``/``neutral`` is accepted only for checks that are not
   required); every required check is present and ``success``.
2. **Protected paths.** No changed path -- including the *previous* path of a
   rename or copy -- matches a ``.github/CODEOWNERS`` pattern or the built-in
   baseline (:data:`BASELINE_PROTECTED`). Matching is case-insensitive and on
   normalized paths; a path that is absolute, contains ``..`` after
   normalization, or contains control characters is held outright.
3. **Gate definitions.** No change to ``.github/workflows/**``, ``policies/**``,
   test-runner/lint/type-check configuration files (``conftest.py`` anywhere,
   ``pytest.ini``, ``setup.cfg``, ``tox.ini``, ``ruff.toml``, ``mypy.ini``,
   ``.pre-commit-config.yaml``, ``scripts/run_opa.py``, ``precommit.*``), the
   gate targets or variables of the ``Makefile``, or the ``[tool.ruff]`` /
   ``[tool.mypy]`` / ``[tool.pytest]`` / ``[tool.coverage]`` sections of
   ``pyproject.toml``. The lists live in :mod:`locus_runtime.gate_definitions`,
   shared with the gateway, which asks (R3) before an agent writes any of them
   (LOCUS-362).
4. **Tests not weakened.** No test file deleted (or renamed out of the test
   tree); no net deletion of test functions or assertions in test files; no
   added skip/xfail markers anywhere.
"""

from __future__ import annotations

import posixpath
import re
import tomllib
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from locus_runtime.gate_definitions import (
    MAKEFILE_GATE_TARGETS,
    PYPROJECT_GATE_TABLES,
    gate_config_reason,
)

MergeAction = Literal["merge", "hold"]

#: Protected even if CODEOWNERS is edited, emptied or deleted (defence in depth):
#: the D-22 list -- policies, gateway, sandbox, secrets, auth, CI, AGENTS.md, CODEOWNERS.
BASELINE_PROTECTED: tuple[str, ...] = (
    "/policies/",
    "/locus_runtime/gateway.py",
    "/locus_runtime/policy_engine.py",
    "/locus_runtime/sandbox.py",
    "/locus_runtime/win_sandbox.py",
    "/locus_runtime/security.py",
    "/locus_runtime/grants.py",
    "/locus_runtime/gate_definitions.py",
    "/locus_tooling/native_secrets.py",
    "/apps/backend/app/policy_gateway.py",
    "/apps/backend/app/request_security.py",
    "/apps/backend/app/control_status.py",
    "/.github/",
    "CODEOWNERS",
    "/AGENTS.md",
    "/SECURITY.md",
    "/THREAT-MODEL.md",
    "/docs/product/",
    # The loop's own guardrails: the loop may not edit the code that guards it.
    "/locus_runtime/loop_runner/",
    # What "better" means (LOCUS-351): the RSI suite, graders, held-out split,
    # scorecard, comparator and candidate isolation.
    "/apps/evals/locus_evals/suite/",
    "/locus_runtime/rsi/",
    # The update trust chain (LOCUS-349, D-26): Dev installs auto-install what
    # merges, so the updater pubkey/endpoints, the shell, the version stamp and
    # the manifest signing/verification are never auto-merged.
    "/apps/desktop-tauri/src-tauri/",
    "/scripts/desktop_channel.py",
    "/locus_tooling/update_contract.py",
    "/locus_tooling/desktop_update.py",
    "/locus_tooling/build_info.py",
)

_SUCCESS = "success"
_SOFT_PASS = frozenset({"skipped", "neutral"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_TEST_FUNC = re.compile(r"^\s*(?:async\s+)?def\s+test\w*\s*\(")
_ASSERT = re.compile(r"^\s*(?:assert\b|self\.assert\w*\s*\(|pytest\.raises\s*\()")
_SKIP_MARKERS = re.compile(
    r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail|importorskip)\s*\("
    r"|unittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|@\s*(?:skip|skipIf|skipUnless|expectedFailure)\b"
    r"|\bit\.skip\s*\(|\bdescribe\.skip\s*\(|\btest\.skip\s*\(|\b(?:it|describe|test)\.todo\s*\(|\bxit\s*\(|\bxdescribe\s*\("
)


# --------------------------------------------------------------------------- #
# Inputs / output
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChangedFile:
    """One file in the PR. ``patch`` is its unified diff (may be empty)."""

    path: str
    status: str = "modified"  # added | modified | removed | renamed | copied
    previous_path: str = ""
    patch: str = ""


@dataclass(frozen=True)
class GateCheck:
    """One CI check on the PR head."""

    name: str
    status: str = "completed"  # queued | in_progress | completed
    conclusion: str = ""  # success | failure | cancelled | skipped | neutral | timed_out | ...


@dataclass(frozen=True)
class MergeDecision:
    action: MergeAction
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def merge(self) -> bool:
        return self.action == "merge"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
class UnsafePath(ValueError):
    """A changed-file path the guard refuses to interpret."""


def normalize_path(raw: str) -> str:
    """Repository-relative, ``/``-separated, lower-case path; raises :class:`UnsafePath`."""
    text = str(raw or "")
    if not text.strip() or _CONTROL_CHARS.search(text):
        raise UnsafePath(f"unsafe path {text!r}")
    text = unicodedata.normalize("NFKC", text.strip()).replace("\\", "/")
    if ":" in text:
        # Drive letters, NTFS alternate data streams ("AGENTS.md::$DATA"): never interpreted.
        raise UnsafePath(f"path with ':' {text!r}")
    if text.startswith("/"):
        raise UnsafePath(f"absolute path {text!r}")
    norm = posixpath.normpath(text)
    if norm in {".", ""} or norm == ".." or norm.startswith("../"):
        raise UnsafePath(f"path escapes the repository {text!r}")
    # A trailing dot/space is ignored by Windows path resolution: "policies./x" == "policies/x".
    parts = [p.rstrip(" .") or p for p in norm.split("/")]
    return "/".join(parts).lower()


# --------------------------------------------------------------------------- #
# CODEOWNERS
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OwnerRule:
    pattern: str
    regex: re.Pattern[str]


def _glob_to_regex(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "*":
            if body[i : i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if body[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def compile_owner_pattern(pattern: str) -> re.Pattern[str]:
    """gitignore-style CODEOWNERS pattern → case-insensitive regex on normalized paths."""
    pat = pattern.strip().replace("\\", "/").lower()
    directory = pat.endswith("/")
    pat = pat.rstrip("/")
    anchored = pat.startswith("/") or "/" in pat
    pat = pat.lstrip("/")
    body = _glob_to_regex(pat)
    prefix = "^" if anchored else "^(?:.*/)?"
    # A pattern names a file or a directory; either way everything beneath matches.
    suffix = "/.*$" if directory else "(?:/.*)?$"
    return re.compile(prefix + body + suffix)


def parse_codeowners(text: str) -> list[OwnerRule]:
    """Every listed pattern is protected (an owner-less line still names a path)."""
    rules: list[OwnerRule] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pattern = stripped.split()[0]
        rules.append(OwnerRule(pattern, compile_owner_pattern(pattern)))
    return rules


def protected_match(path: str, rules: Sequence[OwnerRule]) -> str:
    """The first protected pattern matching normalized ``path`` ('' if none)."""
    for rule in rules:
        if rule.regex.match(path):
            return rule.pattern
    return ""


# --------------------------------------------------------------------------- #
# Gate-config analysis
# --------------------------------------------------------------------------- #
def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith("tests/")
        or "/tests/" in path
        or "/__tests__/" in path
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def _gate_config_reason(path: str) -> str:
    return gate_config_reason(path)


def _pyproject_gate_tables(text: str) -> dict[str, object]:
    data = tomllib.loads(text)
    tool = data.get("tool") or {}
    if not isinstance(tool, dict):
        return {}
    return {name: tool.get(name) for name in PYPROJECT_GATE_TABLES}


_MAKE_RULE = re.compile(r"^([A-Za-z0-9_.%/ -]+?)\s*::?(?!=)(.*)$")
_MAKE_VAR = re.compile(
    r"^\s*(?:export\s+|override\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?|:|::|\+|!)?="
)


def _makefile_gate_facts(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """``({target: [rule line + recipe lines]}, [variable assignment lines])``."""
    targets: dict[str, list[str]] = {}
    variables: list[str] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if raw.startswith("\t"):
            if current is not None:
                current.append(line.strip())
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if _MAKE_VAR.match(line):
            variables.append(re.sub(r"\s+", " ", line.strip()))
            current = None
            continue
        rule = _MAKE_RULE.match(line)
        current = None
        if rule:
            prereqs = rule.group(2).split("##", 1)[0].strip()
            gates = [name for name in rule.group(1).split() if name in MAKEFILE_GATE_TARGETS]
            if gates:
                # A rule naming several gate targets shares one recipe; record it under each.
                current = []
                current.append(f"prereqs: {prereqs}")
                for name in gates:
                    targets.setdefault(name, [])
                    targets[name] = current
        continue
    return targets, sorted(variables)


def _config_reasons(path: str, versions: Mapping[str, tuple[str | None, str | None]]) -> list[str]:
    name = path.rsplit("/", 1)[-1]
    if path not in {"pyproject.toml", "makefile"} and name not in {"pyproject.toml", "makefile"}:
        return []
    pair = None
    for key, value in versions.items():
        try:
            if normalize_path(key) == path:
                pair = value
                break
        except UnsafePath:
            continue
    if pair is None:
        return [f"{path} changed but its before/after contents were not provided"]
    before, after = pair
    if before is None or after is None:
        return [f"{path} added or removed"]
    try:
        if name == "pyproject.toml":
            old, new = _pyproject_gate_tables(before), _pyproject_gate_tables(after)
            return [
                f"pyproject.toml [tool.{table}] changed"
                for table in PYPROJECT_GATE_TABLES
                if old.get(table) != new.get(table)
            ]
        old_t, old_v = _makefile_gate_facts(before)
        new_t, new_v = _makefile_gate_facts(after)
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        return [f"{path} could not be parsed ({type(exc).__name__})"]
    reasons = [
        f"Makefile gate target '{target}' changed"
        for target in sorted(MAKEFILE_GATE_TARGETS)
        if old_t.get(target) != new_t.get(target)
    ]
    if old_v != new_v:
        reasons.append("Makefile variables changed (gate commands may depend on them)")
    return reasons


def _patch_lines(patch: str) -> tuple[list[str], list[str]]:
    added: list[str] = []
    removed: list[str] = []
    for line in str(patch or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #
def _check_reasons(checks: Sequence[GateCheck], required: Iterable[str]) -> list[str]:
    reasons: list[str] = []
    if not checks:
        return ["no CI checks reported on the PR head"]
    by_name: dict[str, list[GateCheck]] = {}
    for check in checks:
        by_name.setdefault(str(check.name).strip().lower(), []).append(check)
    required_names = {str(r).strip().lower() for r in required if str(r).strip()}
    for name in sorted(required_names):
        runs = by_name.get(name)
        if not runs:
            reasons.append(f"required check '{name}' did not run")
            continue
        for run in runs:
            if run.status.lower() != "completed" or run.conclusion.lower() != _SUCCESS:
                reasons.append(
                    f"required check '{name}' is {run.status.lower()}/{run.conclusion.lower() or 'none'}"
                )
    for name, runs in sorted(by_name.items()):
        if name in required_names:
            continue
        for run in runs:
            status, conclusion = run.status.lower(), run.conclusion.lower()
            if status != "completed":
                reasons.append(f"check '{name}' is {status or 'unknown'}")
            elif conclusion != _SUCCESS and conclusion not in _SOFT_PASS:
                reasons.append(f"check '{name}' concluded {conclusion or 'none'}")
    return reasons


def evaluate_auto_merge(
    changed_files: Sequence[ChangedFile],
    checks: Sequence[GateCheck],
    *,
    codeowners_text: str | None,
    required_checks: Iterable[str] = (),
    config_versions: Mapping[str, tuple[str | None, str | None]] | None = None,
) -> MergeDecision:
    """``merge`` only if every D-22 rule passes; otherwise ``hold`` with all reasons.

    ``config_versions`` maps ``pyproject.toml`` / ``Makefile`` (when changed) to
    their ``(base_text, head_text)``; ``None`` for an absent side.
    """
    reasons: list[str] = []
    versions = config_versions or {}

    reasons.extend(_check_reasons(checks, required_checks))

    if codeowners_text is None or not parse_codeowners(codeowners_text):
        reasons.append("CODEOWNERS is missing or empty; protected paths cannot be verified")
        owner_rules: list[OwnerRule] = []
    else:
        owner_rules = parse_codeowners(codeowners_text)
    baseline_rules = parse_codeowners("\n".join(BASELINE_PROTECTED))

    if not changed_files:
        reasons.append("the PR changes no files")

    tests_removed = asserts_removed = 0
    tests_added = asserts_added = 0
    for item in changed_files:
        status = str(item.status or "modified").lower()
        raw_paths = [item.path] + ([item.previous_path] if item.previous_path else [])
        paths: list[str] = []
        unsafe = False
        for raw in raw_paths:
            try:
                paths.append(normalize_path(raw))
            except UnsafePath as exc:
                reasons.append(str(exc))
                unsafe = True
        if unsafe:
            continue
        for path in paths:
            pattern = protected_match(path, owner_rules) or protected_match(path, baseline_rules)
            if pattern:
                reasons.append(f"protected path changed: {path} (matches {pattern})")
            gate = _gate_config_reason(path)
            if gate:
                reasons.append(gate)
        reasons.extend(_config_reasons(paths[0], versions))

        head_path = paths[0]
        old_path = paths[1] if len(paths) > 1 else head_path
        is_test = _is_test_path(head_path) or _is_test_path(old_path)
        if _is_test_path(old_path) and (
            status in {"removed", "deleted"}
            or (status == "renamed" and not _is_test_path(head_path))
        ):
            reasons.append(f"test file deleted or moved out of the test tree: {old_path}")
        added, removed = _patch_lines(item.patch)
        skip_added = [line for line in added if _SKIP_MARKERS.search(line)]
        if skip_added:
            reasons.append(f"skip/xfail marker added in {head_path}")
        if is_test:
            if not item.patch and status not in {"added", "removed", "deleted"}:
                reasons.append(f"test file {head_path} changed but its diff was not provided")
            tests_added += sum(1 for line in added if _TEST_FUNC.match(line))
            tests_removed += sum(1 for line in removed if _TEST_FUNC.match(line))
            asserts_added += sum(1 for line in added if _ASSERT.match(line))
            asserts_removed += sum(1 for line in removed if _ASSERT.match(line))

    if tests_removed > tests_added:
        reasons.append(
            f"net deletion of test functions ({tests_removed} removed, {tests_added} added)"
        )
    if asserts_removed > asserts_added:
        reasons.append(
            f"net deletion of assertions ({asserts_removed} removed, {asserts_added} added)"
        )

    deduped = tuple(dict.fromkeys(reasons))
    return MergeDecision("hold", deduped) if deduped else MergeDecision("merge", ())
