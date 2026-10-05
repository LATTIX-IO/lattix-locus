"""Regression tests for the LOCUS-312 CodeQL findings: path traversal on the
working-folder picker, polynomial ReDoS on model JSON extraction, open redirect
after sign-in, cookie-value allowlisting and exception-detail exposure."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not str(os.environ.get("A2A_JWT_SECRET") or "").strip():
    os.environ["A2A_JWT_SECRET"] = "unit-test-super-secret-value-32bytes"
if not str(os.environ.get("LOCUS_API_BEARER_TOKEN") or "").strip():
    os.environ["LOCUS_API_BEARER_TOKEN"] = "unit-test-bearer"

import app.main as main_module
from app.main import app, store

client = TestClient(app)

READ_HEADERS = {"x-locus-actor": "tester"}
ADMIN_HEADERS = {"Authorization": "Bearer unit-test-bearer", "x-locus-actor": "locus-admin"}

_SENSITIVE = "SENSITIVE-internal-host.corp:5432 Traceback /srv/secret/path.py"


# --- 1. path traversal: working folders confined to the projects root ---------
@pytest.fixture()
def projects_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "projects"
    (root / "repo" / "sub").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(main_module, "_PROJECTS_ROOT", str(root))
    return root


def test_working_folder_accepts_paths_under_root(projects_root: Path) -> None:
    expected = os.path.realpath(projects_root / "repo")
    assert main_module._resolve_working_folder("repo") == expected
    assert main_module._resolve_working_folder("projects/repo") == expected
    # The picker echoes back absolute paths it listed; those stay valid.
    assert main_module._resolve_working_folder(str(projects_root / "repo" / "sub")) == (
        os.path.realpath(projects_root / "repo" / "sub")
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "..",
        "../outside",
        "repo/../../outside",
        "repo/..",
        "..\\outside",
        "repo\\..\\..\\outside",
        "/etc/passwd",
        "C:\\Windows\\System32",
        "C:Windows",
        "\\\\server\\share",
        "repo\x00/../../outside",
        "re\npo",
        "re\tpo",
        pytest.param("a" * 5000, id="oversized"),
    ],
)
def test_working_folder_rejects_traversal(projects_root: Path, value: str) -> None:
    assert main_module._resolve_working_folder(value) is None


def test_working_folder_rejects_absolute_path_outside_root(projects_root: Path) -> None:
    outside = projects_root.parent / "outside"
    assert main_module._resolve_working_folder(str(outside)) is None
    # A sibling whose name merely shares the root's prefix is not "under" it.
    sibling = projects_root.parent / "projects-evil"
    sibling.mkdir()
    assert main_module._resolve_working_folder(str(sibling)) is None


def test_working_folder_rejects_symlink_escape(projects_root: Path) -> None:
    link = projects_root / "escape"
    target = projects_root.parent / "outside"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        # Windows without symlink privilege: a directory junction is the same
        # escape vector and needs no privilege.
        try:
            import _winapi  # type: ignore[import-not-found]

            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError, AttributeError):
            pytest.skip("neither symlinks nor junctions are available on this host")
    assert main_module._resolve_working_folder("escape") is None
    assert main_module._resolve_working_folder(str(link)) is None


def test_workspace_folders_endpoint_denies_traversal(projects_root: Path) -> None:
    ok = client.get("/workspace/folders", headers=READ_HEADERS)
    assert ok.status_code == 200
    assert [f["name"] for f in ok.json()["folders"]] == ["repo"]

    for attempt in ("../", "../outside", str(projects_root.parent / "outside")):
        response = client.get("/workspace/folders", params={"path": attempt}, headers=READ_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["exists"] is False
        assert body["folders"] == []
        assert body["path"] == ""


# --- 2. ReDoS: JSON-object extraction is linear ---------------------------------
def test_extract_json_object_span_matches_previous_regex_semantics() -> None:
    extract = main_module._extract_json_object_span
    assert extract('noise {"respond": false} tail') == '{"respond": false}'
    assert extract('a {"x": {"y": 1}} b } c') == '{"x": {"y": 1}} b }'
    assert extract("no braces") is None
    assert extract("} before {") is None
    assert extract("") is None
    assert extract(None) is None


@pytest.mark.parametrize(
    "payload",
    ["{{" * 50_000, "{" * 100_000, "{{" * 50_000 + "}"],
    ids=["double-brace-50k", "brace-100k", "double-brace-50k-closed"],
)
def test_extract_json_object_span_is_fast_on_adversarial_input(payload: str) -> None:
    started = time.perf_counter()
    main_module._extract_json_object_span(payload)
    assert time.perf_counter() - started < 0.5


def test_mention_gate_handles_adversarial_model_output_quickly(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_resolve_agent_chat_model", lambda a: "gpt-test")
    monkeypatch.setattr(
        main_module, "_run_openai_chat", lambda **kwargs: ("{{" * 50_000, {"mode": "live"})
    )

    class _Agent:
        name = "Reviewer"

    started = time.perf_counter()
    decision = main_module._agent_decides_to_respond(
        _Agent(), from_name="user", message="hi", transcript=""
    )
    assert time.perf_counter() - started < 1.0
    # Unparseable output fails open (participate), as before.
    assert decision["respond"] is True


# --- 6. open redirect after sign-in ------------------------------------------------
@pytest.mark.parametrize(
    "target",
    [
        "//evil.com",
        "//evil.com/path",
        "https://evil.com",
        "http://evil.com/inbox",
        "javascript:alert(1)",
        "/\\evil.com",
        "\\\\evil.com",
        "/\\/evil.com",
        "/ /evil.com",
        "/\t/evil.com",
        "evil.com",
        "",
        pytest.param("/" + "a" * 5000, id="oversized"),
        "/caf\u00e9",
    ],
)
def test_post_auth_redirect_rejects_offsite_targets(target: str) -> None:
    assert main_module._safe_post_auth_redirect_path(target) == "/inbox"


@pytest.mark.parametrize(
    "target", ["/inbox", "/builder/integrations?tab=mcp#top", "/runs/abc-123", "/%2F%2Fevil.com"]
)
def test_post_auth_redirect_keeps_same_origin_paths(target: str) -> None:
    assert main_module._safe_post_auth_redirect_path(target) == target


# --- 4. cookie value allowlist -------------------------------------------------------
def test_oidc_flow_cookie_rejects_values_outside_signed_token_shape() -> None:
    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    from starlette.requests import Request as StarletteRequest

    request = StarletteRequest(
        {
            "type": "http",
            "scheme": "http",
            "server": ("localhost", 8000),
            "headers": [],
            "path": "/",
            "query_string": b"",
        }
    )
    good = main_module._encode_oidc_browser_flow_cookie(
        {"return_to": main_module._safe_post_auth_redirect_path("/inbox?x=1"), "state": "s"}
    )
    response = RedirectResponse(url="/inbox")
    main_module._set_oidc_browser_flow_cookie(response, request, good)
    assert good in response.headers["set-cookie"]

    for bad in ("x; Domain=evil.com", "abc.def", good + "\r\nSet-Cookie: a=b", ""):
        with pytest.raises(HTTPException):
            main_module._set_oidc_browser_flow_cookie(RedirectResponse(url="/"), request, bad)


# --- 5. exception details never reach the client ------------------------------------
def test_skill_import_fetch_failure_hides_exception_detail(monkeypatch) -> None:
    def boom(url: str) -> tuple[str, str]:
        raise RuntimeError(_SENSITIVE)

    monkeypatch.setattr(main_module, "_fetch_remote_skill", boom)
    response = client.post(
        "/skills/import", json={"url": "https://example.com/skill.md"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 502
    assert "skill_import_fetch_failed" in response.json()["detail"]
    assert "SENSITIVE" not in response.text and "Traceback" not in response.text


def test_provider_model_listing_hides_exception_detail(monkeypatch) -> None:
    provider = next(key for key in main_module._PROVIDER_REGISTRY if key != "ollama")

    class _Models:
        def list(self) -> Any:
            raise RuntimeError(_SENSITIVE)

    class _Client:
        models = _Models()

    monkeypatch.setattr(main_module, "_provider_configured", lambda p: True)
    monkeypatch.setattr(main_module, "_get_chat_client", lambda p: (_Client(), ""))
    response = client.get(f"/models/providers/{provider}/models", headers=READ_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["models"] == []
    assert body["error_code"] == "provider_model_list_failed"
    assert "SENSITIVE" not in response.text


def test_local_model_delete_failure_hides_exception_detail(monkeypatch) -> None:
    def boom(model_id: str) -> bool:
        raise RuntimeError(_SENSITIVE)

    monkeypatch.setattr(main_module.local_models, "delete_model", boom)
    response = client.delete("/models/local/some-model", headers=ADMIN_HEADERS)
    assert response.status_code == 502
    assert "local_model_delete_failed" in response.json()["detail"]
    assert "SENSITIVE" not in response.text


def test_integration_probe_failure_hides_exception_detail(monkeypatch) -> None:
    from uuid import uuid4

    integration_id = str(uuid4())
    saved = client.post(
        "/integrations",
        json={
            "id": integration_id,
            "name": "Probe Target",
            "type": "http",
            "base_url": "http://localhost:9999/probe",
        },
        headers=ADMIN_HEADERS,
    )
    assert saved.status_code == 200

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "_FailingClient":
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def request(self, *args: Any, **kwargs: Any) -> Any:
            raise httpx.ConnectError(_SENSITIVE)

    monkeypatch.setattr(main_module.httpx, "Client", _FailingClient)
    try:
        response = client.post(f"/integrations/{integration_id}/test", headers=ADMIN_HEADERS)
        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "SENSITIVE" not in response.text
        assert "Traceback" not in response.text
    finally:
        store.integrations.pop(integration_id, None)


@pytest.mark.parametrize(
    ("candidate", "landing"),
    [
        ("/activity?session=abc", "/activity"),
        ("/settings/engines", "/settings"),
        ("/home", "/home"),
        ("/library/skills/x", "/library"),
        ("https://evil.example/home", "/inbox"),
        ("//evil.example/home", "/inbox"),
        ("/\evil.example", "/inbox"),
        ("/unknown/page", "/inbox"),
        ("", "/inbox"),
    ],
)
def test_post_auth_landing_is_a_constant_section(candidate: str, landing: str) -> None:
    # LOCUS-344: the OIDC flow cookie and redirect carry only constant landings.
    result = main_module._post_auth_landing(candidate)
    assert result == landing
    assert result in (*main_module._POST_AUTH_LANDINGS, main_module._POST_AUTH_REDIRECT_DEFAULT)
