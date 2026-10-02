from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
import secrets
import site
import socket
import subprocess
import sys
import sysconfig
import tomllib
from collections import OrderedDict
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from urllib.parse import urlparse

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FRONTIER_APP_HOME_ENV = "FRONTIER_APP_HOME"
DEFAULT_LOCAL_STACK_HOST = "xfrontier.local"
DEFAULT_ARCHIVE_URL = "https://github.com/LATTIX-IO/lattix-xfrontier/archive/refs/heads/main.zip"
INSTALLER_STATE_SCHEMA_VERSION = 1


def _normalized_gateway_bind_host(value: str | None) -> str:
    host = str(value or "").strip()
    if not host or host == "0.0.0.0":
        return "127.0.0.1"
    return host


def _normalized_gateway_http_port(value: str | None) -> str:
    port = str(value or "").strip()
    return port or "80"


def _http_authority(host: str, port: str) -> str:
    normalized_port = _normalized_gateway_http_port(port)
    return host if normalized_port == "80" else f"{host}:{normalized_port}"


def _default_secure_frontend_origin(env_map: OrderedDict[str, str]) -> str:
    host = (
        str(env_map.get("LOCAL_STACK_HOST") or DEFAULT_LOCAL_STACK_HOST).strip()
        or DEFAULT_LOCAL_STACK_HOST
    )
    port = _normalized_gateway_http_port(env_map.get("LOCAL_GATEWAY_HTTP_PORT"))
    return f"http://{_http_authority(host, port)}"


def _default_secure_local_api_base_url(env_map: OrderedDict[str, str]) -> str:
    host = _normalized_gateway_bind_host(env_map.get("LOCAL_GATEWAY_BIND_HOST"))
    port = _normalized_gateway_http_port(env_map.get("LOCAL_GATEWAY_HTTP_PORT"))
    authority = _http_authority(host, port)
    return f"http://{authority}/api"


def source_repo_root() -> Path:
    return PACKAGE_ROOT


def default_app_home() -> Path:
    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        base = Path(os.getenv("LOCALAPPDATA") or (home / "AppData" / "Local"))
        return base / "Lattix" / "xFrontier"
    if system == "darwin":
        return home / "Library" / "Application Support" / "Lattix" / "xFrontier"
    xdg_data_home = str(os.getenv("XDG_DATA_HOME") or "").strip()
    if xdg_data_home:
        return Path(xdg_data_home) / "lattix" / "xfrontier"
    return home / ".local" / "share" / "lattix" / "xfrontier"


def _looks_like_repo_root(candidate: Path) -> bool:
    return all(
        [
            (candidate / "pyproject.toml").exists(),
            (candidate / "frontier_tooling").exists(),
            (candidate / "docker-compose.yml").exists(),
        ]
    )


def repo_root() -> Path:
    configured = str(os.getenv(FRONTIER_APP_HOME_ENV) or "").strip()
    if configured:
        candidate = Path(configured).expanduser().resolve(strict=False)
        if _looks_like_repo_root(candidate):
            return candidate
    for candidate in (PACKAGE_ROOT, default_app_home()):
        resolved = candidate.expanduser().resolve(strict=False)
        if _looks_like_repo_root(resolved):
            return resolved
    return PACKAGE_ROOT


def python_executable() -> str:
    return sys.executable


def user_scripts_dir() -> Path:
    scheme = sysconfig.get_preferred_scheme("user")
    scripts_path = sysconfig.get_path("scripts", scheme=scheme)
    if scripts_path:
        return Path(scripts_path)
    return Path(site.getuserbase()) / ("Scripts" if os.name == "nt" else "bin")


def python_scripts_dir() -> Path:
    scripts_path = sysconfig.get_path("scripts")
    if scripts_path:
        return Path(scripts_path)
    return Path(sys.executable).resolve().parent


def cli_executable(command_name: str = "lattix", *, scripts_dir: Path | None = None) -> Path:
    resolved_scripts_dir = scripts_dir or user_scripts_dir()
    suffix = ".exe" if os.name == "nt" else ""
    return resolved_scripts_dir / f"{command_name}{suffix}"


def installer_dir(*, root: Path | None = None) -> Path:
    return (root or repo_root()) / ".installer"


def secure_installer_env_path(*, root: Path | None = None) -> Path:
    return installer_dir(root=root) / "local-secure.env"


def lightweight_installer_env_path(*, root: Path | None = None) -> Path:
    return installer_dir(root=root) / "local-lightweight.env"


def installer_state_manifest_path(*, root: Path | None = None) -> Path:
    return installer_dir(root=root) / "state-manifest.json"


def installer_vault_bootstrap_path(*, root: Path | None = None) -> Path:
    return installer_dir(root=root) / "vault-bootstrap.json"


def _read_env_map(path: Path) -> OrderedDict[str, str]:
    env_map: OrderedDict[str, str] = OrderedDict()
    if not path.exists():
        return env_map
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key] = value
    return env_map


def _write_env_map(path: Path, env_map: OrderedDict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(f"{key}={value}" for key, value in env_map.items()) + "\n"
    path.write_text(payload, encoding="utf-8")
    return path


def _random_secret() -> str:
    return base64.b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")


def _normalize_a2a_audience(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text == "agents":
        return "frontier-runtime"
    return text


def _installer_env_path(*, local_profile: bool, root: Path | None = None) -> Path:
    return (
        lightweight_installer_env_path(root=root)
        if local_profile
        else secure_installer_env_path(root=root)
    )


def installer_artifact_paths(*, root: Path | None = None) -> list[Path]:
    base = installer_dir(root=root)
    return [
        secure_installer_env_path(root=root),
        lightweight_installer_env_path(root=root),
        base / "local.env",
        base / "generated-values.yaml",
        installer_state_manifest_path(root=root),
        installer_vault_bootstrap_path(root=root),
    ]


def _project_version(*, root: Path | None = None) -> str:
    pyproject_path = (root or repo_root()) / "pyproject.toml"
    if not pyproject_path.exists():
        return "0.0.0"
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return "0.0.0"
    project = pyproject.get("project")
    if not isinstance(project, dict):
        return "0.0.0"
    version = str(project.get("version") or "").strip()
    return version or "0.0.0"


def _installer_installation_id(*, root: Path | None = None) -> str:
    resolved_root = (root or repo_root()).resolve(strict=False)
    digest = hashlib.sha256(str(resolved_root).encode("utf-8")).hexdigest()
    return digest[:16]


def installer_vault_secret_path(*, root: Path | None = None) -> str:
    installation_id = _installer_installation_id(root=root)
    return f"secret/data/local/frontier/installations/{installation_id}/secrets"


def installer_vault_state_path(*, root: Path | None = None) -> str:
    installation_id = _installer_installation_id(root=root)
    return f"secret/data/local/frontier/installations/{installation_id}/installer-state"


def read_installer_state_manifest(*, root: Path | None = None) -> dict[str, Any]:
    path = installer_state_manifest_path(root=root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalized_installer_state_schema_version(manifest: dict[str, Any]) -> int:
    raw_value = manifest.get("schema_version") if isinstance(manifest, dict) else None
    if raw_value is None:
        return 0
    try:
        version = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return 0
    return version if version >= 0 else 0


def _installer_profiles(*, root: Path | None = None) -> list[str]:
    profiles: list[str] = []
    if secure_installer_env_path(root=root).exists():
        profiles.append("secure")
    if lightweight_installer_env_path(root=root).exists():
        profiles.append("lightweight")
    return profiles


def _in_app_asset_roots(*, root: Path | None = None) -> list[str]:
    resolved_root = (root or repo_root()).resolve(strict=False)
    candidate_maps = [
        _read_env_map(resolved_root / ".env"),
        _read_env_map(secure_installer_env_path(root=resolved_root)),
        _read_env_map(lightweight_installer_env_path(root=resolved_root)),
    ]
    preserved: list[str] = []
    seen: set[str] = set()
    for env_map in candidate_maps:
        configured = str(env_map.get("FRONTIER_AGENT_ASSETS_ROOT") or "").strip()
        if not configured:
            continue
        raw_path = Path(configured).expanduser()
        candidate = raw_path if raw_path.is_absolute() else resolved_root / raw_path
        resolved_candidate = candidate.resolve(strict=False)
        try:
            relative = resolved_candidate.relative_to(resolved_root)
        except ValueError:
            continue
        relative_text = str(relative).replace("\\", "/")
        key = relative_text.casefold()
        if key in seen:
            continue
        preserved.append(relative_text)
        seen.add(key)
    return preserved


def _installer_state_payload(
    *, root: Path | None = None, install_mode: str | None = None
) -> OrderedDict[str, Any]:
    resolved_root = (root or repo_root()).resolve(strict=False)
    secure_env = _read_env_map(secure_installer_env_path(root=resolved_root))
    return OrderedDict(
        [
            ("schema_version", INSTALLER_STATE_SCHEMA_VERSION),
            ("installation_id", _installer_installation_id(root=resolved_root)),
            ("package_version", _project_version(root=resolved_root)),
            (
                "install_mode",
                str(install_mode or ("editable" if (resolved_root / ".git").exists() else "wheel")),
            ),
            ("install_root", str(resolved_root)),
            ("updated_at", datetime.now(timezone.utc).isoformat()),
            ("profiles", _installer_profiles(root=resolved_root)),
            ("auth_mode", str(secure_env.get("FRONTIER_AUTH_MODE") or "").strip()),
            ("local_stack_host", str(secure_env.get("LOCAL_STACK_HOST") or "").strip()),
            ("in_app_asset_roots", _in_app_asset_roots(root=resolved_root)),
            (
                "vault_bootstrap_file",
                str(
                    installer_vault_bootstrap_path(root=resolved_root).relative_to(resolved_root)
                ).replace("\\", "/"),
            ),
            ("vault_secret_path", installer_vault_secret_path(root=resolved_root)),
            ("vault_state_path", installer_vault_state_path(root=resolved_root)),
            (
                "managed_artifacts",
                [
                    str(path.relative_to(resolved_root)).replace("\\", "/")
                    for path in installer_artifact_paths(root=resolved_root)
                ],
            ),
        ]
    )


def _merged_in_app_asset_roots(
    current_manifest: dict[str, Any], discovered_roots: list[str]
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    raw_current_roots = (
        current_manifest.get("in_app_asset_roots") if isinstance(current_manifest, dict) else None
    )
    current_roots: list[str] = (
        [str(item) for item in raw_current_roots] if isinstance(raw_current_roots, list) else []
    )
    for item in [*current_roots, *discovered_roots]:
        normalized = str(item or "").strip().replace("\\", "/")
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        merged.append(normalized)
        seen.add(key)
    return merged


def ensure_installer_state_manifest(
    *, root: Path | None = None, install_mode: str | None = None
) -> Path:
    resolved_root = (root or repo_root()).resolve(strict=False)
    current_manifest = read_installer_state_manifest(root=resolved_root)
    current_version = _normalized_installer_state_schema_version(current_manifest)
    payload = _installer_state_payload(root=resolved_root, install_mode=install_mode)
    discovered_roots = (
        payload["in_app_asset_roots"] if isinstance(payload.get("in_app_asset_roots"), list) else []
    )
    payload["in_app_asset_roots"] = _merged_in_app_asset_roots(
        current_manifest,
        [str(item) for item in discovered_roots],
    )

    if current_version > INSTALLER_STATE_SCHEMA_VERSION:
        payload["schema_version"] = current_version

    path = installer_state_manifest_path(root=resolved_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_installer_state_manifest(
    *, root: Path | None = None, install_mode: str | None = None
) -> Path:
    return ensure_installer_state_manifest(root=root, install_mode=install_mode)


def ensure_compose_env_file(*, local_profile: bool = False, root: Path | None = None) -> Path:
    env_map: OrderedDict[str, str] = OrderedDict()
    resolved_root = root or repo_root()
    installer_env_path = _installer_env_path(local_profile=local_profile, root=resolved_root)
    for source in (resolved_root / ".env.example", resolved_root / ".env", installer_env_path):
        for key, value in _read_env_map(source).items():
            env_map[key] = value

    if not str(env_map.get("A2A_JWT_SECRET") or "").strip():
        env_map["A2A_JWT_SECRET"] = _random_secret()
    if not str(env_map.get("FRONTIER_SECRETS_ENCRYPTION_KEY") or "").strip():
        env_map["FRONTIER_SECRETS_ENCRYPTION_KEY"] = (
            str(env_map.get("A2A_JWT_SECRET") or "").strip() or _random_secret()
        )
    env_map.setdefault("A2A_JWT_ALG", "HS256")
    env_map.setdefault("A2A_JWT_ISS", "lattix-frontier")
    env_map["A2A_JWT_AUD"] = _normalize_a2a_audience(env_map.get("A2A_JWT_AUD"))
    env_map["A2A_TRUSTED_SUBJECTS"] = "backend,orchestrator,research,code,review,coordinator"
    env_map.setdefault("LOCAL_STACK_HOST", DEFAULT_LOCAL_STACK_HOST)
    if local_profile:
        env_map["FRONTIER_RUNTIME_PROFILE"] = "local-lightweight"
        env_map["NEXT_PUBLIC_API_BASE_URL"] = "http://localhost:8000"
        env_map["FRONTEND_ORIGIN"] = "http://localhost:3000"
        env_map["FRONTIER_LOCAL_API_BASE_URL"] = "http://localhost:8000"
    else:
        env_map["FRONTIER_RUNTIME_PROFILE"] = "local-secure"
        env_map["FRONTIER_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR"] = "true"
        env_map["NEXT_PUBLIC_API_BASE_URL"] = "/api"
        env_map["FRONTEND_ORIGIN"] = _default_secure_frontend_origin(env_map)
        secure_api_base = _default_secure_local_api_base_url(env_map)
        configured_api_base = str(env_map.get("FRONTIER_LOCAL_API_BASE_URL") or "").strip()
        if not configured_api_base or configured_api_base == "http://localhost:8000":
            env_map["FRONTIER_LOCAL_API_BASE_URL"] = secure_api_base
    return _write_env_map(installer_env_path, env_map)


def configured_local_api_base_url(*, root: Path | None = None) -> str:
    resolved_root = root or repo_root()
    secure_env_path = secure_installer_env_path(root=resolved_root)
    lightweight_env_path = lightweight_installer_env_path(root=resolved_root)

    if secure_env_path.exists() or not lightweight_env_path.exists():
        env_map = _read_env_map(ensure_compose_env_file(local_profile=False, root=resolved_root))
        default_base = _default_secure_local_api_base_url(env_map)
    else:
        env_map = _read_env_map(ensure_compose_env_file(local_profile=True, root=resolved_root))
        default_base = "http://localhost:8000"

    configured = str(env_map.get("FRONTIER_LOCAL_API_BASE_URL") or "").strip()
    return (configured or default_base).rstrip("/")


def configured_local_api_headers(*, root: Path | None = None) -> dict[str, str]:
    resolved_root = root or repo_root()
    secure_env_path = secure_installer_env_path(root=resolved_root)
    lightweight_env_path = lightweight_installer_env_path(root=resolved_root)

    if secure_env_path.exists() or not lightweight_env_path.exists():
        env_map = _read_env_map(ensure_compose_env_file(local_profile=False, root=resolved_root))
        host = str(env_map.get("LOCAL_STACK_HOST") or "").strip()
        return {"Host": host} if host else {}
    return {}


def configured_local_api_url(path: str, *, root: Path | None = None) -> str:
    return f"{configured_local_api_base_url(root=root)}/{path.lstrip('/')}"


def compose_prefix(*, local: bool, root: Path | None = None) -> list[str]:
    env_path = ensure_compose_env_file(local_profile=local, root=root)
    base = ["docker", "compose", "--env-file", str(env_path)]
    if local:
        base.extend(["-f", "docker-compose.local.yml"])
    return base


def existing_compose_prefix(*, local: bool, root: Path | None = None) -> list[str] | None:
    env_path = _installer_env_path(local_profile=local, root=root)
    if not env_path.exists():
        return None
    base = ["docker", "compose", "--env-file", str(env_path)]
    if local:
        base.extend(["-f", "docker-compose.local.yml"])
    return base


def remove_installer_env_files(*, root: Path | None = None) -> list[Path]:
    removed: list[Path] = []
    for path in (secure_installer_env_path(root=root), lightweight_installer_env_path(root=root)):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def remove_installer_artifacts(*, root: Path | None = None) -> list[Path]:
    removed: list[Path] = []
    seen: set[Path] = set()
    for path in installer_artifact_paths(root=root):
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            path.unlink()
            removed.append(path)
    installer_root = installer_dir(root=root)
    if installer_root.exists():
        try:
            installer_root.rmdir()
        except OSError:
            pass
    return removed


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(args, cwd=str(cwd or repo_root()), check=check, env=env)


def _validated_http_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Only HTTP and HTTPS URLs are supported")
    if not parsed.hostname:
        raise ValueError("A host is required")
    if parsed.username or parsed.password:
        raise ValueError("Embedded URL credentials are not supported")
    if parsed.fragment:
        raise ValueError("URL fragments are not supported")
    return parsed.geturl()


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: int = 10,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    import httpx

    headers = {"Accept": "application/json"}
    if extra_headers:
        headers.update(
            {key: value for key, value in extra_headers.items() if str(value or "").strip()}
        )
    bearer = str(os.getenv("FRONTIER_API_BEARER_TOKEN", "") or "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    response = httpx.request(
        method,
        _validated_http_url(url),
        content=data,
        headers=headers,
        timeout=float(timeout),
        follow_redirects=False,
    )
    response.raise_for_status()
    raw = response.text
    return json.loads(raw) if raw else None


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=False))  # noqa: T201


def _detect_primary_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            address = str(sock.getsockname()[0]).strip()
            if address:
                return address
    except OSError:
        pass
    try:
        address = str(socket.gethostbyname(socket.gethostname())).strip()
    except OSError:
        return None
    return address or None


def portal_urls(*, root: Path | None = None) -> list[str]:
    env_map = _read_env_map(ensure_compose_env_file(local_profile=False, root=root))
    host = (
        str(env_map.get("LOCAL_STACK_HOST") or DEFAULT_LOCAL_STACK_HOST).strip()
        or DEFAULT_LOCAL_STACK_HOST
    )
    port = _normalized_gateway_http_port(env_map.get("LOCAL_GATEWAY_HTTP_PORT"))
    bind_host = _normalized_gateway_bind_host(env_map.get("LOCAL_GATEWAY_BIND_HOST"))
    urls = [f"http://{_http_authority(bind_host, port)}", f"http://{_http_authority(host, port)}"]
    lan_ip = _detect_primary_ipv4()
    if lan_ip and lan_ip != "127.0.0.1":
        urls.append(f"http://{_http_authority(lan_ip, port)}")
    deduped: list[str] = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def detect_sandbox_backend() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "docker-desktop-vm"
    if system == "darwin":
        return "docker-desktop-vm"
    if system == "linux":
        return "docker"
    return "unknown"


def agent_asset_roots() -> list[Path]:
    configured = str(os.getenv("FRONTIER_AGENT_ASSETS_ROOT", "") or "").strip()
    resolved_root = repo_root()
    roots: list[Path] = [(resolved_root / "examples" / "agents").resolve()]
    if configured:
        configured_path = Path(configured)
        if not configured_path.is_absolute():
            configured_path = (resolved_root / configured_path).resolve()
        roots.append(configured_path)
    deduped: list[Path] = []
    seen: set[str] = set()
    for item in roots:
        key = str(item)
        if key not in seen:
            deduped.append(item)
            seen.add(key)
    return deduped


def discover_agent_records() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in agent_asset_roots():
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            agent_id = child.name
            if agent_id in seen:
                continue
            records.append(
                {
                    "id": agent_id,
                    "name": agent_id.replace("-", " ").title(),
                    "path": str(child),
                    "source": str(root),
                }
            )
            seen.add(agent_id)
    return records


def resolve_opa_command() -> str:
    local_opa = repo_root() / ".tools" / "opa" / ("opa.exe" if os.name == "nt" else "opa")
    if local_opa.exists():
        return str(local_opa)
    return "opa"
