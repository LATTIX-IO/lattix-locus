"""Long-term memory port (D-28): the contract every memory store adapter meets.

The backend talks to long-term memory only through :class:`LongTermMemoryStore`.
Two adapters implement it:

* ``SQLiteLongTermMemoryStore`` (:mod:`locus_runtime.memory.sqlite_store`) -- the
  embedded default for the desktop / local profile: one SQLite file under the app
  home, FTS5 keyword search and sqlite-vec semantic search (LOCUS-387).
* ``PostgresLongTermMemoryStore`` (``apps/backend/app/platform_services.py``) --
  Postgres + pgvector for the full stack.

Embeddings come from an :class:`Embedder`. The default implementation
(:mod:`locus_runtime.memory.embedder`) calls a local engine through the gated
model client, so every embedding request is a gateway ``model_call`` (LOCUS-378).
A store must keep working without embeddings: writes are stored and keyword search
answers; semantic search reports itself pending until an embedding model answers.

Adapters are admitted by the contract suite in
``tests/unit/test_memory_store_contract.py``. A breaking change to this port bumps
:data:`PORT_VERSION`'s major number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

#: Contract version of the long-term memory port (D-28 versioned ports).
PORT_VERSION = "1.0"

#: Store kinds reported by the status endpoints.
StoreKind = Literal["sqlite", "postgres"]

#: Semantic-search state reported to the UI.
SemanticState = Literal["ready", "pending_embedding_model", "disabled"]

#: Hard bounds every adapter enforces on what it stores and returns.
MAX_CONTENT_CHARS = 4000
MAX_IDENTIFIER_CHARS = 512
MAX_QUERY_CHARS = 1000
MAX_RESULTS = 200


class EmbeddingUnavailable(RuntimeError):
    """No embedding could be produced (no model, engine down, call denied)."""


@dataclass(frozen=True)
class EmbedderStatus:
    """Whether semantic search can run, and why not (no memory content)."""

    state: SemanticState
    model: str
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@runtime_checkable
class Embedder(Protocol):
    """Turns text into vectors. Raises :class:`EmbeddingUnavailable` when it cannot."""

    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def status(self) -> EmbedderStatus: ...


@runtime_checkable
class LongTermMemoryStore(Protocol):
    """Durable memory entries, consolidation queue and similarity lookup."""

    enabled: bool
    store_kind: str

    @property
    def embedding_model(self) -> str: ...

    @property
    def vector_enabled(self) -> bool: ...

    def initialize(self) -> None: ...

    def healthcheck(self) -> bool: ...

    def get_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def search_entries(
        self,
        query_text: str,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...

    def append_entry(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, Any],
        source: str,
        task_id: str | None = None,
    ) -> None: ...

    def clear_entries(
        self,
        *,
        bucket_id: str | None = None,
        session_id: str | None = None,
        memory_scope: str | None = None,
    ) -> None: ...

    def enqueue_consolidation_candidate(
        self,
        *,
        bucket_id: str,
        session_id: str,
        memory_scope: str,
        entry: dict[str, Any],
        source: str,
        task_id: str | None = None,
        candidate_kind: str = "promotion",
    ) -> None: ...

    def list_consolidation_candidates(
        self,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def mark_consolidation_candidate(
        self,
        candidate_id: str,
        *,
        status: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None: ...

    def count_consolidation_candidates(self, *, status: str = "pending") -> int: ...

    def find_similar_entries(
        self,
        text: str,
        *,
        bucket_id: str | None = None,
        memory_scope: str | None = None,
        threshold: float = 0.92,
        limit: int = 5,
    ) -> list[dict[str, Any]]: ...
