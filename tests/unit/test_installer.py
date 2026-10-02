import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any

import pytest

from frontier_tooling import installer as packaged_installer
from frontier_runtime.install import (
    DiagnosticResult,
    FrontierInstaller,
    InstallerAnswers,
    MissingPrerequisite,
    PrerequisiteDefinition,
)


def test_installer_writes_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_hostname="demo",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.demo.localhost",
        oidc_audience="frontier-ui",
        oidc_jwks_url="http://casdoor.demo.localhost/.well-known/jwks.json",
        oidc_client_id="frontier-web",
        oidc_authorization_url="http://casdoor.demo.localhost/login/oauth/authorize",
        oidc_token_url="http://casdoor.demo.localhost/api/login/oauth/access_token",
        bootstrap_login_username="demo-login",
        bootstrap_login_email="demo-login@demo.localhost",
        bootstrap_login_display_name="Demo Login",
        bootstrap_login_password="DemoPass123!",
        openai_api_key="",
        federation_enabled=True,
        federation_cluster_name="cluster-a",
        federation_region="us-east",
        federation_peers=["https://peer.example.com"],
    )
    env_path = installer._write_env_file(
        answers,
        {
            "A2A_JWT_SECRET": secrets.token_urlsafe(32),
            "POSTGRES_PASSWORD": "db-secret",
            "NEO4J_PASSWORD": "neo-secret",
        },
    )
    text = env_path.read_text(encoding="utf-8")
    assert env_path == tmp_path / ".installer" / "local-secure.env"
    assert "LOCAL_STACK_HOST=demo.localhost" in text
    assert "FRONTIER_RUNTIME_PROFILE=local-secure" in text
    assert "NEXT_PUBLIC_API_BASE_URL=/api" in text
    assert "FRONTEND_ORIGIN=http://demo.localhost" in text
    assert "FRONTIER_REQUIRE_AUTHENTICATED_REQUESTS=true" in text
    assert "FRONTIER_ALLOW_HEADER_ACTOR_AUTH=false" in text
    assert "FRONTIER_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR=true" in text
    assert "FRONTIER_AUTH_MODE=oidc" in text
    assert "CASDOOR_LOCAL_HOST=casdoor.localhost" in text
    assert "CASDOOR_BIND_HOST=127.0.0.1" in text
    assert "CASDOOR_HTTP_PORT=8081" in text
    assert "CASDOOR_PUBLIC_URL=http://casdoor.demo.localhost" in text
    assert "FRONTIER_BOOTSTRAP_ADMIN_USERNAME=frontier-admin" in text
    assert "FRONTIER_BOOTSTRAP_ADMIN_EMAIL=admin@demo.localhost" in text
    assert "FRONTIER_BOOTSTRAP_ADMIN_SUBJECT=frontier-admin" in text
    assert "FRONTIER_ADMIN_ACTORS=frontier-admin,admin@demo.localhost" in text
    assert "FRONTIER_BUILDER_ACTORS=frontier-admin,admin@demo.localhost" in text
    assert "CASDOOR_BOOTSTRAP_LOGIN_USERNAME=demo-login" in text
    assert "CASDOOR_BOOTSTRAP_LOGIN_EMAIL=demo-login@demo.localhost" in text
    assert "CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME=Demo Login" in text
    assert "CASDOOR_BOOTSTRAP_LOGIN_PASSWORD=DemoPass123!" in text
    assert "FRONTIER_API_BEARER_TOKEN=" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=casdoor" in text
    assert "FRONTIER_AUTH_OIDC_ISSUER=http://casdoor.demo.localhost" in text
    assert "FRONTIER_AUTH_OIDC_AUDIENCE=frontier-ui" in text
    assert "FRONTIER_AUTH_OIDC_JWKS_URL=http://casdoor.demo.localhost/.well-known/jwks.json" in text
    assert "FRONTIER_AUTH_OIDC_CLIENT_ID=frontier-web" in text
    assert (
        "FRONTIER_AUTH_OIDC_AUTHORIZATION_URL=http://casdoor.demo.localhost/login/oauth/authorize"
        in text
    )
    assert (
        "FRONTIER_AUTH_OIDC_TOKEN_URL=http://casdoor.demo.localhost/api/login/oauth/access_token"
        in text
    )
    assert (
        "FRONTIER_AUTH_OIDC_SIGNIN_URL=http://casdoor.demo.localhost/login/oauth/authorize" in text
    )
    assert (
        "FRONTIER_AUTH_OIDC_SIGNUP_URL=http://casdoor.demo.localhost/login/oauth/authorize" in text
    )
    assert "FRONTIER_AUTH_OIDC_SCOPES=openid profile email" in text
    assert "A2A_JWT_AUD=frontier-runtime" in text
    assert "FEDERATION_ENABLED=true" in text
    assert "A2A_JWT_SECRET=" in text
    assert "POSTGRES_PASSWORD=db-secret" in text
    assert "NEO4J_PASSWORD=neo-secret" in text


def test_installer_generates_local_secret_when_blank(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(installation_root=str(tmp_path), deployment_mode="local")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)

    assert secrets_map["A2A_JWT_SECRET"]
    assert len(secrets_map["A2A_JWT_SECRET"]) >= 32
    assert secrets_map["POSTGRES_PASSWORD"]
    assert secrets_map["NEO4J_PASSWORD"]
    assert "FRONTIER_API_BEARER_TOKEN" not in secrets_map


def test_installer_reuses_existing_local_secrets_when_blank(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "A2A_JWT_SECRET=existing-a2a",
                "POSTGRES_PASSWORD=existing-postgres",
                "NEO4J_PASSWORD=existing-neo4j",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    answers = InstallerAnswers(installation_root=str(tmp_path), deployment_mode="local")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)

    assert secrets_map["A2A_JWT_SECRET"] == "existing-a2a"
    assert secrets_map["POSTGRES_PASSWORD"] == "existing-postgres"
    assert secrets_map["NEO4J_PASSWORD"] == "existing-neo4j"


def test_installer_reuses_existing_shared_token_when_blank(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "A2A_JWT_SECRET=existing-a2a",
                "POSTGRES_PASSWORD=existing-postgres",
                "NEO4J_PASSWORD=existing-neo4j",
                "FRONTIER_API_BEARER_TOKEN=existing-bearer",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="shared-token",
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)

    assert secrets_map["FRONTIER_API_BEARER_TOKEN"] == "existing-bearer"


def test_secure_local_answers_generate_randomized_bootstrap_identity(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    monkeypatch.setattr("secrets.token_hex", lambda _: "abc123")

    answers = installer.secure_local_answers(tmp_path)

    assert answers.local_hostname == "xfrontier"
    assert answers.local_auth_provider == "oidc"
    assert answers.oidc_provider_template == "casdoor"
    assert answers.bootstrap_admin_username == "frontier-admin-abc123"
    assert answers.bootstrap_admin_email == "frontier-admin-abc123@xfrontier.localhost"
    assert answers.bootstrap_admin_subject == "frontier-admin-abc123"
    assert answers.bootstrap_login_username == ""
    assert answers.bootstrap_login_email == ""
    assert answers.bootstrap_login_display_name == ""
    assert answers.bootstrap_login_password == ""
    assert answers.bootstrap_login_password_generated is False


def test_secure_local_answers_reuse_existing_install_settings(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_STACK_HOST=existing.localhost",
                "FRONTIER_AUTH_MODE=oidc",
                "FRONTIER_AUTH_OIDC_PROVIDER=external",
                "FRONTIER_AUTH_OIDC_ISSUER=https://login.example.com/realms/frontier",
                "FRONTIER_AUTH_OIDC_AUDIENCE=frontier-api",
                "FRONTIER_AUTH_OIDC_JWKS_URL=https://login.example.com/jwks",
                "FRONTIER_AUTH_OIDC_CLIENT_ID=frontier-ui",
                "FRONTIER_AUTH_OIDC_AUTHORIZATION_URL=https://login.example.com/auth",
                "FRONTIER_AUTH_OIDC_TOKEN_URL=https://login.example.com/token",
                "FRONTIER_AUTH_OIDC_SIGNIN_URL=https://login.example.com/sign-in",
                "FRONTIER_AUTH_OIDC_SIGNUP_URL=https://login.example.com/sign-up",
                "FRONTIER_AUTH_OIDC_SCOPES=openid profile email groups",
                "FRONTIER_BOOTSTRAP_ADMIN_USERNAME=existing-admin",
                "FRONTIER_BOOTSTRAP_ADMIN_EMAIL=admin@existing.localhost",
                "FRONTIER_BOOTSTRAP_ADMIN_SUBJECT=existing-admin-subject",
                "CASDOOR_BOOTSTRAP_LOGIN_USERNAME=existing-login",
                "CASDOOR_BOOTSTRAP_LOGIN_EMAIL=login@existing.localhost",
                "CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME=Existing Login",
                "CASDOOR_BOOTSTRAP_LOGIN_PASSWORD=ExistingPass123!",
                "FEDERATION_ENABLED=true",
                "FEDERATION_CLUSTER_NAME=cluster-a",
                "FEDERATION_REGION=us-east",
                "FEDERATION_PEERS=https://peer-a.example.com,https://peer-b.example.com",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    answers = installer.secure_local_answers(tmp_path)

    assert answers.local_hostname == "existing"
    assert answers.local_auth_provider == "oidc"
    assert answers.oidc_provider_template == "external"
    assert answers.oidc_issuer == "https://login.example.com/realms/frontier"
    assert answers.oidc_audience == "frontier-api"
    assert answers.oidc_jwks_url == "https://login.example.com/jwks"
    assert answers.oidc_client_id == "frontier-ui"
    assert answers.oidc_authorization_url == "https://login.example.com/auth"
    assert answers.oidc_token_url == "https://login.example.com/token"
    assert answers.oidc_signin_url == "https://login.example.com/sign-in"
    assert answers.oidc_signup_url == "https://login.example.com/sign-up"
    assert answers.oidc_scopes == ["openid", "profile", "email", "groups"]
    assert answers.bootstrap_admin_username == "existing-admin"
    assert answers.bootstrap_admin_email == "admin@existing.localhost"
    assert answers.bootstrap_admin_subject == "existing-admin-subject"
    assert answers.bootstrap_login_username == "existing-login"
    assert answers.bootstrap_login_email == "login@existing.localhost"
    assert answers.bootstrap_login_display_name == "Existing Login"
    assert answers.bootstrap_login_password == "ExistingPass123!"
    assert answers.federation_enabled is True
    assert answers.federation_cluster_name == "cluster-a"
    assert answers.federation_region == "us-east"
    assert answers.federation_peers == ["https://peer-a.example.com", "https://peer-b.example.com"]


def test_collect_local_answers_interactively_prompts_for_external_oidc(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    prompts = iter(
        [
            "demo",
            "oidc",
            "external",
            "https://login.example.com/realms/frontier",
            "frontier-api",
            "https://login.example.com/realms/frontier/protocol/openid-connect/certs",
            "frontier-ui",
            "https://login.example.com/realms/frontier/protocol/openid-connect/auth",
            "https://login.example.com/realms/frontier/protocol/openid-connect/token",
            "",
            "https://login.example.com/realms/frontier/registrations/start",
            "openid profile email groups",
            "",
            "",
            "",
            "y",
        ]
    )
    monkeypatch.setattr("secrets.token_hex", lambda _: "abc123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    assert answers.local_hostname == "demo"
    assert answers.local_auth_provider == "oidc"
    assert answers.oidc_provider_template == "external"
    assert answers.oidc_issuer == "https://login.example.com/realms/frontier"
    assert answers.oidc_audience == "frontier-api"
    assert answers.oidc_jwks_url.endswith("/certs")
    assert answers.oidc_client_id == "frontier-ui"
    assert answers.oidc_authorization_url.endswith("/auth")
    assert answers.oidc_token_url.endswith("/token")
    assert answers.oidc_signin_url == answers.oidc_authorization_url
    assert answers.oidc_signup_url.endswith("/registrations/start")
    assert answers.oidc_scopes == ["openid", "profile", "email", "groups"]
    assert answers.bootstrap_admin_username == "frontier-admin-abc123"
    assert answers.bootstrap_admin_email == "frontier-admin-abc123@demo.localhost"
    assert answers.bootstrap_admin_subject == "frontier-admin-abc123"


def test_render_panel_produces_boxed_tui_output(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)

    panel = installer._render_panel("Sample", ["Line one", "Line two"])
    lines = panel.splitlines()

    assert "╔" in panel
    assert "Sample" in panel
    assert "Line one" in panel
    assert "╚" in panel
    assert len({len(line) for line in lines}) == 1


def test_collect_local_answers_prints_review_tui(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    prompts = iter(
        ["", "", "", "", "", "", "review-user", "review@example.com", "Review User", "y"]
    )
    monkeypatch.setattr("secrets.token_hex", lambda _: "abc123")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "ReviewPass123!")
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    captured = capsys.readouterr()
    assert answers.bootstrap_admin_username == "frontier-admin-abc123"
    assert answers.bootstrap_login_username == "review-user"
    assert answers.bootstrap_login_password == "ReviewPass123!"
    assert "Lattix xFrontier installer" in captured.out
    assert "Review install settings" in captured.out
    assert "Secure local installation wizard" in captured.out
    assert "Login user  : review-user" in captured.out


def test_collect_local_answers_requires_interactive_casdoor_login_input(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)

    with pytest.raises(
        SystemExit,
        match="Interactive installer input is required to create the Casdoor bootstrap login user",
    ):
        installer.collect_local_answers(installation_root=tmp_path, interactive=False)


def test_collect_local_answers_prompts_for_casdoor_login_bootstrap(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    prompts = iter(
        [
            "demo",
            "oidc",
            "casdoor",
            "frontier-admin-demo",
            "admin@demo.localhost",
            "frontier-admin-demo",
            "frontier-login-demo",
            "login@demo.localhost",
            "Demo Operator",
            "y",
        ]
    )
    monkeypatch.setattr("secrets.token_hex", lambda _: "abc123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "LoginPass123!")

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    assert answers.bootstrap_login_username == "frontier-login-demo"
    assert answers.bootstrap_login_email == "login@demo.localhost"
    assert answers.bootstrap_login_display_name == "Demo Operator"
    assert answers.bootstrap_login_password == "LoginPass123!"
    assert answers.bootstrap_login_password_generated is False


def test_collect_local_answers_reuses_existing_bootstrap_login_password_when_blank(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_STACK_HOST=existing.localhost",
                "FRONTIER_AUTH_MODE=oidc",
                "FRONTIER_AUTH_OIDC_PROVIDER=casdoor",
                "FRONTIER_BOOTSTRAP_ADMIN_USERNAME=existing-admin",
                "FRONTIER_BOOTSTRAP_ADMIN_EMAIL=admin@existing.localhost",
                "FRONTIER_BOOTSTRAP_ADMIN_SUBJECT=existing-admin",
                "CASDOOR_BOOTSTRAP_LOGIN_USERNAME=existing-login",
                "CASDOOR_BOOTSTRAP_LOGIN_EMAIL=login@existing.localhost",
                "CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME=Existing Login",
                "CASDOOR_BOOTSTRAP_LOGIN_PASSWORD=ExistingPass123!",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prompts = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "replacement-login",
            "replacement@example.localhost",
            "Replacement Login",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    assert answers.local_hostname == "existing"
    assert answers.bootstrap_admin_username == "existing-admin"
    assert answers.bootstrap_login_username == "replacement-login"
    assert answers.bootstrap_login_email == "replacement@example.localhost"
    assert answers.bootstrap_login_display_name == "Replacement Login"
    assert answers.bootstrap_login_password == "ExistingPass123!"


def test_collect_local_answers_reprompts_for_required_casdoor_login_fields(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    prompts = iter(
        [
            "demo",
            "oidc",
            "casdoor",
            "frontier-admin-demo",
            "admin@demo.localhost",
            "frontier-admin-demo",
            "",
            "required-user",
            "",
            "required@example.com",
            "",
            "Required User",
            "y",
        ]
    )
    secrets_iter = iter(["", "RequiredPass123!"])
    monkeypatch.setattr("secrets.token_hex", lambda _: "abc123")
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(secrets_iter))

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    captured = capsys.readouterr()
    assert answers.bootstrap_login_username == "required-user"
    assert answers.bootstrap_login_email == "required@example.com"
    assert answers.bootstrap_login_display_name == "Required User"
    assert answers.bootstrap_login_password == "RequiredPass123!"
    assert captured.out.count("A value is required.") >= 4


def test_packaged_installer_bootstraps_casdoor_login_user(monkeypatch, tmp_path: Path) -> None:
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.localhost",
        bootstrap_login_username="demo-login",
        bootstrap_login_email="demo-login@demo.localhost",
        bootstrap_login_display_name="Demo Login",
        bootstrap_login_password="DemoPass123!",
        bootstrap_login_password_generated=True,
    )

    captured_requests: list[tuple[str, str, str | None, dict[str, str]]] = []

    class DummyOpener:
        def open(self, request, timeout=10):
            captured_requests.append(
                (
                    request.full_url,
                    request.get_method(),
                    request.data.decode("utf-8") if request.data else None,
                    dict(request.header_items()),
                )
            )

            class DummyResponse:
                def __init__(self, payload: str) -> None:
                    self._payload = payload

                def read(self) -> bytes:
                    return self._payload.encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

            if request.full_url.endswith("/api/get-account"):
                return DummyResponse('{"status":"ok","data":{"owner":"built-in","name":"admin"}}')
            if "/api/get-user" in request.full_url:
                return DummyResponse('{"status":"error","msg":"not found"}')
            if request.full_url.endswith("/api/add-user"):
                return DummyResponse('{"status":"ok","data":"Affected"}')
            return DummyResponse('{"status":"error","msg":"ignored"}')

    monkeypatch.setattr(
        packaged_installer.urllib_request, "build_opener", lambda *args: DummyOpener()
    )

    bootstrap_login = packaged_installer._bootstrap_casdoor_login_user(answers)

    assert bootstrap_login == {
        "username": "demo-login",
        "email": "demo-login@demo.localhost",
        "display_name": "Demo Login",
        "password_generated": True,
    }
    assert any(url.endswith("/api/get-account") for url, *_ in captured_requests)
    add_user_request = next(item for item in captured_requests if item[0].endswith("/api/add-user"))
    assert add_user_request[1] == "POST"
    assert '"name": "demo-login"' in add_user_request[2]
    assert add_user_request[3]["Host"] == "casdoor.localhost"


def test_packaged_installer_retries_transient_casdoor_gateway_failures(
    monkeypatch, tmp_path: Path
) -> None:
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.localhost",
        bootstrap_login_username="demo-login",
        bootstrap_login_email="demo-login@demo.localhost",
        bootstrap_login_display_name="Demo Login",
        bootstrap_login_password="DemoPass123!",
        bootstrap_login_password_generated=True,
    )

    attempts = {"count": 0}

    class DummyOpener:
        def open(self, request, timeout=10):
            attempts["count"] += 1

            class DummyResponse:
                def __init__(self, payload: str) -> None:
                    self._payload = payload

                def read(self) -> bytes:
                    return self._payload.encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

            if attempts["count"] <= 2:
                raise packaged_installer.urllib_error.HTTPError(
                    request.full_url, 502, "Bad Gateway", hdrs=None, fp=None
                )
            if request.full_url.endswith("/api/get-account"):
                return DummyResponse('{"status":"ok","data":{"owner":"built-in","name":"admin"}}')
            if "/api/get-user" in request.full_url:
                return DummyResponse('{"status":"error","msg":"not found"}')
            if request.full_url.endswith("/api/add-user"):
                return DummyResponse('{"status":"ok","data":"Affected"}')
            return DummyResponse('{"status":"error","msg":"ignored"}')

    monkeypatch.setattr(
        packaged_installer.urllib_request, "build_opener", lambda *args: DummyOpener()
    )
    monkeypatch.setattr(packaged_installer.time, "sleep", lambda *_: None)

    bootstrap_login = packaged_installer._bootstrap_casdoor_login_user(answers)

    assert attempts["count"] >= 3
    assert bootstrap_login is not None
    assert bootstrap_login["username"] == "demo-login"
    assert "password" not in bootstrap_login


def test_packaged_installer_updates_existing_casdoor_login_without_immutable_identity_fields(
    monkeypatch, tmp_path: Path
) -> None:
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.localhost",
        bootstrap_login_username="demo-login",
        bootstrap_login_email="demo-login@demo.localhost",
        bootstrap_login_display_name="Demo Login",
        bootstrap_login_password="DemoPass123!",
        bootstrap_login_password_generated=False,
    )

    captured_requests: list[tuple[str, str, str | None, dict[str, str]]] = []

    class DummyOpener:
        def open(self, request, timeout=10):
            captured_requests.append(
                (
                    request.full_url,
                    request.get_method(),
                    request.data.decode("utf-8") if request.data else None,
                    dict(request.header_items()),
                )
            )

            class DummyResponse:
                def __init__(self, payload: str) -> None:
                    self._payload = payload

                def read(self) -> bytes:
                    return self._payload.encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

            if request.full_url.endswith("/api/get-account"):
                return DummyResponse('{"status":"ok","data":{"owner":"built-in","name":"admin"}}')
            if "/api/get-user" in request.full_url:
                return DummyResponse(
                    '{"status":"ok","data":{"owner":"built-in","name":"demo-login","id":"built-in/demo-login"}}'
                )
            if "/api/update-user?id=" in request.full_url:
                return DummyResponse('{"status":"ok","data":"Affected"}')
            return DummyResponse('{"status":"error","msg":"ignored"}')

    monkeypatch.setattr(
        packaged_installer.urllib_request, "build_opener", lambda *args: DummyOpener()
    )

    bootstrap_login = packaged_installer._bootstrap_casdoor_login_user(answers)

    assert bootstrap_login is not None
    assert bootstrap_login["username"] == "demo-login"
    assert "password" not in bootstrap_login
    update_request = next(item for item in captured_requests if "/api/update-user?id=" in item[0])
    assert update_request[1] == "POST"
    assert '"displayName": "Demo Login"' in str(update_request[2])
    assert '"owner":' not in str(update_request[2])
    assert '"name":' not in str(update_request[2])
    assert '"id":' not in str(update_request[2])


def test_packaged_installer_reuses_matching_existing_casdoor_login_without_update(
    monkeypatch, tmp_path: Path
) -> None:
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.localhost",
        bootstrap_login_username="demo-login",
        bootstrap_login_email="demo-login@demo.localhost",
        bootstrap_login_display_name="Demo Login",
        bootstrap_login_password="DemoPass123!",
        bootstrap_login_password_generated=False,
    )

    captured_requests: list[tuple[str, str, str | None, dict[str, str]]] = []

    class DummyOpener:
        def open(self, request, timeout=10):
            captured_requests.append(
                (
                    request.full_url,
                    request.get_method(),
                    request.data.decode("utf-8") if request.data else None,
                    dict(request.header_items()),
                )
            )

            class DummyResponse:
                def __init__(self, payload: str) -> None:
                    self._payload = payload

                def read(self) -> bytes:
                    return self._payload.encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb) -> bool:
                    return False

            if request.full_url.endswith("/api/get-account"):
                return DummyResponse('{"status":"ok","data":{"owner":"built-in","name":"admin"}}')
            if "/api/get-user" in request.full_url:
                return DummyResponse(
                    '{"status":"ok","data":{"owner":"built-in","name":"demo-login","email":"demo-login@demo.localhost","displayName":"Demo Login","type":"normal-user","isAdmin":true,"isForbidden":false,"isDeleted":false,"signupApplication":"app-built-in"}}'
                )
            return DummyResponse('{"status":"error","msg":"unexpected request"}')

    monkeypatch.setattr(
        packaged_installer.urllib_request, "build_opener", lambda *args: DummyOpener()
    )

    bootstrap_login = packaged_installer._bootstrap_casdoor_login_user(answers)

    assert bootstrap_login is not None
    assert bootstrap_login["username"] == "demo-login"
    assert "password" not in bootstrap_login
    assert not any("/api/update-user" in item[0] for item in captured_requests)
    assert not any("/api/add-user" in item[0] for item in captured_requests)


def test_print_install_result_json_redacts_bootstrap_password(
    monkeypatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FRONTIER_INSTALLER_OUTPUT", "json")

    packaged_installer._print_install_result(
        {
            "installed": True,
            "install_mode": "editable",
            "repo_root": "D:/demo",
            "compose_env": "D:/demo/.installer/local-secure.env",
            "auth_mode": "oidc",
            "security_posture": "Secure local profile (single-host compose, authenticated A2A)",
            "urls": ["http://xfrontier.localhost:8080"],
            "path": {
                "cli_path": "C:/Python/Scripts/lattix.exe",
                "scripts_dir": "C:/Python/Scripts",
                "updated": False,
                "locations": [],
            },
            "bootstrap_login": {
                "username": "demo-login",
                "email": "demo-login@demo.localhost",
                "display_name": "Demo Login",
                "password": "DemoPass123!",
                "password_generated": False,
            },
            "next_steps": ["Open one of the URLs above to reach the portal."],
            "bootstrap_url": "https://example.invalid/main.zip",
        }
    )

    captured = capsys.readouterr()
    rendered = json.loads(captured.out)
    assert "DemoPass123!" not in captured.out
    assert '"password"' not in captured.out
    assert rendered["urls"] == ["http://xfrontier.localhost:8080"]


def test_packaged_installer_display_mode_prefers_tui_for_interactive_stdin(monkeypatch) -> None:
    class DummyStream:
        def __init__(self, tty: bool) -> None:
            self._tty = tty

        def isatty(self) -> bool:
            return self._tty

    monkeypatch.delenv("FRONTIER_INSTALLER_OUTPUT", raising=False)
    monkeypatch.setattr(packaged_installer.sys, "stdout", DummyStream(False))
    monkeypatch.setattr(packaged_installer.sys, "stdin", DummyStream(True))

    assert packaged_installer._display_mode() == "tui"


def test_collect_local_answers_requires_explicit_casdoor_identity_even_when_existing_values_present(
    monkeypatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_STACK_HOST=existing.localhost",
                "FRONTIER_AUTH_MODE=oidc",
                "FRONTIER_AUTH_OIDC_PROVIDER=casdoor",
                "FRONTIER_BOOTSTRAP_ADMIN_USERNAME=existing-admin",
                "FRONTIER_BOOTSTRAP_ADMIN_EMAIL=admin@existing.localhost",
                "FRONTIER_BOOTSTRAP_ADMIN_SUBJECT=existing-admin",
                "CASDOOR_BOOTSTRAP_LOGIN_USERNAME=existing-login",
                "CASDOOR_BOOTSTRAP_LOGIN_EMAIL=login@existing.localhost",
                "CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME=Existing Login",
                "CASDOOR_BOOTSTRAP_LOGIN_PASSWORD=ExistingPass123!",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prompts = iter(
        [
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "typed-login",
            "",
            "typed@example.localhost",
            "",
            "Typed Login",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(prompts))
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    answers = installer.collect_local_answers(installation_root=tmp_path, interactive=True)

    captured = capsys.readouterr()
    assert answers.bootstrap_login_username == "typed-login"
    assert answers.bootstrap_login_email == "typed@example.localhost"
    assert answers.bootstrap_login_display_name == "Typed Login"
    assert answers.bootstrap_login_password == "ExistingPass123!"
    assert captured.out.count("A value is required.") >= 3
    assert "Current    :" not in captured.out
    assert "existing-login" not in captured.out
    assert "login@existing.localhost" not in captured.out
    assert "Existing Login" not in captured.out


def test_render_install_summary_never_echoes_bootstrap_password(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        packaged_installer.shutil,
        "get_terminal_size",
        lambda fallback=(100, 24): os.terminal_size((160, 24)),
    )

    summary = packaged_installer._render_install_summary(
        {
            "installed": True,
            "install_mode": "editable",
            "repo_root": str(tmp_path),
            "compose_env": str(tmp_path / ".installer" / "local-secure.env"),
            "auth_mode": "oidc",
            "security_posture": "Secure local profile (single-host compose, authenticated A2A)",
            "urls": ["http://xfrontier.localhost:8080"],
            "path": {
                "cli_path": "C:/Python/Scripts/lattix.exe",
                "scripts_dir": "C:/Python/Scripts",
                "updated": False,
                "locations": [],
            },
            "bootstrap_login": {
                "username": "demo-login",
                "email": "demo-login@demo.localhost",
                "display_name": "Demo Login",
                "password": "DemoPass123!",
                "password_generated": True,
            },
            "next_steps": ["Open one of the URLs above to reach the portal."],
            "bootstrap_url": "https://example.invalid/main.zip",
        }
    )

    body_lines = [line[2:-2].rstrip() for line in summary.splitlines()[1:-1]]

    assert "DemoPass123!" not in summary
    assert "Generated during install and stored securely" in summary
    assert "  [1] http://xfrontier.localhost:8080" in body_lines


def test_packaged_installer_managed_runtime_paths(tmp_path: Path) -> None:
    managed_venv = tmp_path / ".venv"
    scripts_dir = managed_venv / ("Scripts" if os.name == "nt" else "bin")

    assert packaged_installer._managed_venv_dir(tmp_path) == managed_venv
    assert packaged_installer._managed_scripts_dir(tmp_path) == scripts_dir
    assert packaged_installer._managed_python_executable(tmp_path) == scripts_dir / (
        "python.exe" if os.name == "nt" else "python"
    )


def test_packaged_installer_runtime_env_prepends_managed_scripts_dir(
    monkeypatch, tmp_path: Path
) -> None:
    editable_scripts = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    python_bin = editable_scripts / ("python.exe" if os.name == "nt" else "python")
    path_separator = ";" if os.name == "nt" else ":"
    monkeypatch.setenv(
        "PATH",
        path_separator.join(
            [str(tmp_path / "system" / "bin"), str(editable_scripts), str(tmp_path / "other" / "bin")]
        ),
    )

    env = packaged_installer._runtime_env(
        tmp_path,
        python_bin=python_bin,
        scripts_dir=editable_scripts,
        venv_dir=tmp_path / ".venv",
    )

    assert env["PATH"].split(path_separator) == [
        str(editable_scripts),
        str(tmp_path / "system" / "bin"),
        str(tmp_path / "other" / "bin"),
    ]
    assert env[packaged_installer.FRONTIER_APP_HOME_ENV] == str(tmp_path)
    assert env["FRONTIER_PYTHON_BIN"] == str(python_bin)
    assert env["VIRTUAL_ENV"] == str(tmp_path / ".venv")


def test_packaged_installer_update_preserves_installer_state(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    install_root = tmp_path / "installed"
    (source_root / "frontier_tooling").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='0.2.0'\n", encoding="utf-8"
    )
    (source_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (source_root / "README.md").write_text("new build\n", encoding="utf-8")

    (install_root / ".installer").mkdir(parents=True, exist_ok=True)
    (install_root / ".installer" / "local-secure.env").write_text(
        "A2A_JWT_SECRET=existing-secret\n", encoding="utf-8"
    )
    (install_root / ".env").write_text("LOCAL_STACK_HOST=xfrontier.local\n", encoding="utf-8")
    (install_root / "README.md").write_text("old build\n", encoding="utf-8")

    refreshed_root = packaged_installer._prepare_install_root_for_update(source_root, install_root)

    assert refreshed_root == install_root
    assert (install_root / ".installer" / "local-secure.env").read_text(
        encoding="utf-8"
    ) == "A2A_JWT_SECRET=existing-secret\n"


def test_packaged_installer_prepare_install_root_preserves_existing_installer_state(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    install_root = tmp_path / "installed"
    (source_root / "frontier_tooling").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='0.2.0'\n", encoding="utf-8"
    )
    (source_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (source_root / "README.md").write_text("new build\n", encoding="utf-8")

    (install_root / ".installer").mkdir(parents=True, exist_ok=True)
    (install_root / ".installer" / "local-secure.env").write_text(
        "POSTGRES_PASSWORD=existing-secret\n", encoding="utf-8"
    )
    (install_root / ".env").write_text("LOCAL_STACK_HOST=existing.localhost\n", encoding="utf-8")
    (install_root / "README.md").write_text("old build\n", encoding="utf-8")
    monkeypatch.setattr(packaged_installer, "default_app_home", lambda: install_root)

    refreshed_root = packaged_installer._prepare_install_root(source_root)

    assert refreshed_root == install_root
    assert (install_root / ".installer" / "local-secure.env").read_text(
        encoding="utf-8"
    ) == "POSTGRES_PASSWORD=existing-secret\n"
    assert (install_root / ".env").read_text(
        encoding="utf-8"
    ) == "LOCAL_STACK_HOST=existing.localhost\n"
    assert (install_root / "README.md").read_text(encoding="utf-8") == "new build\n"


def test_packaged_installer_prepare_install_root_preserves_custom_in_app_agent_assets(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    install_root = tmp_path / "installed"
    (source_root / "frontier_tooling").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='0.2.0'\n", encoding="utf-8"
    )
    (source_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (source_root / ".env.example").write_text(
        "FRONTIER_AGENT_ASSETS_ROOT=private-agents\n", encoding="utf-8"
    )

    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / ".env").write_text(
        "FRONTIER_AGENT_ASSETS_ROOT=private-agents\n", encoding="utf-8"
    )
    (install_root / "private-agents" / "custom-agent").mkdir(parents=True, exist_ok=True)
    (install_root / "private-agents" / "custom-agent" / "system-prompt.md").write_text(
        "keep me\n", encoding="utf-8"
    )
    monkeypatch.setattr(packaged_installer, "default_app_home", lambda: install_root)

    refreshed_root = packaged_installer._prepare_install_root(source_root)

    assert refreshed_root == install_root
    assert (install_root / "private-agents" / "custom-agent" / "system-prompt.md").read_text(
        encoding="utf-8"
    ) == "keep me\n"


def test_packaged_installer_prepare_install_root_preserves_user_added_sample_agent_dirs(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    install_root = tmp_path / "installed"
    (source_root / "frontier_tooling").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='0.2.0'\n", encoding="utf-8"
    )
    (source_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (source_root / "examples" / "agents" / "built-in-agent").mkdir(parents=True, exist_ok=True)
    (source_root / "examples" / "agents" / "built-in-agent" / "system-prompt.md").write_text(
        "new built-in\n", encoding="utf-8"
    )

    (install_root / "examples" / "agents" / "user-agent").mkdir(parents=True, exist_ok=True)
    (install_root / "examples" / "agents" / "user-agent" / "system-prompt.md").write_text(
        "keep custom agent\n", encoding="utf-8"
    )
    (install_root / "examples" / "agents" / "built-in-agent").mkdir(parents=True, exist_ok=True)
    (install_root / "examples" / "agents" / "built-in-agent" / "system-prompt.md").write_text(
        "old built-in\n", encoding="utf-8"
    )
    monkeypatch.setattr(packaged_installer, "default_app_home", lambda: install_root)

    refreshed_root = packaged_installer._prepare_install_root(source_root)

    assert refreshed_root == install_root
    assert (install_root / "examples" / "agents" / "user-agent" / "system-prompt.md").read_text(
        encoding="utf-8"
    ) == "keep custom agent\n"
    assert (install_root / "examples" / "agents" / "built-in-agent" / "system-prompt.md").read_text(
        encoding="utf-8"
    ) == "new built-in\n"


def test_packaged_installer_prepare_install_root_preserves_manifest_declared_in_app_asset_roots(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "source"
    install_root = tmp_path / "installed"
    (source_root / "frontier_tooling").mkdir(parents=True, exist_ok=True)
    (source_root / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='0.2.0'\n", encoding="utf-8"
    )
    (source_root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install_root / ".installer").mkdir(parents=True, exist_ok=True)
    (install_root / ".installer" / "state-manifest.json").write_text(
        '{"schema_version":1,"in_app_asset_roots":["private-agents"]}\n',
        encoding="utf-8",
    )
    (install_root / "private-agents" / "manifest-agent").mkdir(parents=True, exist_ok=True)
    (install_root / "private-agents" / "manifest-agent" / "system-prompt.md").write_text(
        "keep via manifest\n", encoding="utf-8"
    )
    monkeypatch.setattr(packaged_installer, "default_app_home", lambda: install_root)

    refreshed_root = packaged_installer._prepare_install_root(source_root)

    assert refreshed_root == install_root
    assert (install_root / "private-agents" / "manifest-agent" / "system-prompt.md").read_text(
        encoding="utf-8"
    ) == "keep via manifest\n"


def test_packaged_installer_retries_secure_gateway_on_fallback_port(
    monkeypatch, tmp_path: Path
) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    compose_env = installer_dir / "local-secure.env"
    compose_env.write_text(
        "\n".join(
            [
                "LOCAL_STACK_HOST=xfrontier.local",
                "LOCAL_GATEWAY_BIND_HOST=127.0.0.1",
                "LOCAL_GATEWAY_HTTP_PORT=80",
                "FRONTEND_ORIGIN=http://xfrontier.local",
                "FRONTIER_LOCAL_API_BASE_URL=http://127.0.0.1/api",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def _fake_compose_run(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        current_env_lines = compose_env.read_text(encoding="utf-8").splitlines()
        if "LOCAL_GATEWAY_HTTP_PORT=80" in current_env_lines:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="Error response from daemon: failed to set up container networking: driver failed programming external connectivity on endpoint xfrontier-local-gateway-1: Bind for 127.0.0.1:80 failed: port is already allocated\n",
            )
        return subprocess.CompletedProcess(command, 0, stdout="started\n", stderr="")

    monkeypatch.setattr(packaged_installer, "_compose_up_with_output", _fake_compose_run)
    monkeypatch.setattr(
        packaged_installer, "_prebuild_secure_stack_images", lambda install_root, env: None
    )
    monkeypatch.setattr(
        packaged_installer, "_select_fallback_gateway_port", lambda bind_host, occupied_port: 8080
    )
    monkeypatch.setattr(
        packaged_installer,
        "portal_urls",
        lambda root=None: ["http://xfrontier.local:8080", "http://127.0.0.1:8080"],
    )

    urls = packaged_installer._auto_start_stack(tmp_path, {})
    updated_env = compose_env.read_text(encoding="utf-8")

    assert len(calls) == 2
    assert urls == ["http://xfrontier.local:8080", "http://127.0.0.1:8080"]
    assert "LOCAL_GATEWAY_HTTP_PORT=8080" in updated_env
    assert "FRONTEND_ORIGIN=http://xfrontier.local:8080" in updated_env
    assert "FRONTIER_LOCAL_API_BASE_URL=http://127.0.0.1:8080/api" in updated_env


def test_auto_start_stack_prebuilds_secure_images_once(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(
        packaged_installer,
        "run_command",
        lambda command, *, cwd=None, check=True, env=None: (
            commands.append(command)
            or subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        packaged_installer,
        "_compose_up_with_output",
        lambda command, *, cwd, env: subprocess.CompletedProcess(
            command, 0, stdout="started\n", stderr=""
        ),
    )
    monkeypatch.setattr(
        packaged_installer, "portal_urls", lambda root=None: ["http://xfrontier.local"]
    )

    urls = packaged_installer._auto_start_stack(tmp_path, {})

    assert urls == ["http://xfrontier.local"]
    assert commands == [
        [
            "docker",
            "compose",
            "--env-file",
            str(tmp_path / ".installer" / "local-secure.env"),
            "build",
            "frontend",
            "backend",
            "agent-research",
        ]
    ]


def test_casdoor_bootstrap_endpoint_uses_effective_secure_gateway_port(
    monkeypatch, tmp_path: Path
) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_GATEWAY_BIND_HOST=127.0.0.1",
                "LOCAL_GATEWAY_HTTP_PORT=8080",
                "LOCAL_STACK_HOST=xfrontier.local",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(packaged_installer.FRONTIER_APP_HOME_ENV, str(tmp_path))

    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://casdoor.localhost",
        bootstrap_login_username="demo-login",
        bootstrap_login_password="DemoPass123!",
    )

    base_url, host_headers = packaged_installer._casdoor_bootstrap_endpoint(answers)

    assert base_url == "http://127.0.0.1:8080"
    assert host_headers == {"Host": "casdoor.localhost"}


def test_casdoor_bootstrap_endpoint_uses_direct_loopback_issuer_without_gateway_rewrite(
    tmp_path: Path,
) -> None:
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://127.0.0.1:8081",
        bootstrap_login_username="demo-login",
        bootstrap_login_password="DemoPass123!",
    )

    base_url, host_headers = packaged_installer._casdoor_bootstrap_endpoint(answers)

    assert base_url == "http://127.0.0.1:8081"
    assert host_headers == {}


def test_bootstrap_failure_reports_postgres_volume_guidance(monkeypatch, tmp_path: Path) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (installer_dir / "local-secure.env").write_text(
        "LOCAL_GATEWAY_HTTP_PORT=80\n", encoding="utf-8"
    )
    monkeypatch.setenv(packaged_installer.FRONTIER_APP_HOME_ENV, str(tmp_path))
    monkeypatch.setattr(
        packaged_installer,
        "_compose_service_logs_text",
        lambda install_root, service, tail=80: (
            'panic: pq: password authentication failed for user "frontier"'
            if service == "casdoor"
            else ""
        ),
    )

    message = packaged_installer._diagnose_casdoor_bootstrap_failure()

    assert "postgresql volume appears to be using different credentials" in message.lower()
    assert "down -v --remove-orphans" in message
    assert "lattix remove" in message


def test_packaged_installer_ensure_local_vault_bootstrap_initializes_and_unseals(
    monkeypatch, tmp_path: Path
) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[list[str], str | None, bool]] = []
    raw_calls: list[tuple[list[str], str | None, bool]] = []

    responses = iter(
        [
            {"initialized": False, "sealed": True},
            {"root_token": "root-token", "unseal_keys_b64": ["unseal-key"]},
            {"initialized": True, "sealed": True},
            {"sealed": False},
        ]
    )

    def _fake_run_vault_cli_json(install_root, vault_args, *, token=None, allow_nonzero=False):
        calls.append((vault_args, token, allow_nonzero))
        if vault_args[:2] == ["secrets", "list"]:
            return {}
        return next(responses)

    def _fake_run_vault_cli(install_root, vault_args, *, token=None, allow_nonzero=False):
        raw_calls.append((vault_args, token, allow_nonzero))
        return "ok"

    monkeypatch.setattr(packaged_installer, "_run_vault_cli_json", _fake_run_vault_cli_json)
    monkeypatch.setattr(packaged_installer, "_run_vault_cli", _fake_run_vault_cli)

    bootstrap = packaged_installer._ensure_local_vault_bootstrap(tmp_path)

    assert bootstrap["root_token"] == "root-token"
    assert bootstrap["unseal_key"] == "unseal-key"
    bootstrap_file = installer_dir / "vault-bootstrap.json"
    assert bootstrap_file.exists()
    assert json.loads(bootstrap_file.read_text(encoding="utf-8"))["root_token"] == "root-token"
    assert any(args[:2] == ["operator", "init"] for args, *_ in calls)
    assert any(args[:2] == ["operator", "unseal"] for args, *_ in raw_calls)
    assert any(args[:2] == ["secrets", "enable"] for args, *_ in raw_calls)
    assert all("-format=json" not in args for args, *_ in raw_calls)


def test_packaged_installer_syncs_sensitive_install_state_to_vault(
    monkeypatch, tmp_path: Path
) -> None:
    installer_dir = tmp_path / ".installer"
    installer_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='lattix-frontier'\nversion='1.2.3'\n", encoding="utf-8"
    )
    (tmp_path / ".env").write_text(
        "FRONTIER_AGENT_ASSETS_ROOT=private-agents\nOPENAI_API_KEY=root-openai\n", encoding="utf-8"
    )
    (installer_dir / "local-secure.env").write_text(
        "\n".join(
            [
                "LOCAL_STACK_HOST=xfrontier.local",
                "FRONTIER_AUTH_MODE=oidc",
                "A2A_JWT_SECRET=a2a-secret",
                "POSTGRES_PASSWORD=postgres-secret",
                "NEO4J_PASSWORD=neo4j-secret",
                "CASDOOR_BOOTSTRAP_LOGIN_PASSWORD=login-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (installer_dir / "generated-values.yaml").write_text("clusterName: demo\n", encoding="utf-8")

    captured_writes: list[tuple[str, dict[str, Any], str]] = []

    monkeypatch.setattr(
        packaged_installer,
        "_ensure_local_vault_bootstrap",
        lambda install_root: {"root_token": "root-token"},
    )
    monkeypatch.setattr(
        packaged_installer,
        "_vault_kv_put",
        lambda install_root, api_path, payload, *, token: (
            captured_writes.append((api_path, payload, token)) or {"written": True}
        ),
    )

    synced = packaged_installer._sync_installer_state_to_vault(tmp_path, install_mode="wheel")

    assert synced["vault_secret_path"].startswith("secret/data/local/frontier/installations/")
    assert synced["vault_state_path"].startswith("secret/data/local/frontier/installations/")
    assert len(captured_writes) == 2

    secret_write = next(item for item in captured_writes if item[0] == synced["vault_secret_path"])
    state_write = next(item for item in captured_writes if item[0] == synced["vault_state_path"])

    assert secret_write[2] == "root-token"
    assert secret_write[1]["A2A_JWT_SECRET"] == "a2a-secret"
    assert secret_write[1]["POSTGRES_PASSWORD"] == "postgres-secret"
    assert secret_write[1]["NEO4J_PASSWORD"] == "neo4j-secret"
    assert secret_write[1]["CASDOOR_BOOTSTRAP_LOGIN_PASSWORD"] == "login-secret"
    assert secret_write[1]["OPENAI_API_KEY"] == "root-openai"
    assert "LOCAL_STACK_HOST" not in secret_write[1]

    decoded_state = json.loads(base64.b64decode(state_write[1]["payload_b64"]).decode("utf-8"))
    assert decoded_state["install_mode"] == "wheel"
    assert decoded_state["env_snapshots"]["secure_env"]["LOCAL_STACK_HOST"] == "xfrontier.local"
    assert (
        decoded_state["env_snapshots"]["root_env"]["FRONTIER_AGENT_ASSETS_ROOT"] == "private-agents"
    )
    assert "POSTGRES_PASSWORD" not in decoded_state["env_snapshots"]["secure_env"]
    assert (
        base64.b64decode(decoded_state["generated_helm_values_b64"]).decode("utf-8")
        == "clusterName: demo\n"
    )


def test_installer_defaults_to_casdoor_oidc_preset(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(installation_root=str(tmp_path), deployment_mode="local")

    env_path = installer._write_env_file(
        answers,
        {
            "A2A_JWT_SECRET": secrets.token_urlsafe(32),
            "POSTGRES_PASSWORD": "db-secret",
            "NEO4J_PASSWORD": "neo-secret",
        },
    )
    text = env_path.read_text(encoding="utf-8")

    assert "FRONTIER_AUTH_MODE=oidc" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=casdoor" in text
    assert "FRONTIER_AUTH_OIDC_ISSUER=http://127.0.0.1:8081" in text
    assert "FRONTIER_AUTH_OIDC_AUDIENCE=frontier-ui" in text
    assert "FRONTIER_AUTH_OIDC_JWKS_URL=http://127.0.0.1:8081/.well-known/jwks.json" in text
    assert "FRONTIER_AUTH_OIDC_CLIENT_ID=frontier-web" in text
    assert (
        "FRONTIER_AUTH_OIDC_AUTHORIZATION_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    )
    assert "FRONTIER_AUTH_OIDC_TOKEN_URL=http://127.0.0.1:8081/api/login/oauth/access_token" in text


def test_installer_writes_casdoor_preset_when_selected(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="oidc",
        oidc_provider_template="casdoor",
        oidc_issuer="http://127.0.0.1:8081",
        oidc_audience="frontier-ui",
        oidc_jwks_url="http://127.0.0.1:8081/.well-known/jwks.json",
        oidc_client_id="frontier-web",
        oidc_authorization_url="http://127.0.0.1:8081/login/oauth/authorize",
        oidc_token_url="http://127.0.0.1:8081/api/login/oauth/access_token",
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)
    env_path = installer._write_env_file(answers, secrets_map)
    text = env_path.read_text(encoding="utf-8")

    assert "FRONTIER_API_BEARER_TOKEN=" in text
    assert "FRONTIER_AUTH_MODE=oidc" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=casdoor" in text
    assert "FRONTIER_AUTH_OIDC_ISSUER=http://127.0.0.1:8081" in text
    assert "FRONTIER_AUTH_OIDC_AUDIENCE=frontier-ui" in text
    assert "FRONTIER_AUTH_OIDC_JWKS_URL=http://127.0.0.1:8081/.well-known/jwks.json" in text
    assert "FRONTIER_AUTH_OIDC_CLIENT_ID=frontier-web" in text
    assert (
        "FRONTIER_AUTH_OIDC_AUTHORIZATION_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    )
    assert "FRONTIER_AUTH_OIDC_TOKEN_URL=http://127.0.0.1:8081/api/login/oauth/access_token" in text
    assert "FRONTIER_AUTH_OIDC_SIGNIN_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    assert "FRONTIER_AUTH_OIDC_SIGNUP_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    assert "FRONTIER_AUTH_OIDC_SCOPES=openid profile email" in text
    assert "FRONTIER_API_BEARER_TOKEN" not in secrets_map


def test_installer_treats_legacy_casdoor_mode_as_oidc(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="casdoor",
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)
    env_path = installer._write_env_file(answers, secrets_map)
    text = env_path.read_text(encoding="utf-8")

    assert "FRONTIER_AUTH_MODE=oidc" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=casdoor" in text
    assert "FRONTIER_AUTH_OIDC_SIGNIN_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    assert "FRONTIER_AUTH_OIDC_SIGNUP_URL=http://127.0.0.1:8081/login/oauth/authorize" in text
    assert "FRONTIER_API_BEARER_TOKEN" not in secrets_map


def test_installer_supports_external_oidc_provider(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="oidc",
        oidc_provider_template="external",
        oidc_issuer="https://login.example.com/realms/frontier",
        oidc_audience="frontier-api",
        oidc_jwks_url="https://login.example.com/realms/frontier/protocol/openid-connect/certs",
        oidc_client_id="frontier-ui",
        oidc_authorization_url="https://login.example.com/realms/frontier/protocol/openid-connect/auth",
        oidc_token_url="https://login.example.com/realms/frontier/protocol/openid-connect/token",
        oidc_signin_url="https://login.example.com/realms/frontier/protocol/openid-connect/auth?prompt=login",
        oidc_signup_url="https://login.example.com/realms/frontier/registrations/start",
        oidc_scopes=["openid", "profile", "email", "groups"],
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)
    env_path = installer._write_env_file(answers, secrets_map)
    text = env_path.read_text(encoding="utf-8")

    assert "FRONTIER_AUTH_MODE=oidc" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=oidc" in text
    assert "FRONTIER_AUTH_OIDC_ISSUER=https://login.example.com/realms/frontier" in text
    assert "FRONTIER_AUTH_OIDC_AUDIENCE=frontier-api" in text
    assert "FRONTIER_AUTH_OIDC_CLIENT_ID=frontier-ui" in text
    assert (
        "FRONTIER_AUTH_OIDC_SIGNIN_URL=https://login.example.com/realms/frontier/protocol/openid-connect/auth?prompt=login"
        in text
    )
    assert (
        "FRONTIER_AUTH_OIDC_SIGNUP_URL=https://login.example.com/realms/frontier/registrations/start"
        in text
    )
    assert "FRONTIER_AUTH_OIDC_SCOPES=openid profile email groups" in text
    assert "FRONTIER_API_BEARER_TOKEN" not in secrets_map


def test_installer_rejects_oidc_urls_with_embedded_credentials(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="oidc",
        oidc_provider_template="external",
        oidc_issuer="https://user:pass@login.example.com/realms/frontier",
        oidc_audience="frontier-api",
        oidc_jwks_url="https://login.example.com/realms/frontier/protocol/openid-connect/certs",
        oidc_client_id="frontier-ui",
        oidc_authorization_url="https://login.example.com/realms/frontier/protocol/openid-connect/auth",
        oidc_token_url="https://login.example.com/realms/frontier/protocol/openid-connect/token",
    )

    with pytest.raises(ValueError, match="must not embed credentials"):
        installer._write_env_file(
            answers,
            {
                "A2A_JWT_SECRET": secrets.token_urlsafe(32),
                "POSTGRES_PASSWORD": "db-secret",
                "NEO4J_PASSWORD": "neo-secret",
            },
        )


def test_installer_shared_token_mode_still_generates_bearer_secret(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="local",
        local_auth_provider="shared-token",
    )
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    secrets_map = installer._collect_local_secrets(answers)
    env_path = installer._write_env_file(answers, secrets_map)
    text = env_path.read_text(encoding="utf-8")

    assert secrets_map["FRONTIER_API_BEARER_TOKEN"]
    assert "FRONTIER_AUTH_MODE=shared-token" in text
    assert "FRONTIER_API_BEARER_TOKEN=" in text
    assert "FRONTIER_AUTH_OIDC_PROVIDER=" in text


def test_installer_does_not_collect_local_secret_for_enterprise_only(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(installation_root=str(tmp_path), deployment_mode="enterprise")

    secrets_map = installer._collect_local_secrets(answers)

    assert secrets_map == {}


def test_installer_writes_helm_values(tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = InstallerAnswers(
        installation_root=str(tmp_path),
        deployment_mode="enterprise",
        federation_enabled=True,
        federation_cluster_name="cluster-a",
        federation_region="us-west",
        federation_peers=["https://peer-a.example.com", "https://peer-b.example.com"],
    )
    values_path = installer._write_generated_helm_values(answers)
    text = values_path.read_text(encoding="utf-8")
    assert "clusterName: cluster-a" in text
    assert "https://peer-a.example.com" in text


def test_resolve_missing_prerequisites_attempts_install_and_continues(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="helm",
        display_name="Helm",
        check_name="command:helm",
        install_commands=(("winget", "install", "Helm.Helm"),),
        manual_steps=("Install Helm manually.",),
    )
    missing = [
        MissingPrerequisite(
            definition=definition,
            result=DiagnosticResult(name="command:helm", ok=False, message="missing from PATH"),
        )
    ]

    monkeypatch.setattr(installer, "_ask_yes_no", lambda prompt, default: True)
    monkeypatch.setattr(
        installer,
        "_run_install_command",
        lambda command: subprocess.CompletedProcess(command, 0, stdout="installed", stderr=""),
    )
    monkeypatch.setattr(
        installer,
        "_run_prerequisite_check",
        lambda definition: DiagnosticResult(name=definition.check_name, ok=True, message="found"),
    )

    installer._resolve_missing_prerequisites(missing, intro="Need Helm")


def test_resolve_missing_prerequisites_decline_exits_with_actionable_message(
    monkeypatch, tmp_path: Path
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="kubectl",
        display_name="kubectl",
        check_name="command:kubectl",
        install_commands=(("winget", "install", "Kubernetes.kubectl"),),
        manual_steps=("Install kubectl manually.",),
    )
    missing = [
        MissingPrerequisite(
            definition=definition,
            result=DiagnosticResult(name="command:kubectl", ok=False, message="missing from PATH"),
        )
    ]

    monkeypatch.setattr(installer, "_ask_yes_no", lambda prompt, default: False)

    with pytest.raises(SystemExit) as excinfo:
        installer._resolve_missing_prerequisites(missing, intro="Need kubectl")

    message = str(excinfo.value)
    assert "Outstanding prerequisites" in message
    assert "kubectl" in message
    assert "winget install Kubernetes.kubectl" in message


def test_attempt_missing_prerequisites_reports_failed_install(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="docker",
        display_name="Docker",
        check_name="command:docker",
        install_commands=(("winget", "install", "Docker.DockerDesktop"),),
        manual_steps=("Install Docker manually.",),
    )
    missing = [
        MissingPrerequisite(
            definition=definition,
            result=DiagnosticResult(name="command:docker", ok=False, message="missing from PATH"),
        )
    ]

    monkeypatch.setattr(
        installer,
        "_run_install_command",
        lambda command: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="installer failed"
        ),
    )

    unresolved = installer._attempt_missing_prerequisite_installs(missing)

    assert len(unresolved) == 1
    assert unresolved[0].result.message == "installer failed"


def test_installer_yes_no_accepts_default_and_explicit_answers(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = iter(["", "n", "yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert installer._ask_yes_no("Proceed?", True) is True
    assert installer._ask_yes_no("Proceed?", True) is False
    assert installer._ask_yes_no("Proceed?", False) is True


def test_installer_yes_no_reprompts_on_invalid_input(
    monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    answers = iter(["maybe", "Y"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert installer._ask_yes_no("Proceed?", False) is True
    captured = capsys.readouterr()
    assert "Please answer yes or no." in captured.out


def test_run_prerequisite_check_validates_named_commands(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="helm",
        display_name="Helm",
        check_name="command:helm",
    )
    monkeypatch.setattr(
        "shutil.which", lambda command: "C:/Tools/helm.exe" if command == "helm" else None
    )

    result = installer._run_prerequisite_check(definition)

    assert result.ok is True
    assert result.name == "command:helm"


def test_run_prerequisite_check_reports_missing_commands(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="kubectl",
        display_name="kubectl",
        check_name="command:kubectl",
    )
    monkeypatch.setattr("shutil.which", lambda command: None)

    result = installer._run_prerequisite_check(definition)

    assert result.ok is False
    assert "kubectl is missing from PATH" == result.message


def test_attempt_missing_prerequisites_rechecks_after_install(monkeypatch, tmp_path: Path) -> None:
    installer = FrontierInstaller(repo_root=tmp_path)
    definition = PrerequisiteDefinition(
        key="helm",
        display_name="Helm",
        check_name="command:helm",
        install_commands=(("winget", "install", "Helm.Helm"),),
    )
    missing = [
        MissingPrerequisite(
            definition=definition,
            result=DiagnosticResult(name="command:helm", ok=False, message="missing from PATH"),
        )
    ]
    monkeypatch.setattr(
        installer,
        "_run_install_command",
        lambda command: subprocess.CompletedProcess(command, 0, stdout="installed", stderr=""),
    )
    monkeypatch.setattr(
        installer,
        "_run_prerequisite_check",
        lambda definition: DiagnosticResult(
            name=definition.check_name, ok=False, message="still missing after install"
        ),
    )

    unresolved = installer._attempt_missing_prerequisite_installs(missing)

    assert len(unresolved) == 1
    assert unresolved[0].result.message == "still missing after install"
