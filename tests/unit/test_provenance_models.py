"""LOCUS-358 / D-29: weights-only format check, eval hook, local-model verdict."""

from __future__ import annotations

import json
import pickle
import struct
import zipfile
from pathlib import Path
from typing import Any

import pytest

from locus_tooling.provenance import models, records
from locus_tooling.provenance.inspection import inspect_local_model
from tests.provenance_support import model_record, write_ollama_manifest, write_record

DIGEST = "c" * 64


def _safetensors(path: Path) -> Path:
    header = json.dumps({"w": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + b"\x00\x00\x80\x3f")
    return path


def _gguf(path: Path) -> Path:
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32)
    return path


def test_safetensors_and_gguf_pass(tmp_path: Path) -> None:
    _safetensors(tmp_path / "model.safetensors")
    _gguf(tmp_path / "model.gguf")
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "llama"}), encoding="utf-8")
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("Apache-2.0", encoding="utf-8")
    check = models.check_model_dir(tmp_path)
    assert check.status == "pass", check.refused
    assert {(w["path"], w["format"]) for w in check.weights} == {
        ("model.safetensors", "safetensors"),
        ("model.gguf", "gguf"),
    }


@pytest.mark.parametrize("name", ["pytorch_model.bin", "model.pt", "model.ckpt", "weights.pkl"])
def test_pickle_formats_are_refused(tmp_path: Path, name: str) -> None:
    _safetensors(tmp_path / "ok.safetensors")
    (tmp_path / name).write_bytes(pickle.dumps({"w": [1.0]}))
    check = models.check_model_dir(tmp_path)
    assert check.status == "fail"
    assert [r["path"] for r in check.refused] == [name]


def test_pickle_disguised_as_safetensors_is_refused(tmp_path: Path) -> None:
    (tmp_path / "model.safetensors").write_bytes(pickle.dumps([1, 2, 3]))
    check = models.check_model_dir(tmp_path)
    assert check.status == "fail" and "pickle" in check.refused[0]["reason"]


def test_torch_zip_under_a_metadata_name_is_refused(tmp_path: Path) -> None:
    _gguf(tmp_path / "model.gguf")
    with zipfile.ZipFile(tmp_path / "extra.json", "w") as archive:
        archive.writestr("archive/data.pkl", pickle.dumps(1))
    assert models.check_model_dir(tmp_path).status == "fail"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("modeling_custom.py", "class Model: pass\n"),
        ("config.json", json.dumps({"auto_map": {"AutoModel": "modeling_custom.Model"}})),
        ("tokenizer_config.json", json.dumps({"trust_remote_code": True})),
    ],
)
def test_custom_loader_code_is_refused(tmp_path: Path, name: str, content: str) -> None:
    _safetensors(tmp_path / "model.safetensors")
    (tmp_path / name).write_text(content, encoding="utf-8")
    check = models.check_model_dir(tmp_path)
    assert check.status == "fail"
    assert check.refused[0]["path"] == name


def test_no_weights_at_all_fails(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert models.check_model_dir(tmp_path).status == "fail"


def test_behavioural_eval_is_pending_until_locus_351_installs_a_suite() -> None:
    assert models.run_behavioural_eval("ollama/x@1")["status"] == "pending"
    models.install_behavioural_eval(lambda ref: {"status": "pass", "suite": f"test:{ref}"})
    try:
        assert models.run_behavioural_eval("ollama/x@1") == {
            "status": "pass",
            "suite": "test:ollama/x@1",
        }
    finally:
        models.install_behavioural_eval(None)
    assert models.run_behavioural_eval("ollama/x@1")["status"] == "pending"


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (
            "qwen2.5-coder:7b",
            ("registry.ollama.ai", "library", "qwen2.5-coder", "7b", "qwen2.5-coder"),
        ),
        ("qwen3", ("registry.ollama.ai", "library", "qwen3", "latest", "qwen3")),
        (
            "someone/deepseek-r1:8b",
            ("registry.ollama.ai", "someone", "deepseek-r1", "8b", "someone/deepseek-r1"),
        ),
    ],
)
def test_parse_ollama_refs(ref: str, expected: tuple[str, ...]) -> None:
    parsed = models.parse_ollama_ref(ref)
    assert parsed is not None
    assert (
        parsed.registry,
        parsed.namespace,
        parsed.model,
        parsed.tag,
        parsed.attestation_name,
    ) == expected


@pytest.mark.parametrize("ref", ["", "../../etc:passwd", "a/b/c/d:1", "bad name:1"])
def test_bad_ollama_refs_are_rejected(ref: str) -> None:
    assert models.parse_ollama_ref(ref) is None


def _setup(tmp_path: Path, *, digest: str = DIGEST, **record_kwargs: Any) -> tuple[Path, Path]:
    prov, store = tmp_path / "prov", tmp_path / "ollama"
    write_record(prov, model_record(digest=DIGEST, **record_kwargs))
    write_ollama_manifest(store, "qwen2.5-coder", "7b", digest)
    return prov, store


def test_local_model_verdict_passes_with_a_signed_record_and_matching_weights(
    tmp_path: Path,
) -> None:
    prov, store = _setup(tmp_path)
    verdict = models.local_model_verdict(
        "ollama", "qwen2.5-coder:7b", roots=[prov], models_dir=store
    )
    assert verdict.passing, verdict.reason


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"signed": False}, "sign-off pending"),
        ({"eval_status": "pending"}, "LOCUS-351"),
        ({"digest": "d" * 64}, "differ from the attested"),
    ],
)
def test_local_model_verdict_refuses(tmp_path: Path, kwargs: dict[str, Any], reason: str) -> None:
    prov, store = _setup(tmp_path, **kwargs)
    verdict = models.local_model_verdict(
        "ollama", "qwen2.5-coder:7b", roots=[prov], models_dir=store
    )
    assert not verdict.passing and reason in verdict.reason


def test_local_model_verdict_refuses_missing_manifest_other_tag_and_other_engines(
    tmp_path: Path,
) -> None:
    prov, store = _setup(tmp_path)
    no_manifest = models.local_model_verdict(
        "ollama", "qwen2.5-coder:7b", roots=[prov], models_dir=tmp_path / "none"
    )
    assert not no_manifest.passing and "manifest" in no_manifest.reason
    other_tag = models.local_model_verdict(
        "ollama", "qwen2.5-coder:14b", roots=[prov], models_dir=store
    )
    assert not other_tag.passing and "no attestation" in other_tag.reason
    engine = models.local_model_verdict(
        "openai", "qwen2.5-coder:7b", roots=[prov], models_dir=store
    )
    assert not engine.passing and "only Ollama" in engine.reason


def test_inspect_local_model_drafts_a_valid_unsigned_record(tmp_path: Path) -> None:
    store = tmp_path / "ollama"
    blob_digest = "e" * 64
    write_ollama_manifest(store, "qwen2.5-coder", "7b", blob_digest)
    blob = models.ollama_blob_path(blob_digest, store)
    blob.parent.mkdir(parents=True)
    _gguf(blob)
    research = {
        "origin": {
            "p28_status": "listed",
            "countries": ["CN"],
            "summary": "Qwen (Alibaba Cloud)",
            "confidence": "high",
            "evidence": [
                {"claim": "publisher", "url": "https://example.test/", "retrieved": "2026-10-04"}
            ],
        },
        "maintainers": [{"name": "Qwen team", "role": "publisher"}],
        "funding": {"summary": "corporate", "sources": []},
    }
    record, path = inspect_local_model(
        research,
        provenance_root=tmp_path / "prov",
        lineage="qwen",
        ollama="qwen2.5-coder:7b",
        models_dir=store,
    )
    assert path.name == "qwen2.5-coder@7b.json" and path.with_suffix(".md").is_file()
    assert records.schema_errors(record) == []
    assert record["model"]["format_check"]["status"] == "pass"
    real_digest = record["model"]["format_check"]["weights"][0]["sha256"]
    assert record["artifacts"][0]["sha256"] == real_digest
    verdict = records.evaluate(record)
    assert not verdict.passing
    assert "sign-off pending" in verdict.reason and "LOCUS-351" in verdict.reason
