"""Gate and CI definition paths, shared by the gateway and the D-22 merge guard.

A *gate definition* is a file that decides whether a change is accepted: CI
workflows, policy, test-runner / lint / type-check configuration, the build
targets that run them and code ownership. Two places must agree on the list:

* the auto-merge guard (:mod:`locus_runtime.loop_runner.merge_guard`, D-22)
  holds a self-improvement PR that changes one, and
* the gateway (:mod:`locus_runtime.gateway`, LOCUS-362) classifies an agent's
  write to one, in any workspace, as R3 (ask), so injected text cannot quietly
  weaken the gates the run will be judged by (P8).

The Rego mirror lives in ``policies/filesystem_access.rego``;
``tests/policy/test_policy_parity.py`` asserts the two lists are identical.
This module is pure (no IO) and is itself a protected path (D-22 baseline,
CODEOWNERS): weakening it is a principal-reviewed change.
"""

from __future__ import annotations

#: Files whose any change redefines a gate (exact basenames, any directory, lower-case).
GATE_CONFIG_BASENAMES: frozenset[str] = frozenset(
    {
        "conftest.py",
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".mypy.ini",
        ".pre-commit-config.yaml",
        ".coveragerc",
        "noxfile.py",
        # Other CI systems' pipeline definitions.
        ".gitlab-ci.yml",
        "azure-pipelines.yml",
        "jenkinsfile",
    }
)
#: Repository-relative gate files / directory prefixes (lower-case; a trailing
#: ``/`` names a directory).
GATE_CONFIG_PATHS: tuple[str, ...] = (
    ".github/workflows/",
    ".circleci/",
    "policies/",
    "scripts/run_opa.py",
    "precommit.sh",
    "precommit.ps1",
    # The RSI scorecard (LOCUS-351): the eval suite, its graders and held-out
    # split, and the scorecard / comparator / candidate-isolation code define
    # what "better" means for the self-improvement loop. The loop must not be
    # able to edit its own exam or its promotion rule.
    "apps/evals/locus_evals/suite/",
    "locus_runtime/rsi/",
)
#: ``pyproject.toml`` tables that configure gates.
PYPROJECT_GATE_TABLES: tuple[str, ...] = ("ruff", "mypy", "pytest", "coverage")
#: ``Makefile`` targets that run gates.
MAKEFILE_GATE_TARGETS: frozenset[str] = frozenset(
    {
        "test",
        "unit-test",
        "integration-test",
        "performance-test",
        "lint",
        "typecheck",
        "policy-test",
        "helm-validate",
    }
)
#: Files that carry gate configuration among other content. The merge guard
#: compares their gate sections / targets; the gateway never sees file content
#: (only the target path), so a write to any of them is treated as a gate edit.
MIXED_GATE_BASENAMES: frozenset[str] = frozenset({"pyproject.toml", "makefile", "gnumakefile"})
#: The whole ``.github`` tree is protected for writes: workflows and composite
#: actions run in CI, CODEOWNERS decides review, and the instruction/prompt files
#: there steer other agents (D-22 baseline protects ``/.github/`` too).
GATE_WRITE_DIRS: tuple[str, ...] = (".github/",)
#: Code ownership files (any directory: GitHub reads root, ``docs/`` and ``.github/``).
OWNERSHIP_BASENAMES: frozenset[str] = frozenset({"codeowners"})

#: Agent instruction and memory files. Coding agents (Locus included) load them
#: as instructions, so a write is an injection-persistence path (P8). They
#: steer future runs the way gates do. ``AGENTS.md`` is also D-22 protected.
AGENT_INSTRUCTION_BASENAMES: frozenset[str] = frozenset(
    {"agents.md", "claude.md", "claude.local.md", "gemini.md", ".cursorrules", ".windsurfrules"}
)
AGENT_INSTRUCTION_PATHS: tuple[str, ...] = (".claude/", ".cursor/", ".ai-memory/")

#: Every basename whose write is a gate edit (gateway; any directory).
GATE_WRITE_BASENAMES: frozenset[str] = (
    GATE_CONFIG_BASENAMES | MIXED_GATE_BASENAMES | OWNERSHIP_BASENAMES | AGENT_INSTRUCTION_BASENAMES
)
#: Every repository-relative prefix / file whose write is a gate edit (gateway).
GATE_WRITE_PATHS: tuple[str, ...] = tuple(
    dict.fromkeys((*GATE_CONFIG_PATHS, *GATE_WRITE_DIRS, *AGENT_INSTRUCTION_PATHS))
)


def _prefix_matches(path: str, prefix: str) -> bool:
    return path == prefix.rstrip("/") or path.startswith(prefix)


def gate_config_reason(path: str) -> str:
    """Why normalized repository-relative ``path`` is a gate config file ('' if not).

    The merge guard's rule 3 (``pyproject.toml`` / ``Makefile`` are compared
    section by section there, not here).
    """
    name = path.rsplit("/", 1)[-1]
    if name in GATE_CONFIG_BASENAMES:
        return f"gate configuration file changed: {path}"
    for prefix in GATE_CONFIG_PATHS:
        if _prefix_matches(path, prefix):
            return f"gate definition changed: {path}"
    return ""


def gate_write_reason(relative: str, *, full: str = "") -> str:
    """Why a write is a gate edit ('' if it is not).

    ``relative`` is the target relative to the workspace (write root) that
    contains it, ``/``-separated; ``full`` the whole normalized target (used for
    the any-depth checks when the target lies outside every write root).
    Matching is case-insensitive.
    """
    rel = relative.strip("/").lower()
    whole = (full or relative).replace("\\", "/").lower()
    name = whole.rstrip("/").rsplit("/", 1)[-1]
    if name in GATE_WRITE_BASENAMES:
        return f"gate definition file: {name}"
    if rel:
        for prefix in GATE_WRITE_PATHS:
            if _prefix_matches(rel, prefix):
                return f"gate definition path: {prefix}"
    # A CI workflow directory at any depth (nested repositories, unknown roots).
    if "/.github/workflows/" in f"/{whole}":
        return "gate definition path: .github/workflows/"
    return ""
