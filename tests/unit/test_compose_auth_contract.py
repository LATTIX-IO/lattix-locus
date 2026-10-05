from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_secure_local_compose_includes_casdoor_service_and_oidc_envs() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "casdoor:" in compose
    assert "casbin/casdoor-all-in-one" in compose
    assert (
        "LOCUS_AUTH_OIDC_ISSUER: ${LOCUS_AUTH_OIDC_ISSUER:-http://127.0.0.1:8081}" in compose
    )
    assert (
        "LOCUS_ADMIN_ACTORS: ${LOCUS_ADMIN_ACTORS:-locus-admin,admin@locus.localhost}"
        in compose
    )
    assert (
        "LOCUS_BUILDER_ACTORS: ${LOCUS_BUILDER_ACTORS:-locus-admin,admin@locus.localhost}"
        in compose
    )


def test_secure_local_compose_casdoor_uses_postgres_by_default() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    casdoor_script = (REPO_ROOT / "docker" / "casdoor" / "start-casdoor.sh").read_text(
        encoding="utf-8"
    )

    assert "entrypoint:" in compose
    assert "tr -d" in compose
    assert "/docker/casdoor/start-casdoor.sh > /tmp/start-casdoor.sh" in compose
    assert "exec /bin/bash /tmp/start-casdoor.sh" in compose
    assert "./docker/casdoor/start-casdoor.sh:/docker/casdoor/start-casdoor.sh:ro" in compose
    assert "CASDOOR_POSTGRES_HOST: ${CASDOOR_POSTGRES_HOST:-postgres}" in compose
    assert "CASDOOR_POSTGRES_USER: ${CASDOOR_POSTGRES_USER:-${POSTGRES_USER:-frontier}}" in compose
    assert "CASDOOR_POSTGRES_DB: ${CASDOOR_POSTGRES_DB:-${POSTGRES_DB:-frontier}}" in compose
    assert "CASDOOR_PUBLIC_URL: ${CASDOOR_PUBLIC_URL:-http://127.0.0.1:8081}" in compose
    assert 'ports: ["${CASDOOR_BIND_HOST:-127.0.0.1}:${CASDOOR_HTTP_PORT:-8081}:8000"]' in compose
    assert "condition: service_healthy" in compose
    assert "driverName = postgres" in casdoor_script
    assert "dbname=${POSTGRES_DB}" in casdoor_script
    assert "exec /server --createDatabase=true" in casdoor_script


def test_secure_local_compose_vault_avoids_double_loading_config() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'command: ["server"]' in compose
    assert 'command: ["server", "-config=/vault/config/vault.hcl"]' not in compose


def test_local_gateway_routes_casdoor_host() -> None:
    caddyfile = (REPO_ROOT / "docker" / "local" / "Caddyfile").read_text(encoding="utf-8")

    assert "http://{$LOCAL_STACK_HOST:locus.local}" in caddyfile
    assert "http://{$CASDOOR_LOCAL_HOST:casdoor.localhost}" in caddyfile
    assert "reverse_proxy casdoor:8000" in caddyfile
