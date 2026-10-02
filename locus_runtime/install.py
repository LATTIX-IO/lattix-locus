from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import os
from pathlib import Path
import getpass
import re
import secrets
import shutil
import socket
import subprocess
import textwrap
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class DiagnosticResult:
    name: str
    ok: bool
    message: str


def hostname_prefix_valid(prefix: str) -> DiagnosticResult:
    normalized = str(prefix).strip()
    ok = bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", normalized))
    return DiagnosticResult(
        "hostname-prefix", ok, "valid" if ok else "Use lowercase letters, numbers, and hyphens only"
    )


def writable_directory(path: Path) -> DiagnosticResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return DiagnosticResult("writable-directory", False, str(exc))
    return DiagnosticResult("writable-directory", True, "writable")


def port_available(port: int) -> DiagnosticResult:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with sock:
            sock.bind(("127.0.0.1", port))
    except OSError as exc:
        return DiagnosticResult(f"port:{port}", False, str(exc))
    return DiagnosticResult(f"port:{port}", True, "available")


def _normalize_absolute_http_url(
    value: str, *, setting_name: str, allow_query: bool = False
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if any(ord(char) < 32 for char in raw):
        raise ValueError(f"{setting_name} contains invalid control characters")

    parsed = urlsplit(raw)
    scheme = str(parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"{setting_name} must use http or https")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{setting_name} must be an absolute URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{setting_name} must not embed credentials")
    if parsed.fragment:
        raise ValueError(f"{setting_name} must not include a fragment")
    if not allow_query and parsed.query:
        raise ValueError(f"{setting_name} must not include query parameters")
    if "\\" in parsed.netloc or "\\" in parsed.path:
        raise ValueError(f"{setting_name} contains invalid path separators")

    return urlunsplit(
        (scheme, parsed.netloc, parsed.path or "", parsed.query if allow_query else "", "")
    )


def sandbox_backend_available() -> DiagnosticResult:
    """Check if any sandbox backend (bubblewrap, seatbelt, or Docker) is available."""
    import platform as _platform

    system = _platform.system().lower()
    if system == "linux" and shutil.which("bwrap"):
        return DiagnosticResult("sandbox-backend", True, "bubblewrap (kernel sandbox)")
    if system == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return DiagnosticResult("sandbox-backend", True, "seatbelt (macOS kernel sandbox)")
    if shutil.which("docker"):
        return DiagnosticResult("sandbox-backend", True, "Docker (hardened container)")
    return DiagnosticResult(
        "sandbox-backend",
        False,
        "No sandbox backend found. Install bubblewrap (Linux: apt install bubblewrap) or Docker.",
    )


def docker_daemon_available() -> DiagnosticResult:
    if shutil.which("docker") is None:
        return DiagnosticResult("docker-daemon", False, "Docker is not installed or not on PATH")
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode != 0:
        return DiagnosticResult(
            "docker-daemon", False, "Start Docker Desktop or the docker service before continuing"
        )
    return DiagnosticResult("docker-daemon", True, "available")


def docker_compose_available() -> DiagnosticResult:
    if shutil.which("docker") is None:
        return DiagnosticResult("docker-compose", False, "Docker is not installed or not on PATH")
    result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
    if result.returncode != 0:
        return DiagnosticResult(
            "docker-compose", False, "Docker Compose v2 plugin is not available"
        )
    return DiagnosticResult("docker-compose", True, "available")


@dataclass(frozen=True)
class PrerequisiteDefinition:
    key: str
    display_name: str
    check_name: str
    install_commands: tuple[tuple[str, ...], ...] = ()
    manual_steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissingPrerequisite:
    definition: PrerequisiteDefinition
    result: DiagnosticResult


@dataclass
class InstallerAnswers:
    installation_root: str
    deployment_mode: str = "local"
    local_hostname: str = "locus"
    local_auth_provider: str = "oidc"
    oidc_provider_template: str = "casdoor"
    bootstrap_admin_username: str = ""
    bootstrap_admin_email: str = ""
    bootstrap_admin_subject: str = ""
    bootstrap_login_username: str = ""
    bootstrap_login_email: str = ""
    bootstrap_login_display_name: str = ""
    bootstrap_login_password: str = ""
    bootstrap_login_password_generated: bool = False
    openai_api_key: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_client_id: str = ""
    oidc_authorization_url: str = ""
    oidc_token_url: str = ""
    oidc_signin_url: str = ""
    oidc_signup_url: str = ""
    oidc_scopes: list[str] = field(default_factory=lambda: ["openid", "profile", "email"])
    federation_enabled: bool = False
    federation_cluster_name: str = ""
    federation_region: str = ""
    federation_peers: list[str] = field(default_factory=list)


class LocusInstaller:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)

    def _existing_secure_env_values(self) -> dict[str, str]:
        env_path = self.repo_root / ".installer" / "local-secure.env"
        if not env_path.exists():
            return {}
        env_map: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_map[key] = value
        return env_map

    def _prompt_secret_with_existing(
        self, prompt: str, description: str, *, existing_value: str = ""
    ) -> str:
        provided = self._prompt_secret(prompt, description)
        if provided:
            return provided
        return str(existing_value or "").strip()

    @staticmethod
    def _hostname_prefix_from_env(host_value: str) -> str:
        normalized = str(host_value or "").strip().lower()
        for suffix in (".localhost", ".local"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
                break
        validation = hostname_prefix_valid(normalized)
        return normalized if validation.ok else LocusInstaller._default_local_hostname()

    @staticmethod
    def _csv_env_values(value: str) -> list[str]:
        return [item.strip() for item in str(value or "").split(",") if item.strip()]

    def _existing_answers_defaults(self, installation_root: Path) -> InstallerAnswers | None:
        existing_env = self._existing_secure_env_values()
        if not existing_env:
            return None

        local_auth_provider = self._normalize_auth_provider(
            existing_env.get("LOCUS_AUTH_MODE", "oidc")
        )
        oidc_provider_template = ""
        if local_auth_provider == "oidc":
            provider_name = (
                str(existing_env.get("LOCUS_AUTH_OIDC_PROVIDER") or "").strip().lower()
            )
            oidc_provider_template = "casdoor" if provider_name == "casdoor" else "external"

        federation_peers = self._csv_env_values(existing_env.get("FEDERATION_PEERS", ""))

        return InstallerAnswers(
            installation_root=str(installation_root),
            deployment_mode="local",
            local_hostname=self._hostname_prefix_from_env(existing_env.get("LOCAL_STACK_HOST", "")),
            local_auth_provider=local_auth_provider,
            oidc_provider_template=oidc_provider_template,
            bootstrap_admin_username=str(
                existing_env.get("LOCUS_BOOTSTRAP_ADMIN_USERNAME") or ""
            ).strip(),
            bootstrap_admin_email=str(
                existing_env.get("LOCUS_BOOTSTRAP_ADMIN_EMAIL") or ""
            ).strip(),
            bootstrap_admin_subject=str(
                existing_env.get("LOCUS_BOOTSTRAP_ADMIN_SUBJECT") or ""
            ).strip(),
            bootstrap_login_username=str(
                existing_env.get("CASDOOR_BOOTSTRAP_LOGIN_USERNAME") or ""
            ).strip(),
            bootstrap_login_email=str(
                existing_env.get("CASDOOR_BOOTSTRAP_LOGIN_EMAIL") or ""
            ).strip(),
            bootstrap_login_display_name=str(
                existing_env.get("CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME") or ""
            ).strip(),
            bootstrap_login_password=str(
                existing_env.get("CASDOOR_BOOTSTRAP_LOGIN_PASSWORD") or ""
            ),
            bootstrap_login_password_generated=False,
            openai_api_key=str(existing_env.get("OPENAI_API_KEY") or "").strip(),
            oidc_issuer=str(existing_env.get("LOCUS_AUTH_OIDC_ISSUER") or "").strip(),
            oidc_audience=str(existing_env.get("LOCUS_AUTH_OIDC_AUDIENCE") or "").strip(),
            oidc_jwks_url=str(existing_env.get("LOCUS_AUTH_OIDC_JWKS_URL") or "").strip(),
            oidc_client_id=str(existing_env.get("LOCUS_AUTH_OIDC_CLIENT_ID") or "").strip(),
            oidc_authorization_url=str(
                existing_env.get("LOCUS_AUTH_OIDC_AUTHORIZATION_URL") or ""
            ).strip(),
            oidc_token_url=str(existing_env.get("LOCUS_AUTH_OIDC_TOKEN_URL") or "").strip(),
            oidc_signin_url=str(existing_env.get("LOCUS_AUTH_OIDC_SIGNIN_URL") or "").strip(),
            oidc_signup_url=str(existing_env.get("LOCUS_AUTH_OIDC_SIGNUP_URL") or "").strip(),
            oidc_scopes=str(
                existing_env.get("LOCUS_AUTH_OIDC_SCOPES") or "openid profile email"
            ).split(),
            federation_enabled=str(existing_env.get("FEDERATION_ENABLED") or "").strip().lower()
            in {"1", "true", "yes", "on"},
            federation_cluster_name=str(existing_env.get("FEDERATION_CLUSTER_NAME") or "").strip(),
            federation_region=str(existing_env.get("FEDERATION_REGION") or "").strip(),
            federation_peers=federation_peers,
        )

    @staticmethod
    def _terminal_width() -> int:
        return max(72, min(shutil.get_terminal_size(fallback=(100, 24)).columns, 120))

    @classmethod
    def _wrap_panel_lines(cls, lines: list[str]) -> list[str]:
        width = cls._terminal_width() - 4
        wrapped: list[str] = []
        for line in lines:
            if not line:
                wrapped.append("")
                continue
            wrapped.extend(
                textwrap.wrap(line, width=width, replace_whitespace=False, drop_whitespace=False)
                or [""]
            )
        return wrapped

    @classmethod
    def _render_panel(cls, title: str, lines: list[str]) -> str:
        wrapped = cls._wrap_panel_lines(lines)
        content = [title, *wrapped]
        width = max(cls._terminal_width() - 4, len(title)) if content else len(title)
        top = f"╔{'═' * (width + 2)}╗"
        body = [f"║ {line.ljust(width)} ║" for line in content]
        bottom = f"╚{'═' * (width + 2)}╝"
        return "\n".join([top, *body, bottom])

    @classmethod
    def _print_panel(cls, title: str, lines: list[str]) -> None:
        print(cls._render_panel(title, lines))  # noqa: T201

    @staticmethod
    def _default_local_hostname() -> str:
        return "locus"

    @staticmethod
    def _normalized_local_bind_host(value: str | None) -> str:
        host = str(value or "").strip()
        if not host or host in {"0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return host

    @staticmethod
    def _normalized_local_http_port(value: str | None, *, default: str) -> str:
        port = str(value or "").strip()
        return port or default

    @classmethod
    def _default_casdoor_public_url(cls) -> str:
        host = cls._normalized_local_bind_host(
            os.getenv("CASDOOR_BIND_HOST") or os.getenv("LOCAL_GATEWAY_BIND_HOST") or "127.0.0.1"
        )
        port = cls._normalized_local_http_port(os.getenv("CASDOOR_HTTP_PORT"), default="8081")
        authority = host if port == "80" else f"{host}:{port}"
        return f"http://{authority}"

    @staticmethod
    def _raw_input(prompt: str = "› ") -> str:
        return builtins.input(prompt)

    @classmethod
    def _prompt_with_default(cls, prompt: str, default: str, description: str = "") -> str:
        lines = [description] if description else []
        lines.extend(
            [
                f"Field      : {prompt}",
                f"Default    : {default}",
                "Action     : Press Enter to accept the default or type a custom value.",
            ]
        )
        cls._print_panel("Installer prompt", lines)
        response = cls._raw_input().strip()
        return response or default

    @classmethod
    def _prompt_choice(
        cls,
        prompt: str,
        choices: tuple[str, ...],
        *,
        default: str,
        descriptions: dict[str, str] | None = None,
    ) -> str:
        normalized_choices = {choice.casefold(): choice for choice in choices}
        while True:
            lines = [f"Field      : {prompt}", "Choose an option by number or name."]
            for index, choice in enumerate(choices, start=1):
                suffix = " (default)" if choice == default else ""
                description = str((descriptions or {}).get(choice) or "").strip()
                lines.append(f"  {index}. {choice}{suffix}")
                if description:
                    lines.append(f"     {description}")
            cls._print_panel("Installer choice", lines)
            response = cls._raw_input().strip().casefold()
            if not response:
                return default
            if response.isdigit():
                index = int(response)
                if 1 <= index <= len(choices):
                    return choices[index - 1]
            if response in normalized_choices:
                return normalized_choices[response]
            print(f"Please choose one of: {', '.join(choices)}")  # noqa: T201

    @staticmethod
    def _prompt_hostname(default: str) -> str:
        while True:
            candidate = (
                LocusInstaller._prompt_with_default(
                    "Local portal hostname prefix",
                    default,
                    description="This becomes the local gateway hostname for the portal, for example locus.local.",
                )
                .strip()
                .lower()
            )
            validation = hostname_prefix_valid(candidate)
            if validation.ok:
                return candidate
            print(validation.message)  # noqa: T201

    @classmethod
    def _prompt_required_value(cls, prompt: str, default: str = "", description: str = "") -> str:
        while True:
            if default:
                candidate = cls._prompt_with_default(prompt, default, description)
            else:
                cls._print_panel(
                    "Installer prompt",
                    [
                        *([description] if description else []),
                        f"Field      : {prompt}",
                        "Action     : Enter a required value to continue.",
                    ],
                )
                candidate = cls._raw_input().strip()
            candidate = candidate.strip()
            if candidate:
                return candidate
            print("A value is required.")  # noqa: T201

    @classmethod
    def _prompt_required_explicit_value(cls, prompt: str, description: str = "") -> str:
        while True:
            lines = [*([description] if description else []), f"Field      : {prompt}"]
            lines.append("Action     : Enter a required value to continue.")
            cls._print_panel("Installer prompt", lines)
            candidate = cls._raw_input().strip()
            if candidate:
                return candidate
            print("A value is required.")  # noqa: T201

    @classmethod
    def _prompt_secret(cls, prompt: str, description: str) -> str:
        cls._print_panel(
            "Installer secret",
            [
                description,
                f"Secret     : {prompt}",
                "Action     : Leave blank to generate a strong per-install secret.",
            ],
        )
        return getpass.getpass("› ").strip()

    @classmethod
    def _prompt_required_secret(cls, prompt: str, description: str) -> str:
        while True:
            cls._print_panel(
                "Installer secret",
                [
                    description,
                    f"Secret     : {prompt}",
                    "Action     : Enter a required value to continue.",
                ],
            )
            candidate = getpass.getpass("› ").strip()
            if candidate:
                return candidate
            print("A value is required.")  # noqa: T201

    @classmethod
    def _render_answers_summary(cls, answers: InstallerAnswers) -> str:
        lines = [
            f"Install root: {answers.installation_root}",
            f"Hostname    : {answers.local_hostname}.localhost",
            f"Auth mode   : {answers.local_auth_provider}",
            f"OIDC preset : {answers.oidc_provider_template or 'n/a'}",
            f"Admin user  : {answers.bootstrap_admin_username}",
            f"Admin email : {answers.bootstrap_admin_email}",
            f"Admin sub   : {answers.bootstrap_admin_subject}",
        ]
        if str(answers.bootstrap_login_username or "").strip():
            lines.extend(
                [
                    f"Login user  : {answers.bootstrap_login_username}",
                    f"Login email : {answers.bootstrap_login_email}",
                    f"Login name  : {answers.bootstrap_login_display_name}",
                    f"Login pass  : {'Generated during install' if answers.bootstrap_login_password_generated else 'Provided during install'}",
                ]
            )
        if answers.local_auth_provider == "oidc" and answers.oidc_provider_template == "external":
            lines.extend(
                [
                    "",
                    "External OIDC",
                    f"  Issuer    : {answers.oidc_issuer}",
                    f"  Audience  : {answers.oidc_audience}",
                    f"  Client ID : {answers.oidc_client_id}",
                ]
            )
        lines.extend(
            [
                "",
                "Security posture",
                "  • Secure local profile with authenticated requests",
                "  • Signed A2A messages and replay protection stay enabled",
                "  • This is still a single-host Docker deployment, not hosted per-agent isolation",
            ]
        )
        return cls._render_panel("Review install settings", lines)

    @staticmethod
    def _suggest_bootstrap_admin_identity(hostname_prefix: str) -> dict[str, str]:
        suffix = secrets.token_hex(3)
        username = f"locus-admin-{suffix}"
        email = f"{username}@{hostname_prefix}.localhost"
        return {
            "username": username,
            "email": email,
            "subject": username,
        }

    def secure_local_answers(self, installation_root: Path) -> InstallerAnswers:
        existing_answers = self._existing_answers_defaults(installation_root)
        if existing_answers is not None:
            return existing_answers

        hostname_prefix = self._default_local_hostname()
        bootstrap_admin = self._suggest_bootstrap_admin_identity(hostname_prefix)
        return InstallerAnswers(
            installation_root=str(installation_root),
            deployment_mode="local",
            local_hostname=hostname_prefix,
            local_auth_provider="oidc",
            oidc_provider_template="casdoor",
            bootstrap_admin_username=bootstrap_admin["username"],
            bootstrap_admin_email=bootstrap_admin["email"],
            bootstrap_admin_subject=bootstrap_admin["subject"],
        )

    def collect_local_answers(
        self, *, installation_root: Path, interactive: bool
    ) -> InstallerAnswers:
        if not interactive:
            raise SystemExit(
                "Interactive installer input is required to create the Casdoor bootstrap login user for local installs. "
                "Rerun the installer in an interactive terminal."
            )

        has_existing_install_settings = bool(self._existing_secure_env_values())

        while True:
            answers = self.secure_local_answers(installation_root)
            self._print_panel(
                "Lattix Locus installer",
                [
                    "Secure local installation wizard",
                    "Press Enter to accept a recommended value.",
                    "Sensitive values can be generated automatically.",
                ],
            )

            answers.local_hostname = self._prompt_hostname(answers.local_hostname)
            answers.local_auth_provider = self._prompt_choice(
                "Operator authentication mode",
                ("oidc", "shared-token"),
                default=answers.local_auth_provider,
                descriptions={
                    "oidc": "Recommended. Uses a real identity provider for operator sign-in.",
                    "shared-token": "Fallback. Generates a backend bearer token instead of full OIDC.",
                },
            )

            if answers.local_auth_provider == "oidc":
                answers.oidc_provider_template = self._prompt_choice(
                    "OIDC provider preset",
                    ("casdoor", "external"),
                    default=answers.oidc_provider_template,
                    descriptions={
                        "casdoor": "Turnkey local IAM preset that matches the bundled secure-local topology.",
                        "external": "Bring your own OIDC provider and enter the URLs explicitly.",
                    },
                )
                if answers.oidc_provider_template == "external":
                    answers.oidc_issuer = self._prompt_required_value(
                        "OIDC issuer URL",
                        description="Absolute issuer URL used to validate operator bearer tokens.",
                    )
                    answers.oidc_audience = self._prompt_required_value(
                        "OIDC audience",
                        "locus-ui",
                        description="Audience expected in tokens presented to the local stack.",
                    )
                    answers.oidc_jwks_url = self._prompt_required_value(
                        "OIDC JWKS URL",
                        description="Public key endpoint used to validate signed operator tokens.",
                    )
                    answers.oidc_client_id = self._prompt_required_value(
                        "OIDC client ID",
                        "locus-web",
                        description="Client identifier for the UI sign-in flow.",
                    )
                    answers.oidc_authorization_url = self._prompt_required_value(
                        "OIDC authorization URL",
                        description="Browser redirect target for sign-in.",
                    )
                    answers.oidc_token_url = self._prompt_required_value(
                        "OIDC token URL",
                        description="Token exchange endpoint for the configured OIDC provider.",
                    )
                    answers.oidc_signin_url = self._prompt_with_default(
                        "OIDC sign-in URL",
                        answers.oidc_authorization_url,
                        description="Optional override for the UI sign-in button.",
                    )
                    answers.oidc_signup_url = self._prompt_with_default(
                        "OIDC sign-up URL",
                        answers.oidc_authorization_url,
                        description="Optional override for the UI create-account button.",
                    )
                    scopes = self._prompt_with_default(
                        "OIDC scopes (space separated)",
                        "openid profile email",
                        description="Scopes requested during operator sign-in.",
                    )
                    answers.oidc_scopes = [scope for scope in scopes.split() if scope]
                else:
                    answers.oidc_provider_template = "casdoor"
            else:
                answers.oidc_provider_template = ""

            if has_existing_install_settings:
                bootstrap_admin = {
                    "username": str(answers.bootstrap_admin_username or "").strip(),
                    "email": str(answers.bootstrap_admin_email or "").strip(),
                    "subject": str(answers.bootstrap_admin_subject or "").strip(),
                }
            else:
                bootstrap_admin = self._suggest_bootstrap_admin_identity(answers.local_hostname)
            answers.bootstrap_admin_username = self._prompt_with_default(
                "Bootstrap admin username",
                bootstrap_admin["username"],
                description="First operator identity granted admin and builder capabilities.",
            )
            answers.bootstrap_admin_email = self._prompt_with_default(
                "Bootstrap admin email",
                bootstrap_admin["email"],
                description="Email claim expected from the initial operator identity.",
            )
            answers.bootstrap_admin_subject = self._prompt_with_default(
                "Bootstrap admin subject",
                bootstrap_admin["subject"],
                description="Subject claim that maps the initial operator into the admin allowlist.",
            )

            if (
                answers.local_auth_provider == "oidc"
                and answers.oidc_provider_template == "casdoor"
            ):
                answers.bootstrap_login_username = self._prompt_required_explicit_value(
                    "Bootstrap login username",
                    description="Casdoor user created automatically so you can sign in from the login screen after install.",
                )
                answers.bootstrap_login_email = self._prompt_required_explicit_value(
                    "Bootstrap login email",
                    description="Email address assigned to the installer-created Casdoor login user.",
                )
                answers.bootstrap_login_display_name = self._prompt_required_explicit_value(
                    "Bootstrap login display name",
                    description="Friendly display name shown for the installer-created login user.",
                )
                existing_bootstrap_password = str(answers.bootstrap_login_password or "")
                if existing_bootstrap_password:
                    answers.bootstrap_login_password = self._prompt_secret_with_existing(
                        "CASDOOR bootstrap login password",
                        "Password for the installer-created Casdoor login user. Leave blank to keep the existing password during an in-place reinstall.",
                        existing_value=existing_bootstrap_password,
                    )
                else:
                    answers.bootstrap_login_password = self._prompt_required_secret(
                        "CASDOOR bootstrap login password",
                        "Password for the installer-created Casdoor login user. This value must be entered explicitly.",
                    )
                answers.bootstrap_login_password_generated = False
            else:
                answers.bootstrap_login_username = ""
                answers.bootstrap_login_email = ""
                answers.bootstrap_login_display_name = ""
                answers.bootstrap_login_password = ""
                answers.bootstrap_login_password_generated = False

            print(self._render_answers_summary(answers))  # noqa: T201
            if self._ask_yes_no("Proceed with these settings?", True):
                return answers

    @staticmethod
    def _normalize_auth_provider(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized == "casdoor":
            return "oidc"
        if normalized in {"oidc", "shared-token"}:
            return normalized
        return "oidc"

    @staticmethod
    def _normalize_oidc_provider_template(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"", "casdoor", "external", "generic", "custom"}:
            return "external" if normalized in {"", "generic", "custom"} else normalized
        return "external"

    @staticmethod
    def _normalized_oidc_provider_name(answers: InstallerAnswers) -> str:
        auth_provider = LocusInstaller._normalize_auth_provider(answers.local_auth_provider)
        if auth_provider != "oidc":
            return ""
        if str(answers.local_auth_provider or "").strip().lower() == "casdoor":
            return "casdoor"
        template = LocusInstaller._normalize_oidc_provider_template(
            answers.oidc_provider_template
        )
        return "casdoor" if template == "casdoor" else "oidc"

    @classmethod
    def _resolved_oidc_settings(cls, answers: InstallerAnswers) -> dict[str, str]:
        provider_name = cls._normalized_oidc_provider_name(answers)
        if provider_name == "casdoor":
            issuer = _normalize_absolute_http_url(
                answers.oidc_issuer or cls._default_casdoor_public_url(),
                setting_name="LOCUS_AUTH_OIDC_ISSUER",
            )
            authorization_url = _normalize_absolute_http_url(
                answers.oidc_authorization_url or f"{issuer}/login/oauth/authorize",
                setting_name="LOCUS_AUTH_OIDC_AUTHORIZATION_URL",
                allow_query=True,
            )
            return {
                "provider": "casdoor",
                "issuer": issuer,
                "audience": answers.oidc_audience or "locus-ui",
                "jwks_url": _normalize_absolute_http_url(
                    answers.oidc_jwks_url or f"{issuer}/.well-known/jwks.json",
                    setting_name="LOCUS_AUTH_OIDC_JWKS_URL",
                ),
                "client_id": answers.oidc_client_id or "locus-web",
                "authorization_url": authorization_url,
                "token_url": _normalize_absolute_http_url(
                    answers.oidc_token_url or f"{issuer}/api/login/oauth/access_token",
                    setting_name="LOCUS_AUTH_OIDC_TOKEN_URL",
                ),
                "signin_url": _normalize_absolute_http_url(
                    answers.oidc_signin_url or authorization_url,
                    setting_name="LOCUS_AUTH_OIDC_SIGNIN_URL",
                    allow_query=True,
                ),
                "signup_url": _normalize_absolute_http_url(
                    answers.oidc_signup_url or authorization_url,
                    setting_name="LOCUS_AUTH_OIDC_SIGNUP_URL",
                    allow_query=True,
                ),
                "scopes": cls._oidc_scopes_value(answers),
            }
        authorization_url = _normalize_absolute_http_url(
            answers.oidc_authorization_url,
            setting_name="LOCUS_AUTH_OIDC_AUTHORIZATION_URL",
            allow_query=True,
        )
        return {
            "provider": provider_name,
            "issuer": _normalize_absolute_http_url(
                answers.oidc_issuer, setting_name="LOCUS_AUTH_OIDC_ISSUER"
            ),
            "audience": answers.oidc_audience,
            "jwks_url": _normalize_absolute_http_url(
                answers.oidc_jwks_url, setting_name="LOCUS_AUTH_OIDC_JWKS_URL"
            ),
            "client_id": answers.oidc_client_id,
            "authorization_url": authorization_url,
            "token_url": _normalize_absolute_http_url(
                answers.oidc_token_url, setting_name="LOCUS_AUTH_OIDC_TOKEN_URL"
            ),
            "signin_url": _normalize_absolute_http_url(
                answers.oidc_signin_url or authorization_url,
                setting_name="LOCUS_AUTH_OIDC_SIGNIN_URL",
                allow_query=True,
            ),
            "signup_url": _normalize_absolute_http_url(
                answers.oidc_signup_url or authorization_url,
                setting_name="LOCUS_AUTH_OIDC_SIGNUP_URL",
                allow_query=True,
            ),
            "scopes": cls._oidc_scopes_value(answers),
        }

    @staticmethod
    def _oidc_scopes_value(answers: InstallerAnswers) -> str:
        scopes = [str(scope).strip() for scope in answers.oidc_scopes if str(scope).strip()]
        return " ".join(scopes) if scopes else "openid profile email"

    @staticmethod
    def _resolved_bootstrap_admin_identity(answers: InstallerAnswers) -> dict[str, str]:
        username = str(answers.bootstrap_admin_username or "").strip() or "locus-admin"
        email = (
            str(answers.bootstrap_admin_email or "").strip()
            or f"admin@{answers.local_hostname}.localhost"
        )
        subject = str(answers.bootstrap_admin_subject or "").strip() or username
        references: list[str] = []
        for candidate in [username, email, subject]:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in references:
                references.append(normalized)
        return {
            "username": username,
            "email": email,
            "subject": subject,
            "actor_list": ",".join(references),
        }

    @staticmethod
    def _resolved_bootstrap_login_identity(answers: InstallerAnswers) -> dict[str, str]:
        username = str(answers.bootstrap_login_username or "").strip()
        email = str(answers.bootstrap_login_email or "").strip()
        display_name = str(answers.bootstrap_login_display_name or "").strip()
        password = str(answers.bootstrap_login_password or "")
        return {
            "username": username,
            "email": email,
            "display_name": display_name,
            "password": password,
        }

    def _write_env_file(self, answers: InstallerAnswers, secrets_map: dict[str, str]) -> Path:
        generated_dir = self.repo_root / ".installer"
        generated_dir.mkdir(parents=True, exist_ok=True)
        env_path = generated_dir / "local-secure.env"
        base_lines: list[str] = []
        example_path = self.repo_root / ".env.example"
        if example_path.exists():
            base_lines = [
                line.rstrip("\n")
                for line in example_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        generated_lines = [
            *base_lines,
            f"LOCAL_STACK_HOST={answers.local_hostname}.localhost",
            "CASDOOR_LOCAL_HOST=casdoor.localhost",
            f"CASDOOR_BIND_HOST={self._normalized_local_bind_host(os.getenv('CASDOOR_BIND_HOST') or os.getenv('LOCAL_GATEWAY_BIND_HOST') or '127.0.0.1')}",
            f"CASDOOR_HTTP_PORT={self._normalized_local_http_port(os.getenv('CASDOOR_HTTP_PORT'), default='8081')}",
            "LOCUS_RUNTIME_PROFILE=local-secure",
            "LOCUS_SECURE_LOCAL_MODE=true",
            "LOCUS_REQUIRE_AUTHENTICATED_REQUESTS=true",
            "LOCUS_ALLOW_HEADER_ACTOR_AUTH=false",
            "LOCUS_LOCAL_BOOTSTRAP_AUTHENTICATED_OPERATOR=true",
            "NEXT_PUBLIC_API_BASE_URL=/api",
            f"FRONTEND_ORIGIN=http://{answers.local_hostname}.localhost",
            f"LOCUS_AUTH_MODE={self._normalize_auth_provider(answers.local_auth_provider)}",
            "A2A_JWT_AUD=locus-runtime",
            "A2A_TRUSTED_SUBJECTS=backend,orchestrator,research,code,review,coordinator",
            f"FEDERATION_ENABLED={'true' if answers.federation_enabled else 'false'}",
            f"FEDERATION_CLUSTER_NAME={answers.federation_cluster_name}",
            f"FEDERATION_REGION={answers.federation_region}",
            f"FEDERATION_PEERS={','.join(answers.federation_peers)}",
        ]
        casdoor_public_url = _normalize_absolute_http_url(
            answers.oidc_issuer
            if self._normalized_oidc_provider_name(answers) == "casdoor"
            and str(answers.oidc_issuer or "").strip()
            else self._default_casdoor_public_url(),
            setting_name="CASDOOR_PUBLIC_URL",
        )
        generated_lines.insert(3, f"CASDOOR_PUBLIC_URL={casdoor_public_url}")
        bootstrap_admin = self._resolved_bootstrap_admin_identity(answers)
        bootstrap_login = self._resolved_bootstrap_login_identity(answers)
        generated_lines.extend(
            [
                f"LOCUS_BOOTSTRAP_ADMIN_USERNAME={bootstrap_admin['username']}",
                f"LOCUS_BOOTSTRAP_ADMIN_EMAIL={bootstrap_admin['email']}",
                f"LOCUS_BOOTSTRAP_ADMIN_SUBJECT={bootstrap_admin['subject']}",
                f"LOCUS_ADMIN_ACTORS={bootstrap_admin['actor_list']}",
                f"LOCUS_BUILDER_ACTORS={bootstrap_admin['actor_list']}",
                f"CASDOOR_BOOTSTRAP_ADMIN_USERNAME={bootstrap_admin['username']}",
                f"CASDOOR_BOOTSTRAP_ADMIN_EMAIL={bootstrap_admin['email']}",
                f"CASDOOR_BOOTSTRAP_LOGIN_USERNAME={bootstrap_login['username']}",
                f"CASDOOR_BOOTSTRAP_LOGIN_EMAIL={bootstrap_login['email']}",
                f"CASDOOR_BOOTSTRAP_LOGIN_DISPLAY_NAME={bootstrap_login['display_name']}",
                f"CASDOOR_BOOTSTRAP_LOGIN_PASSWORD={bootstrap_login['password']}",
            ]
        )
        if self._normalize_auth_provider(answers.local_auth_provider) == "oidc":
            oidc_settings = self._resolved_oidc_settings(answers)
            generated_lines.extend(
                [
                    "NEXT_PUBLIC_LOCUS_ACTOR=",
                    "LOCUS_API_BEARER_TOKEN=",
                    f"LOCUS_AUTH_OIDC_PROVIDER={oidc_settings['provider']}",
                    f"LOCUS_AUTH_OIDC_ISSUER={oidc_settings['issuer']}",
                    f"LOCUS_AUTH_OIDC_AUDIENCE={oidc_settings['audience']}",
                    f"LOCUS_AUTH_OIDC_JWKS_URL={oidc_settings['jwks_url']}",
                    f"LOCUS_AUTH_OIDC_CLIENT_ID={oidc_settings['client_id']}",
                    f"LOCUS_AUTH_OIDC_AUTHORIZATION_URL={oidc_settings['authorization_url']}",
                    f"LOCUS_AUTH_OIDC_TOKEN_URL={oidc_settings['token_url']}",
                    f"LOCUS_AUTH_OIDC_SIGNIN_URL={oidc_settings['signin_url']}",
                    f"LOCUS_AUTH_OIDC_SIGNUP_URL={oidc_settings['signup_url']}",
                    f"LOCUS_AUTH_OIDC_SCOPES={oidc_settings['scopes']}",
                ]
            )
        else:
            shared_token = secrets_map.get("LOCUS_API_BEARER_TOKEN", "")
            generated_lines.extend(
                [
                    f"LOCUS_API_BEARER_TOKEN={shared_token}",
                    "NEXT_PUBLIC_LOCUS_ACTOR=",
                    "LOCUS_AUTH_OIDC_PROVIDER=",
                    "LOCUS_AUTH_OIDC_ISSUER=",
                    "LOCUS_AUTH_OIDC_AUDIENCE=",
                    "LOCUS_AUTH_OIDC_JWKS_URL=",
                    "LOCUS_AUTH_OIDC_CLIENT_ID=",
                    "LOCUS_AUTH_OIDC_AUTHORIZATION_URL=",
                    "LOCUS_AUTH_OIDC_TOKEN_URL=",
                    "LOCUS_AUTH_OIDC_SIGNIN_URL=",
                    "LOCUS_AUTH_OIDC_SIGNUP_URL=",
                    "LOCUS_AUTH_OIDC_SCOPES=",
                ]
            )
        postgres_password = secrets_map.get("POSTGRES_PASSWORD", "")
        # Database name and role keep their pre-rename defaults so existing data volumes still match.
        postgres_db = os.getenv("POSTGRES_DB", "frontier")
        postgres_user = os.getenv("POSTGRES_USER", "frontier")
        neo4j_password = secrets_map.get("NEO4J_PASSWORD", "")
        generated_lines.extend(
            [
                f"POSTGRES_USER={postgres_user}",
                f"POSTGRES_PASSWORD={postgres_password}",
                f"POSTGRES_DB={postgres_db}",
                f"POSTGRES_DSN=postgresql://{postgres_user}:{postgres_password}@postgres:5432/{postgres_db}",
                "NEO4J_USERNAME=neo4j",
                f"NEO4J_PASSWORD={neo4j_password}",
            ]
        )
        for key, value in secrets_map.items():
            if key in {"LOCUS_API_BEARER_TOKEN", "POSTGRES_PASSWORD", "NEO4J_PASSWORD"}:
                continue
            generated_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(generated_lines) + "\n", encoding="utf-8")
        return env_path

    def _collect_local_secrets(self, answers: InstallerAnswers) -> dict[str, str]:
        if answers.deployment_mode == "enterprise":
            return {}
        existing_env = self._existing_secure_env_values()
        a2a_secret = self._prompt_secret_with_existing(
            "A2A_JWT_SECRET",
            "Shared signing secret for authenticated agent-to-agent traffic in the secure local stack.",
            existing_value=existing_env.get("A2A_JWT_SECRET", ""),
        ) or secrets.token_urlsafe(32)
        postgres_password = self._prompt_secret_with_existing(
            "POSTGRES_PASSWORD",
            "Database password for the local PostgreSQL instance used by the secure local stack.",
            existing_value=existing_env.get("POSTGRES_PASSWORD", ""),
        ) or secrets.token_urlsafe(24)
        neo4j_password = self._prompt_secret_with_existing(
            "NEO4J_PASSWORD",
            "Graph database password for the local Neo4j service.",
            existing_value=existing_env.get("NEO4J_PASSWORD", ""),
        ) or secrets.token_urlsafe(24)
        secrets_map = {
            "A2A_JWT_SECRET": a2a_secret,
            "POSTGRES_PASSWORD": postgres_password,
            "NEO4J_PASSWORD": neo4j_password,
        }
        if self._normalize_auth_provider(answers.local_auth_provider) == "shared-token":
            provided_bearer = self._prompt_secret_with_existing(
                "LOCUS_API_BEARER_TOKEN",
                "Fallback backend bearer token when you are not wiring operator auth through OIDC yet.",
                existing_value=existing_env.get("LOCUS_API_BEARER_TOKEN", ""),
            )
            secrets_map["LOCUS_API_BEARER_TOKEN"] = provided_bearer or secrets.token_urlsafe(32)
        return secrets_map

    def _write_generated_helm_values(self, answers: InstallerAnswers) -> Path:
        output = self.repo_root / ".installer" / "generated-values.yaml"
        output.parent.mkdir(parents=True, exist_ok=True)
        peer_lines = "\n".join(f"  - {peer}" for peer in answers.federation_peers)
        output.write_text(
            "\n".join(
                [
                    f"clusterName: {answers.federation_cluster_name}",
                    f"region: {answers.federation_region}",
                    "peers:",
                    peer_lines or "  []",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return output

    def _ask_yes_no(self, prompt: str, default: bool) -> bool:
        suffix = "[Y/n]" if default else "[y/N]"
        while True:
            self._print_panel(
                "Installer confirmation",
                [
                    prompt,
                    f"Default    : {'Yes' if default else 'No'}",
                    f"Input      : {suffix}",
                ],
            )
            response = self._raw_input().strip().lower()
            if not response:
                return default
            if response in {"y", "yes"}:
                return True
            if response in {"n", "no"}:
                return False
            print("Please answer yes or no.")  # noqa: T201

    def _run_install_command(self, command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True)

    def _run_prerequisite_check(self, definition: PrerequisiteDefinition) -> DiagnosticResult:
        check_name = str(definition.check_name or "").strip()
        if check_name == "docker-daemon":
            return docker_daemon_available()
        if check_name == "docker-compose":
            return docker_compose_available()
        if check_name.startswith("command:"):
            command = check_name.split(":", 1)[1].strip()
            if not command:
                return DiagnosticResult(check_name, False, "missing command name")
            if shutil.which(command) is None:
                return DiagnosticResult(check_name, False, f"{command} is missing from PATH")
            return DiagnosticResult(check_name, True, "available")
        return DiagnosticResult(
            check_name or definition.key,
            False,
            f"Unsupported prerequisite check: {check_name or definition.key}",
        )

    def _attempt_missing_prerequisite_installs(
        self,
        missing: Iterable[MissingPrerequisite],
    ) -> list[MissingPrerequisite]:
        unresolved: list[MissingPrerequisite] = []
        for item in missing:
            install_succeeded = False
            failure_message = item.result.message
            for command in item.definition.install_commands:
                result = self._run_install_command(command)
                if result.returncode == 0:
                    recheck = self._run_prerequisite_check(item.definition)
                    if recheck.ok:
                        install_succeeded = True
                        break
                    failure_message = recheck.message
                    continue
                failure_message = result.stderr or result.stdout or failure_message
            if not install_succeeded:
                unresolved.append(
                    MissingPrerequisite(
                        definition=item.definition,
                        result=DiagnosticResult(item.definition.check_name, False, failure_message),
                    )
                )
        return unresolved

    def _resolve_missing_prerequisites(
        self, missing: list[MissingPrerequisite], intro: str
    ) -> None:
        if not missing:
            return
        if self._ask_yes_no(intro, True):
            unresolved = self._attempt_missing_prerequisite_installs(missing)
            if not unresolved:
                return
            missing = unresolved
        lines = ["Outstanding prerequisites:"]
        for item in missing:
            lines.append(f"- {item.definition.display_name}: {item.result.message}")
            for command in item.definition.install_commands:
                lines.append("  " + " ".join(command))
            for step in item.definition.manual_steps:
                lines.append("  " + step)
        raise SystemExit("\n".join(lines))
