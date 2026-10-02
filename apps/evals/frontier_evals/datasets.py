"""Datasets for evaluation.

* ``synthetic-mini`` — a handful of self-contained Python bug-fix tasks that
  materialize as real git repos on disk. Runs anywhere; used to validate the
  full eval pipeline (and as a smoke benchmark for any model).
* ``swe-bench`` — real SWE-bench Verified instances loaded via the ``datasets``
  package, each backed by a per-instance Docker container (live mode).

Each dataset yields ``LoadedInstance`` objects that can build a fresh
``SweTask`` (with a bound executor) on demand.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from frontier_runtime.harness.executor import DockerContainerExecutor, LocalDirectExecutor
from frontier_runtime.harness.swe_agent import SweTask


def _shell_python() -> str:
    import subprocess

    for cand in ("python3", "python"):
        try:
            if (
                subprocess.run(["bash", "-c", f"{cand} --version"], capture_output=True).returncode
                == 0
            ):
                return cand
        except OSError:
            continue
    return sys.executable.replace("\\", "/")


@dataclass
class FixRecipe:
    """The known-correct edits — used only by the scripted reference solver."""

    edits: list[dict[str, str]] = field(default_factory=list)  # {path, old, new}


@dataclass
class LoadedInstance:
    instance_id: str
    problem_statement: str
    files: dict[str, str]  # path -> buggy content
    test_script: str  # contents of runtests.py (asserts; exit 0 = resolved)
    fix: FixRecipe
    test_command: str = ""


# ---------------------------------------------------------------------------
# Synthetic mini dataset
# ---------------------------------------------------------------------------

_SYNTHETIC: list[LoadedInstance] = [
    LoadedInstance(
        instance_id="syn-add-sign",
        problem_statement="mathlib.add(2, 3) returns -1 instead of 5. Fix the addition operator.",
        files={
            "mathlib/__init__.py": "",
            "mathlib/core.py": "def add(a, b):\n    return a - b\n",
        },
        test_script=(
            "import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            "from mathlib.core import add\n"
            "assert add(2, 3) == 5\n"
            "assert add(-1, 1) == 0\n"
            "print('OK')\n"
        ),
        fix=FixRecipe(
            edits=[
                {"path": "mathlib/core.py", "old": "    return a - b", "new": "    return a + b"}
            ]
        ),
    ),
    LoadedInstance(
        instance_id="syn-max-empty",
        problem_statement=(
            "utils.safe_max(values) raises on an empty list; it should return None when empty."
        ),
        files={
            "utils.py": ("def safe_max(values):\n    return max(values)\n"),
        },
        test_script=(
            "import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            "from utils import safe_max\n"
            "assert safe_max([3, 1, 2]) == 3\n"
            "assert safe_max([]) is None\n"
            "print('OK')\n"
        ),
        fix=FixRecipe(
            edits=[
                {
                    "path": "utils.py",
                    "old": "def safe_max(values):\n    return max(values)\n",
                    "new": (
                        "def safe_max(values):\n"
                        "    if not values:\n"
                        "        return None\n"
                        "    return max(values)\n"
                    ),
                }
            ]
        ),
    ),
    LoadedInstance(
        instance_id="syn-strip-prefix",
        problem_statement=(
            "text.strip_prefix(s, p) should remove prefix p from s only when present, "
            "but it currently always slices len(p) chars."
        ),
        files={
            "text.py": ("def strip_prefix(s, p):\n    return s[len(p):]\n"),
        },
        test_script=(
            "import os, sys; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
            "from text import strip_prefix\n"
            "assert strip_prefix('foobar', 'foo') == 'bar'\n"
            "assert strip_prefix('bar', 'foo') == 'bar'\n"
            "print('OK')\n"
        ),
        fix=FixRecipe(
            edits=[
                {
                    "path": "text.py",
                    "old": "def strip_prefix(s, p):\n    return s[len(p):]\n",
                    "new": (
                        "def strip_prefix(s, p):\n"
                        "    if s.startswith(p):\n"
                        "        return s[len(p):]\n"
                        "    return s\n"
                    ),
                }
            ]
        ),
    ),
]


def synthetic_instances(instance_ids: list[str] | None = None) -> list[LoadedInstance]:
    if not instance_ids:
        return list(_SYNTHETIC)
    wanted = set(instance_ids)
    return [i for i in _SYNTHETIC if i.instance_id in wanted]


def materialize_synthetic(inst: LoadedInstance, root: Path, *, seed: int | None = None) -> SweTask:
    """Write the buggy repo to ``root``, git-init it, return a bound SweTask."""
    import subprocess

    for rel, content in inst.files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (root / "runtests.py").write_text(inst.test_script, encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "eval@example.com")
    git("config", "user.name", "eval")
    git("add", "-A")
    git("commit", "-q", "-m", "buggy baseline")

    py = _shell_python()
    return SweTask(
        instance_id=inst.instance_id,
        problem_statement=inst.problem_statement,
        executor=LocalDirectExecutor(root),
        test_command=f"{py} runtests.py",
        seed=seed,
        metadata={"dataset": "synthetic-mini", "fix": inst.fix.edits},
    )


# ---------------------------------------------------------------------------
# SWE-bench (live)
# ---------------------------------------------------------------------------


def swebench_tasks(
    instance_ids: list[str],
    *,
    docker_host: str = "",
    container_resolver: Callable[[str], str] | None = None,
    seed: int | None = None,
) -> list[SweTask]:
    """Build SweTasks backed by per-instance SWE-bench Docker containers.

    ``container_resolver(instance_id) -> container_id`` boots/locates the
    official SWE-bench image for the instance (caller-provided so this module
    stays free of a hard docker/datasets dependency). Problem statements are
    pulled from the ``princeton-nlp/SWE-bench_Verified`` dataset when available.
    """
    statements = _load_swebench_statements(instance_ids)
    tasks: list[SweTask] = []
    for iid in instance_ids:
        if container_resolver is None:
            raise RuntimeError(
                "swebench_tasks requires a container_resolver to boot per-instance images"
            )
        container_id = container_resolver(iid)
        executor = DockerContainerExecutor(
            container_id, workdir_path="/testbed", docker_host=docker_host
        )
        tasks.append(
            SweTask(
                instance_id=iid,
                problem_statement=statements.get(iid, ""),
                executor=executor,
                test_command="",  # SWE-bench grades via the official harness, not run_tests
                base_ref="HEAD",
                seed=seed,
                metadata={"dataset": "swe-bench-verified"},
            )
        )
    return tasks


def swebench_instance_ids(
    limit: int = 20, *, split: str = "test", dataset: str = "princeton-nlp/SWE-bench_Verified"
) -> list[str]:
    """List the first ``limit`` instance ids from a SWE-bench dataset.

    Runs on the runner (needs the ``datasets`` extra). Used to pick a real,
    reproducible subset instead of hand-guessing instance ids.
    """
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError as exc:  # pragma: no cover - runner-only
        raise RuntimeError(
            "swebench_instance_ids requires the 'swebench' extra (datasets)"
        ) from exc
    ds = load_dataset(dataset, split=split)
    ids = [row["instance_id"] for row in ds]
    return ids[:limit] if limit and limit > 0 else ids


def _load_swebench_statements(instance_ids: list[str]) -> dict[str, str]:
    try:
        from datasets import load_dataset  # type: ignore
    except ImportError:
        return {}
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    wanted = set(instance_ids)
    out: dict[str, str] = {}
    for row in ds:
        if row["instance_id"] in wanted:
            out[row["instance_id"]] = row.get("problem_statement", "")
    return out
