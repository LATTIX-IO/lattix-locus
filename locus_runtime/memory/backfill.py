"""Background embedding backfill for the embedded memory store (LOCUS-387).

Writes never wait for an embedding: the store marks entries pending and calls
:meth:`EmbeddingBackfillWorker.wake`. The worker embeds pending entries in small
batches, off the request path, and otherwise wakes every ``interval`` seconds, so
entries written while no embedding model was available are embedded once one is
pulled. The embedder's own retry delay keeps a missing model from being polled hard.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

logger = logging.getLogger(__name__)


class BackfillTarget(Protocol):
    def backfill_embeddings(self, *, batch_size: int = 32) -> int: ...


class EmbeddingBackfillWorker:
    """One daemon thread that drains pending embeddings for one store."""

    def __init__(
        self,
        target: BackfillTarget,
        *,
        interval: float = 300.0,
        batch_size: int = 32,
        max_batches_per_wake: int = 50,
    ) -> None:
        self._target = target
        self._interval = max(1.0, float(interval))
        self._batch_size = max(1, int(batch_size))
        self._max_batches = max(1, int(max_batches_per_wake))
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="locus-memory-embeddings", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def wake(self) -> None:
        self._wake.set()

    def run_once(self) -> int:
        """Drain up to ``max_batches_per_wake`` batches; returns entries embedded."""
        total = 0
        for _ in range(self._max_batches):
            if self._stop.is_set():
                break
            try:
                done = self._target.backfill_embeddings(batch_size=self._batch_size)
            except Exception as exc:  # noqa: BLE001 - the worker must outlive one bad batch
                logger.warning("memory.backfill_error error=%s", type(exc).__name__)
                break
            total += done
            if done < self._batch_size:
                break
        return total

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._wake.wait(self._interval)
            self._wake.clear()
