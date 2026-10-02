"""Local open-weight model management via Ollama.

Ollama is the integration point deliberately: it already solves download,
checksumming, quantization selection, GPU/CPU fallback, and serves an
OpenAI-compatible API — the platform passes through rather than reimplementing
any of that. Only models on the curated catalog below may be pulled
(allowlist; arbitrary registry pulls are rejected).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

# Curated pick list, ordered smallest→largest. Sizes are approximate download
# sizes for the default quantization; min_ram_gb is a practical host-memory guide
# for CPU inference. Tags are exact Ollama registry references (this list is an
# allowlist — every id must be a real `ollama pull` target). Catalog reviewed
# against the Ollama library in July 2026.
#
# NOTE on MoE entries (Qwen3 30B/235B, Qwen3-Coder, Llama 4): all weights must be
# resident in RAM/VRAM, so min_ram_gb tracks the full download size — but only a
# few billion parameters are active per token, so inference is far faster than a
# dense model of the same footprint.
LOCAL_MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "id": "gemma3:270m",
        "label": "Gemma 3 270M (ultra-tiny)",
        "family": "Google Gemma",
        "size_gb": 0.3,
        "min_ram_gb": 2,
        "notes": "Smallest in the catalog; smoke tests, embedded, draft/speculative decoding.",
    },
    {
        "id": "qwen2.5:0.5b",
        "label": "Qwen 2.5 0.5B (tiny)",
        "family": "Qwen",
        "size_gb": 0.4,
        "min_ram_gb": 4,
        "notes": "Smallest footprint; smoke tests and constrained hosts.",
    },
    {
        "id": "gemma3:1b",
        "label": "Gemma 3 1B",
        "family": "Google Gemma",
        "size_gb": 0.8,
        "min_ram_gb": 4,
        "notes": "Tiny, fast; 32K context. Current-gen replacement for tiny Gemma 2.",
    },
    {
        "id": "llama3.2:1b",
        "label": "Llama 3.2 1B",
        "family": "Meta Llama",
        "size_gb": 1.3,
        "min_ram_gb": 4,
        "notes": "Very fast on CPU; light assistant tasks.",
    },
    {
        "id": "qwen3:1.7b",
        "label": "Qwen 3 1.7B",
        "family": "Qwen",
        "size_gb": 1.4,
        "min_ram_gb": 4,
        "notes": "Latest-gen tiny with a toggleable thinking mode.",
    },
    {
        "id": "llama3.2:3b",
        "label": "Llama 3.2 3B",
        "family": "Meta Llama",
        "size_gb": 2.0,
        "min_ram_gb": 8,
        "notes": "Good default for local-first assistants.",
    },
    {
        "id": "qwen3:4b",
        "label": "Qwen 3 4B",
        "family": "Qwen",
        "size_gb": 2.5,
        "min_ram_gb": 8,
        "notes": "Strong small generalist with toggleable reasoning; recommended local-first default.",
    },
    {
        "id": "gemma3:4b",
        "label": "Gemma 3 4B (multimodal)",
        "family": "Google Gemma",
        "size_gb": 3.3,
        "min_ram_gb": 8,
        "notes": "Vision-capable; 128K context. Good small multimodal option.",
    },
    {
        "id": "mistral:7b",
        "label": "Mistral 7B",
        "family": "Mistral",
        "size_gb": 4.1,
        "min_ram_gb": 16,
        "notes": "Efficient generalist.",
    },
    {
        "id": "qwen2.5:7b",
        "label": "Qwen 2.5 7B",
        "family": "Qwen",
        "size_gb": 4.7,
        "min_ram_gb": 16,
        "notes": "Strong multilingual generalist (prior gen; see Qwen 3 8B).",
    },
    {
        "id": "qwen2.5-coder:7b",
        "label": "Qwen 2.5 Coder 7B",
        "family": "Qwen",
        "size_gb": 4.7,
        "min_ram_gb": 16,
        "notes": "Compact code-focused model; light coding on 16 GB hosts.",
    },
    {
        "id": "llama3.1:8b",
        "label": "Llama 3.1 8B",
        "family": "Meta Llama",
        "size_gb": 4.9,
        "min_ram_gb": 16,
        "notes": "Strong general model; 16 GB+ host or GPU recommended.",
    },
    {
        "id": "qwen3:8b",
        "label": "Qwen 3 8B",
        "family": "Qwen",
        "size_gb": 5.2,
        "min_ram_gb": 16,
        "notes": "Current-gen 8B generalist with hybrid thinking; strong quality for size.",
    },
    {
        "id": "deepseek-r1:8b",
        "label": "DeepSeek R1 8B",
        "family": "DeepSeek",
        "size_gb": 5.2,
        "min_ram_gb": 16,
        "notes": "Reasoning-tuned distillation (R1-0528 revision).",
    },
    {
        "id": "gemma2:9b",
        "label": "Gemma 2 9B",
        "family": "Google Gemma",
        "size_gb": 5.4,
        "min_ram_gb": 16,
        "notes": "Prior-gen; strong quality for size (see Gemma 3 12B).",
    },
    {
        "id": "gemma3:12b",
        "label": "Gemma 3 12B (multimodal)",
        "family": "Google Gemma",
        "size_gb": 8.1,
        "min_ram_gb": 16,
        "notes": "Vision-capable; 128K context. Strong mid-size generalist.",
    },
    {
        "id": "deepseek-r1:14b",
        "label": "DeepSeek R1 14B",
        "family": "DeepSeek",
        "size_gb": 9.0,
        "min_ram_gb": 24,
        "notes": "Larger reasoning distillation; stronger math/logic than 8B.",
    },
    {
        "id": "qwen3:14b",
        "label": "Qwen 3 14B",
        "family": "Qwen",
        "size_gb": 9.3,
        "min_ram_gb": 24,
        "notes": "Dense mid-size generalist with reasoning mode.",
    },
    {
        "id": "gpt-oss:20b",
        "label": "GPT-OSS 20B",
        "family": "OpenAI GPT-OSS",
        "size_gb": 13.0,
        "min_ram_gb": 16,
        "notes": "OpenAI open-weight reasoning model (MXFP4); fits 16 GB hosts or a single consumer GPU.",
    },
    {
        "id": "gemma3:27b",
        "label": "Gemma 3 27B (multimodal)",
        "family": "Google Gemma",
        "size_gb": 17.0,
        "min_ram_gb": 32,
        "notes": "Vision-capable flagship Gemma; 128K context. 24 GB+ GPU or large host.",
    },
    {
        "id": "qwen3:30b",
        "label": "Qwen 3 30B-A3B (MoE)",
        "family": "Qwen",
        "size_gb": 19.0,
        "min_ram_gb": 32,
        "notes": "MoE: ~3B active params — near-14B speed at higher quality. Efficient all-rounder.",
    },
    {
        "id": "qwen3-coder:30b",
        "label": "Qwen 3 Coder 30B-A3B (MoE)",
        "family": "Qwen",
        "size_gb": 19.0,
        "min_ram_gb": 32,
        "notes": "Code-specialized MoE (~3.3B active); 256K context. Current-gen coding default.",
    },
    {
        "id": "qwen3:32b",
        "label": "Qwen 3 32B",
        "family": "Qwen",
        "size_gb": 20.0,
        "min_ram_gb": 32,
        "notes": "Dense flagship-class generalist; strongest Qwen 3 that fits a single 24-32 GB GPU.",
    },
    {
        "id": "deepseek-r1:32b",
        "label": "DeepSeek R1 32B",
        "family": "DeepSeek",
        "size_gb": 20.0,
        "min_ram_gb": 32,
        "notes": "Large reasoning distillation; strong on math/programming benchmarks.",
    },
    {
        "id": "deepseek-r1:70b",
        "label": "DeepSeek R1 70B",
        "family": "DeepSeek",
        "size_gb": 43.0,
        "min_ram_gb": 64,
        "notes": "70B reasoning distillation; multi-GPU or 64 GB+ host.",
    },
    {
        "id": "gpt-oss:120b",
        "label": "GPT-OSS 120B",
        "family": "OpenAI GPT-OSS",
        "size_gb": 65.0,
        "min_ram_gb": 80,
        "notes": "OpenAI open-weight flagship; needs ~80 GB (datacenter GPU or very large host).",
    },
    {
        "id": "llama4:scout",
        "label": "Llama 4 Scout (MoE, multimodal)",
        "family": "Meta Llama",
        "size_gb": 67.0,
        "min_ram_gb": 80,
        "notes": "16x17B MoE (109B total / 17B active); multimodal, very long context. Datacenter GPU or large host.",
    },
    {
        "id": "qwen3:235b",
        "label": "Qwen 3 235B-A22B (MoE)",
        "family": "Qwen",
        "size_gb": 142.0,
        "min_ram_gb": 160,
        "notes": "Flagship MoE (22B active); frontier open-weight quality. Multi-GPU / very large host only.",
    },
]

_CATALOG_IDS = {str(item["id"]) for item in LOCAL_MODEL_CATALOG}

# Pull state per model id. Mutated only under _PULL_LOCK.
_PULLS: dict[str, dict[str, Any]] = {}
_PULL_LOCK = threading.Lock()


# Runtime override (set from platform settings); takes precedence over env.
_BASE_URL_OVERRIDE = ""


def set_base_url_override(value: str) -> None:
    global _BASE_URL_OVERRIDE  # noqa: PLW0603
    _BASE_URL_OVERRIDE = str(value or "").strip().rstrip("/")


def ollama_base_url() -> str:
    if _BASE_URL_OVERRIDE:
        return _BASE_URL_OVERRIDE
    return str(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).strip().rstrip("/")


def ollama_openai_base_url() -> str:
    return f"{ollama_base_url()}/v1"


def _request_json(
    path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 5.0
) -> Any:
    request = urllib.request.Request(
        f"{ollama_base_url()}{path}",
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured internal endpoint
        return json.loads(response.read().decode("utf-8", errors="replace") or "{}")


def ollama_available() -> bool:
    try:
        _request_json("/api/version", timeout=3.0)
        return True
    except Exception:  # noqa: BLE001
        return False


def installed_models() -> list[dict[str, Any]]:
    try:
        payload = _request_json("/api/tags", timeout=5.0)
    except Exception:  # noqa: BLE001
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    results: list[dict[str, Any]] = []
    for item in models or []:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "id": str(item.get("name") or item.get("model") or ""),
                "size_bytes": int(item.get("size") or 0),
                "modified_at": str(item.get("modified_at") or ""),
            }
        )
    return [item for item in results if item["id"]]


def is_catalog_model(model_id: str) -> bool:
    return str(model_id or "").strip() in _CATALOG_IDS


def pull_states() -> dict[str, dict[str, Any]]:
    with _PULL_LOCK:
        return {model_id: dict(state) for model_id, state in _PULLS.items()}


def _run_pull(model_id: str) -> None:
    request = urllib.request.Request(
        f"{ollama_base_url()}/api/pull",
        data=json.dumps({"model": model_id, "stream": True}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        # Model downloads are multi-GB; the stream emits JSON progress lines.
        with urllib.request.urlopen(request, timeout=3600) as response:  # noqa: S310
            for raw_line in response:
                try:
                    line = json.loads(raw_line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(line, dict):
                    continue
                error = str(line.get("error") or "")
                if error:
                    raise RuntimeError(error)
                total = int(line.get("total") or 0)
                completed = int(line.get("completed") or 0)
                with _PULL_LOCK:
                    state = _PULLS.setdefault(model_id, {})
                    state["status"] = "downloading"
                    state["detail"] = str(line.get("status") or "")
                    if total > 0:
                        state["progress_percent"] = round(min(100.0, completed * 100.0 / total), 1)
        with _PULL_LOCK:
            _PULLS[model_id] = {
                "status": "ready",
                "detail": "Model installed.",
                "progress_percent": 100.0,
                "finished_at": time.time(),
            }
    except Exception as exc:  # noqa: BLE001
        with _PULL_LOCK:
            _PULLS[model_id] = {
                "status": "error",
                "detail": str(exc)[:300],
                "progress_percent": _PULLS.get(model_id, {}).get("progress_percent", 0.0),
                "finished_at": time.time(),
            }


def start_pull(model_id: str) -> dict[str, Any]:
    """Begin downloading a catalog model. Returns the initial pull state.

    Raises ValueError for non-catalog models (allowlist) and RuntimeError when
    the Ollama runtime is unreachable.
    """
    normalized = str(model_id or "").strip()
    if not is_catalog_model(normalized):
        raise ValueError("Model is not on the approved local-model catalog")
    if not ollama_available():
        raise RuntimeError("Local model runtime (Ollama) is not reachable")
    with _PULL_LOCK:
        current = _PULLS.get(normalized)
        if current and current.get("status") == "downloading":
            return dict(current)
        _PULLS[normalized] = {
            "status": "downloading",
            "detail": "Starting download...",
            "progress_percent": 0.0,
            "started_at": time.time(),
        }
        state = dict(_PULLS[normalized])
    worker = threading.Thread(
        target=_run_pull, args=(normalized,), name=f"ollama-pull-{normalized}", daemon=True
    )
    worker.start()
    return state


def delete_model(model_id: str) -> bool:
    normalized = str(model_id or "").strip()
    if not is_catalog_model(normalized):
        raise ValueError("Model is not on the approved local-model catalog")
    try:
        request = urllib.request.Request(
            f"{ollama_base_url()}/api/delete",
            data=json.dumps({"model": normalized}).encode("utf-8"),
            method="DELETE",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30):  # noqa: S310
            pass
        with _PULL_LOCK:
            _PULLS.pop(normalized, None)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise
