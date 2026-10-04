"""Memory embeddings through the gated model client (LOCUS-378, D-29).

Embeddings used to be fetched with a direct OpenAI client that bypassed the
gateway. :class:`GatedEmbedder` instead resolves a **local** engine (Ollama by
default) and calls :meth:`locus_runtime.model_client.ModelClient.embed`, so every
request is authorized as a gateway ``model_call`` and its usage is audited (counts
only, never text).

D-29: memory is embedded on a local engine only. A provider whose registry entry is
not a local engine (OpenAI, NIM, ...) is refused here, before any request.

Missing embeddings never turn memory off: when the model is not pulled, the engine
is down or policy denies the call, :meth:`GatedEmbedder.embed` raises
:class:`EmbeddingUnavailable`, the store keeps the entry for keyword search, and the
embedder backs off (``retry_seconds`` doubling up to ``max_retry_seconds``) before it
calls the engine again, so a missing model causes no request storm.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence

from locus_runtime import model_client as mc
from locus_runtime.memory.contract import EmbedderStatus, EmbeddingUnavailable

logger = logging.getLogger(__name__)

#: Env: the local embedding model (an Ollama tag). Empty disables semantic search.
EMBEDDING_MODEL_ENV = "LOCUS_MEMORY_EMBEDDING_MODEL"
#: Env: the engine that serves it; must be a local engine (D-29).
EMBEDDING_PROVIDER_ENV = "LOCUS_MEMORY_EMBEDDING_PROVIDER"
#: Default local embedding model: Nomic Embed Text v1.5 (Nomic AI, US; Apache-2.0),
#: 768 dimensions, ~274 MB. Pulled by the desktop launcher next to the chat model.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_PROVIDER = "ollama"

_REASON_MAX = 240

ClientFactory = Callable[[mc.ModelEndpoint], mc.ModelClient]


def configured_embedding_model() -> str:
    """The configured embedding model; an explicitly empty value disables embeddings."""
    raw = os.getenv(EMBEDDING_MODEL_ENV)
    if raw is None:
        return DEFAULT_EMBEDDING_MODEL
    return raw.strip()


def configured_embedding_provider() -> str:
    raw = str(os.getenv(EMBEDDING_PROVIDER_ENV) or "").strip().lower()
    return raw or DEFAULT_EMBEDDING_PROVIDER


def _default_client_factory(timeout: float) -> ClientFactory:
    def factory(endpoint: mc.ModelEndpoint) -> mc.ModelClient:
        return mc.ModelClient(endpoint, timeout=timeout, max_retries=0)

    return factory


class GatedEmbedder:
    """:class:`~locus_runtime.memory.contract.Embedder` over the gated model client."""

    def __init__(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        settings: mc.ProviderSettings | None = None,
        client_factory: ClientFactory | None = None,
        retry_seconds: float = 60.0,
        max_retry_seconds: float = 3600.0,
        timeout: float = 15.0,
        max_batch: int = 32,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._model = configured_embedding_model() if model is None else str(model).strip()
        self._provider = (
            configured_embedding_provider() if provider is None else str(provider).strip().lower()
        )
        self._settings = settings
        self._client_factory = client_factory or _default_client_factory(timeout)
        self._retry_seconds = max(0.0, float(retry_seconds))
        self._max_retry_seconds = max(self._retry_seconds, float(max_retry_seconds))
        self._failures = 0
        self._max_batch = max(1, int(max_batch))
        self._clock = clock
        self._lock = threading.Lock()
        self._client: mc.ModelClient | None = None
        self._client_key: tuple[str, str] = ("", "")
        self._ready = False
        self._reason = "no embedding has been requested yet"
        self._retry_at = 0.0

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider

    def status(self) -> EmbedderStatus:
        if not self._model:
            return EmbedderStatus(
                state="disabled",
                model="",
                reason=f"no embedding model configured ({EMBEDDING_MODEL_ENV} is empty)",
            )
        with self._lock:
            if self._ready:
                return EmbedderStatus(state="ready", model=self._model)
            return EmbedderStatus(
                state="pending_embedding_model", model=self._model, reason=self._reason
            )

    def _resolve_client(self) -> mc.ModelClient:
        spec = mc.PROVIDERS.get(self._provider)
        if spec is None or not spec.local:
            raise EmbeddingUnavailable(
                f"memory embeddings run on a local engine only (D-29); "
                f"'{self._provider}' is not a local engine"
            )
        endpoint = mc.resolve_endpoint(self._provider, self._model, settings=self._settings)
        key = (endpoint.base_url, endpoint.model)
        if self._client is None or self._client_key != key:
            self._client = self._client_factory(endpoint)
            self._client_key = key
        return self._client

    def _fail(self, reason: str) -> EmbeddingUnavailable:
        text = mc.redact_reason(reason)[:_REASON_MAX]
        with self._lock:
            changed = self._ready or self._reason != text
            self._ready = False
            self._reason = text
            # Exponential back-off: a missing model is retried ever less often.
            delay = min(self._max_retry_seconds, self._retry_seconds * (2**self._failures))
            self._failures = min(self._failures + 1, 16)
            self._retry_at = self._clock() + delay
        if changed:
            # Reason only: never the text being embedded.
            logger.warning("memory.embeddings_unavailable model=%s reason=%s", self._model, text)
        return EmbeddingUnavailable(text)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = [str(text) for text in texts]
        if not inputs:
            return []
        if not self._model:
            raise EmbeddingUnavailable("no embedding model configured")
        with self._lock:
            if not self._ready and self._clock() < self._retry_at:
                raise EmbeddingUnavailable(self._reason)
        try:
            client = self._resolve_client()
            vectors: list[list[float]] = []
            for start in range(0, len(inputs), self._max_batch):
                vectors.extend(client.embed(inputs[start : start + self._max_batch]))
        except EmbeddingUnavailable as exc:
            raise self._fail(str(exc)) from exc
        except mc.ModelCallDenied as exc:
            raise self._fail(f"embedding call denied by policy: {exc.reason}") from exc
        except mc.ModelProviderError as exc:
            raise self._fail(
                f"embedding model '{self._model}' is not available on the local engine "
                f"({exc.code}): {exc.reason}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - never let embeddings break memory
            raise self._fail(f"embedding failed ({type(exc).__name__})") from exc
        if len(vectors) != len(inputs):
            raise self._fail("embedding response did not match the inputs")
        with self._lock:
            if not self._ready:
                logger.info("memory.embeddings_ready model=%s", self._model)
            self._ready = True
            self._reason = ""
            self._retry_at = 0.0
            self._failures = 0
        return vectors
