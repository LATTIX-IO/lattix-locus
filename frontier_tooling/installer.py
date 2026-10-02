from __future__ import annotations

import base64
from datetime import datetime, timezone
import os
from pathlib import Path
import platform
import json
import http.client
import re
import shutil
import ssl
import stat
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
import http.cookiejar
from urllib.parse import urlsplit, urlunsplit

from frontier_runtime.install import FrontierInstaller, InstallerAnswers

from .common import (
    DEFAULT_ARCHIVE_URL,
    FRONTIER_APP_HOME_ENV,
    cli_executable,
    compose_prefix,
    default_app_home,
    ensure_compose_env_file,
    ensure_installer_state_manifest,
    installer_vault_bootstrap_path,
    installer_vault_secret_path,
    installer_vault_state_path,
    portal_urls,
    print_json,
    python_executable,
    read_installer_state_manifest,
    run_command,
    source_repo_root,
)


CASDOOR_BOOTSTRAP_MAX_ATTEMPTS = 90
CASDOOR_BOOTSTRAP_RETRY_DELAY_SECONDS = 2
_LOCAL_GATEWAY_PORT_FALLBACKS = (8080, 8081, 8088, 8888)
_INSTALLER_VAULT_BOOTSTRAP_SCHEMA_VERSION = 1
_MANAGED_VENV_DIRNAME = ".venv"
_SECURE_STACK_BUILD_SERVICES = ("frontend", "backend", "agent-research")
_INSTALLER_VAULT_SECRET_KEY_FRAGMENTS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "API_KEY",
    "PRIVATE_KEY",
    "CLIENT_SECRET",
)
_INSTALLER_VAULT_IGNORED_SECRET_KEYS = {"VAULT_TOKEN"}


def bootstrap_url() -> str:
    return DEFAULT_ARCHIVE_URL


def _display_mode() -> str:
    configured = str(os.getenv("FRONTIER_INSTALLER_OUTPUT") or "").strip().lower()
    if configured in {"json", "tui"}:
        return configured
    return "tui" if sys.stdout.isatty() or sys.stdin.isatty() else "json"


def _friendly_install_mode(mode: str) -> str:
    return "Source checkout" if mode == "editable" else "Published install"


def _render_box(title: str, lines: list[str]) -> str:
    width = max(72, min(shutil.get_terminal_size(fallback=(100, 24)).columns, 120)) - 4
    wrapped_lines: list[str] = []
    for line in lines:
        if not line:
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            textwrap.wrap(line, width=width, replace_whitespace=False, drop_whitespace=False)
            or [""]
        )
    content = [title, *wrapped_lines]
    top = f"╔{'═' * (width + 2)}╗"
    body = [f"║ {line.ljust(width)} ║" for line in content]
    bottom = f"╚{'═' * (width + 2)}╝"
    return "\n".join([top, *body, bottom])


def _render_install_summary(payload: dict[str, Any]) -> str:
    raw_path_info = payload.get("path")
    path_info: dict[str, Any] = raw_path_info if isinstance(raw_path_info, dict) else {}

    raw_urls = payload.get("urls")
    urls: list[str] = [str(item) for item in raw_urls] if isinstance(raw_urls, list) else []

    raw_next_steps = payload.get("next_steps")
    next_steps: list[str] = (
        [str(item) for item in raw_next_steps] if isinstance(raw_next_steps, list) else []
    )

    raw_bootstrap_login = payload.get("bootstrap_login")
    bootstrap_login: dict[str, Any] = (
        raw_bootstrap_login if isinstance(raw_bootstrap_login, dict) else {}
    )

    raw_path_locations = path_info.get("locations")
    path_locations: list[str] = (
        [str(item) for item in raw_path_locations] if isinstance(raw_path_locations, list) else []
    )
    password_status = "Password not recorded in installer output"
    if bootstrap_login:
        if bootstrap_login.get("password_generated"):
            password_status = "Generated during install and stored securely"
        else:
            password_status = "Uses the password entered during install"

    lines = [
        f"Status      : {'Ready' if payload.get('installed') else 'Not installed'}",
        f"Mode        : {_friendly_install_mode(str(payload.get('install_mode') or 'unknown'))}",
        f"Isolation   : {payload.get('security_posture')}",
        f"Auth mode   : {payload.get('auth_mode')}",
        f"App home    : {payload.get('repo_root')}",
        f"Env file    : {payload.get('compose_env')}",
        f"Runtime     : {payload.get('managed_runtime')}",
        f"CLI path    : {path_info.get('cli_path')}",
        f"Scripts dir : {path_info.get('scripts_dir')}",
        f"PATH update : {'Applied' if path_info.get('updated') else 'Already present'}",
    ]
    if payload.get("vault_secret_path"):
        lines.append(f"Vault secret: {payload.get('vault_secret_path')}")
    if payload.get("vault_state_path"):
        lines.append(f"Vault state : {payload.get('vault_state_path')}")
    if path_locations:
        lines.append(f"PATH scope  : {', '.join(str(item) for item in path_locations)}")

    if bootstrap_login.get("username"):
        lines.append("")
        lines.append("Bootstrap login")
        lines.append(f"  User      : {bootstrap_login.get('username')}")
        lines.append(f"  Email     : {bootstrap_login.get('email')}")
        lines.append(f"  Name      : {bootstrap_login.get('display_name')}")
        lines.append(f"  Password  : {password_status}")

    lines.append("")
    lines.append("Portal URLs")
    for index, url in enumerate(urls, start=1):
        lines.append(f"  [{index}] {url}")

    lines.append("")
    lines.append("Next steps")
    for step in next_steps:
        lines.append(f"  • {step}")
    lines.append("  • If PATH was updated, open a new terminal before running `lattix` manually.")

    lines.append("")
    lines.append(f"Install src : {payload.get('bootstrap_url')}")
    return _render_box("Lattix xFrontier install complete", lines)


def _print_install_result(payload: dict[str, Any]) -> None:
    safe_payload = dict(payload)
    raw_bootstrap_login = safe_payload.get("bootstrap_login")
    if isinstance(raw_bootstrap_login, dict):
        sanitized_bootstrap_login = dict(raw_bootstrap_login)
        sanitized_bootstrap_login.pop("password", None)
        safe_payload["bootstrap_login"] = sanitized_bootstrap_login

    if _display_mode() == "json":
        print_json(safe_payload)
        return
    print(_render_install_summary(safe_payload))  # noqa: T201


def _install_mode(root: Path) -> str:
    return "editable" if (root / ".git").exists() else "wheel"


def _managed_venv_dir(root: Path) -> Path:
    return root / _MANAGED_VENV_DIRNAME


def _managed_scripts_dir(root: Path) -> Path:
    venv_root = _managed_venv_dir(root)
    return venv_root / ("Scripts" if os.name == "nt" else "bin")


def _managed_python_executable(root: Path) -> Path:
    scripts_dir = _managed_scripts_dir(root)
    executable_name = "python.exe" if os.name == "nt" else "python"
    return scripts_dir / executable_name


def _pip_install_args(root: Path, python_bin: Path) -> list[str]:
    args = [str(python_bin), "-m", "pip", "install"]
    if _install_mode(root) == "editable":
        args.append("-e")
    args.append(".[dev]")
    return args


def _bootstrap_managed_venv(install_root: Path, env: dict[str, str]) -> dict[str, str]:
    venv_dir = _managed_venv_dir(install_root)
    python_bin = _managed_python_executable(install_root)
    scripts_dir = _managed_scripts_dir(install_root)
    if not python_bin.exists():
        run_command([python_executable(), "-m", "venv", str(venv_dir)], cwd=install_root, env=env)
    runtime_env = env.copy()
    runtime_env.update(
        {
            "VIRTUAL_ENV": str(venv_dir),
            "PATH": _append_path_once(str(env.get("PATH") or ""), str(scripts_dir)),
        }
    )
    run_command(
        [str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
        cwd=install_root,
        env=runtime_env,
    )
    return {
        "python_bin": str(python_bin),
        "scripts_dir": str(scripts_dir),
        "venv_dir": str(venv_dir),
    }


def _path_separator_for_scripts_dir(scripts_dir: Path, current_path: str) -> str:
    if ";" in current_path:
        return ";"
    if scripts_dir.name.lower() == "scripts":
        return ";"
    return os.pathsep


def _runtime_env(
    install_root: Path, *, python_bin: Path, scripts_dir: Path, venv_dir: Path | None = None
) -> dict[str, str]:
    current_path = str(os.getenv("PATH") or "")
    path_separator = _path_separator_for_scripts_dir(scripts_dir, current_path)
    scripts_dir_str = str(scripts_dir)
    path_entries = [entry for entry in current_path.split(path_separator) if entry]
    path_entries = [entry for entry in path_entries if entry.casefold() != scripts_dir_str.casefold()]
    path_entries.insert(0, scripts_dir_str)
    env = os.environ.copy()
    env["PATH"] = path_separator.join(path_entries)
    env[FRONTIER_APP_HOME_ENV] = str(install_root)
    env["VIRTUAL_ENV"] = str(venv_dir or _managed_venv_dir(install_root))
    env["FRONTIER_PYTHON_BIN"] = str(python_bin)
    return env


def _interactive_install() -> bool:
    configured = str(os.getenv("FRONTIER_INSTALLER_INTERACTIVE") or "").strip().lower()
    if configured in {"0", "false", "no"}:
        return False
    if configured in {"1", "true", "yes"}:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def _best_effort_owner_only_permissions(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        return


def _read_json_map(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_map(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _best_effort_owner_only_permissions(path)
    return path


def _vault_exec_command(
    install_root: Path,
    vault_args: list[str],
    *,
    token: str | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = compose_prefix(local=False, root=install_root) + ["exec", "-T"]
    if token:
        command.extend(["-e", f"VAULT_TOKEN={token}"])
    command.extend(["-e", "VAULT_ADDR=http://127.0.0.1:8200", "vault", "vault", *vault_args])
    return subprocess.run(
        command, cwd=str(install_root), check=check, capture_output=True, text=True
    )


def _run_vault_cli_json(
    install_root: Path,
    vault_args: list[str],
    *,
    token: str | None = None,
    allow_nonzero: bool = False,
) -> dict[str, Any]:
    completed = _vault_exec_command(install_root, vault_args, token=token, check=False)
    raw_output = (completed.stdout or completed.stderr or "").strip()
    payload: dict[str, Any] = {}
    if raw_output:
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
    if completed.returncode != 0 and not allow_nonzero:
        raise RuntimeError(raw_output or f"Vault command failed: {' '.join(vault_args)}")
    return payload


def _run_vault_cli(
    install_root: Path,
    vault_args: list[str],
    *,
    token: str | None = None,
    allow_nonzero: bool = False,
) -> str:
    completed = _vault_exec_command(install_root, vault_args, token=token, check=False)
    raw_output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0 and not allow_nonzero:
        raise RuntimeError(raw_output or f"Vault command failed: {' '.join(vault_args)}")
    return raw_output


def _vault_kv_components(api_path: str) -> tuple[str, str]:
    normalized = str(api_path or "").strip().strip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 3 or parts[1] != "data":
        raise ValueError("Vault installer paths must use the KV-v2 <mount>/data/<path> form")
    return parts[0], "/".join(parts[2:])


def _ensure_local_vault_bootstrap(install_root: Path) -> dict[str, Any]:
    bootstrap_path = installer_vault_bootstrap_path(root=install_root)
    bootstrap = _read_json_map(bootstrap_path)

    status = _run_vault_cli_json(install_root, ["status", "-format=json"], allow_nonzero=True)
    initialized = bool(status.get("initialized"))
    if not initialized:
        init_payload = _run_vault_cli_json(
            install_root,
            ["operator", "init", "-format=json", "-key-shares=1", "-key-threshold=1"],
        )
        unseal_keys = (
            init_payload.get("unseal_keys_b64")
            if isinstance(init_payload.get("unseal_keys_b64"), list)
            else []
        )
        root_token = str(init_payload.get("root_token") or "").strip()
        unseal_key = str(unseal_keys[0] if unseal_keys else "").strip()
        if not root_token or not unseal_key:
            raise RuntimeError(
                "Vault initialization did not return the expected root token and unseal key"
            )
        bootstrap = {
            "schema_version": _INSTALLER_VAULT_BOOTSTRAP_SCHEMA_VERSION,
            "vault_addr": "http://127.0.0.1:8200",
            "root_token": root_token,
            "unseal_key": unseal_key,
            "initialized_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_json_map(bootstrap_path, bootstrap)
        status = _run_vault_cli_json(install_root, ["status", "-format=json"], allow_nonzero=True)

    if not bootstrap:
        raise RuntimeError(
            "Local Vault is already initialized, but the installer does not have durable bootstrap metadata. "
            f"Expected {bootstrap_path} to exist so it can unseal and update the local Vault-backed installer state."
        )

    if bool(status.get("sealed")):
        unseal_key = str(bootstrap.get("unseal_key") or "").strip()
        if not unseal_key:
            raise RuntimeError(
                "Vault is sealed and no durable unseal key is available in installer metadata"
            )
        _run_vault_cli(install_root, ["operator", "unseal", unseal_key])
        status = _run_vault_cli_json(install_root, ["status", "-format=json"], allow_nonzero=True)
        if bool(status.get("sealed")):
            raise RuntimeError("Vault remained sealed after the installer attempted to unseal it")

    root_token = str(bootstrap.get("root_token") or "").strip()
    if not root_token:
        raise RuntimeError(
            "Vault bootstrap metadata is missing the root token required for installer state writes"
        )

    mounts = _run_vault_cli_json(
        install_root, ["secrets", "list", "-format=json"], token=root_token
    )
    secret_mount = mounts.get("secret/") if isinstance(mounts, dict) else None
    mount_options: dict[str, Any] = {}
    if isinstance(secret_mount, dict):
        raw_mount_options = secret_mount.get("options")
        if isinstance(raw_mount_options, dict):
            mount_options = raw_mount_options
    if not isinstance(secret_mount, dict):
        _run_vault_cli(
            install_root,
            ["secrets", "enable", "-path=secret", "-version=2", "kv"],
            token=root_token,
        )
    elif (
        str(secret_mount.get("type") or "") != "kv"
        or str(mount_options.get("version") or "") != "2"
    ):
        raise RuntimeError(
            "The local Vault secret/ mount is present but is not configured as KV v2"
        )

    return bootstrap


def _vault_kv_put(
    install_root: Path, api_path: str, payload: dict[str, Any], *, token: str
) -> dict[str, Any]:
    mount, logical_path = _vault_kv_components(api_path)
    kv_args = ["kv", "put", "-format=json", f"-mount={mount}", logical_path]
    for key, value in payload.items():
        kv_args.append(f"{key}={value}")
    return _run_vault_cli_json(install_root, kv_args, token=token)


def _is_sensitive_env_key(key: str) -> bool:
    normalized = str(key or "").strip().upper()
    if not normalized or normalized in _INSTALLER_VAULT_IGNORED_SECRET_KEYS:
        return False
    return any(fragment in normalized for fragment in _INSTALLER_VAULT_SECRET_KEY_FRAGMENTS)


def _classified_installer_env_values(
    install_root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    aggregated_secrets: dict[str, str] = {}
    classified_maps: dict[str, dict[str, str]] = {}
    sources = {
        "root_env": install_root / ".env",
        "secure_env": install_root / ".installer" / "local-secure.env",
        "lightweight_env": install_root / ".installer" / "local-lightweight.env",
    }
    for label, path in sources.items():
        non_secret_map: dict[str, str] = {}
        env_map = _read_installer_env_map(path)
        for key, value in env_map.items():
            if _is_sensitive_env_key(key):
                if str(value or "").strip():
                    aggregated_secrets[key] = value
                continue
            non_secret_map[key] = value
        classified_maps[label] = non_secret_map
    return aggregated_secrets, classified_maps


def _installer_state_snapshot(install_root: Path, *, install_mode: str) -> dict[str, Any]:
    manifest = read_installer_state_manifest(root=install_root)
    _secrets, classified_maps = _classified_installer_env_values(install_root)
    generated_values_path = install_root / ".installer" / "generated-values.yaml"
    generated_values_text = (
        generated_values_path.read_text(encoding="utf-8") if generated_values_path.exists() else ""
    )
    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "install_mode": install_mode,
        "install_root": str(install_root.resolve(strict=False)),
        "manifest": manifest,
        "env_snapshots": classified_maps,
        "generated_helm_values_b64": base64.b64encode(generated_values_text.encode("utf-8")).decode(
            "ascii"
        )
        if generated_values_text
        else "",
    }


def _sync_installer_state_to_vault(install_root: Path, *, install_mode: str) -> dict[str, str]:
    ensure_installer_state_manifest(root=install_root, install_mode=install_mode)
    bootstrap = _ensure_local_vault_bootstrap(install_root)
    root_token = str(bootstrap.get("root_token") or "").strip()
    if not root_token:
        raise RuntimeError(
            "Vault bootstrap metadata is missing the root token required for durable installer writes"
        )

    secret_path = installer_vault_secret_path(root=install_root)
    state_path = installer_vault_state_path(root=install_root)
    secrets_payload, _classified_maps = _classified_installer_env_values(install_root)
    if secrets_payload:
        _vault_kv_put(install_root, secret_path, secrets_payload, token=root_token)

    state_snapshot = _installer_state_snapshot(install_root, install_mode=install_mode)
    state_record = {
        "schema_version": str(state_snapshot.get("schema_version") or 1),
        "updated_at": str(state_snapshot.get("updated_at") or ""),
        "payload_b64": base64.b64encode(
            json.dumps(state_snapshot, sort_keys=True).encode("utf-8")
        ).decode("ascii"),
    }
    _vault_kv_put(install_root, state_path, state_record, token=root_token)
    return {"vault_secret_path": secret_path, "vault_state_path": state_path}


def _collect_installer_answers(install_root: Path) -> InstallerAnswers:
    installer = FrontierInstaller(repo_root=install_root)
    collect_local_answers = installer.collect_local_answers
    return collect_local_answers(installation_root=install_root, interactive=_interactive_install())


def _write_secure_installer_env(install_root: Path, answers: InstallerAnswers) -> Path:
    installer = FrontierInstaller(repo_root=install_root)
    secrets_map = installer._collect_local_secrets(answers)
    installer._write_generated_helm_values(answers)
    return installer._write_env_file(answers, secrets_map)


def _source_copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", ".venv", ".pytest_cache", ".mypy_cache", "__pycache__", "node_modules"}
    if Path(directory).name == ".installer":
        ignored.update(names)
    return {name for name in names if name in ignored}


def _prepare_install_root(source_root: Path) -> Path:
    install_root = default_app_home().resolve(strict=False)
    install_root.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(prefix="frontier-app-home-", dir=str(install_root.parent))
    )
    staged_root = staging_parent / install_root.name
    try:
        shutil.copytree(source_root, staged_root, ignore=_source_copy_ignore)
        if install_root.exists():
            _preserve_existing_install_state(install_root, staged_root)
            shutil.rmtree(install_root)
        staged_root.replace(install_root)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
    return install_root


def _preserve_existing_install_state(existing_root: Path, staged_root: Path) -> None:
    for relative_name in (".installer", ".env"):
        source_path = existing_root / relative_name
        if not source_path.exists():
            continue
        target_path = staged_root / relative_name
        if source_path.is_dir():
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)

    for source_path, target_path, preserve_existing_only in _preserved_install_data_paths(
        existing_root, staged_root
    ):
        if not source_path.exists():
            continue
        if source_path.is_dir():
            _merge_preserved_directory(
                source_path, target_path, preserve_existing_only=preserve_existing_only
            )
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if preserve_existing_only and target_path.exists():
            continue
        shutil.copy2(source_path, target_path)


def _resolve_path_within_root(root: Path, configured_path: str) -> Path | None:
    candidate = str(configured_path or "").strip()
    if not candidate:
        return None
    raw_path = Path(candidate).expanduser()
    combined = raw_path if raw_path.is_absolute() else root / raw_path
    resolved_root = root.resolve(strict=False)
    resolved_candidate = combined.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_candidate


def _installer_env_value_maps(existing_root: Path) -> list[dict[str, str]]:
    env_maps: list[dict[str, str]] = []
    for path in (
        existing_root / ".env",
        existing_root / ".installer" / "local-secure.env",
        existing_root / ".installer" / "local-lightweight.env",
    ):
        env_map = _read_installer_env_map(path)
        if env_map:
            env_maps.append(env_map)
    return env_maps


def _preserved_install_data_paths(
    existing_root: Path, staged_root: Path
) -> list[tuple[Path, Path, bool]]:
    preserved: list[tuple[Path, Path, bool]] = []
    seen: set[str] = set()

    default_agents_root = existing_root / "examples" / "agents"
    if default_agents_root.exists():
        target_default_agents_root = staged_root / "examples" / "agents"
        preserved.append((default_agents_root, target_default_agents_root, True))
        seen.add(str(default_agents_root.resolve(strict=False)).casefold())

    ensure_installer_state_manifest(root=existing_root)
    manifest = read_installer_state_manifest(root=existing_root)
    raw_manifest_asset_roots = manifest.get("in_app_asset_roots")
    manifest_asset_roots: list[Any] = (
        raw_manifest_asset_roots if isinstance(raw_manifest_asset_roots, list) else []
    )
    for relative_path in manifest_asset_roots:
        configured_root = _resolve_path_within_root(existing_root, str(relative_path))
        if configured_root is None or not configured_root.exists():
            continue
        key = str(configured_root.resolve(strict=False)).casefold()
        if key in seen:
            continue
        relative = configured_root.resolve(strict=False).relative_to(
            existing_root.resolve(strict=False)
        )
        preserved.append((configured_root, staged_root / relative, False))
        seen.add(key)

    for env_map in _installer_env_value_maps(existing_root):
        configured_root = _resolve_path_within_root(
            existing_root, env_map.get("FRONTIER_AGENT_ASSETS_ROOT", "")
        )
        if configured_root is None or not configured_root.exists():
            continue
        key = str(configured_root.resolve(strict=False)).casefold()
        if key in seen:
            continue
        relative_path = configured_root.resolve(strict=False).relative_to(
            existing_root.resolve(strict=False)
        )
        preserved.append((configured_root, staged_root / relative_path, False))
        seen.add(key)

    return preserved


def _merge_preserved_directory(
    source_dir: Path, target_dir: Path, *, preserve_existing_only: bool
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for source_path in source_dir.iterdir():
        target_path = target_dir / source_path.name
        if preserve_existing_only and target_path.exists():
            continue
        if source_path.is_dir():
            if preserve_existing_only:
                if not target_path.exists():
                    shutil.copytree(source_path, target_path)
                continue
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)


def _prepare_install_root_for_update(source_root: Path, existing_root: Path) -> Path:
    install_root = existing_root.resolve(strict=False)
    install_root.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(prefix="frontier-app-update-", dir=str(install_root.parent))
    )
    staged_root = staging_parent / install_root.name
    try:
        shutil.copytree(source_root, staged_root, ignore=_source_copy_ignore)
        if install_root.exists():
            _preserve_existing_install_state(install_root, staged_root)
            shutil.rmtree(install_root)
        staged_root.replace(install_root)
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
    return install_root


def _append_path_once(current: str, addition: str) -> str:
    entries = [entry for entry in current.split(os.pathsep) if entry]
    lowered = {entry.casefold() for entry in entries}
    if addition.casefold() not in lowered:
        entries.append(addition)
    return os.pathsep.join(entries)


def _update_windows_user_path(scripts_dir: Path) -> tuple[bool, list[str]]:
    import ctypes
    import winreg

    ctypes_api: Any = ctypes
    winreg_api: Any = winreg
    modified = False
    with winreg_api.OpenKey(
        winreg_api.HKEY_CURRENT_USER,
        "Environment",
        0,
        winreg_api.KEY_READ | winreg_api.KEY_WRITE,
    ) as key:
        try:
            current_value, _ = winreg_api.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_value = ""
        updated_value = _append_path_once(str(current_value or ""), str(scripts_dir))
        if updated_value != str(current_value or ""):
            winreg_api.SetValueEx(key, "Path", 0, winreg_api.REG_EXPAND_SZ, updated_value)
            modified = True
    if modified:
        ctypes_api.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, 0
        )
    return modified, ["HKCU\\Environment\\Path"]


def _shell_profile_targets() -> list[Path]:
    home = Path.home()
    shell = Path(str(os.getenv("SHELL") or "")).name.lower()
    system = platform.system().lower()
    if system == "darwin":
        if shell == "zsh":
            return [home / ".zprofile"]
        if shell == "bash":
            return [home / ".bash_profile"]
        return [home / ".profile"]
    if shell == "zsh":
        return [home / ".zshrc"]
    if shell == "bash":
        return [home / ".bashrc", home / ".profile"]
    return [home / ".profile"]


def _update_posix_user_path(scripts_dir: Path) -> tuple[bool, list[str]]:
    block = (
        "# >>> lattix-xfrontier >>>\n"
        f'export PATH="{scripts_dir}:$PATH"\n'
        "# <<< lattix-xfrontier <<<\n"
    )
    modified_files: list[str] = []
    for profile_path in _shell_profile_targets():
        existing = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
        if str(scripts_dir) in existing:
            continue
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        newline = "" if not existing or existing.endswith("\n") else "\n"
        profile_path.write_text(f"{existing}{newline}{block}", encoding="utf-8")
        modified_files.append(str(profile_path))
    return bool(modified_files), modified_files


def _ensure_scripts_path(scripts_dir: Path) -> dict[str, object]:
    os.environ.update({"PATH": _append_path_once(str(os.getenv("PATH") or ""), str(scripts_dir))})
    if os.name == "nt":
        updated, locations = _update_windows_user_path(scripts_dir)
    else:
        updated, locations = _update_posix_user_path(scripts_dir)
    return {
        "scripts_dir": str(scripts_dir),
        "updated": updated,
        "locations": locations,
        "cli_path": str(cli_executable(scripts_dir=scripts_dir)),
    }


def _refresh_existing_local_stacks(
    install_root: Path, env: dict[str, str]
) -> tuple[list[str], list[str]]:
    refreshed_profiles: list[str] = []
    urls: list[str] = []
    secure_env_path = install_root / ".installer" / "local-secure.env"
    lightweight_env_path = install_root / ".installer" / "local-lightweight.env"

    if secure_env_path.exists() or not lightweight_env_path.exists():
        _prebuild_secure_stack_images(install_root, env)
        run_command(
            compose_prefix(local=False, root=install_root) + ["up", "-d", "--remove-orphans"],
            cwd=install_root,
            env=env,
        )
        refreshed_profiles.append("secure")
        urls = portal_urls(root=install_root)

    if lightweight_env_path.exists():
        run_command(
            compose_prefix(local=True, root=install_root) + ["up", "-d", "--remove-orphans"],
            cwd=install_root,
            env=env,
        )
        refreshed_profiles.append("lightweight")

    return refreshed_profiles, urls


def _git_stdout(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "git command failed").strip()
        raise SystemExit(message)
    return (completed.stdout or "").strip()


def _update_editable_checkout(install_root: Path, env: dict[str, str]) -> tuple[str, str]:
    status_output = _git_stdout(["git", "status", "--porcelain"], cwd=install_root, env=env)
    if status_output:
        raise SystemExit(
            "The editable xFrontier checkout has local changes. Commit or stash them before running `lattix update` so the updater does not overwrite work in progress."
        )

    branch = (
        _git_stdout(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=install_root, env=env)
        or "main"
    )
    before_ref = _git_stdout(["git", "rev-parse", "HEAD"], cwd=install_root, env=env)
    run_command(["git", "pull", "--ff-only"], cwd=install_root, env=env)
    after_ref = _git_stdout(["git", "rev-parse", "HEAD"], cwd=install_root, env=env)
    return branch, "updated" if before_ref != after_ref else "already-current"


def _validated_archive_download_url(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise SystemExit("Installer archive URL must use http or https.")
    if not parsed.hostname:
        raise SystemExit("Installer archive URL must include a host.")
    if parsed.username or parsed.password:
        raise SystemExit("Installer archive URL must not include credentials.")
    if parsed.fragment:
        raise SystemExit("Installer archive URL must not include fragments.")
    return parsed.geturl()


def _download_url_bytes(url: str, *, redirects_remaining: int = 3) -> bytes:
    validated_url = _validated_archive_download_url(url)
    parsed = urlsplit(validated_url)
    hostname = parsed.hostname
    if not hostname:
        raise SystemExit("Installer archive URL must include a host.")
    connection: http.client.HTTPConnection
    if parsed.scheme.lower() == "https":
        # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
        connection = http.client.HTTPSConnection(
            hostname,
            parsed.port,
            timeout=30,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(hostname, parsed.port, timeout=30)

    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"

    try:
        connection.request("GET", target, headers={"User-Agent": "frontier-updater/1.0"})
        response = connection.getresponse()
        if response.status in {301, 302, 303, 307, 308}:
            location = response.getheader("Location")
            if not location or redirects_remaining <= 0:
                raise SystemExit("Installer archive download redirected too many times.")
            return _download_url_bytes(
                urllib_parse.urljoin(validated_url, location),
                redirects_remaining=redirects_remaining - 1,
            )
        if response.status >= 400:
            raise SystemExit(f"Installer archive download failed with HTTP {response.status}.")
        return response.read()
    finally:
        connection.close()


def _download_update_archive(target_dir: Path) -> Path:
    archive_url = bootstrap_url()
    archive_bytes = _download_url_bytes(archive_url)
    archive_path = target_dir / "update.zip"
    archive_path.write_bytes(archive_bytes)
    unpack_dir = target_dir / "archive"
    shutil.unpack_archive(str(archive_path), str(unpack_dir))
    extracted_roots = [path for path in unpack_dir.iterdir() if path.is_dir()]
    if not extracted_roots:
        raise SystemExit("Downloaded update archive did not contain a repository directory.")
    return extracted_roots[0]


def _update_published_install(install_root: Path) -> str:
    temp_root = Path(tempfile.mkdtemp(prefix="frontier-update-download-"))
    try:
        source_root = _download_update_archive(temp_root)
        _prepare_install_root_for_update(source_root, install_root)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
    return "updated"


def _print_update_result(payload: dict[str, Any]) -> None:
    if _display_mode() == "json":
        print_json(payload)
        return

    lines = [
        f"Status      : {'Ready' if payload.get('updated') else 'No changes'}",
        f"Mode        : {_friendly_install_mode(str(payload.get('install_mode') or 'unknown'))}",
        f"App home    : {payload.get('repo_root')}",
        f"Env file    : {payload.get('compose_env')}",
        f"Runtime     : {payload.get('managed_runtime')}",
        f"CLI path    : {payload.get('path', {}).get('cli_path')}",
        f"Refresh     : {payload.get('refresh_status')}",
    ]
    if payload.get("vault_secret_path"):
        lines.append(f"Vault secret: {payload.get('vault_secret_path')}")
    if payload.get("vault_state_path"):
        lines.append(f"Vault state : {payload.get('vault_state_path')}")

    refreshed_profiles = (
        payload.get("refreshed_profiles")
        if isinstance(payload.get("refreshed_profiles"), list)
        else []
    )
    if refreshed_profiles:
        lines.append(f"Profiles    : {', '.join(str(item) for item in refreshed_profiles)}")

    urls = payload.get("urls") if isinstance(payload.get("urls"), list) else []
    if urls:
        lines.append("")
        lines.append("Portal URLs")
        for index, url in enumerate(urls, start=1):
            lines.append(f"  [{index}] {url}")

    next_steps = payload.get("next_steps") if isinstance(payload.get("next_steps"), list) else []
    if next_steps:
        lines.append("")
        lines.append("Next steps")
        for step in next_steps:
            lines.append(f"  • {step}")

    print(_render_box("Lattix xFrontier updated", lines))  # noqa: T201


def update() -> None:
    install_root = source_repo_root()
    mode = _install_mode(install_root)

    if mode == "wheel":
        refresh_status = _update_published_install(install_root)

    bootstrap_env = os.environ.copy()
    bootstrap_env[FRONTIER_APP_HOME_ENV] = str(install_root)
    managed_runtime = _bootstrap_managed_venv(install_root, bootstrap_env)
    scripts_dir = Path(managed_runtime["scripts_dir"])
    python_bin = Path(managed_runtime["python_bin"])
    venv_dir = Path(managed_runtime["venv_dir"])

    path_update = _ensure_scripts_path(scripts_dir)
    install_env = _runtime_env(
        install_root, python_bin=python_bin, scripts_dir=scripts_dir, venv_dir=venv_dir
    )

    if mode == "editable":
        branch, refresh_status = _update_editable_checkout(install_root, install_env)
    else:
        branch = "published"

    os.environ[FRONTIER_APP_HOME_ENV] = str(install_root)
    secure_env_path = install_root / ".installer" / "local-secure.env"
    lightweight_env_path = install_root / ".installer" / "local-lightweight.env"
    if secure_env_path.exists() or not lightweight_env_path.exists():
        compose_env = ensure_compose_env_file(local_profile=False, root=install_root)
    else:
        compose_env = ensure_compose_env_file(local_profile=True, root=install_root)
    ensure_installer_state_manifest(root=install_root, install_mode=mode)
    _best_effort_owner_only_permissions(compose_env)
    run_command(_pip_install_args(install_root, python_bin), cwd=install_root, env=install_env)
    _require_docker_stack_prerequisites(install_env)
    refreshed_profiles, urls = _refresh_existing_local_stacks(install_root, install_env)
    vault_sync = _sync_installer_state_to_vault(install_root, install_mode=mode)
    _print_update_result(
        {
            "updated": refresh_status == "updated",
            "install_mode": mode,
            "repo_root": str(install_root),
            "compose_env": str(compose_env.resolve()),
            "managed_runtime": str(venv_dir),
            "path": path_update,
            "refresh_status": refresh_status,
            "refreshed_profiles": refreshed_profiles,
            "urls": urls,
            **vault_sync,
            "next_steps": [
                "Local workflows, agents, settings, and installer env files were preserved in place.",
                "Installer-generated passwords and env-backed install state were synchronized into the durable local Vault store.",
                "Open one of the URLs above to verify the refreshed build.",
                "Run `lattix health` if you want to confirm backend readiness after the update.",
            ],
            "branch": branch,
        }
    )


def _casdoor_bootstrap_identity_enabled(answers: InstallerAnswers) -> bool:
    if FrontierInstaller._normalize_auth_provider(answers.local_auth_provider) != "oidc":
        return False
    if (
        FrontierInstaller._normalize_oidc_provider_template(answers.oidc_provider_template)
        != "casdoor"
    ):
        return False
    return bool(
        str(answers.bootstrap_login_username or "").strip()
        and str(answers.bootstrap_login_password or "")
    )


def _effective_secure_gateway_settings() -> dict[str, str]:
    install_root = (
        Path(str(os.getenv(FRONTIER_APP_HOME_ENV) or source_repo_root()))
        .expanduser()
        .resolve(strict=False)
    )
    compose_env = ensure_compose_env_file(local_profile=False, root=install_root)
    return _read_installer_env_map(compose_env)


def _current_install_root() -> Path:
    return (
        Path(str(os.getenv(FRONTIER_APP_HOME_ENV) or source_repo_root()))
        .expanduser()
        .resolve(strict=False)
    )


def _casdoor_bootstrap_endpoint(answers: InstallerAnswers) -> tuple[str, dict[str, str]]:
    issuer = FrontierInstaller._resolved_oidc_settings(answers)["issuer"]
    parsed = urlsplit(issuer)
    host = str(parsed.hostname or "").strip().lower()
    if host.endswith(".localhost"):
        gateway_settings = _effective_secure_gateway_settings()
        bind_host = (
            str(
                gateway_settings.get("LOCAL_GATEWAY_BIND_HOST")
                or os.getenv("LOCAL_GATEWAY_BIND_HOST")
                or "127.0.0.1"
            ).strip()
            or "127.0.0.1"
        )
        port = (
            str(
                gateway_settings.get("LOCAL_GATEWAY_HTTP_PORT")
                or os.getenv("LOCAL_GATEWAY_HTTP_PORT")
                or "80"
            ).strip()
            or "80"
        )
        netloc = bind_host if port == "80" else f"{bind_host}:{port}"
        return urlunsplit(("http", netloc, "", "", "")), {"Host": parsed.netloc}
    return issuer.rstrip("/"), {}


def _compose_service_logs_text(install_root: Path, service: str, *, tail: int = 80) -> str:
    command = compose_prefix(local=False, root=install_root) + [
        "logs",
        "--tail",
        str(tail),
        service,
    ]
    completed = subprocess.run(
        command, cwd=str(install_root), check=False, capture_output=True, text=True
    )
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()


def _diagnose_casdoor_bootstrap_failure() -> str:
    install_root = _current_install_root()
    casdoor_logs = _compose_service_logs_text(install_root, "casdoor")
    gateway_logs = _compose_service_logs_text(install_root, "local-gateway")
    secure_env = ensure_compose_env_file(local_profile=False, root=install_root)

    if "password authentication failed for user" in casdoor_logs:
        return (
            " Casdoor could not start because the local PostgreSQL volume appears to be using different credentials than the current installer env."
            " This usually happens after a prior secure-local install or partial reinstall left the database volume in place."
            f" For a clean reinstall, run `docker compose --env-file {secure_env} down -v --remove-orphans` (or `lattix remove`) and then retry the bootstrap."
        )
    if "lookup casdoor: i/o timeout" in gateway_logs:
        return (
            " The local gateway is up, but Casdoor never became reachable behind it."
            f" Inspect `docker compose --env-file {secure_env} logs casdoor` for the underlying startup error, then retry once Casdoor is healthy."
        )
    return ""


def _urlopen_json(
    opener: urllib_request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = urllib_request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with opener.open(request, timeout=10) as response:
        payload = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "msg": payload.strip() or "non-json response",
            "data": None,
            "data2": None,
        }
    return (
        parsed
        if isinstance(parsed, dict)
        else {"status": "error", "msg": "unexpected response shape", "data": parsed, "data2": None}
    )


def _casdoor_login_admin(
    opener: urllib_request.OpenerDirector, base_url: str, headers: dict[str, str]
) -> None:
    login_url = (
        f"{base_url.rstrip('/')}"
        "/api/login?clientId=app-built-in&responseType=code&redirectUri=http://localhost"
        "&scope=openid%20profile%20email&state=frontier-bootstrap"
    )
    login_payload = urllib_parse.urlencode(
        {
            "application": "app-built-in",
            "organization": "built-in",
            "username": "built-in/admin",
            "password": "123",
        }
    ).encode("utf-8")
    _urlopen_json(
        opener,
        login_url,
        method="POST",
        data=login_payload,
        headers={
            **headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    account = _urlopen_json(
        opener,
        f"{base_url.rstrip('/')}/api/get-account",
        headers={**headers, "Accept": "application/json"},
    )
    raw_account_data = account.get("data")
    account_data: dict[str, Any] = raw_account_data if isinstance(raw_account_data, dict) else {}
    if (
        account.get("status") != "ok"
        or account_data.get("owner") != "built-in"
        or account_data.get("name") != "admin"
    ):
        raise RuntimeError(
            "Unable to authenticate the seeded Casdoor admin account for bootstrap user creation."
        )


def _casdoor_existing_user_matches_login_contract(
    existing_user: dict[str, Any], answers: InstallerAnswers
) -> bool:
    return (
        str(existing_user.get("owner") or "").strip() == "built-in"
        and str(existing_user.get("name") or "").strip()
        == str(answers.bootstrap_login_username or "").strip()
        and str(existing_user.get("email") or "").strip()
        == str(answers.bootstrap_login_email or "").strip()
        and str(existing_user.get("displayName") or "").strip()
        == str(answers.bootstrap_login_display_name or "").strip()
        and str(existing_user.get("type") or "").strip() == "normal-user"
        and bool(existing_user.get("isAdmin")) is True
        and bool(existing_user.get("isForbidden")) is False
        and bool(existing_user.get("isDeleted")) is False
        and str(existing_user.get("signupApplication") or "").strip() == "app-built-in"
    )


def _bootstrap_casdoor_login_user(answers: InstallerAnswers) -> dict[str, Any] | None:
    if not _casdoor_bootstrap_identity_enabled(answers):
        return None

    base_url, host_headers = _casdoor_bootstrap_endpoint(answers)
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib_request.build_opener(urllib_request.HTTPCookieProcessor(cookie_jar))
    login_username = str(answers.bootstrap_login_username or "").strip()
    create_payload = {
        "owner": "built-in",
        "name": login_username,
        "displayName": str(answers.bootstrap_login_display_name or "").strip(),
        "email": str(answers.bootstrap_login_email or "").strip(),
        "password": str(answers.bootstrap_login_password or ""),
        "passwordType": "plain",
        "signupApplication": "app-built-in",
        "type": "normal-user",
        "isAdmin": True,
        "isForbidden": False,
        "isDeleted": False,
    }

    last_error: Exception | None = None
    for _ in range(CASDOOR_BOOTSTRAP_MAX_ATTEMPTS):
        try:
            _casdoor_login_admin(opener, base_url, host_headers)
            user_id = urllib_parse.quote(f"built-in/{login_username}", safe="")
            existing = _urlopen_json(
                opener,
                f"{base_url.rstrip('/')}/api/get-user?id={user_id}",
                headers={**host_headers, "Accept": "application/json"},
            )
            existing_data = existing.get("data") if isinstance(existing.get("data"), dict) else None
            exists = (
                existing.get("status") == "ok"
                and isinstance(existing_data, dict)
                and existing_data.get("name") == login_username
            )
            if (
                exists
                and isinstance(existing_data, dict)
                and _casdoor_existing_user_matches_login_contract(existing_data, answers)
            ):
                return {
                    "username": login_username,
                    "email": str(answers.bootstrap_login_email or "").strip(),
                    "display_name": str(answers.bootstrap_login_display_name or "").strip(),
                    "password_generated": bool(answers.bootstrap_login_password_generated),
                }
            endpoint = "/api/update-user" if exists else "/api/add-user"
            request_url = f"{base_url.rstrip('/')}{endpoint}"
            if exists:
                request_url = f"{request_url}?id={user_id}"
            payload = {
                "displayName": str(answers.bootstrap_login_display_name or "").strip(),
                "email": str(answers.bootstrap_login_email or "").strip(),
                "password": str(answers.bootstrap_login_password or ""),
                "passwordType": "plain",
                "signupApplication": "app-built-in",
                "type": "normal-user",
                "isAdmin": True,
                "isForbidden": False,
                "isDeleted": False,
            }
            if not exists:
                payload = create_payload
            response = _urlopen_json(
                opener,
                request_url,
                method="POST",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    **host_headers,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            if response.get("status") != "ok":
                raise RuntimeError(
                    str(response.get("msg") or "Casdoor bootstrap user operation failed")
                )
            return {
                "username": login_username,
                "email": str(answers.bootstrap_login_email or "").strip(),
                "display_name": str(answers.bootstrap_login_display_name or "").strip(),
                "password_generated": bool(answers.bootstrap_login_password_generated),
            }
        except (urllib_error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            last_error = exc
            time.sleep(CASDOOR_BOOTSTRAP_RETRY_DELAY_SECONDS)
    raise RuntimeError(
        "Unable to provision bootstrap Casdoor login user after waiting for the local gateway and Casdoor to become ready: "
        f"{last_error}{_diagnose_casdoor_bootstrap_failure()}"
    )


def _require_docker_stack_prerequisites(env: dict[str, str]) -> None:
    docker_check = run_command(["docker", "compose", "version"], check=False, env=env)
    if docker_check.returncode != 0:
        raise SystemExit(
            "Docker Compose v2 is required on PATH before the installer can auto-start the stack."
        )
    daemon_check = run_command(["docker", "info"], check=False, env=env)
    if daemon_check.returncode != 0:
        raise SystemExit(
            "Docker is installed but the daemon is not ready. Start Docker Desktop or the docker service and rerun the installer."
        )


def _read_installer_env_map(path: Path) -> dict[str, str]:
    env_map: dict[str, str] = {}
    if not path.exists():
        return env_map
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env_map[key] = value
    return env_map


def _write_installer_env_map(path: Path, env_map: dict[str, str]) -> None:
    payload = "\n".join(f"{key}={value}" for key, value in env_map.items()) + "\n"
    path.write_text(payload, encoding="utf-8")


def _secure_gateway_origin(env_map: dict[str, str]) -> str:
    host = str(env_map.get("LOCAL_STACK_HOST") or "xfrontier.local").strip() or "xfrontier.local"
    port = str(env_map.get("LOCAL_GATEWAY_HTTP_PORT") or "80").strip() or "80"
    authority = host if port == "80" else f"{host}:{port}"
    return f"http://{authority}"


def _secure_local_api_base(env_map: dict[str, str]) -> str:
    bind_host = str(env_map.get("LOCAL_GATEWAY_BIND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if bind_host == "0.0.0.0":
        bind_host = "127.0.0.1"
    port = str(env_map.get("LOCAL_GATEWAY_HTTP_PORT") or "80").strip() or "80"
    authority = bind_host if port == "80" else f"{bind_host}:{port}"
    return f"http://{authority}/api"


def _compose_up_with_output(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=str(cwd), env=env, check=False, capture_output=True, text=True
    )


def _prebuild_secure_stack_images(install_root: Path, env: dict[str, str]) -> None:
    command = compose_prefix(local=False, root=install_root) + [
        "build",
        *_SECURE_STACK_BUILD_SERVICES,
    ]
    run_command(command, cwd=install_root, env=env)


def _port_conflict_from_compose_output(output: str) -> tuple[str, int] | None:
    match = re.search(
        r"Bind for (?P<host>[^:]+):(?P<port>\d+) failed: port is already allocated", output
    )
    if not match:
        return None
    return match.group("host"), int(match.group("port"))


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _select_fallback_gateway_port(bind_host: str, occupied_port: int) -> int | None:
    for candidate in _LOCAL_GATEWAY_PORT_FALLBACKS:
        if candidate == occupied_port:
            continue
        if _port_is_available(bind_host, candidate):
            return candidate
    return None


def _rewrite_secure_gateway_port(install_root: Path, gateway_port: int) -> Path:
    compose_env = ensure_compose_env_file(local_profile=False, root=install_root)
    env_map = _read_installer_env_map(compose_env)
    env_map["LOCAL_GATEWAY_HTTP_PORT"] = str(gateway_port)
    env_map["FRONTEND_ORIGIN"] = _secure_gateway_origin(env_map)
    env_map["FRONTIER_LOCAL_API_BASE_URL"] = _secure_local_api_base(env_map)
    _write_installer_env_map(compose_env, env_map)
    return compose_env


def _raise_compose_failure(command: list[str], completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout, end="", file=sys.stdout)
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    raise subprocess.CalledProcessError(
        completed.returncode, command, output=completed.stdout, stderr=completed.stderr
    )


def _auto_start_stack(install_root: Path, env: dict[str, str]) -> list[str]:
    _prebuild_secure_stack_images(install_root, env)
    command = compose_prefix(local=False, root=install_root) + ["up", "-d", "--remove-orphans"]
    completed = _compose_up_with_output(command, cwd=install_root, env=env)
    if completed.returncode == 0:
        return portal_urls(root=install_root)

    combined_output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    port_conflict = _port_conflict_from_compose_output(combined_output)
    if port_conflict is not None:
        bind_host, occupied_port = port_conflict
        fallback_port = _select_fallback_gateway_port(bind_host, occupied_port)
        if fallback_port is not None:
            _rewrite_secure_gateway_port(install_root, fallback_port)
            completed = _compose_up_with_output(command, cwd=install_root, env=env)
            if completed.returncode == 0:
                return portal_urls(root=install_root)

    _raise_compose_failure(command, completed)
    return portal_urls(root=install_root)


def main() -> None:
    source_root = source_repo_root()
    mode = _install_mode(source_root)
    install_root = source_root if mode == "editable" else _prepare_install_root(source_root)
    os.environ[FRONTIER_APP_HOME_ENV] = str(install_root)
    answers = _collect_installer_answers(install_root)
    if _interactive_install() and not str(os.getenv("FRONTIER_INSTALLER_OUTPUT") or "").strip():
        os.environ["FRONTIER_INSTALLER_OUTPUT"] = "tui"
    bootstrap_env = os.environ.copy()
    bootstrap_env[FRONTIER_APP_HOME_ENV] = str(install_root)
    managed_runtime = _bootstrap_managed_venv(install_root, bootstrap_env)
    scripts_dir = Path(managed_runtime["scripts_dir"])
    python_bin = Path(managed_runtime["python_bin"])
    venv_dir = Path(managed_runtime["venv_dir"])
    path_update = _ensure_scripts_path(scripts_dir)
    _write_secure_installer_env(install_root, answers)
    compose_env = ensure_compose_env_file(local_profile=False, root=install_root)
    ensure_installer_state_manifest(root=install_root, install_mode=mode)
    _best_effort_owner_only_permissions(compose_env)
    install_env = _runtime_env(
        install_root, python_bin=python_bin, scripts_dir=scripts_dir, venv_dir=venv_dir
    )
    run_command(_pip_install_args(install_root, python_bin), cwd=install_root, env=install_env)
    _require_docker_stack_prerequisites(install_env)
    urls = _auto_start_stack(install_root, install_env)
    vault_sync = _sync_installer_state_to_vault(install_root, install_mode=mode)
    bootstrap_login = _bootstrap_casdoor_login_user(answers)
    next_steps = [
        "Open one of the URLs above to reach the portal.",
        "Installer-generated passwords and env-backed install state were synchronized into the durable local Vault store.",
        "Run ``lattix health`` to verify backend readiness.",
        "Use the hosted or enterprise deployment path when you need per-agent workload isolation beyond the secure local single-host profile.",
    ]
    if bootstrap_login is not None:
        bootstrap_username = str(bootstrap_login.get("username") or "").strip()
        if bootstrap_username:
            next_steps.insert(
                1, f"Sign in via /auth using {bootstrap_username} once the stack is ready."
            )
    _print_install_result(
        {
            "installed": True,
            "install_mode": mode,
            "repo_root": str(install_root),
            "compose_env": str(compose_env.resolve()),
            "auto_started": True,
            "urls": urls,
            "managed_runtime": str(venv_dir),
            "path": path_update,
            "auth_mode": answers.local_auth_provider,
            "security_posture": "Secure local profile (single-host compose, authenticated A2A)",
            "bootstrap_url": bootstrap_url(),
            "bootstrap_login": bootstrap_login,
            **vault_sync,
            "next_steps": next_steps,
        }
    )


if __name__ == "__main__":
    main()
