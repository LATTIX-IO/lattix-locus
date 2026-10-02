"""Build a JSON representation of the built-in agent registry."""

from __future__ import annotations

import json
from pathlib import Path

from locus_tooling.common import discover_agent_records


def main() -> None:
    records = discover_agent_records()
    output_path = Path("agents.registry.json")
    output_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    print(output_path)  # noqa: T201


if __name__ == "__main__":
    main()
