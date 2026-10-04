"""LOCUS-358 / D-29: the model client's attestation lookup for P28-listed lineages.

Hosted/API inference of a listed lineage is refused whatever any attestation
says; a local engine (Ollama on loopback) may serve it only with a passing local
model attestation whose weights digest matches the Ollama manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from locus_runtime import model_client as mc
from tests.provenance_support import model_record, write_ollama_manifest, write_record

DIGEST = "f" * 64
LOCAL = "http://127.0.0.1:11434/v1"


@pytest.fixture()
def attested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A principal-signed, eval-passed attestation for qwen2.5-coder:7b and its manifest."""
    prov, store = tmp_path / "prov", tmp_path / "ollama"
    write_record(prov, model_record(name="qwen2.5-coder", tag="7b", digest=DIGEST))
    write_ollama_manifest(store, "qwen2.5-coder", "7b", DIGEST)
    monkeypatch.setenv("LOCUS_PROVENANCE_DIR", str(prov))
    monkeypatch.setenv("OLLAMA_MODELS", str(store))
    return prov


@pytest.mark.parametrize(
    ("model", "lineage"),
    [
        ("qwen2.5-coder:7b", "qwen"),
        ("deepseek-ai/deepseek-v4.1-flash", "deepseek"),
        ("01-ai/yi-large", "01-ai"),
        ("gpt-oss:20b", ""),
    ],
)
def test_listed_model_lineage(model: str, lineage: str) -> None:
    assert mc.listed_model_lineage(model) == lineage
    assert mc.is_provenance_excluded(model) is bool(lineage)


def test_local_listed_model_runs_with_a_passing_attestation(attested: Path) -> None:
    endpoint = mc.resolve_endpoint("ollama", "qwen2.5-coder:7b", base_url=LOCAL)
    assert endpoint.local and endpoint.model == "qwen2.5-coder:7b"
    assert mc.provenance_denial("ollama", "qwen2.5-coder:7b", "http://localhost:11434/v1") == ""


@pytest.mark.parametrize(
    ("provider", "model", "base_url", "api_key"),
    [
        ("nim", "qwen/qwen3-coder", "", "nvapi-test"),
        ("ollama", "qwen2.5-coder:7b", "http://gpu-box.lan:11434/v1", ""),
        ("openai", "qwen2.5-coder:7b", "https://api.example.com/v1", "sk-test-0123456789abcdef"),
    ],
)
def test_hosted_listed_model_is_refused_whatever_the_attestation(
    attested: Path, provider: str, model: str, base_url: str, api_key: str
) -> None:
    with pytest.raises(mc.ModelProviderError) as excinfo:
        mc.resolve_endpoint(provider, model, base_url=base_url, api_key=api_key)
    assert excinfo.value.code == mc.MODEL_CALL_DENIED
    assert "hosted inference" in excinfo.value.reason


def test_local_listed_model_without_attestation_is_refused(attested: Path) -> None:
    with pytest.raises(mc.ModelProviderError) as excinfo:
        mc.resolve_endpoint("ollama", "qwen2.5-coder:14b", base_url=LOCAL)
    assert excinfo.value.code == mc.MODEL_CALL_DENIED
    assert "needs a passing provenance attestation" in excinfo.value.reason


def test_local_listed_model_with_unsigned_or_uneval_attestation_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prov, store = tmp_path / "prov", tmp_path / "ollama"
    write_record(prov, model_record(name="qwen2.5-coder", tag="7b", digest=DIGEST, signed=False))
    write_record(
        prov, model_record(name="deepseek-r1", tag="8b", digest=DIGEST, eval_status="pending")
    )
    write_ollama_manifest(store, "qwen2.5-coder", "7b", DIGEST)
    write_ollama_manifest(store, "deepseek-r1", "8b", DIGEST)
    monkeypatch.setenv("LOCUS_PROVENANCE_DIR", str(prov))
    monkeypatch.setenv("OLLAMA_MODELS", str(store))
    assert "sign-off" in mc.provenance_denial("ollama", "qwen2.5-coder:7b", LOCAL)
    assert "LOCUS-351" in mc.provenance_denial("ollama", "deepseek-r1:8b", LOCAL)


def test_local_listed_model_with_swapped_weights_is_refused(attested: Path, tmp_path: Path) -> None:
    write_ollama_manifest(tmp_path / "ollama", "qwen2.5-coder", "7b", "0" * 64)
    assert "differ from the attested" in mc.provenance_denial("ollama", "qwen2.5-coder:7b", LOCAL)


def test_other_loopback_engines_cannot_use_an_attestation(attested: Path) -> None:
    denial = mc.provenance_denial("openai", "qwen2.5-coder:7b", "http://localhost:8000/v1")
    assert "only Ollama models are attestable" in denial


def test_clean_models_need_no_attestation(attested: Path) -> None:
    assert mc.provenance_denial("ollama", "gpt-oss:20b", LOCAL) == ""
    assert (
        mc.provenance_denial(
            "nim", "nvidia/nemotron-3-ultra-550b-a55b", "https://integrate.api.nvidia.com/v1"
        )
        == ""
    )


def test_attestation_lookup_failure_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    import locus_tooling.provenance.models as models

    def boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(models, "local_model_verdict", boom)
    assert "attestation lookup failed" in mc.provenance_denial("ollama", "qwen2.5-coder:7b", LOCAL)
