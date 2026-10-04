"""LOCUS-351: the variant archive and the metering proxy (the trusted model endpoint)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from locus_runtime.rsi.metering import MeteringProxy, usage_from_body
from locus_runtime.rsi.scorecard import (
    SampleRecord,
    Scorecard,
    TamperCheck,
    build_scorecard,
    compare,
)
from locus_runtime.rsi.variants import VariantArchive, variant_tag

OK = TamperCheck(verified_before=True, verified_after=True)


def _card(
    sha: str,
    branch: str = "main",
    *,
    passes: int = 1,
    digest: str = "h1",
    tamper: TamperCheck = OK,
    model: str = "ollama/m",
) -> Scorecard:
    records = [
        SampleRecord.model_validate(
            {"task_id": f"t{i}", "split": "heldout", "status": "pass" if i < passes else "fail"}
        )
        for i in range(4)
    ]
    return build_scorecard(
        records,
        tamper=tamper,
        meta={
            "git_sha": sha,
            "branch": branch,
            "model": model,
            "split_digests": {"heldout": digest},
        },
    )


# --------------------------------------------------------------------------- #
# Variant archive
# --------------------------------------------------------------------------- #
def test_archive_records_variants_and_finds_the_base_branch_baseline(tmp_path: Path) -> None:
    archive = VariantArchive(tmp_path)
    t0 = datetime(2026, 10, 4, tzinfo=UTC)
    archive.record(_card("a" * 40, passes=1), now=t0, source="cli")
    newer_main = _card("b" * 40, passes=2)
    archive.record(newer_main, now=t0 + timedelta(minutes=1))
    candidate = _card("c" * 40, branch="loop/loc-1-x", passes=4)
    path = archive.record(
        candidate, comparison=compare(newer_main, candidate), now=t0 + timedelta(minutes=2)
    )
    assert path.parent == tmp_path / "variants" and path.name.endswith("-cccccccccccc.json")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["git_sha"] == "c" * 40 and saved["comparison"]["decision"] in {"hold", "promote"}
    assert [e["git_sha"][0] for e in archive.entries()] == ["a", "b", "c"]
    baseline = archive.baseline("main")
    assert baseline is not None and baseline.git_sha == "b" * 40
    assert archive.baseline("main", heldout_digest="other") is None
    assert archive.baseline("main", model="ollama/other") is None
    loaded = archive.load(path.name)
    assert loaded is not None and loaded[0] == candidate


def test_archive_never_uses_a_tampered_or_incomplete_baseline(tmp_path: Path) -> None:
    archive = VariantArchive(tmp_path)
    archive.record(_card("a" * 40))
    archive.record(_card("b" * 40, tamper=TamperCheck(verified_before=True)))
    baseline = archive.baseline("main")
    assert baseline is not None and baseline.git_sha == "a" * 40


def test_archive_refuses_a_variant_without_a_sha_and_unsafe_names(tmp_path: Path) -> None:
    archive = VariantArchive(tmp_path)
    with pytest.raises(ValueError):
        archive.record(_card(""))
    with pytest.raises(ValueError):
        archive.record(_card("not-a-sha"))
    assert archive.load("../escape.json") is None
    assert archive.baseline() is None


def test_variant_tag_names() -> None:
    assert variant_tag("ABCDEF0123456789" + "0" * 24) == "variant/abcdef012345"
    with pytest.raises(ValueError):
        variant_tag("main; rm -rf /")


# --------------------------------------------------------------------------- #
# Metering proxy
# --------------------------------------------------------------------------- #
def test_usage_parsing_json_and_sse() -> None:
    body = json.dumps({"usage": {"prompt_tokens": 120, "completion_tokens": 30}}).encode()
    assert usage_from_body(body, "application/json") == (120, 30)
    sse = (
        b'data: {"choices": []}\n\n'
        b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 1}}\n\n'
        b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 7}}\n\n'
        b"data: [DONE]\n\n"
    )
    assert usage_from_body(sse, "text/event-stream") == (10, 7)
    assert usage_from_body(b"not json", "application/json") == (0, 0)


def _upstream(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        if b"boom" in request.content:
            return httpx.Response(500, json={"error": "upstream"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 5},
            },
        )

    return httpx.MockTransport(handler)


def test_proxy_meters_tokens_watches_canaries_and_limits_egress() -> None:
    seen: list[httpx.Request] = []
    proxy = MeteringProxy(
        "http://127.0.0.1:11434/v1",
        expected_model="m",
        canaries=["CANARY_1"],
        transport=_upstream(seen),
    )
    base = proxy.start()
    try:
        assert base.startswith("http://127.0.0.1:") and base.endswith("/v1")
        with httpx.Client(trust_env=False) as client:
            ok = client.post(f"{base}/chat/completions", json={"model": "m", "messages": []})
            assert ok.status_code == 200 and ok.json()["usage"]["prompt_tokens"] == 100
            leak = client.post(
                f"{base}/chat/completions",
                json={"model": "m", "messages": [{"role": "tool", "content": "token CANARY_1"}]},
            )
            assert leak.status_code == 200
            failed = client.post(
                f"{base}/chat/completions", json={"model": "m", "messages": ["boom"]}
            )
            assert failed.status_code == 500
            wrong_model = client.post(f"{base}/chat/completions", json={"model": "other"})
            assert wrong_model.status_code == 403
            wrong_path = client.post(f"{base}/../admin", json={})
            assert wrong_path.status_code == 403
            other_path = client.get(base.replace("/v1", "") + "/api/tags")
            assert other_path.status_code == 403
            models = client.get(f"{base}/models")
            assert models.status_code == 200
        snap = proxy.take()
        assert snap.requests == 3 and snap.completions == 2
        assert (snap.prompt_tokens, snap.completion_tokens, snap.tokens) == (200, 10, 210)
        assert snap.canary_hits == ["CANARY_1"] and snap.models == ["m"]
        assert snap.refused == 3
        assert proxy.take().requests == 0  # take() resets
        # Only the one upstream, never the caller's Authorization header.
        assert {r.url.host for r in seen} == {"127.0.0.1"}
        assert all("authorization" not in r.headers for r in seen)
    finally:
        proxy.close()


def test_proxy_reports_an_unreachable_upstream() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with MeteringProxy("http://127.0.0.1:9/v1", transport=httpx.MockTransport(down)) as proxy:
        with httpx.Client(trust_env=False) as client:
            response = client.post(f"{proxy.origin}/v1/chat/completions", json={"model": "m"})
        assert response.status_code == 502
        snap = proxy.take()
        assert snap.upstream_errors == 1 and snap.completions == 0


def test_proxy_rejects_non_http_upstreams() -> None:
    with pytest.raises(ValueError):
        MeteringProxy("file:///etc/passwd")
