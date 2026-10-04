"""Loop configuration, safety switches and the local run ledger (LOCUS-338).

Everything the runner persists lives under ``LOCUS_LOOP_HOME`` (default
``~/.locus/loop``):

* ``state.json``  -- the ledger: runs started per UTC day, failure counts per
  issue, the active run (for crash resume) and the last outcome. Written
  atomically.
* ``loop.lock``   -- the machine-wide single-run lock (max concurrent = 1) with
  a TTL, so a crashed process never wedges the loop.
* ``DISABLED``    -- the file kill switch (``LOCUS_LOOP_DISABLED=1`` is the env
  one). Either stops the loop before the next step.
* ``runs/<run_id>/`` -- checkpoint, trajectory, gateway audit and result per run.
* ``worktrees/<run_id>/`` -- the isolated git worktree of each run.
* ``runs.jsonl`` -- one line per finished run (outcome, usage, gate failures, eval);
  ``lattix loop report`` is built from it (LOCUS-339).
* ``perf-baseline.json`` / ``perf-history.jsonl`` -- the performance budget gate.
* ``eval-history.jsonl`` -- eval gate resolve rates.
* ``scorecard-history.jsonl`` / ``variants/`` -- RSI scorecards and the variant
  archive (LOCUS-351); ``scorecards/<run_id>/`` -- each scorecard run's output.
* ``failure-patterns.json`` -- failure fingerprints already filed to Linear.
"""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DISABLED_ENV = "LOCUS_LOOP_DISABLED"
HOME_ENV = "LOCUS_LOOP_HOME"
KILL_FILE = "DISABLED"
_TRUE = frozenset({"1", "true", "yes", "on"})

DEFAULT_ACTIVE_STATES: tuple[str, ...] = ("Todo", "In Progress", "Rework")
DEFAULT_EXCLUDE_LABELS: tuple[str, ...] = (
    "epic",
    "agent:ineligible",
    "agent:human-review-required",
)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUE


def _env_fraction(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return value if 0.0 <= value <= 1.0 else default


def _env_list(name: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in str(os.getenv(name) or "").split(",") if s.strip())


def default_loop_home() -> Path:
    raw = str(os.getenv(HOME_ENV) or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".locus" / "loop"


def read_workflow_front_matter(path: Path) -> dict[str, Any]:
    """The YAML front matter of ``WORKFLOW.md`` ({} if absent/unparseable)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True)
class LoopConfig:
    """Runner configuration: WORKFLOW.md front matter + ``LOCUS_LOOP_*`` env."""

    repo_path: Path
    home: Path
    project_slug: str
    active_states: tuple[str, ...] = DEFAULT_ACTIVE_STATES
    exclude_labels: tuple[str, ...] = DEFAULT_EXCLUDE_LABELS
    required_label: str = "agent:eligible"
    in_progress_state: str = "In Progress"
    review_state: str = "In Review"
    todo_state: str = "Todo"
    blocked_state: str = "Blocked"
    remote: str = "origin"
    base_branch: str = "main"
    poll_interval_seconds: float = 30.0
    max_runs_per_day: int = 5
    max_failures: int = 2
    lock_ttl_seconds: float = 7200.0
    auto_merge: bool = False
    required_checks: tuple[str, ...] = ()
    merge_method: str = "squash"
    #: Replacement commands for detected repo checks, by check id (tests | lint | typecheck),
    #: from ``LOCUS_LOOP_{TEST,LINT,TYPECHECK}_COMMAND``. A check is replaced, never dropped.
    check_commands: tuple[tuple[str, str], ...] = ()
    # -- LOCUS-339 quality gates and feedback. The dataclass defaults are the
    # conservative ones (nothing writes outside the loop home); ``load`` turns the
    # feedback steps on unless the environment turns them off.
    quality_gates: bool = True
    perf_gate: bool = True
    perf_iterations: int = 30
    gate_python: str = "python"
    typecheck_roots: tuple[str, ...] = ("locus_runtime", "locus_tooling")
    typecheck_args: tuple[str, ...] = ()
    eval_gate: str = "off"  # off | advisory | required
    eval_threshold: float = 0.30
    eval_max_steps: int = 20
    propose_skills: bool = False
    file_failure_issues: bool = False
    max_failure_issues_per_day: int = 3
    failure_issue_min_occurrences: int = 2
    # -- LOCUS-351 RSI scorecard. ``LoopConfig.load`` defaults it to ``advisory``
    # when the candidate can run in an OS jail here (LOCUS-379), else ``off``; the
    # bare dataclass (tests, embedders) stays off.
    scorecard_mode: str = "off"  # off | advisory | required
    scorecard_trials: int = 1
    scorecard_splits: tuple[str, ...] = ("dev", "heldout")
    scorecard_model: str = ""
    scorecard_python: str = ""
    tag_variants: bool = False

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    @property
    def worktrees_dir(self) -> Path:
        return self.home / "worktrees"

    @classmethod
    def load(cls, repo_path: Path | str, *, home: Path | None = None) -> LoopConfig:
        repo = Path(repo_path).resolve()
        front = read_workflow_front_matter(repo / "WORKFLOW.md")
        tracker = front.get("tracker") or {}
        provider = tracker.get("provider") or {}
        symphony = front.get("symphony") or {}
        polling = front.get("polling") or {}
        slug = str(os.getenv("LOCUS_LOOP_PROJECT_SLUG") or provider.get("project_slug") or "")
        try:
            interval = float(polling.get("interval_ms") or 30000) / 1000.0
        except (TypeError, ValueError):
            interval = 30.0
        exclude = tuple(str(x) for x in (tracker.get("exclude_labels") or DEFAULT_EXCLUDE_LABELS))
        return cls(
            repo_path=repo,
            home=(home or default_loop_home()).resolve(),
            project_slug=slug.strip(),
            active_states=tuple(
                str(x) for x in (tracker.get("active_states") or DEFAULT_ACTIVE_STATES)
            ),
            exclude_labels=exclude,
            base_branch=str(symphony.get("default_branch") or "main"),
            poll_interval_seconds=_env_float("LOCUS_LOOP_POLL_SECONDS", interval),
            max_runs_per_day=_env_int("LOCUS_LOOP_MAX_RUNS_PER_DAY", 5),
            max_failures=_env_int("LOCUS_LOOP_MAX_FAILURES", 2),
            lock_ttl_seconds=_env_float("LOCUS_LOOP_LOCK_TTL_SECONDS", 7200.0),
            auto_merge=str(os.getenv("LOCUS_LOOP_AUTO_MERGE") or "").strip().lower() in _TRUE,
            required_checks=_env_list("LOCUS_LOOP_REQUIRED_CHECKS"),
            check_commands=tuple(
                (check_id, str(os.getenv(env) or "").strip())
                for check_id, env in (
                    ("tests", "LOCUS_LOOP_TEST_COMMAND"),
                    ("lint", "LOCUS_LOOP_LINT_COMMAND"),
                    ("typecheck", "LOCUS_LOOP_TYPECHECK_COMMAND"),
                )
                if str(os.getenv(env) or "").strip()
            ),
            quality_gates=_env_bool("LOCUS_LOOP_QUALITY_GATES", True),
            perf_gate=_env_bool("LOCUS_LOOP_PERF_GATE", True),
            perf_iterations=_env_int("LOCUS_LOOP_PERF_ITERATIONS", 30),
            gate_python=str(os.getenv("LOCUS_LOOP_GATE_PYTHON") or "").strip() or "python",
            typecheck_roots=_env_list("LOCUS_LOOP_TYPECHECK_ROOTS")
            or ("locus_runtime", "locus_tooling"),
            typecheck_args=tuple(str(os.getenv("LOCUS_LOOP_TYPECHECK_ARGS") or "").split()),
            eval_gate=_eval_mode(os.getenv("LOCUS_LOOP_EVAL_GATE")),
            eval_threshold=_env_fraction("LOCUS_LOOP_EVAL_THRESHOLD", 0.30),
            eval_max_steps=_env_int("LOCUS_LOOP_EVAL_MAX_STEPS", 20),
            propose_skills=_env_bool("LOCUS_LOOP_PROPOSE_SKILLS", True),
            file_failure_issues=_env_bool("LOCUS_LOOP_FILE_FAILURE_ISSUES", True),
            max_failure_issues_per_day=_env_int("LOCUS_LOOP_MAX_FAILURE_ISSUES_PER_DAY", 3),
            failure_issue_min_occurrences=_env_int("LOCUS_LOOP_FAILURE_ISSUE_MIN_OCCURRENCES", 2),
            scorecard_mode=_scorecard_mode(os.getenv("LOCUS_LOOP_SCORECARD")),
            scorecard_trials=_env_int("LOCUS_LOOP_SCORECARD_TRIALS", 1),
            scorecard_splits=_env_list("LOCUS_LOOP_SCORECARD_SPLITS") or ("dev", "heldout"),
            scorecard_model=str(os.getenv("LOCUS_LOOP_SCORECARD_MODEL") or "").strip(),
            scorecard_python=str(os.getenv("LOCUS_LOOP_SCORECARD_PYTHON") or "").strip(),
            tag_variants=_env_bool("LOCUS_LOOP_TAG_VARIANTS", False),
        )


def _eval_mode(value: str | None) -> str:
    from locus_runtime.loop_runner.eval_gate import parse_eval_mode

    return parse_eval_mode(value, "advisory")


def _scorecard_mode(value: str | None) -> str:
    from locus_runtime.loop_runner.scorecard_gate import (
        default_scorecard_mode,
        parse_scorecard_mode,
    )

    # LOCUS-379: the candidate runs in an OS jail (AppContainer / seatbelt /
    # bubblewrap), so the scorecard is advisory by default where that jail exists.
    # Without one it stays off (P32): the candidate would run as the OS user.
    if not str(value or "").strip():
        return default_scorecard_mode()[0]
    return parse_scorecard_mode(value, "off")


# --------------------------------------------------------------------------- #
# Kill switch
# --------------------------------------------------------------------------- #
def kill_switch_reason(home: Path) -> str:
    """Why the loop is disabled ('' when enabled). Checked before every step."""
    if str(os.getenv(DISABLED_ENV) or "").strip().lower() in _TRUE:
        return f"{DISABLED_ENV} is set"
    if (home / KILL_FILE).exists():
        return f"kill-switch file {home / KILL_FILE} exists"
    return ""


# --------------------------------------------------------------------------- #
# Atomic JSON
# --------------------------------------------------------------------------- #
def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# Lock (max concurrent = 1, TTL)
# --------------------------------------------------------------------------- #
class LoopBusy(RuntimeError):
    pass


@dataclass
class RunLock:
    path: Path
    ttl_seconds: float
    clock: Any = time.time
    held: bool = False

    def holder(self) -> dict[str, Any]:
        return _read_json(self.path)

    def _stale(self, data: dict[str, Any]) -> bool:
        try:
            acquired = float(data.get("acquired_at") or 0)
        except (TypeError, ValueError):
            return True
        return bool((float(self.clock()) - acquired) >= self.ttl_seconds)

    def acquire(self, owner: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"owner": owner, "pid": os.getpid(), "acquired_at": self.clock()})
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                current = self.holder()
                if current and not self._stale(current):
                    raise LoopBusy(f"another loop run holds the lock ({current.get('owner')})")
                with suppress(FileNotFoundError):
                    self.path.unlink()  # stale: the holder crashed or overran its TTL
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            self.held = True
            return
        raise LoopBusy("could not acquire the loop lock")

    def refresh(self, owner: str) -> None:
        if self.held:
            write_json_atomic(
                self.path, {"owner": owner, "pid": os.getpid(), "acquired_at": self.clock()}
            )

    def release(self) -> None:
        if self.held:
            with suppress(FileNotFoundError):
                self.path.unlink()
            self.held = False


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
@dataclass
class Ledger:
    """Persistent loop state (``state.json``)."""

    path: Path
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, home: Path) -> Ledger:
        path = home / "state.json"
        return cls(path=path, data=_read_json(path))

    def save(self) -> None:
        write_json_atomic(self.path, self.data)

    # runs per day
    def runs_today(self, today: str) -> int:
        return int((self.data.get("runs_per_day") or {}).get(today, 0))

    def count_run(self, today: str) -> None:
        per_day = {k: v for k, v in (self.data.get("runs_per_day") or {}).items() if k >= today}
        per_day[today] = int(per_day.get(today, 0)) + 1
        self.data["runs_per_day"] = per_day

    # failures per issue
    def failures(self, issue_key: str) -> int:
        return int((self.data.get("failures") or {}).get(issue_key, 0))

    def record_failure(self, issue_key: str) -> int:
        failures = dict(self.data.get("failures") or {})
        count = int(failures.get(issue_key, 0)) + 1
        failures[issue_key] = count
        self.data["failures"] = failures
        return count

    def clear_failures(self, issue_key: str) -> None:
        failures = dict(self.data.get("failures") or {})
        failures.pop(issue_key, None)
        self.data["failures"] = failures

    # active run (crash resume)
    @property
    def active(self) -> dict[str, Any] | None:
        active = self.data.get("active")
        return active if isinstance(active, dict) else None

    def set_active(self, record: dict[str, Any] | None) -> None:
        self.data["active"] = record

    # open loop PRs awaiting the merge guard
    @property
    def open_prs(self) -> list[dict[str, Any]]:
        return [p for p in (self.data.get("open_prs") or []) if isinstance(p, dict)]

    def add_open_pr(self, record: dict[str, Any]) -> None:
        self.data["open_prs"] = [*self.open_prs, record]

    def remove_open_pr(self, number: int) -> None:
        self.data["open_prs"] = [p for p in self.open_prs if int(p.get("number") or -1) != number]

    def set_last(self, record: dict[str, Any]) -> None:
        self.data["last_run"] = record


# --------------------------------------------------------------------------- #
# Run history (LOCUS-339)
# --------------------------------------------------------------------------- #
RUN_HISTORY_FILE = "runs.jsonl"


def append_run_history(home: Path, record: dict[str, Any]) -> None:
    """Append one finished run to ``runs.jsonl`` (the report's source of truth)."""
    path = home / RUN_HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def read_run_history(home: Path, limit: int = 5000) -> list[dict[str, Any]]:
    """The last ``limit`` finished runs (unreadable lines are skipped)."""
    try:
        lines = (home / RUN_HISTORY_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def today_utc(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def loop_status(home: Path | None = None, *, max_runs_per_day: int | None = None) -> dict[str, Any]:
    """Read-only status for ``lattix loop status`` and the posture report."""
    base = (home or default_loop_home()).resolve()
    ledger = Ledger.load(base)
    reason = kill_switch_reason(base)
    lock = _read_json(base / "loop.lock")
    today = today_utc()
    return {
        "enabled": not reason,
        "disabled_reason": reason,
        "home": str(base),
        "runs_today": ledger.runs_today(today),
        "max_runs_per_day": max_runs_per_day or _env_int("LOCUS_LOOP_MAX_RUNS_PER_DAY", 5),
        "lock": {"owner": lock.get("owner"), "acquired_at": lock.get("acquired_at")}
        if lock
        else None,
        "active_run": ledger.active,
        "last_run": ledger.data.get("last_run"),
        "open_prs": ledger.open_prs,
        "failures": ledger.data.get("failures") or {},
        "scorecard": _scorecard_posture(),
        "warnings": _host_warnings(),
    }


def _host_warnings() -> list[str]:
    """Host conditions that weaken the loop's guarantees (names only, never values).

    On Windows, secret-named variables in ``HKCU\\Environment`` are readable by every
    AppContainer, the RSI candidate included (LOCUS-380)."""
    from locus_runtime.rsi.secret_scan import persistent_env_warning

    try:
        warning = persistent_env_warning()
    except Exception:  # noqa: BLE001 - status is read-only and never fails
        return []
    return [warning] if warning else []


def _scorecard_posture() -> dict[str, Any]:
    from locus_runtime.loop_runner.scorecard_gate import scorecard_posture

    try:
        return scorecard_posture()
    except Exception as exc:  # noqa: BLE001 - status is read-only and never fails
        return {"mode": "off", "reason": f"scorecard posture unavailable ({type(exc).__name__})"}
