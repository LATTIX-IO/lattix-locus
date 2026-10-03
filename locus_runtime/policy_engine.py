"""Rego policy evaluation behind a single ``PolicyEngine`` interface (D-12, LOCUS-328).

The repository's ``policies/*.rego`` files are the only source of policy truth.
This module evaluates them with a real Rego engine; there is no Python copy of
the rules (that copy, ``OPAClient``, was removed -- see THREAT-MODEL T9).

Backends are registered by name:

* ``opa-sidecar`` (default) -- an OPA binary run as a loopback-only sidecar
  (``opa run --server --addr 127.0.0.1:<free port>``) over the repo policies,
  or an already-running loopback sidecar named by ``LOCUS_OPA_URL``.
* ``regorus`` -- reserved for the embedded engine (13 §5); not implemented.

Every failure is a deny. ``decide`` never raises for evaluation problems and
never returns ``allow=True`` unless the engine answered with a boolean ``true``
for the policy's ``allow`` rule.
"""

from __future__ import annotations

import atexit
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib import parse as urlparse

import httpx

logger = logging.getLogger(__name__)

# The repository policies. Names map to the Rego package ``lattix.<name>``.
KNOWN_POLICIES: frozenset[str] = frozenset(
    {
        "agent_policy",
        "budget_policy",
        "computer_use",
        "data_classification",
        "filesystem_access",
        "network_egress",
        "network_policy",
        "tool_jail",
    }
)
POLICY_PACKAGE_PREFIX = "lattix"

# Fail-closed reason codes. The first entry of ``Decision.reasons`` is one of
# these whenever evaluation did not complete.
REASON_UNAVAILABLE = "policy_engine_unavailable"
REASON_TIMEOUT = "policy_engine_timeout"
REASON_HTTP_ERROR = "policy_engine_http_error"
REASON_MALFORMED = "policy_engine_malformed_result"
REASON_ERROR = "policy_engine_error"
REASON_UNDEFINED = "policy_undefined"
REASON_UNKNOWN_POLICY = "unknown_policy"
REASON_INVALID_INPUT = "policy_input_invalid"

DEFAULT_BACKEND = "opa-sidecar"
DEFAULT_TIMEOUT_SECONDS = 1.0
DEFAULT_STARTUP_TIMEOUT_SECONDS = 15.0

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_POLICY_DIR = _REPO_ROOT / "policies"
_TEST_PACKAGE_RE = re.compile(r"^\s*package\s+[\w.]+_test\s*$", re.MULTILINE)


class PolicyEngineConfigError(ValueError):
    """The engine configuration is unsafe or unusable (e.g. non-loopback OPA URL)."""


@dataclass(frozen=True)
class Decision:
    """Outcome of one policy evaluation.

    ``allow`` is True only when the engine evaluated the policy's ``allow`` rule
    to boolean ``true``. ``outputs`` carries the policy's other top-level rule
    values (e.g. ``classification`` for ``data_classification``, which has no
    ``allow`` rule and therefore never allows).
    """

    allow: bool
    reasons: list[str]
    policy_version: str
    backend: str
    outputs: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PolicyEngine(Protocol):
    name: str

    def decide(self, policy: str, input: dict[str, Any]) -> Decision: ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------- #
# Policy bundle identity
# --------------------------------------------------------------------------- #
def is_test_module(source: str) -> bool:
    return bool(_TEST_PACKAGE_RE.search(source))


def compute_policy_bundle_hash(sources: Iterable[str]) -> str:
    """Content hash of a set of Rego modules, independent of paths and order.

    Test modules (``package *_test``) are excluded so the hash names the
    deployed policy set only. The same function hashes local files and the raw
    modules an external sidecar reports, so both modes agree on identical text.
    """
    digests = sorted(
        hashlib.sha256(source.encode("utf-8")).hexdigest()
        for source in sources
        if not is_test_module(source)
    )
    combined = hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest()
    return f"sha256:{combined}"


def policy_files(policy_dir: Path) -> list[Path]:
    """Deployable Rego modules in ``policy_dir`` (top level only; tests excluded)."""
    files = sorted(path for path in policy_dir.glob("*.rego") if path.is_file())
    return [path for path in files if not is_test_module(path.read_bytes().decode("utf-8"))]


def policy_dir_version(policy_dir: Path) -> str:
    return compute_policy_bundle_hash(
        path.read_bytes().decode("utf-8") for path in policy_files(policy_dir)
    )


def default_policy_dir() -> Path:
    configured = str(os.getenv("LOCUS_POLICY_DIR") or "").strip()
    return Path(configured).resolve() if configured else _DEFAULT_POLICY_DIR


# --------------------------------------------------------------------------- #
# OPA binary discovery and URL validation
# --------------------------------------------------------------------------- #
def _native_bin_dir() -> Path | None:
    try:
        from locus_tooling.common import default_app_home

        return default_app_home() / "bin"
    except Exception:  # noqa: BLE001 - discovery is best effort; absence means "not found"
        return None


def find_opa_binary() -> str | None:
    """Locate OPA: ``LOCUS_OPA_BIN``, repo ``.tools/opa``, native bin dir, then PATH."""
    configured = str(os.getenv("LOCUS_OPA_BIN") or "").strip()
    if configured:
        return configured if Path(configured).is_file() else None
    candidates = [_REPO_ROOT / ".tools" / "opa" / "opa.exe", _REPO_ROOT / ".tools" / "opa" / "opa"]
    bin_dir = _native_bin_dir()
    if bin_dir is not None:
        candidates += [bin_dir / "opa.exe", bin_dir / "opa"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("opa")


def validate_loopback_url(url: str) -> str:
    """Return ``url`` normalised (no trailing slash) if it is a plain loopback HTTP(S) origin."""
    parsed = urlparse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise PolicyEngineConfigError("LOCUS_OPA_URL must use http or https")
    if parsed.username or parsed.password:
        raise PolicyEngineConfigError("LOCUS_OPA_URL must not carry credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.params:
        raise PolicyEngineConfigError("LOCUS_OPA_URL must be an origin with no path or query")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise PolicyEngineConfigError("LOCUS_OPA_URL requires a host")
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise PolicyEngineConfigError(
                "LOCUS_OPA_URL must point at a loopback address (127.0.0.0/8, ::1, localhost)"
            )
    try:
        parsed.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError as exc:
        raise PolicyEngineConfigError("LOCUS_OPA_URL has an invalid port") from exc
    return f"{parsed.scheme}://{parsed.netloc}"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _timeout_from_env(name: str, default: float) -> float:
    try:
        value = float(str(os.getenv(name) or default))
    except ValueError:
        return default
    return value if value > 0 else default


# --------------------------------------------------------------------------- #
# OPA sidecar backend
# --------------------------------------------------------------------------- #
class OpaSidecarEngine:
    """Evaluate the repository Rego policies through a loopback OPA sidecar.

    Two modes:

    * managed (``start()``): spawn ``opa run --server`` on ``127.0.0.1`` with a
      free port over the deployable policy files, wait for ``/health``, stop it
      on :meth:`close` or interpreter exit;
    * external (``base_url`` / ``LOCUS_OPA_URL``): connect to an already-running
      loopback sidecar; the policy version is hashed from the modules it reports.
    """

    name = "opa-sidecar"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        opa_binary: str | None = None,
        policy_dir: Path | None = None,
        timeout_seconds: float | None = None,
        startup_timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,
        policy_version: str | None = None,
    ) -> None:
        self._base_url = validate_loopback_url(base_url) if base_url else None
        self._external = base_url is not None
        self._opa_binary = opa_binary
        self._policy_dir = Path(policy_dir) if policy_dir else default_policy_dir()
        self._timeout = timeout_seconds or _timeout_from_env(
            "LOCUS_OPA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        )
        self._startup_timeout = startup_timeout_seconds or DEFAULT_STARTUP_TIMEOUT_SECONDS
        self._process: subprocess.Popen[bytes] | None = None
        self._stderr: Any = None
        self._lock = threading.Lock()
        # trust_env=False: never route policy traffic through HTTP(S)_PROXY.
        self._client = httpx.Client(
            timeout=self._timeout,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        )
        self._policy_version = policy_version or ""

    # -- construction helpers ------------------------------------------------
    @classmethod
    def from_env(cls, **kwargs: Any) -> OpaSidecarEngine:
        """External sidecar if ``LOCUS_OPA_URL`` is set (loopback only), else managed."""
        url = str(os.getenv("LOCUS_OPA_URL") or "").strip()
        if url:
            return cls(base_url=url, **kwargs)
        return cls(**kwargs)

    @property
    def base_url(self) -> str | None:
        return self._base_url

    @property
    def policy_version(self) -> str:
        return self._policy_version

    @property
    def running(self) -> bool:
        if self._external:
            return self._base_url is not None
        return self._process is not None and self._process.poll() is None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> OpaSidecarEngine:
        """Start (managed) or attach to (external) the sidecar and wait until healthy.

        Raises ``RuntimeError`` if OPA cannot be found or never becomes healthy;
        a caller that ignores that and calls :meth:`decide` still gets denies.
        """
        with self._lock:
            if self._external:
                self._wait_healthy(deadline=time.monotonic() + self._startup_timeout)
                if not self._policy_version:
                    self._policy_version = self._remote_policy_version()
                return self
            if self.running:
                return self
            binary = self._opa_binary or find_opa_binary()
            if not binary:
                raise RuntimeError(
                    "OPA binary not found (LOCUS_OPA_BIN, .tools/opa, bin dir, PATH)"
                )
            files = policy_files(self._policy_dir)
            if not files:
                raise RuntimeError(f"no Rego policies found in {self._policy_dir}")
            self._policy_version = compute_policy_bundle_hash(
                path.read_bytes().decode("utf-8") for path in files
            )
            port = _free_loopback_port()
            argv = [
                binary,
                "run",
                "--server",
                f"--addr=127.0.0.1:{port}",
                "--disable-telemetry",
                "--log-level=error",
                # Bare file names, run from the policy dir: OPA reads "X:..." as a
                # "prefix:path" data mapping, which breaks absolute Windows paths.
                *[f"./{path.name}" for path in files],
            ]
            # Bounded diagnostics: OPA runs at --log-level=error; startup errors only.
            self._stderr = tempfile.TemporaryFile()
            self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                stdin=subprocess.DEVNULL,
                stdout=self._stderr,
                stderr=self._stderr,
                cwd=str(self._policy_dir),
            )
            self._base_url = f"http://127.0.0.1:{port}"
            atexit.register(self.close)
            try:
                self._wait_healthy(deadline=time.monotonic() + self._startup_timeout)
            except Exception:
                self._stop_process()
                raise
            logger.info(
                "policy_engine.started",
                extra={"backend": self.name, "policy_version": self._policy_version},
            )
            return self

    def close(self) -> None:
        with self._lock:
            self._stop_process()
            self._client.close()

    def __enter__(self) -> OpaSidecarEngine:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        stderr, self._stderr = self._stderr, None
        if not self._external:
            self._base_url = None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if stderr is not None:
            stderr.close()

    def _stderr_tail(self, limit: int = 500) -> str:
        handle = self._stderr
        if handle is None:
            return ""
        try:
            handle.seek(0)
            tail: bytes = handle.read()[-limit:]
            return tail.decode("utf-8", errors="replace").strip()
        except (OSError, ValueError):
            return ""

    def _wait_healthy(self, *, deadline: float) -> None:
        last_error = "not checked"
        while time.monotonic() < deadline:
            if not self._external and (self._process is None or self._process.poll() is not None):
                raise RuntimeError(
                    f"OPA sidecar exited during startup (rc={self._process.returncode if self._process else None}): "
                    f"{self._stderr_tail()}"
                )
            try:
                response = self._client.get(f"{self._base_url}/health")
                if response.status_code == 200:
                    return
                last_error = f"status {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
            time.sleep(0.05)
        raise RuntimeError(f"OPA sidecar did not become healthy: {last_error}")

    def _remote_policy_version(self) -> str:
        try:
            response = self._client.get(f"{self._base_url}/v1/policies")
            response.raise_for_status()
            modules = response.json().get("result") or []
            return compute_policy_bundle_hash(
                str(module.get("raw") or "") for module in modules if isinstance(module, dict)
            )
        except Exception:  # noqa: BLE001 - version is informational; decisions stay fail-closed
            return "unknown"

    # -- evaluation ----------------------------------------------------------
    def _deny(self, *reasons: str) -> Decision:
        return Decision(
            allow=False,
            reasons=list(reasons),
            policy_version=self._policy_version or "unknown",
            backend=self.name,
        )

    def decide(self, policy: str, input: dict[str, Any]) -> Decision:  # noqa: A002 - interface name
        if policy not in KNOWN_POLICIES:
            return self._deny(REASON_UNKNOWN_POLICY)
        if not isinstance(input, dict):
            return self._deny(REASON_INVALID_INPUT)
        try:
            body = json.dumps({"input": input}, allow_nan=False)
        except (TypeError, ValueError):
            return self._deny(REASON_INVALID_INPUT)
        base_url = self._base_url
        if not base_url or not self.running:
            return self._deny(REASON_UNAVAILABLE)
        url = f"{base_url}/v1/data/{POLICY_PACKAGE_PREFIX}/{policy}"
        try:
            response = self._client.post(
                url, content=body, headers={"Content-Type": "application/json"}
            )
        except httpx.TimeoutException:
            return self._deny(REASON_TIMEOUT)
        except httpx.TransportError:
            return self._deny(REASON_UNAVAILABLE)
        except Exception:  # noqa: BLE001 - any unexpected failure denies
            logger.exception("policy_engine.error", extra={"policy": policy})
            return self._deny(REASON_ERROR)
        if response.status_code != 200:
            return self._deny(REASON_HTTP_ERROR, f"status:{response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            return self._deny(REASON_MALFORMED)
        return self._interpret(policy, payload)

    def _interpret(self, policy: str, payload: Any) -> Decision:
        if not isinstance(payload, dict):
            return self._deny(REASON_MALFORMED)
        if "result" not in payload:
            # OPA omits "result" when the package is undefined (policy not loaded).
            return self._deny(REASON_UNDEFINED)
        result = payload["result"]
        if not isinstance(result, dict):
            return self._deny(REASON_MALFORMED)
        allow_value = result.get("allow")
        if allow_value is not None and not isinstance(allow_value, bool):
            return self._deny(REASON_MALFORMED)
        outputs = {key: value for key, value in result.items() if key != "allow"}
        allow = allow_value is True
        if allow:
            reasons = [f"{policy}.allow"]
        elif allow_value is None:
            reasons = [f"{policy}.no_allow_rule"]
        elif result.get("deny") is True:
            reasons = [f"{policy}.deny"]
        else:
            reasons = [f"{policy}.not_allowed"]
        return Decision(
            allow=allow,
            reasons=reasons,
            policy_version=self._policy_version or "unknown",
            backend=self.name,
            outputs=outputs,
        )


# --------------------------------------------------------------------------- #
# Backend registry
# --------------------------------------------------------------------------- #
EngineFactory = Callable[..., PolicyEngine]


def _regorus_reserved(**_: Any) -> PolicyEngine:
    raise NotImplementedError(
        "the 'regorus' policy backend is reserved (13 §5) and not implemented yet"
    )


_BACKENDS: dict[str, EngineFactory] = {
    OpaSidecarEngine.name: OpaSidecarEngine.from_env,
    "regorus": _regorus_reserved,
}


def register_backend(name: str, factory: EngineFactory) -> None:
    _BACKENDS[name] = factory


def available_backends() -> list[str]:
    return sorted(_BACKENDS)


def build_policy_engine(backend: str | None = None, **kwargs: Any) -> PolicyEngine:
    """Construct (but do not start) the configured backend (``LOCUS_POLICY_ENGINE``)."""
    name = backend or str(os.getenv("LOCUS_POLICY_ENGINE") or "").strip() or DEFAULT_BACKEND
    factory = _BACKENDS.get(name)
    if factory is None:
        raise PolicyEngineConfigError(f"unknown policy engine backend: {name!r}")
    return factory(**kwargs)


def policy_engine_available() -> bool:
    """Whether a policy engine could be brought up here (no process is started).

    True when a valid loopback ``LOCUS_OPA_URL`` is configured or an OPA binary
    can be located. Used by posture reporting; it does not prove enforcement.
    """
    url = str(os.getenv("LOCUS_OPA_URL") or "").strip()
    if url:
        try:
            validate_loopback_url(url)
        except PolicyEngineConfigError:
            return False
        return True
    return find_opa_binary() is not None
