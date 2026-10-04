"""LOCUS-352: the framework engine adapters are gone; legacy engine values map to native.

The LangChain, LangGraph, Semantic Kernel and AutoGen chat adapters were removed.
Stored platform settings, agent configs and run inputs may still name those
engines (or a hybrid strategy); they must resolve to the native engine, whose
model calls go through the gated model client, rather than fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_generated_artifacts import ADMIN_HEADERS, AUTH_HEADERS, client, main_module, store

LEGACY_ENGINES = ("langgraph", "langchain", "semantic-kernel", "semantic_kernel", "autogen", "x")


@pytest.mark.parametrize("engine", (*LEGACY_ENGINES, "", None, "native"))
def test_every_engine_value_normalizes_to_native(engine: str | None) -> None:
    assert main_module._normalize_runtime_engine(engine) == "native"
    assert main_module._normalize_runtime_engine_list([engine]) == ["native"]


def test_stored_legacy_platform_settings_resolve_to_native_single() -> None:
    settings = main_module.PlatformSettings(
        default_runtime_engine="autogen",
        allowed_runtime_engines=["langgraph", "semantic-kernel"],
        allow_runtime_engine_override=True,
        enforce_runtime_engine_allowlist=True,
        default_runtime_strategy="hybrid",
        default_hybrid_runtime_routing={"default": "langgraph", "retrieval": "langchain"},
    )
    # Loading never fails and never keeps a removed engine.
    assert settings.default_runtime_engine == "native"
    assert settings.allowed_runtime_engines == ["native"]
    assert settings.default_runtime_strategy == "single"
    assert set(settings.default_hybrid_runtime_routing.values()) == {"native"}
    run_input = {
        "runtime": {
            "engine": "langchain",
            "strategy": "hybrid",
            "hybrid_routing": {"tooling": "semantic-kernel", "collaboration": "autogen"},
        }
    }

    info = main_module._resolve_runtime_engine(run_input, settings)

    assert info["selected_engine"] == info["executed_engine"] == "native"
    assert info["mode"] == "native"
    assert info["strategy"] == "single"
    assert info["allowed_engines"] == ["native"]
    assert info["node_mapping"]
    assert all(handler.startswith("native.") for handler in info["node_mapping"].values())
    for role in main_module._HYBRID_RUNTIME_ROLES:
        node = main_module._resolve_node_runtime_engine(info, role)
        assert node == {"selected_engine": "native", "executed_engine": "native", "mode": "native"}


def test_runtime_info_stored_by_an_older_build_runs_native() -> None:
    stale = {
        "strategy": "hybrid",
        "selected_engine": "langgraph",
        "executed_engine": "langgraph",
        "mode": "delegated",
        "hybrid_effective_routing": {"retrieval": "langchain"},
    }
    node = main_module._resolve_node_runtime_engine(stale, "retrieval")
    assert node == {"selected_engine": "native", "executed_engine": "native", "mode": "native"}


def test_saving_legacy_engine_settings_stores_native() -> None:
    original = store.platform_settings.model_copy(deep=True)
    try:
        response = client.post(
            "/platform/settings",
            headers=ADMIN_HEADERS,
            json={
                "default_runtime_engine": "langgraph",
                "allowed_runtime_engines": ["native", "autogen"],
                "default_runtime_strategy": "hybrid",
                "default_hybrid_runtime_routing": {"default": "semantic-kernel"},
            },
        )
        assert response.status_code == 200, response.text
        saved = store.platform_settings
        assert saved.default_runtime_engine == "native"
        assert saved.allowed_runtime_engines == ["native"]
        assert saved.default_runtime_strategy == "single"
        assert set(saved.default_hybrid_runtime_routing.values()) == {"native"}
    finally:
        store.platform_settings = original


def test_runtime_providers_no_longer_reports_framework_adapters() -> None:
    response = client.get("/runtime/providers", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "framework_adapters" not in response.json()


def test_framework_chat_adapters_are_gone() -> None:
    for name in (
        "_run_langchain_chat",
        "_run_langgraph_chat",
        "_run_semantic_kernel_chat",
        "_run_autogen_chat",
        "_run_framework_chat",
        "_framework_runtime_probe",
    ):
        assert not hasattr(main_module, name), name
