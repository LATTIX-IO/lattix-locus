"""LOCUS-328: PolicyEngine fail-closed behaviour and configuration (no OPA needed)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from locus_runtime import policy_engine as pe
from locus_runtime.policy_engine import (
    KNOWN_POLICIES,
    OpaSidecarEngine,
    PolicyEngine,
    PolicyEngineConfigError,
    build_policy_engine,
    compute_policy_bundle_hash,
    validate_loopback_url,
)
from locus_runtime.security import path_within_allowed_roots

LOOPBACK = "http://127.0.0.1:8181"


def _engine(handler, **kwargs) -> OpaSidecarEngine:
    return OpaSidecarEngine(
        base_url=LOOPBACK,
        transport=httpx.MockTransport(handler),
        policy_version="sha256:test",
        **kwargs,
    )


def _raise(exc: Exception):
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _respond(status: int = 200, body: object | str = None):
    def handler(request: httpx.Request) -> httpx.Response:
        content = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, content=content)

    return handler


# --- fail closed --------------------------------------------------------------


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (_raise(httpx.ConnectError("refused")), pe.REASON_UNAVAILABLE),
        (_raise(httpx.ReadTimeout("slow")), pe.REASON_TIMEOUT),
        (_raise(httpx.ConnectTimeout("slow")), pe.REASON_TIMEOUT),
        (_raise(RuntimeError("boom")), pe.REASON_ERROR),
        (_respond(500, {"code": "internal_error"}), pe.REASON_HTTP_ERROR),
        (_respond(400, {"code": "invalid_parameter"}), pe.REASON_HTTP_ERROR),
        (_respond(200, "not json{"), pe.REASON_MALFORMED),
        (_respond(200, ["result"]), pe.REASON_MALFORMED),
        (_respond(200, {"result": True}), pe.REASON_MALFORMED),
        (_respond(200, {"result": {"allow": "true"}}), pe.REASON_MALFORMED),
        (_respond(200, {"result": {"allow": 1}}), pe.REASON_MALFORMED),
        (_respond(200, {}), pe.REASON_UNDEFINED),
    ],
)
def test_engine_failures_deny(handler, reason: str) -> None:
    decision = _engine(handler).decide("agent_policy", {"tool": "execute_step"})
    assert decision.allow is False
    assert decision.reasons[0] == reason
    assert decision.backend == "opa-sidecar"
    assert decision.policy_version == "sha256:test"


def test_http_error_reason_carries_status() -> None:
    decision = _engine(_respond(503, {})).decide("network_policy", {})
    assert decision.reasons == [pe.REASON_HTTP_ERROR, "status:503"]


def test_unknown_policy_denies_without_calling_engine() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"result": {"allow": True}})

    engine = _engine(handler)
    for name in ("not_a_policy", "../system", "agent_policy/../x", ""):
        decision = engine.decide(name, {})
        assert decision.allow is False
        assert decision.reasons == [pe.REASON_UNKNOWN_POLICY]
    assert calls == []


@pytest.mark.parametrize("bad_input", [{"x": object()}, {"x": float("nan")}, ["list"]])
def test_invalid_input_denies(bad_input) -> None:
    decision = _engine(_respond(200, {"result": {"allow": True}})).decide("agent_policy", bad_input)
    assert decision.allow is False
    assert decision.reasons == [pe.REASON_INVALID_INPUT]


def test_managed_engine_not_started_is_unavailable() -> None:
    engine = OpaSidecarEngine(
        transport=httpx.MockTransport(_respond(200, {"result": {"allow": True}}))
    )
    decision = engine.decide("network_policy", {"source": "orchestrator", "target": "opa"})
    assert decision.allow is False
    assert decision.reasons == [pe.REASON_UNAVAILABLE]


def test_managed_start_without_binary_raises_and_still_denies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pe, "find_opa_binary", lambda: None)
    engine = OpaSidecarEngine()
    with pytest.raises(RuntimeError, match="OPA binary not found"):
        engine.start()
    assert engine.decide("network_policy", {}).reasons == [pe.REASON_UNAVAILABLE]


def test_configured_opa_bin_that_does_not_exist_is_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCUS_OPA_BIN", str(tmp_path / "missing-opa"))
    assert pe.find_opa_binary() is None


# --- successful evaluation ------------------------------------------------------


def test_allow_requires_boolean_true_and_posts_input_to_package_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {"allow": True, "operation": "execute_step"}})

    decision = _engine(handler).decide("agent_policy", {"tool": "execute_step"})
    assert decision.allow is True
    assert decision.reasons == ["agent_policy.allow"]
    assert decision.outputs == {"operation": "execute_step"}
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/v1/data/lattix/agent_policy"
    assert json.loads(seen[0].content) == {"input": {"tool": "execute_step"}}


def test_deny_and_classifier_results() -> None:
    denied = _engine(_respond(200, {"result": {"allow": False, "deny": True}})).decide(
        "agent_policy", {}
    )
    assert (denied.allow, denied.reasons) == (False, ["agent_policy.deny"])

    not_allowed = _engine(_respond(200, {"result": {"allow": False}})).decide("tool_jail", {})
    assert (not_allowed.allow, not_allowed.reasons) == (False, ["tool_jail.not_allowed"])

    # data_classification has no allow rule: it informs, it never grants.
    classified = _engine(_respond(200, {"result": {"classification": "restricted"}})).decide(
        "data_classification", {"text": "ssn"}
    )
    assert classified.allow is False
    assert classified.reasons == ["data_classification.no_allow_rule"]
    assert classified.outputs["classification"] == "restricted"


def test_engine_satisfies_protocol_and_ignores_proxy_env() -> None:
    engine = _engine(_respond(200, {"result": {"allow": True}}))
    assert isinstance(engine, PolicyEngine)
    assert engine._client.trust_env is False


# --- loopback-only sidecar URL ----------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:8181",
        "http://0.0.0.0:8181",
        "http://opa.internal:8181",
        "https://example.com",
        "http://127.0.0.1.nip.io:8181",
        "http://user:pw@127.0.0.1:8181",
        "http://127.0.0.1:8181/v1/data",
        "http://127.0.0.1:8181?x=1",
        "ftp://127.0.0.1:8181",
        "127.0.0.1:8181",
        "",
    ],
)
def test_non_loopback_or_unsafe_urls_are_refused(url: str) -> None:
    with pytest.raises(PolicyEngineConfigError):
        validate_loopback_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8181", "http://127.9.9.9:1/", "http://[::1]:8181", "http://localhost:8181"],
)
def test_loopback_urls_are_accepted(url: str) -> None:
    assert validate_loopback_url(url) == url.rstrip("/")


def test_locus_opa_url_non_loopback_is_refused_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_OPA_URL", "http://192.168.1.10:8181")
    with pytest.raises(PolicyEngineConfigError):
        build_policy_engine()
    assert pe.policy_engine_available() is False


def test_locus_opa_url_loopback_builds_external_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCUS_OPA_URL", "http://127.0.0.1:18181")
    engine = build_policy_engine()
    assert isinstance(engine, OpaSidecarEngine)
    assert engine.base_url == "http://127.0.0.1:18181"
    engine.close()


# --- policy version -----------------------------------------------------------------


def test_policy_bundle_hash_is_stable_and_content_addressed() -> None:
    a = "package lattix.a\n\ndefault allow = false\n"
    b = "package lattix.b\n\ndefault allow = true\n"
    test_module = "package lattix.a_test\n\ntest_x if { true }\n"
    first = compute_policy_bundle_hash([a, b])
    assert first.startswith("sha256:")
    assert compute_policy_bundle_hash([b, a]) == first
    assert compute_policy_bundle_hash([a, b, test_module]) == first
    assert compute_policy_bundle_hash([a, b + " "]) != first


def test_repo_policy_version_covers_the_seven_policies_and_excludes_tests() -> None:
    policy_dir = Path(pe.__file__).resolve().parents[1] / "policies"
    files = pe.policy_files(policy_dir)
    assert {path.stem for path in files} == KNOWN_POLICIES
    version = pe.policy_dir_version(policy_dir)
    assert version == pe.policy_dir_version(policy_dir)
    assert version.startswith("sha256:")


# --- registry -------------------------------------------------------------------------


def test_backend_registry_reserves_regorus() -> None:
    assert {"opa-sidecar", "regorus"} <= set(pe.available_backends())
    with pytest.raises(NotImplementedError):
        build_policy_engine("regorus")
    with pytest.raises(PolicyEngineConfigError):
        build_policy_engine("python-copy")


# --- capability path scopes (non-policy helper kept in security.py) --------------------


def test_capability_path_scope_uses_canonical_containment(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    target = allowed_root / "nested" / "artifact.txt"
    target.parent.mkdir(parents=True)
    target.write_text("ok", encoding="utf-8")
    assert path_within_allowed_roots(str(target), [str(allowed_root)]) is True
    assert (
        path_within_allowed_roots(str(allowed_root / ".." / "x.txt"), [str(allowed_root)]) is False
    )


def test_capability_path_scope_denies_prefix_bypass(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    target = tmp_path / "allowed-evil" / "artifact.txt"
    target.parent.mkdir()
    target.write_text("nope", encoding="utf-8")
    assert path_within_allowed_roots(str(target), [str(allowed_root)]) is False
    assert path_within_allowed_roots(str(target), []) is False
