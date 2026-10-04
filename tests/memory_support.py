"""Test doubles for the long-term memory port (LOCUS-387). Test-only.

``ConceptEmbedder`` maps words to a few fixed "concepts", so texts that share no
keyword can still be semantically close (``kitten`` ~ ``feline``) -- enough to
tell semantic hits from keyword hits without a model.
"""

from __future__ import annotations

from collections.abc import Sequence

from locus_runtime.memory.contract import EmbedderStatus, EmbeddingUnavailable

DIMENSIONS = 8

_CONCEPTS: dict[str, int] = {
    "cat": 0,
    "cats": 0,
    "kitten": 0,
    "feline": 0,
    "car": 1,
    "automobile": 1,
    "vehicle": 1,
    "invoice": 2,
    "billing": 2,
    "payment": 2,
    "garden": 3,
    "tomato": 3,
}


def concept_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in text.lower().replace(".", " ").split():
        index = _CONCEPTS.get(word)
        if index is not None:
            vector[index] += 1.0
    vector[DIMENSIONS - 1] += 0.01  # never all-zero
    return vector


class ConceptEmbedder:
    """An Embedder that can be switched between available and unavailable."""

    def __init__(self, *, available: bool = True, model: str = "concept-embed") -> None:
        self.available = available
        self._model = model
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if not self.available:
            raise EmbeddingUnavailable("embedding model 'concept-embed' is not pulled")
        return [concept_vector(text) for text in texts]

    def status(self) -> EmbedderStatus:
        if self.available:
            return EmbedderStatus(state="ready", model=self._model)
        return EmbedderStatus(
            state="pending_embedding_model",
            model=self._model,
            reason="embedding model 'concept-embed' is not pulled",
        )
