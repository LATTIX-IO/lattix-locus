"""File-aware context deduplication for memory entries.

When file operations accumulate redundantly in context (e.g., multiple
reads/writes to the same file), this module keeps only the most recent
operation per file path, reducing token waste.

Feature flag: LOCUS_MEMORY_FILE_DEDUP_ENABLED (default: false)
"""

from __future__ import annotations

import re
from typing import Any

_FILE_PATH_PATTERN = re.compile(
    r"(?:^|\s)(?:read|wrote|modified|created|deleted|updated|opened|saved|edited)\s+"
    r"[`'\"]?([^\s`'\"]+\.[a-zA-Z0-9]{1,10})[`'\"]?",
    re.IGNORECASE,
)


def _extract_file_path(entry: dict[str, Any]) -> str | None:
    """Extract a file path from an entry's content or metadata."""
    raw_metadata = entry.get("metadata")
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    file_path = str(metadata.get("file_path") or metadata.get("path") or "").strip()
    if file_path:
        return file_path

    content = str(entry.get("content") or "")
    match = _FILE_PATH_PATTERN.search(content)
    if match:
        return match.group(1)
    return None


def dedup_file_operations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the most recent operation per file path.

    Non-file entries pass through unchanged. Entries are processed in
    order; later entries for the same file replace earlier ones.
    """
    if not entries:
        return []

    last_seen: dict[str, int] = {}
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        file_path = _extract_file_path(entry)
        if file_path:
            last_seen[file_path] = i

    if not last_seen:
        return entries

    keep_indices: set[int] = set(last_seen.values())
    result: list[dict[str, Any]] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            result.append(entry)
            continue
        file_path = _extract_file_path(entry)
        if file_path is None or i in keep_indices:
            result.append(entry)
    return result
