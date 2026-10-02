"""CLI helper to scaffold a Locus agent."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("--destination", default="generated-agents")
    args = parser.parse_args()
    if Path(args.destination) != Path("generated-agents"):
        raise SystemExit(
            "Custom destinations are not supported by the apps/workers scaffold. Use the default generated-agents flow or call the worker scaffold script directly."
        )
    subprocess.run(
        [sys.executable, "apps/workers/scripts/scaffold_agent_service.py", args.name],
        check=True,
    )


if __name__ == "__main__":
    main()
