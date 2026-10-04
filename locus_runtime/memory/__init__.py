"""Long-term memory module (D-28): port, embedded SQLite adapter, gated embeddings.

See :mod:`locus_runtime.memory.contract` for the port and its adapters.
"""

from locus_runtime.memory.contract import (
    PORT_VERSION,
    Embedder,
    EmbedderStatus,
    EmbeddingUnavailable,
    LongTermMemoryStore,
)

__all__ = [
    "PORT_VERSION",
    "Embedder",
    "EmbedderStatus",
    "EmbeddingUnavailable",
    "LongTermMemoryStore",
]
