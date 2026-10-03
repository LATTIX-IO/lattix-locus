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

import yaml  # type: ignore[import-untyped]

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
        )


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
    }
