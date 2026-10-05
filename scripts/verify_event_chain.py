"""Verify the in-memory event chain."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _default_chain_path() -> Path:
    configured = str(os.getenv("LOCUS_EVENT_CHAIN_PATH", "") or "").strip()
    if configured:
        return Path(configured)
    return Path(".locus/event-chain.json")


def _load_events(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit(f"Event chain file must contain a JSON array: {path}")
    return [item for item in raw if isinstance(item, dict)]


def _hash_event(event: dict[str, object]) -> str:
    payload = {key: value for key, value in event.items() if key != "hash"}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    path = _default_chain_path()
    events = _load_events(path)
    previous_hash = ""
    for index, event in enumerate(events):
        expected_previous = str(event.get("previous_hash", ""))
        if expected_previous != previous_hash:
            print(
                {
                    "valid": False,
                    "broken_index": index,
                    "reason": "previous_hash mismatch",
                    "path": str(path),
                }
            )  # noqa: T201
            return
        current_hash = _hash_event(event)
        recorded_hash = str(event.get("hash", ""))
        if recorded_hash and recorded_hash != current_hash:
            print(
                {
                    "valid": False,
                    "broken_index": index,
                    "reason": "hash mismatch",
                    "path": str(path),
                }
            )  # noqa: T201
            return
        previous_hash = current_hash
    print({"valid": True, "broken_index": None, "count": len(events), "path": str(path)})  # noqa: T201


if __name__ == "__main__":
    main()
