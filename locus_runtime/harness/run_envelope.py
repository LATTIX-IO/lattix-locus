"""Run envelopes: what a run must achieve and what it may spend (P2, 11 §3).

A :class:`RunEnvelope` carries the goal, the **done criteria** the run is
verified against, the capability set, the budget and the autonomy tier. The
verified run loop (:mod:`locus_runtime.harness.verified_loop`) refuses to end a
run as ``done`` unless every done criterion passes (P3).

Done criteria come in three kinds:

* :class:`CommandCheck` -- a command (test, lint, typecheck) run through the
  gated executor; passes on the expected exit code (0 by default).
* :class:`FileCheck` -- a file/assertion check: the file exists (or must not),
  contains a literal, or matches a regular expression. Read through the gated
  executor.
* :class:`AcceptanceCriterion` -- a free-text statement judged by a model
  (the acceptance judge) against the diff. A judge verdict never overrides a
  failing command or file check.

:func:`build_envelope` derives an envelope from task text (an issue body) plus
repository defaults detected in the workspace (pytest, ruff, mypy, npm
test/lint/typecheck). Defaults for the budget and tier come from ``LOCUS_RUN_*``
and ``LOCUS_AUTONOMY_TIER`` environment variables.

Envelopes serialize to plain JSON (``to_dict`` / ``from_dict``) so they can be
stored in a run checkpoint and shown/edited in the UI.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from locus_runtime.gateway import (
    AutonomyTier,
    BudgetFigures,
    Capabilities,
    default_allowed_executables,
)

CheckKind = Literal["command", "file", "acceptance"]

#: Agent tools a coding run may use by default (the loop-level ``update_plan``,
#: ``report_blocker`` and ``submit`` are always available).
DEFAULT_CODING_TOOLS: tuple[str, ...] = (
    "execute_bash",
    "search",
    "str_replace_editor",
    "run_tests",
)
#: Canonical gateway operations a coding run needs (agent_policy ``allowed_tools``).
GATEWAY_OPERATIONS: tuple[str, ...] = ("read_file", "write_file", "process_exec")
_AUTONOMY_TIERS: frozenset[str] = frozenset({"tiered", "supervised", "envelope-autonomous"})
_MAX_GOAL_CHARS = 400
_MAX_CRITERIA = 20


# --------------------------------------------------------------------------- #
# Done criteria
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CommandCheck:
    """A command that must exit with ``expected_exit_code`` (P3 evidence: exit code)."""

    id: str
    command: str
    expected_exit_code: int = 0
    timeout_seconds: int = 600
    description: str = ""
    kind: CheckKind = "command"

    def label(self) -> str:
        return self.description or f"`{self.command}` exits {self.expected_exit_code}"


@dataclass(frozen=True)
class FileCheck:
    """A file/assertion check on the workspace.

    ``must_exist`` false asserts absence. ``contains`` is a literal substring;
    ``pattern`` is a Python regular expression searched in the content (MULTILINE).
    """

    id: str
    path: str
    must_exist: bool = True
    contains: str = ""
    pattern: str = ""
    description: str = ""
    kind: CheckKind = "file"

    def label(self) -> str:
        if self.description:
            return self.description
        if not self.must_exist:
            return f"{self.path} does not exist"
        if self.contains:
            return f"{self.path} contains {self.contains!r}"
        if self.pattern:
            return f"{self.path} matches /{self.pattern}/"
        return f"{self.path} exists"


@dataclass(frozen=True)
class AcceptanceCriterion:
    """A free-text acceptance criterion judged by a model against the diff."""

    id: str
    text: str
    kind: CheckKind = "acceptance"

    def label(self) -> str:
        return self.text


DoneCriterion = CommandCheck | FileCheck | AcceptanceCriterion


def criterion_from_dict(data: dict[str, Any]) -> DoneCriterion:
    kind = str(data.get("kind") or "")
    if kind == "command":
        return CommandCheck(
            id=str(data["id"]),
            command=str(data["command"]),
            expected_exit_code=int(data.get("expected_exit_code", 0)),
            timeout_seconds=int(data.get("timeout_seconds", 600)),
            description=str(data.get("description") or ""),
        )
    if kind == "file":
        return FileCheck(
            id=str(data["id"]),
            path=str(data["path"]),
            must_exist=bool(data.get("must_exist", True)),
            contains=str(data.get("contains") or ""),
            pattern=str(data.get("pattern") or ""),
            description=str(data.get("description") or ""),
        )
    if kind == "acceptance":
        return AcceptanceCriterion(id=str(data["id"]), text=str(data["text"]))
    raise ValueError(f"unknown done-criterion kind: {kind!r}")


# --------------------------------------------------------------------------- #
# Budget, capabilities, envelope
# --------------------------------------------------------------------------- #
def _env_number(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class RunBudget:
    """Hard limits for one run (11 §5 budget gate: report at 80%, stop at 100%).

    ``max_context_tokens`` bounds the prompt size of a single model call (0 = no
    check); the other limits are cumulative over the run.
    """

    max_steps: int = 50
    max_seconds: float = 3600.0
    max_tokens: int = 2_000_000
    max_cost_usd: float = 5.0
    max_actions: int = 200
    max_context_tokens: int = 0

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_seconds", "max_tokens", "max_cost_usd", "max_actions"):
            if not getattr(self, name) > 0:
                raise ValueError(f"budget {name} must be positive (budgets are hard stops)")

    @classmethod
    def from_env(cls, **overrides: Any) -> RunBudget:
        """Defaults from ``LOCUS_RUN_MAX_{STEPS,SECONDS,TOKENS,COST_USD,ACTIONS}``."""
        base = cls()
        values: dict[str, Any] = {
            "max_steps": int(_env_number("LOCUS_RUN_MAX_STEPS", base.max_steps)),
            "max_seconds": _env_number("LOCUS_RUN_MAX_SECONDS", base.max_seconds),
            "max_tokens": int(_env_number("LOCUS_RUN_MAX_TOKENS", base.max_tokens)),
            "max_cost_usd": _env_number("LOCUS_RUN_MAX_COST_USD", base.max_cost_usd),
            "max_actions": int(_env_number("LOCUS_RUN_MAX_ACTIONS", base.max_actions)),
        }
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)


@dataclass(frozen=True)
class EnvelopeCapabilities:
    """Capability set of the run (P2/P7), in the shape the gateway enforces.

    ``tools`` narrows the agent tools the loop offers; ``read_roots`` /
    ``write_roots`` / ``executables`` / ``egress_hosts`` become the gateway
    session's :class:`~locus_runtime.gateway.Capabilities` via
    :meth:`RunEnvelope.gateway_capabilities`.
    """

    tools: tuple[str, ...] = DEFAULT_CODING_TOOLS
    read_roots: tuple[str, ...] = ()
    write_roots: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    egress_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunEnvelope:
    goal: str
    done_criteria: tuple[DoneCriterion, ...]
    capabilities: EnvelopeCapabilities = field(default_factory=EnvelopeCapabilities)
    budget: RunBudget = field(default_factory=RunBudget)
    autonomy_tier: AutonomyTier = "tiered"

    def __post_init__(self) -> None:
        if not str(self.goal or "").strip():
            raise ValueError("an envelope needs a goal")
        if not self.done_criteria:
            raise ValueError("an envelope needs at least one done criterion (P2)")
        ids = [c.id for c in self.done_criteria]
        if len(set(ids)) != len(ids):
            raise ValueError(f"done-criterion ids must be unique: {ids}")
        if self.autonomy_tier not in _AUTONOMY_TIERS:
            raise ValueError(f"unknown autonomy tier: {self.autonomy_tier!r}")

    # -- views ---------------------------------------------------------------
    @property
    def command_checks(self) -> tuple[CommandCheck, ...]:
        return tuple(c for c in self.done_criteria if isinstance(c, CommandCheck))

    @property
    def file_checks(self) -> tuple[FileCheck, ...]:
        return tuple(c for c in self.done_criteria if isinstance(c, FileCheck))

    @property
    def acceptance_criteria(self) -> tuple[AcceptanceCriterion, ...]:
        return tuple(c for c in self.done_criteria if isinstance(c, AcceptanceCriterion))

    def describe_criteria(self) -> str:
        return "\n".join(f"- [{c.id}] ({c.kind}) {c.label()}" for c in self.done_criteria)

    def gateway_capabilities(
        self, *, budget: BudgetFigures | None = None, runtime_profile: str = ""
    ) -> Capabilities:
        """The gateway session capabilities matching this envelope (for the runner
        that opens the run's :class:`~locus_runtime.gateway.GatewaySession`)."""
        executables = self.capabilities.executables or default_allowed_executables()
        return Capabilities(
            allowed_tools=frozenset({*GATEWAY_OPERATIONS, *self.capabilities.tools}),
            read_roots=tuple(self.capabilities.read_roots or self.capabilities.write_roots),
            write_roots=tuple(self.capabilities.write_roots),
            allowed_executables=tuple(executables),
            allowed_egress_hosts=tuple(self.capabilities.egress_hosts),
            autonomy_tier=self.autonomy_tier,
            max_tool_calls=self.budget.max_actions,
            budget=budget
            or BudgetFigures(
                tokens_used=0,
                max_tokens=self.budget.max_tokens,
                duration_used_seconds=0,
                max_duration_seconds=self.budget.max_seconds,
                cost_used_usd=0,
                max_cost_usd=self.budget.max_cost_usd,
            ),
            runtime_profile=runtime_profile,
        )

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "done_criteria": [asdict(c) for c in self.done_criteria],
            "capabilities": {k: list(v) for k, v in asdict(self.capabilities).items()},
            "budget": asdict(self.budget),
            "autonomy_tier": self.autonomy_tier,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunEnvelope:
        caps = data.get("capabilities") or {}
        return cls(
            goal=str(data["goal"]),
            done_criteria=tuple(criterion_from_dict(c) for c in data["done_criteria"]),
            capabilities=EnvelopeCapabilities(**{k: tuple(v) for k, v in caps.items()}),
            budget=RunBudget(**(data.get("budget") or {})),
            autonomy_tier=data.get("autonomy_tier", "tiered"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


# --------------------------------------------------------------------------- #
# Builder: task text + repository defaults
# --------------------------------------------------------------------------- #
class RepoReader(Protocol):
    """What detection needs from a workspace (an :class:`Executor` satisfies it)."""

    def read_file(self, path: str) -> str | None: ...
    def exists(self, path: str) -> bool: ...


def _safe_read(reader: RepoReader, path: str) -> str | None:
    try:
        return reader.read_file(path)
    except Exception:  # noqa: BLE001 - unreadable (or gateway-blocked) means "absent"
        return None


def _safe_exists(reader: RepoReader, path: str) -> bool:
    try:
        return bool(reader.exists(path))
    except Exception:  # noqa: BLE001
        return False


_NPM_DEFAULT_TEST = "no test specified"


def detect_repo_checks(reader: RepoReader) -> list[CommandCheck]:
    """Repository default checks: pytest, ruff, mypy and npm test/lint/typecheck.

    Detection reads marker files only (never runs anything). Checks are returned
    in the order a human would run them: tests, lint, typecheck.
    """
    checks: list[CommandCheck] = []
    pyproject = _safe_read(reader, "pyproject.toml") or ""
    setup_cfg = _safe_read(reader, "setup.cfg") or ""
    tox_ini = _safe_read(reader, "tox.ini") or ""

    has_pytest = (
        "[tool.pytest" in pyproject
        or _safe_exists(reader, "pytest.ini")
        or "[tool:pytest]" in setup_cfg
        or "[pytest]" in tox_ini
        or _safe_exists(reader, "conftest.py")
    )
    if has_pytest:
        checks.append(
            CommandCheck(id="tests", command="python -m pytest -q", description="tests pass")
        )
    if (
        "[tool.ruff" in pyproject
        or _safe_exists(reader, "ruff.toml")
        or _safe_exists(reader, ".ruff.toml")
    ):
        checks.append(CommandCheck(id="lint", command="ruff check .", description="lint is clean"))
    if "[tool.mypy" in pyproject or _safe_exists(reader, "mypy.ini"):
        checks.append(
            CommandCheck(id="typecheck", command="mypy .", description="type check is clean")
        )

    package_json = _safe_read(reader, "package.json")
    if package_json:
        try:
            scripts = (json.loads(package_json) or {}).get("scripts") or {}
        except (ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict):
            test_script = str(scripts.get("test") or "")
            if test_script and _NPM_DEFAULT_TEST not in test_script:
                if not any(c.id == "tests" for c in checks):
                    checks.append(
                        CommandCheck(id="tests", command="npm test", description="tests pass")
                    )
                else:
                    checks.append(
                        CommandCheck(
                            id="npm-tests", command="npm test", description="npm tests pass"
                        )
                    )
            if scripts.get("lint") and not any(c.id == "lint" for c in checks):
                checks.append(
                    CommandCheck(id="lint", command="npm run lint", description="lint is clean")
                )
            if scripts.get("typecheck") and not any(c.id == "typecheck" for c in checks):
                checks.append(
                    CommandCheck(
                        id="typecheck",
                        command="npm run typecheck",
                        description="type check is clean",
                    )
                )
    return checks


_CRITERIA_HEADING = re.compile(
    r"^\s*(?:#+\s*|\*\*)?\s*(?:acceptance criteria|done criteria|definition of done|done when)"
    r"\s*(?:\*\*)?\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)
_ANY_HEADING = re.compile(r"^\s*(?:#+\s+\S|\*\*[^*]+\*\*\s*:?\s*$|[A-Z][\w /-]{2,60}:\s*$)")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+?)\s*$")
_COMMAND_CRITERION = re.compile(
    r"^`([^`]+)`\s+(?:passes|pass|succeeds|is clean|is green|exits\s+(?:with\s+)?0)\b",
    re.IGNORECASE,
)


def parse_task_criteria(task_text: str) -> list[DoneCriterion]:
    """Done criteria stated in task text (an issue body).

    Bullets under an "Acceptance criteria" / "Done criteria" / "Definition of
    done" / "Done when" heading become criteria. A bullet of the form
    "`<command>` passes" becomes a :class:`CommandCheck`; anything else is a
    free-text :class:`AcceptanceCriterion` for the judge.
    """
    criteria: list[DoneCriterion] = []
    in_section = False
    n_cmd = n_ac = 0
    for line in str(task_text or "").splitlines():
        if _CRITERIA_HEADING.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            text = bullet.group(1).strip()
            command = _COMMAND_CRITERION.match(text)
            if command:
                n_cmd += 1
                criteria.append(
                    CommandCheck(
                        id=f"task-cmd-{n_cmd}", command=command.group(1).strip(), description=text
                    )
                )
            else:
                n_ac += 1
                criteria.append(AcceptanceCriterion(id=f"ac-{n_ac}", text=text))
            if len(criteria) >= _MAX_CRITERIA:
                break
            continue
        if line.strip() and _ANY_HEADING.match(line):
            in_section = False  # next section
    return criteria


def goal_from_task(task_text: str) -> str:
    """The first meaningful line of the task (a title), bounded."""
    for line in str(task_text or "").splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:_MAX_GOAL_CHARS]
    return ""


def build_envelope(
    task_text: str,
    *,
    repo: RepoReader | None = None,
    workspace_root: str = "",
    extra_criteria: Iterable[DoneCriterion] = (),
    test_command: str = "",
    budget: RunBudget | None = None,
    autonomy_tier: AutonomyTier | None = None,
    tools: tuple[str, ...] = DEFAULT_CODING_TOOLS,
    executables: tuple[str, ...] = (),
    egress_hosts: tuple[str, ...] = (),
) -> RunEnvelope:
    """Envelope from task text + repository defaults (P2: defaults fill the gaps).

    Order of criteria: an explicit ``test_command`` (or detected repo checks),
    then criteria stated in the task, then ``extra_criteria``. When nothing
    checkable is found, the goal itself becomes one judged acceptance criterion
    so the envelope always has at least one done criterion.
    """
    goal = goal_from_task(task_text)
    if not goal:
        raise ValueError("task text is empty; a run needs a goal")
    criteria: list[DoneCriterion] = []
    if test_command.strip():
        criteria.append(
            CommandCheck(id="tests", command=test_command.strip(), description="tests pass")
        )
    elif repo is not None:
        criteria.extend(detect_repo_checks(repo))
    for criterion in [*parse_task_criteria(task_text), *extra_criteria]:
        if any(c.id == criterion.id for c in criteria):
            continue
        if isinstance(criterion, CommandCheck) and any(
            isinstance(c, CommandCheck) and c.command == criterion.command for c in criteria
        ):
            continue
        criteria.append(criterion)
    if not criteria:
        criteria.append(AcceptanceCriterion(id="ac-goal", text=f"The change accomplishes: {goal}"))

    tier = autonomy_tier or str(os.getenv("LOCUS_AUTONOMY_TIER") or "tiered").strip().lower()
    if tier not in _AUTONOMY_TIERS:
        tier = "tiered"
    roots = (workspace_root,) if workspace_root else ()
    return RunEnvelope(
        goal=goal,
        done_criteria=tuple(criteria),
        capabilities=EnvelopeCapabilities(
            tools=tuple(tools),
            read_roots=roots,
            write_roots=roots,
            executables=tuple(executables),
            egress_hosts=tuple(egress_hosts),
        ),
        budget=budget or RunBudget.from_env(),
        autonomy_tier=tier,  # type: ignore[arg-type]
    )
