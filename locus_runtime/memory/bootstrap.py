"""Default memory bootstrap (LOCUS-387): the embedded store and the Personal collection.

Memory is on by default on the desktop / local profile with zero setup:

* :func:`default_store_path` -- ``<app_home>/data/memory/locus-memory.db``.
* :func:`ensure_personal_collection` -- registers the default ``Personal``
  collection in the memory store. Idempotent: the collection has a stable id, so
  repeated first runs and restarts never duplicate it.
* :func:`ensure_catalog_entry` -- adds a registered collection to the backend's
  knowledge catalog when it is missing there (same stable id; existing entries,
  including their document counts, are never overwritten).

The desktop first run (:mod:`locus_tooling.desktop_firstrun`) calls
:func:`ensure_personal_collection` before the backend starts; the backend calls
both functions on every start, so an install that predates this change, or a
catalog that lost the entry, gets the collection back lazily.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

#: Stable id of the default collection; the knowledge bucket is ``knowledge:personal``.
PERSONAL_COLLECTION_ID = "personal"
PERSONAL_COLLECTION_NAME = "Personal"
PERSONAL_COLLECTION_DESCRIPTION = (
    "Your default collection: notes and documents you add, searchable by your agents."
)
#: The built-in vector store id the backend binds collections to by default.
PLATFORM_VECTOR_STORE_ID = "platform"

MEMORY_DIRNAME = "memory"
MEMORY_DB_FILENAME = "locus-memory.db"


class CollectionRegistry(Protocol):
    def ensure_collection(
        self, collection_id: str, *, name: str, description: str = ""
    ) -> bool: ...


def default_store_path(app_home: Path) -> Path:
    """Where the embedded memory store lives under the app home."""
    return Path(app_home) / "data" / MEMORY_DIRNAME / MEMORY_DB_FILENAME


def ensure_personal_collection(registry: CollectionRegistry) -> bool:
    """Register the Personal collection in ``registry``; ``True`` when newly created."""
    return registry.ensure_collection(
        PERSONAL_COLLECTION_ID,
        name=PERSONAL_COLLECTION_NAME,
        description=PERSONAL_COLLECTION_DESCRIPTION,
    )


def catalog_entry(
    collection_id: str = PERSONAL_COLLECTION_ID,
    *,
    name: str = PERSONAL_COLLECTION_NAME,
    description: str = PERSONAL_COLLECTION_DESCRIPTION,
    created_at: str = "",
) -> dict[str, Any]:
    """A knowledge-catalog record for a collection (the backend's catalog shape)."""
    return {
        "id": collection_id,
        "name": name,
        "description": description,
        "created_at": created_at or datetime.now(UTC).isoformat(),
        "document_count": 0,
        "chunk_count": 0,
        "vector_store_id": PLATFORM_VECTOR_STORE_ID,
        "default": collection_id == PERSONAL_COLLECTION_ID,
    }


def ensure_catalog_entry(
    catalog: MutableMapping[str, dict[str, Any]], record: dict[str, Any] | None = None
) -> bool:
    """Add ``record`` (default: Personal) to ``catalog`` if its id is absent.

    Returns ``True`` when added. A present entry is left untouched.
    """
    entry = dict(record) if record is not None else catalog_entry()
    collection_id = str(entry.get("id") or "").strip()
    if not collection_id or collection_id in catalog:
        return False
    catalog[collection_id] = entry
    return True
