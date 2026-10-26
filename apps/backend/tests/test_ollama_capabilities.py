"""LOCUS-480: Ollama chat eligibility is based on live /api/show evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import local_models


@pytest.fixture(autouse=True)
def reset_capability_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    local_models._CAPABILITY_CACHE.clear()
    monkeypatch.setattr(local_models, "ollama_base_url", lambda: "http://127.0.0.1:11434")


def test_probes_real_capability_names_without_trusting_custom_model_filenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def show(path: str, **kwargs: Any) -> dict[str, Any]:
        assert path == "/api/show"
        assert kwargs["method"] == "POST"
        calls.append(kwargs["payload"])
        return {"capabilities": ["completion", "tools", "vision"]}

    monkeypatch.setattr(local_models, "_request_json", show)
    custom_id = "hf.co/team/custom-finetune:Q4_K_M"
    assert local_models.model_capabilities(custom_id) == ["completion", "tools", "vision"]
    assert local_models.model_capabilities(custom_id) == ["completion", "tools", "vision"]
    assert calls == [{"model": custom_id}]


def test_unknown_or_failed_capability_probe_does_not_claim_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(_path: str, **_kwargs: Any) -> None:
        raise TimeoutError("Ollama show probe timeout")

    monkeypatch.setattr(local_models, "_request_json", unavailable)
    assert local_models.model_capabilities("custom:latest") == []
    assert local_models.model_capabilities("") == []


def test_embedding_only_capability_is_preserved_without_chat_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        local_models, "_request_json",
        lambda _path, **_kwargs: {"capabilities": ["embedding"]},
    )
    assert local_models.model_capabilities("hf.co/my/private-model") == ["embedding"]


def test_invalid_ids_do_not_generate_network_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Invalid ID should not generate a show probe")

    monkeypatch.setattr(local_models, "_request_json", never)
    assert local_models.model_capabilities("a" * 257) == []
    assert local_models.model_capabilities(" ") == []
