"""The loop's pre-PR verifier suite, selected from the run's diff (LOCUS-339).

After a run ends ``done`` and before the runner opens a PR, it runs a suite of
repository gates over the change:

* ``tests``      -- pytest on the tests that map to the changed Python files
* ``lint``       -- ``ruff check`` on the changed Python files
* ``typecheck``  -- mypy on the typed packages the change touches
* ``frontend-*`` -- ``npm run lint`` / ``npm run test`` only when ``apps/frontend/`` changed
* ``policy*``    -- ``opa test policies`` (+ ``tests/policy``) only when ``policies/`` changed
* ``perf``       -- the performance budget suite (:mod:`.perf_budget`) when the change
  touches code on a measured path (backend, runtime, policies)

Selection (:func:`select_gate_checks`) is a pure function of the changed paths
and the repository's file list. Every command is an **argv list** (no shell).
``LOCUS_LOOP_{TEST,LINT,TYPECHECK}_COMMAND`` overrides *replace* the argv of
their check and always keep it in the suite (an override never removes a check).

Execution (:func:`run_gate_suite`) goes through an :class:`Executor` bound to a
gateway session, the same jail the agent ran in: the commands run the
agent-authored code, so they never run on the host directly (P6).
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Literal

from locus_runtime.gateway import redact_text
from locus_runtime.harness.executor import GATEWAY_BLOCKED_EXIT_CODE, ExecResult, Executor

GateStatus = Literal["pass", "fail", "blocked", "skipped"]

COMMAND_NOT_FOUND_EXIT_CODE = 127
_OUTPUT_TAIL_CHARS = 2000
_MAX_PATH_ARGS = 200
_MAX_MAPPED_TESTS = 60

PERF_MODULE = "locus_runtime.loop_runner.perf_budget"
PERF_MODULE_PATH = "locus_runtime/loop_runner/perf_budget.py"
#: Changes under these prefixes can move the measured latencies.
PERF_PREFIXES: tuple[str, ...] = ("apps/backend/", "locus_runtime/", "policies/")
FRONTEND_PREFIX = "apps/frontend/"
POLICY_PREFIX = "policies/"
POLICY_TESTS_PREFIX = "tests/policy/"
#: Override env name -> check id (the same variables the envelope honours).
OVERRIDE_CHECK_IDS: tuple[str, ...] = ("tests", "lint", "typecheck")


# --------------------------------------------------------------------------- #
# Selection (pure)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GateSpec:
    """One check of the suite: a fixed argv run in the workspace."""

    id: str
    argv: tuple[str, ...]
    reason: str
    timeout_seconds: int = 900
    kind: Literal["command", "perf"] = "command"


@dataclass(frozen=True)
class GateSettings:
    python: str = "python"
    opa: str = "opa"
    npm: str = "npm"
    typecheck_roots: tuple[str, ...] = ("locus_runtime", "locus_tooling")
    typecheck_args: tuple[str, ...] = ()
    perf_enabled: bool = True
    perf_iterations: int = 30
    timeout_seconds: int = 900


@dataclass(frozen=True)
class GateSelection:
    checks: tuple[GateSpec, ...]
    skipped: tuple[tuple[str, str], ...] = ()  # (check id, why it is not run)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [{"id": c.id, "argv": list(c.argv), "reason": c.reason} for c in self.checks],
            "skipped": [{"id": i, "reason": r} for i, r in self.skipped],
        }


def parse_command(text: str) -> tuple[str, ...]:
    """An override command string as argv (POSIX quoting; never run through a shell)."""
    try:
        return tuple(shlex.split(str(text or "")))
    except ValueError:
        return ()


def normalize_path(path: str) -> str:
    """Repository-relative POSIX path ('' for anything unsafe: absolute, traversal)."""
    value = str(path or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    if not value or value.startswith("/") or ":" in value or "\x00" in value:
        return ""
    if any(part == ".." for part in value.split("/")):
        return ""
    return value


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def map_tests(changed: Iterable[str], tracked: Iterable[str]) -> list[str]:
    """Tests for the changed area: changed test files, plus ``test_<module>.py`` files
    for each changed module (``__init__.py`` maps by its package name)."""
    tracked_set = {p for p in (normalize_path(t) for t in tracked) if p}
    tests_by_name: dict[str, list[str]] = {}
    for path in sorted(tracked_set):
        if is_test_file(path):
            tests_by_name.setdefault(PurePosixPath(path).name, []).append(path)
    selected: list[str] = []

    def add(path: str) -> None:
        if path not in selected:
            selected.append(path)

    for raw in changed:
        path = normalize_path(raw)
        if not path.endswith(".py"):
            continue
        if is_test_file(path):
            if path in tracked_set:  # a deleted test cannot run
                add(path)
            continue
        pure = PurePosixPath(path)
        stem = pure.parent.name if pure.name == "__init__.py" else pure.stem
        if not stem or stem == "conftest":
            continue
        for test in tests_by_name.get(f"test_{stem}.py", []):
            add(test)
    return selected[:_MAX_MAPPED_TESTS]


def _bounded_paths(paths: Sequence[str]) -> tuple[str, ...]:
    return tuple(paths) if len(paths) <= _MAX_PATH_ARGS else (".",)


def select_gate_checks(
    changed: Sequence[str],
    tracked: Sequence[str],
    *,
    settings: GateSettings | None = None,
    overrides: Mapping[str, Sequence[str]] | None = None,
) -> GateSelection:
    """The verifier suite for a diff. Pure: same inputs, same suite.

    ``changed`` are the paths the run changed (added, modified, deleted);
    ``tracked`` are the repository's files after the change. ``overrides`` maps
    a check id (``tests`` | ``lint`` | ``typecheck``) to a replacement argv.
    """
    cfg = settings or GateSettings()
    replace_with = {k: tuple(v) for k, v in (overrides or {}).items() if k and tuple(v)}
    paths = sorted({p for p in (normalize_path(c) for c in changed) if p})
    tracked_set = {p for p in (normalize_path(t) for t in tracked) if p}
    present_py = [p for p in paths if p.endswith(".py") and p in tracked_set]
    any_py = any(p.endswith(".py") for p in paths)
    checks: list[GateSpec] = []
    skipped: list[tuple[str, str]] = []
    timeout = cfg.timeout_seconds

    def command(check_id: str, argv: Sequence[str], reason: str) -> None:
        override = replace_with.pop(check_id, ())
        if override:
            checks.append(
                GateSpec(
                    check_id, override, f"{reason} (command overridden)", timeout_seconds=timeout
                )
            )
        else:
            checks.append(GateSpec(check_id, tuple(argv), reason, timeout_seconds=timeout))

    # tests: changed-area unit tests
    tests = map_tests(paths, tracked_set)
    if tests:
        command(
            "tests", (cfg.python, "-m", "pytest", "-q", *tests), f"{len(tests)} mapped test file(s)"
        )
    elif "tests" in replace_with:
        command("tests", (), "override always runs")
    elif any_py:
        skipped.append(("tests", "no test file maps to the changed Python files"))
    else:
        skipped.append(("tests", "no Python change"))

    # lint: ruff on the changed Python files
    if present_py:
        command(
            "lint",
            (cfg.python, "-m", "ruff", "check", *_bounded_paths(present_py)),
            f"{len(present_py)} changed Python file(s)",
        )
    elif "lint" in replace_with:
        command("lint", (), "override always runs")
    else:
        skipped.append(("lint", "no changed Python file"))

    # typecheck: the typed packages the change touches
    roots = [
        r
        for r in cfg.typecheck_roots
        if any(p == f"{r}.py" or p.startswith(f"{r}/") for p in present_py)
    ]
    if roots:
        command(
            "typecheck",
            (cfg.python, "-m", "mypy", *roots, *cfg.typecheck_args),
            "touches " + ", ".join(roots),
        )
    elif "typecheck" in replace_with:
        command("typecheck", (), "override always runs")
    else:
        skipped.append(("typecheck", "no change in a type-checked package"))

    # Any override for a check id the suite does not know is still kept (never dropped).
    for check_id, argv in sorted(replace_with.items()):
        checks.append(GateSpec(check_id, argv, "configured override", timeout_seconds=timeout))
    replace_with.clear()

    # frontend: only when apps/frontend changed
    if any(p.startswith(FRONTEND_PREFIX) for p in paths):
        prefix = FRONTEND_PREFIX.rstrip("/")
        for script in ("lint", "test"):
            checks.append(
                GateSpec(
                    f"frontend-{script}",
                    (cfg.npm, "--prefix", prefix, "run", script),
                    "apps/frontend changed",
                    timeout_seconds=timeout,
                )
            )
    else:
        skipped.append(("frontend", "apps/frontend unchanged"))

    # policies: only when policies/ changed
    if any(p.startswith(POLICY_PREFIX) for p in paths):
        checks.append(
            GateSpec(
                "policy",
                (cfg.opa, "test", POLICY_PREFIX.rstrip("/")),
                "policies changed",
                timeout_seconds=timeout,
            )
        )
        if any(t.startswith(POLICY_TESTS_PREFIX) and t.endswith(".py") for t in tracked_set):
            checks.append(
                GateSpec(
                    "policy-tests",
                    (cfg.python, "-m", "pytest", "-q", POLICY_TESTS_PREFIX.rstrip("/")),
                    "policies changed",
                    timeout_seconds=timeout,
                )
            )
    else:
        skipped.append(("policy", "policies unchanged"))

    # performance budgets: changes on a measured path
    if not cfg.perf_enabled:
        skipped.append(("perf", "disabled (LOCUS_LOOP_PERF_GATE=0)"))
    elif PERF_MODULE_PATH not in tracked_set:
        skipped.append(("perf", "the repository has no performance budget suite"))
    elif any(p.startswith(PERF_PREFIXES) for p in paths):
        checks.append(
            GateSpec(
                "perf",
                (cfg.python, "-m", PERF_MODULE, "--iterations", str(max(5, cfg.perf_iterations))),
                "change on a measured path",
                timeout_seconds=timeout,
                kind="perf",
            )
        )
    else:
        skipped.append(("perf", "no change on a measured path"))
    return GateSelection(tuple(checks), tuple(skipped))


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
@dataclass
class GateResult:
    id: str
    status: GateStatus
    argv: list[str] = field(default_factory=list)
    detail: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    output_tail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GateReport:
    results: list[GateResult]
    skipped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if r.status == "fail"]

    @property
    def blocked(self) -> list[GateResult]:
        return [r for r in self.results if r.status == "blocked"]

    @property
    def passed(self) -> bool:
        return not self.failed and not self.blocked

    @property
    def failing_ids(self) -> list[str]:
        return [r.id for r in self.results if r.status in {"fail", "blocked"}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "results": [r.to_dict() for r in self.results],
            "skipped": [{"id": i, "reason": r} for i, r in self.skipped],
        }

    def summary(self) -> str:
        parts = [
            f"{r.id}: {r.status}" + (f" ({r.detail})" if r.detail else "") for r in self.results
        ]
        return "; ".join(parts) or "no checks selected"

    def markdown(self) -> list[str]:
        lines: list[str] = []
        for r in self.results:
            line = f"- `{r.id}` **{r.status}**: `{_short_argv(r.argv)}`"
            if r.exit_code is not None:
                line += f" exit {r.exit_code}"
            if r.detail:
                line += f" ({_clean(r.detail, 200)})"
            lines.append(line)
        for check_id, reason in self.skipped:
            lines.append(f"- `{check_id}` skipped: {_clean(reason, 120)}")
        return lines or ["- (no checks selected)"]


#: Turns a perf run's stdout into ``(status, detail, evidence)``; see :mod:`.perf_budget`.
PerfEvaluator = Callable[[str], tuple[GateStatus, str, dict[str, Any]]]


def _clean(text: Any, limit: int) -> str:
    return redact_text(str(text or ""), limit=limit).replace("<!--", "&lt;!--").replace("`", "'")


def _short_argv(argv: Sequence[str]) -> str:
    joined = " ".join(str(a) for a in argv)
    return _clean(joined if len(joined) <= 160 else joined[:157] + "...", 170)


def _tail(result: ExecResult) -> str:
    text = result.combined()
    if len(text) > _OUTPUT_TAIL_CHARS:
        text = "[...]\n" + text[-_OUTPUT_TAIL_CHARS:]
    return redact_text(text, limit=_OUTPUT_TAIL_CHARS + 16)


def run_gate_check(
    executor: Executor, spec: GateSpec, *, perf_evaluator: PerfEvaluator | None = None
) -> GateResult:
    started = time.time()
    argv = list(spec.argv)
    try:
        res = executor.run(argv, timeout=spec.timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - a crashing executor is blocked, never a pass
        return GateResult(spec.id, "blocked", argv, f"executor error: {type(exc).__name__}")
    out = GateResult(
        id=spec.id,
        status="fail",
        argv=argv,
        exit_code=res.exit_code,
        duration_seconds=round(res.duration_seconds or (time.time() - started), 3),
        output_tail=_tail(res),
    )
    decision = res.gateway
    if (decision is not None and not decision.allowed) or (
        res.exit_code == GATEWAY_BLOCKED_EXIT_CODE and (res.stderr or "").lstrip().startswith("[")
    ):
        out.status = "blocked"
        reasons = ", ".join(decision.reasons) if decision is not None else ""
        out.detail = f"the gateway did not allow `{spec.argv[0] if spec.argv else ''}`" + (
            f" ({_clean(reasons, 200)})" if reasons else ""
        )
        return out
    if res.exit_code == COMMAND_NOT_FOUND_EXIT_CODE:
        out.status = "blocked"
        out.detail = "command not found (exit 127)"
        return out
    if res.timed_out:
        out.detail = f"timed out after {spec.timeout_seconds}s"
        return out
    if res.exit_code != 0:
        out.detail = f"exit code {res.exit_code}"
        return out
    if spec.kind == "perf":
        if perf_evaluator is None:
            out.status, out.detail = "blocked", "no performance baseline store configured"
            return out
        status, detail, evidence = perf_evaluator(res.stdout or "")
        out.status, out.detail, out.evidence = status, detail, evidence
        return out
    out.status = "pass"
    return out


def run_gate_suite(
    executor: Executor,
    selection: GateSelection,
    *,
    perf_evaluator: PerfEvaluator | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> GateReport:
    """Run every selected check in order (all of them: the report shows every failure)."""
    results: list[GateResult] = []
    for spec in selection.checks:
        if should_stop is not None and should_stop():
            results.append(GateResult(spec.id, "blocked", list(spec.argv), "loop disabled"))
            continue
        results.append(run_gate_check(executor, spec, perf_evaluator=perf_evaluator))
    return GateReport(results=results, skipped=list(selection.skipped))


def gate_executables(selection: GateSelection) -> tuple[str, ...]:
    """Executable names the suite needs (argv[0] basenames), for the gate session."""
    names: list[str] = []
    for spec in selection.checks:
        if spec.argv:
            name = PurePosixPath(str(spec.argv[0]).replace("\\", "/")).name
            if name and name not in names:
                names.append(name)
    return tuple(names)
