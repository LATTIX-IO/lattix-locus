"""First-run sidecar provisioning for the desktop install.

On first launch the lean installer has only the small bundled binaries (Node,
NATS, the backend). This fetches the heavy sidecars (Postgres+pgvector, Neo4j +
JRE, Ollama) into the writable app-home bin dir and pulls the model, streaming
progress lines the Tauri splash drains. Subsequent launches find everything
present and skip the fetch.

IO (provision / model pull) is injectable so the flow is unit-tested offline.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from . import native_binaries as nb

ProgressFn = Callable[[str], None]
ProvisionFn = Callable[..., "nb.ProvisionReport"]
ModelPullFn = Callable[[str, str], int]
WhichFn = Callable[[list[str], "Path | None"], "str | None"]


def _default_progress(message: str) -> None:
    # Structured, prefixed line the Tauri shell can parse for the splash.
    print(f"[firstrun] {message}", flush=True)


def _default_model_pull(ollama_bin: str, model: str) -> int:
    return subprocess.run([ollama_bin, "pull", model], check=False).returncode


def ensure_sidecars(
    bin_dir: Path,
    *,
    targets: list[str] | None = None,
    model: str | None = "gpt-oss:20b",
    progress: ProgressFn | None = None,
    provision: ProvisionFn | None = None,
    model_pull: ModelPullFn | None = None,
    which: WhichFn | None = None,
) -> "nb.ProvisionReport":
    """Provision missing sidecars into ``bin_dir`` then pull ``model`` if Ollama
    is available. Returns the provision report; never raises on a single failure
    (the supervisor degrades around whatever is still missing)."""
    progress = progress or _default_progress
    provision = provision or nb.provision
    model_pull = model_pull or _default_model_pull
    which = which or nb._which
    targets = list(targets if targets is not None else nb.DEFAULT_TARGETS)
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    progress(f"checking sidecars: {', '.join(targets)}")
    report = provision(targets, bin_dir)
    for name in report.installed:
        progress(f"installed {name}")
    for name in report.skipped:
        progress(f"present {name}")
    for name, detail in report.manual.items():
        progress(f"manual {name}: {detail}")
    for name, err in report.failed.items():
        progress(f"FAILED {name}: {err}")
    for warning in report.warnings:
        progress(warning)

    if model:
        ollama = which(["ollama"], bin_dir)
        if ollama:
            progress(f"pulling model {model} (first run — large download)")
            rc = model_pull(ollama, model)
            progress(f"model pull exit={rc}")
        else:
            progress("ollama not available; skipping model pull")
    progress("first-run provisioning complete")
    return report


def main(argv: list[str] | None = None) -> int:
    from .desktop import writable_bin_dir

    args = list(argv if argv is not None else sys.argv[1:])
    model = args[0] if args else "gpt-oss:20b"
    ensure_sidecars(writable_bin_dir(), model=model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
