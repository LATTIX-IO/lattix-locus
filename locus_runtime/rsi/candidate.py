"""Run the eval against CANDIDATE code in a separate, secret-free, jailed instance.

A :class:`CandidateInstance` (LOCUS-351) executes one eval sample at a time with
the candidate checkout's Python code (the loop's working copy or a branch), never
with the installed instance and never inside the evaluator's process:

* **Code.** A child process: ``<python> -s <trusted candidate_entry.py>`` with
  ``PYTHONPATH`` = the candidate's code first and ``PYTHONSAFEPATH`` /
  ``PYTHONNOUSERSITE`` set. The entry reports where ``locus_runtime`` was loaded
  from; a run that did not load the candidate's code is an error, not a result.
* **Separate app home.** A fresh temp directory is ``LOCUS_APP_HOME``,
  ``LOCUS_LOOP_HOME``, ``HOME`` / ``USERPROFILE`` / ``APPDATA`` / ``LOCALAPPDATA``
  and ``TEMP``. Telemetry goes to its own SQLite there (``LOCUS_TELEMETRY_DB``).
  No user data is copied in.
* **No secrets.** The environment is built from an allowlist, not copied: no
  ``*_API_KEY``, tokens, ``GH_*``, cloud or Linear credentials. The OS keychain is
  disabled (``PYTHON_KEYRING_BACKEND`` = the null backend) and only keyless model
  endpoints (local Ollama) can be used.

**OS jail (LOCUS-379, the default).** The child runs in the platform jail
(:mod:`.jail`: Windows AppContainer without capabilities, macOS seatbelt, Linux
bubblewrap). It can read only system files, its interpreter, a read-only copy of
the candidate's code (never ``.git``) and the trusted entry script, and write only
its temp home and the sample's workspace. It has no network: everything else
crosses the stdio bridge (:mod:`.bridge`) to this (trusted) parent, which

* relays model calls to the metering proxy (:mod:`.metering`);
* relays policy queries to its own OPA sidecar (the trusted binary and bundle);
* runs the agent's commands in the tool jail (a separate profile, the workspace
  as the only writable root, no network);
* runs the two host-git operations on the sealed workspace (LOCUS-362).

Before scoring, :meth:`CandidateInstance.verify_isolation` proves the jail from
inside: a canary outside the granted paths must be unreadable and unwritable, the
user's home unlistable, loopback and external TCP unreachable, and no keychain or
secret-like variable visible. The scorecard records the tier as ``isolation``.

``LOCUS_RSI_CANDIDATE_UNJAILED=1`` is the explicit, loud opt-out: the child then
runs as the principal's OS user outside any jail (the LOCUS-351 behaviour), the
scorecard says ``isolation: "none"`` and :func:`.scorecard.compare` never
promotes it. Limits: ``docs/development/rsi-scorecard.md`` section 4.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from locus_runtime.rsi import jail as jail_mod
from locus_runtime.rsi.bridge import PROTOCOL, BridgeError, BridgeServer, b64decode, b64encode

logger = logging.getLogger(__name__)

ENTRY_SCRIPT = Path(__file__).resolve().with_name("candidate_entry.py")
NULL_KEYRING = "keyring.backends.null.Keyring"
#: A loopback port nothing listens on (discard): library egress via the env proxy fails.
DEAD_PROXY = "http://127.0.0.1:9"
#: Parent variables passed through unchanged (no secrets: OS plumbing only).
PASS_THROUGH: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "OS",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "LANG",
    "LC_ALL",
    "TZ",
)
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|_PAT\b|AUTH)", re.I)
#: OPA REST paths the bridge relays (decisions, health, the loaded modules).
_OPA_DATA_PATH = re.compile(r"^/v1/data/lattix/[a-z][a-z0-9_]{0,63}$")
_MAX_HTTP_BODY = 16 * 1024 * 1024
_MAX_COMMAND_CHARS = 256 * 1024
#: Job Object / resource caps of the jailed candidate process.
CANDIDATE_MEMORY_BYTES = 4 * 1024**3
CANDIDATE_ACTIVE_PROCESSES = 16
UNJAILED_WARNING = (
    "WARNING: LOCUS_RSI_CANDIDATE_UNJAILED=1: the RSI candidate instance runs "
    "agent-written code as your OS user WITHOUT an OS jail (your file permissions "
    "apply). Its scorecard records isolation 'none' and is never promoted."
)


class CandidateError(RuntimeError):
    """The candidate instance could not produce a trustworthy result."""


def is_secret_like(name: str) -> bool:
    return bool(_SECRET_NAME.search(name)) and name != "PYTHON_KEYRING_BACKEND"


def resolve_isolation(requested: str | None = None) -> str:
    """The candidate's isolation tier on this host (raises when it cannot be jailed).

    ``requested`` (tests, callers) names a tier; ``None`` picks the platform's. The
    unjailed tier ``none`` needs ``LOCUS_RSI_CANDIDATE_UNJAILED=1`` either way."""
    unjailed = jail_mod.unjailed_requested()
    if requested is not None:
        if requested == jail_mod.ISOLATION_NONE and not unjailed:
            raise CandidateError(
                f"an unjailed candidate needs {jail_mod.UNJAILED_ENV}=1 (explicit opt-out)"
            )
        if requested not in (*jail_mod.JAILED_TIERS, jail_mod.ISOLATION_NONE):
            raise CandidateError(f"unknown candidate isolation {requested!r}")
        return requested
    if unjailed:
        return jail_mod.ISOLATION_NONE
    availability = jail_mod.jail_availability()
    if availability.tier is None:
        raise CandidateError(
            f"no OS jail for the candidate instance on this host ({availability.reason}); "
            f"set {jail_mod.UNJAILED_ENV}=1 to run it unjailed (isolation 'none', never promoted)"
        )
    return availability.tier


class CandidateInstance:
    """A separate, secret-free, jailed instance of the candidate checkout (one per eval)."""

    def __init__(
        self,
        checkout: Path,
        *,
        model_base_url: str,
        model: str,
        provider: str = "ollama",
        python: str = "",
        runtime: str = "",
        opa_bin: str = "",
        policy_dir: str = "",
        toolchain_home: str = "",
        timeout_seconds: float = 1800.0,
        home: Path | None = None,
        keep_home: bool = False,
        parent_env: Mapping[str, str] | None = None,
        isolation: str | None = None,
        runtime_cache: Path | None = None,
        runtime_site_packages: bool = True,
    ) -> None:
        self.checkout = Path(checkout).resolve()
        if not (self.checkout / "locus_runtime").is_dir():
            raise CandidateError(f"not a Locus checkout (no locus_runtime/): {self.checkout}")
        if provider != "ollama":
            # Keyed providers would need a secret inside the candidate (out of scope).
            raise CandidateError(
                f"the candidate instance supports keyless providers only, not {provider!r}"
            )
        self.isolation = resolve_isolation(isolation)
        if self.isolation == jail_mod.ISOLATION_NONE:
            logger.warning("rsi.candidate_unjailed")
            sys.stderr.write(UNJAILED_WARNING + "\n")
        self.model_base_url = model_base_url
        self.model = model
        self.provider = provider
        self.python = python or sys.executable
        self.runtime = runtime
        self.opa_bin = opa_bin
        self.policy_dir = policy_dir
        self.toolchain_home = toolchain_home
        self.timeout_seconds = timeout_seconds
        self.keep_home = keep_home
        self.runtime_cache = runtime_cache
        self.runtime_site_packages = runtime_site_packages
        self._parent_env = dict(parent_env if parent_env is not None else os.environ)
        self._owns_home = home is None
        self.home = Path(home or tempfile.mkdtemp(prefix="locus-candidate-")).resolve()
        for sub in ("app", "loop", "tmp", "profile", "appdata", "localappdata", "runs"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        #: Read-only for the jailed child: the code copy (``src``) and the trusted entry.
        self.code_dir = self.home.parent / f"{self.home.name}-code"
        self._prepared = False
        self._jail_python = ""
        self._runtime_roots: list[str] = []
        self._opa: Any = None
        self._http: Any = None
        self._workspace: Path | None = None
        self._granted_workspaces: set[str] = set()
        self._toolchain_granted = False
        self.setup_seconds = 0.0
        self.bridge_stats: dict[str, Any] = {}

    @property
    def jailed(self) -> bool:
        return self.isolation != jail_mod.ISOLATION_NONE

    @property
    def code_root(self) -> Path:
        """Where the child imports the candidate's code from."""
        return self.code_dir / "src" if self.jailed else self.checkout

    @property
    def entry_script(self) -> Path:
        return self.code_dir / "trusted" / ENTRY_SCRIPT.name if self.jailed else ENTRY_SCRIPT

    # ------------------------------------------------------------- environment
    def environment(self) -> dict[str, str]:
        """The candidate's whole environment (built from an allowlist, never copied)."""
        upper = {k.upper(): v for k, v in self._parent_env.items()}
        env = {name: upper[name] for name in PASS_THROUGH if name in upper}
        home = self.home
        proxy_host = "127.0.0.1"
        env.update(
            {
                "LOCUS_APP_HOME": str(home / "app"),
                "LOCUS_LOOP_HOME": str(home / "loop"),
                "LOCUS_TELEMETRY_DB": str(home / "telemetry.db"),
                "LOCUS_TELEMETRY_LOCAL": "1",
                "HOME": str(home / "profile"),
                "USERPROFILE": str(home / "profile"),
                "APPDATA": str(home / "appdata"),
                "LOCALAPPDATA": str(home / "localappdata"),
                "XDG_DATA_HOME": str(home / "localappdata"),
                "XDG_CONFIG_HOME": str(home / "appdata"),
                "TEMP": str(home / "tmp"),
                "TMP": str(home / "tmp"),
                "TMPDIR": str(home / "tmp"),
                "PYTHON_KEYRING_BACKEND": NULL_KEYRING,
                "PYTHONPATH": str(self.code_root),
                "PYTHONNOUSERSITE": "1",
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
                "LANGSMITH_TRACING": "false",
                "LANGCHAIN_TRACING_V2": "false",
                "LANGSMITH_TRACING_V2": "false",
                "HTTP_PROXY": DEAD_PROXY,
                "HTTPS_PROXY": DEAD_PROXY,
                "ALL_PROXY": DEAD_PROXY,
                "NO_PROXY": f"{proxy_host},localhost",
                "OLLAMA_BASE_URL": self.model_base_url,
                "OLLAMA_MODEL": self.model,
                "LOCUS_REQUIRE_OPA": "1",
                "LOCUS_RSI_ISOLATION": self.isolation,
            }
        )
        if self.jailed:
            # Policy decisions and agent commands are the parent's (bridge); the
            # policy copy only feeds the gateway's path classification.
            env["LOCUS_RSI_BRIDGE"] = "stdio"
            env["LOCUS_POLICY_DIR"] = str(self.code_root / "policies")
        else:
            if self.opa_bin:
                env["LOCUS_OPA_BIN"] = self.opa_bin
            if self.policy_dir:
                env["LOCUS_POLICY_DIR"] = self.policy_dir
            if self.toolchain_home:
                env["LOCUS_TOOLCHAIN_HOME"] = self.toolchain_home
        if self.runtime:
            env["LOCUS_AGENT_RUNTIME"] = self.runtime
        leaked = sorted(k for k in env if is_secret_like(k))
        if leaked:  # pragma: no cover - the allowlist above names none
            raise CandidateError(f"secret-like variables in the candidate environment: {leaked}")
        return env

    # ------------------------------------------------------------- jail setup
    def _layout(self) -> jail_mod.JailLayout:
        write = [str(self.home)]
        if self._workspace is not None:
            write.append(str(self._workspace))
        return jail_mod.JailLayout(
            read=(str(self.code_dir), *self._runtime_roots), write=tuple(write)
        )

    def _prepare(self) -> None:
        """Copy the code, build or locate the interpreter and grant the layout (once)."""
        if self._prepared:
            return
        started = time.monotonic()
        from locus_runtime.rsi.win_appcontainer import CANDIDATE_PROFILE

        windows = self.isolation == "appcontainer"
        try:
            self.code_dir.mkdir(parents=True, exist_ok=True)
            if windows:
                # Granted while empty: everything copied in inherits read+execute.
                jail_mod.grant_layout(
                    "appcontainer",
                    CANDIDATE_PROFILE,
                    jail_mod.JailLayout(read=(str(self.code_dir),)),
                )
                jail_mod.grant_layout(
                    "appcontainer", CANDIDATE_PROFILE, jail_mod.JailLayout(write=(str(self.home),))
                )
            jail_mod.copy_code(self.checkout, self.code_dir / "src")
            (self.code_dir / "trusted").mkdir(exist_ok=True)
            shutil.copy2(ENTRY_SCRIPT, self.code_dir / "trusted" / ENTRY_SCRIPT.name)
            info = jail_mod.interpreter_info(self.python)
            if windows:
                from locus_runtime.rsi import win_appcontainer as wac
                from locus_runtime.win_toolchain import toolchain_app_home

                cache = self.runtime_cache or (toolchain_app_home() / "rsi" / "candidate-runtime")
                sid = wac.profile_sid(CANDIDATE_PROFILE)
                python = jail_mod.ensure_runtime(
                    info,
                    cache,
                    grant=lambda d: wac.grant_paths(sid, read=[str(d)]),
                    site_packages=self.runtime_site_packages,
                )
                self._jail_python = str(python)
                self._runtime_roots = [str(python.parent)]
            else:
                self._jail_python = info.executable
                self._runtime_roots = info.read_roots()
        except CandidateError:
            raise
        except Exception as exc:
            raise CandidateError(
                f"the candidate jail could not be prepared ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        self._prepared = True
        self.setup_seconds = round(time.monotonic() - started, 2)

    def _engine(self) -> Any:
        """The parent's OPA sidecar (trusted binary + bundle), started on first use."""
        if self._opa is None:
            from locus_runtime.policy_engine import OpaSidecarEngine, find_opa_binary

            binary = self.opa_bin or find_opa_binary()
            if not binary:
                raise BridgeError("no OPA binary for the parent's policy engine")
            engine = OpaSidecarEngine(
                opa_binary=binary,
                policy_dir=Path(self.policy_dir) if self.policy_dir else None,
                timeout_seconds=10.0,
            )
            engine.start()
            self._opa = engine
        return self._opa

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.Client(timeout=900.0, trust_env=False, follow_redirects=False)
        return self._http

    def _grant_workspace(self, workspace: Path) -> None:
        if self.isolation != "appcontainer" or str(workspace) in self._granted_workspaces:
            return
        from locus_runtime.rsi.win_appcontainer import CANDIDATE_PROFILE, TOOLS_PROFILE

        layout = jail_mod.JailLayout(write=(str(workspace),))
        jail_mod.grant_layout("appcontainer", CANDIDATE_PROFILE, layout)
        jail_mod.grant_layout("appcontainer", TOOLS_PROFILE, layout)
        self._granted_workspaces.add(str(workspace))

    # ------------------------------------------------------------- bridge handlers
    def tool_jail_facts(self) -> dict[str, Any]:
        """What the tool jail really provides (fed to the candidate's tool_jail)."""
        if self.isolation == "appcontainer":
            return {
                "strategy": "windows-appcontainer",
                "allow_network": False,
                "appcontainer": True,
                "job_object": True,
                "require_appcontainer": True,
            }
        from locus_runtime.gateway import current_uid_user

        strategy = "kernel-bwrap" if self.isolation == "bwrap" else "kernel-seatbelt"
        return {
            "strategy": strategy,
            "readonly_rootfs": True,
            "run_as_user": current_uid_user(),
            "allow_network": False,
        }

    def _hello(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL,
            "tool_jail": self.tool_jail_facts(),
            "shell": "sh" if self.isolation == "appcontainer" else "bash",
        }

    def _http_target(self, channel: str, method: str, path: str) -> str:
        if channel == "model":
            parts = urlsplit(self.model_base_url)
            if path not in ("/v1/chat/completions", "/v1/models") or method not in {"GET", "POST"}:
                raise BridgeError("model channel: path or method not allowed")
            return f"{parts.scheme}://{parts.netloc}{path}"
        if channel == "opa":
            allowed = (method == "GET" and path in {"/health", "/v1/policies"}) or (
                method == "POST" and bool(_OPA_DATA_PATH.match(path))
            )
            if not allowed:
                raise BridgeError("opa channel: path or method not allowed")
            return f"{self._engine().base_url}{path}"
        raise BridgeError("unknown http channel")

    def _http_call(self, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        channel = str(payload.get("channel") or "")
        method = str(payload.get("method") or "").upper()
        path = str(payload.get("path") or "")
        url = self._http_target(channel, method, path)
        body = b64decode(payload.get("body") or "")
        if len(body) > _MAX_HTTP_BODY:
            raise BridgeError("request body too large")
        raw_headers = payload.get("headers") or {}
        headers = {
            str(k).lower(): str(v)[:200]
            for k, v in (raw_headers.items() if isinstance(raw_headers, dict) else ())
            if str(k).lower() in {"content-type", "accept"}
        }
        try:
            response = self._client().request(method, url, content=body or None, headers=headers)
        except httpx.HTTPError as exc:
            raise BridgeError(f"{channel} upstream unreachable ({type(exc).__name__})") from exc
        return {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", "application/json"),
            "body": b64encode(response.content),
        }

    def _exec(self, payload: dict[str, Any]) -> dict[str, Any]:
        workspace = self._workspace
        if workspace is None:
            raise BridgeError("no workspace is bound to this run")
        command = payload.get("command")
        if (
            not isinstance(command, list)
            or not command
            or len(command) > 512
            or not all(isinstance(c, str) and "\0" not in c for c in command)
            or sum(len(c) for c in command) > _MAX_COMMAND_CHARS
        ):
            raise BridgeError("exec: malformed command")
        try:
            timeout = max(1, min(3600, int(payload.get("timeout") or 60)))
        except (TypeError, ValueError) as exc:
            raise BridgeError("exec: malformed timeout") from exc
        return self._run_tool(list(command), workspace, timeout)

    def _run_tool(self, command: list[str], workspace: Path, timeout: int) -> dict[str, Any]:
        from locus_runtime.sandbox import minimal_agent_env

        base = self.environment()
        path_prepend: list[str] | None = None
        layout = jail_mod.JailLayout(write=(str(workspace),))
        exec_paths: list[str] = []
        profile = ""
        if self.isolation == "appcontainer":
            from locus_runtime.rsi.win_appcontainer import TOOLS_PROFILE, profile_sid
            from locus_runtime.win_toolchain import (
                MISSING_TOOLCHAIN_HINT,
                discover_toolchain,
                ensure_toolchain_grant,
                needs_toolchain,
            )

            profile = TOOLS_PROFILE
            toolchain = discover_toolchain(
                Path(self.toolchain_home) if self.toolchain_home else None
            )
            if toolchain is not None:
                if not self._toolchain_granted:
                    ensure_toolchain_grant(toolchain.root, profile_sid(TOOLS_PROFILE))
                    self._toolchain_granted = True
                path_prepend = toolchain.path_dirs()
            if needs_toolchain(command):
                if toolchain is None:
                    return {
                        "exit_code": 127,
                        "stdout": "",
                        "stderr": f"[toolchain missing] {MISSING_TOOLCHAIN_HINT}",
                        "duration_seconds": 0.0,
                        "timed_out": False,
                        "backend": "rsi-tool-jail",
                    }
                command = toolchain.resolve(command)
        elif self.isolation == "seatbelt":
            extra = ["/usr/local", "/opt/homebrew", *self._runtime_roots]
            layout = jail_mod.JailLayout(read=tuple(extra), write=(str(workspace),))
            exec_paths = [*jail_mod.MACOS_SYSTEM_PATHS, *extra]
        env = minimal_agent_env(None, base=base, path_prepend=path_prepend)
        try:
            result = jail_mod.run_tool(
                self.isolation,
                command,
                layout=layout,
                env=env,
                cwd=str(workspace),
                timeout=float(timeout),
                profile=profile,
                exec_paths=exec_paths,
            )
        except OSError as exc:
            return {
                "exit_code": 126,
                "stdout": "",
                "stderr": f"[tool jail] the command could not start ({type(exc).__name__})",
                "duration_seconds": 0.0,
                "timed_out": False,
                "backend": "rsi-tool-jail",
            }
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_seconds": result.duration_seconds,
            "timed_out": result.timed_out,
            "backend": f"rsi-tool-jail:{self.isolation}",
        }

    def _git(self, payload: dict[str, Any]) -> dict[str, Any]:
        from locus_runtime.loop_runner.delivery import DeliveryError, GitOps, HostWorkspaceGit

        workspace = self._workspace
        if workspace is None:
            raise BridgeError("no workspace is bound to this run")
        host = HostWorkspaceGit(GitOps(), workspace)
        call = payload.get("call")
        try:
            if call == "has_uncommitted_changes":
                return {"changes": host.has_uncommitted_changes()}
            if call == "diff":
                base = payload.get("base")
                specs = payload.get("pathspecs") or []
                if (
                    not isinstance(base, str)
                    or len(base) > 200
                    or not isinstance(specs, list)
                    or len(specs) > 64
                    or not all(isinstance(p, str) and len(p) <= 300 for p in specs)
                ):
                    raise BridgeError("git diff: malformed arguments")
                return {"diff": host.diff(base, specs)}
        except DeliveryError as exc:
            raise BridgeError(f"host git refused: {str(exc)[:200]}") from exc
        raise BridgeError("git: unknown call")

    def handlers(self) -> dict[str, Any]:
        return {"hello": self._hello, "http": self._http_call, "exec": self._exec, "git": self._git}

    # ------------------------------------------------------------- execution
    def _invoke(self, request: Mapping[str, Any], *, check: bool = True) -> dict[str, Any]:
        tag = secrets.token_hex(6)
        req_path = self.home / "runs" / f"{tag}.request.json"
        out_path = self.home / "runs" / f"{tag}.result.json"
        req_path.write_text(json.dumps(dict(request), sort_keys=True), encoding="utf-8")
        if self.jailed:
            code, stderr = self._run_jailed(req_path, out_path)
        else:
            code, stderr = self._run_unjailed(req_path, out_path)
        if code != 0 or not out_path.is_file():
            tail = stderr.strip().splitlines()[-3:]
            raise CandidateError(f"the candidate exited {code}: {' | '.join(tail)[:400]}")
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandidateError("the candidate wrote no readable result") from exc
        if not isinstance(result, dict):
            raise CandidateError("the candidate result is not an object")
        if check:
            self.check_isolation(result.get("isolation") or {})
        return result

    def _argv(self, python: str, req_path: Path, out_path: Path) -> list[str]:
        return [python, "-s", str(self.entry_script), str(req_path), str(out_path)]

    def _run_unjailed(self, req_path: Path, out_path: Path) -> tuple[int, str]:
        argv = self._argv(self.python, req_path, out_path)
        try:
            done = subprocess.run(
                argv,
                cwd=str(self.home),
                env=self.environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise CandidateError(
                f"the candidate run timed out after {self.timeout_seconds:.0f}s"
            ) from exc
        except OSError as exc:
            raise CandidateError(
                f"the candidate python could not start ({type(exc).__name__})"
            ) from exc
        return done.returncode, done.stderr or done.stdout or ""

    def _run_jailed(self, req_path: Path, out_path: Path) -> tuple[int, str]:
        from locus_runtime.rsi.win_appcontainer import CANDIDATE_PROFILE

        self._prepare()
        try:
            proc = jail_mod.launch(
                self.isolation,
                self._argv(self._jail_python, req_path, out_path),
                layout=self._layout(),
                env=self.environment(),
                cwd=str(self.home),
                profile=CANDIDATE_PROFILE,
                exec_paths=self._runtime_roots,
                memory_bytes=CANDIDATE_MEMORY_BYTES,
                active_processes=CANDIDATE_ACTIVE_PROCESSES,
            )
        except OSError as exc:
            raise CandidateError(
                f"the jailed candidate could not start ({type(exc).__name__}: {str(exc)[:200]})"
            ) from exc
        server = BridgeServer(proc.stdout, proc.stdin, self.handlers()).start()
        errors = _Tail(proc.stderr)
        errors.start()
        try:
            code = proc.wait(self.timeout_seconds)
            if code is None:
                proc.kill()
                proc.wait(30.0)
                raise CandidateError(
                    f"the candidate run timed out after {self.timeout_seconds:.0f}s"
                )
        finally:
            server.join(30.0)
            errors.join(10.0)
            proc.close()
            self.bridge_stats = {
                "ops": dict(server.counts),
                "refused": server.refused,
                "error": server.error,
            }
        return code, errors.text()

    def check_isolation(self, facts: Mapping[str, Any]) -> None:
        """Fail unless the run used the candidate's code, this home and no keychain."""
        loaded = str(facts.get("locus_runtime") or "")
        try:
            under_checkout = Path(loaded).resolve().is_relative_to(self.code_root.resolve())
        except (OSError, ValueError):
            under_checkout = False
        if not under_checkout:
            raise CandidateError(
                f"the candidate did not load the candidate's code ({loaded or 'unknown'})"
            )
        if Path(str(facts.get("app_home") or "")).resolve() != (self.home / "app").resolve():
            raise CandidateError("the candidate did not run in its own app home")
        backend = str(facts.get("keyring_backend") or "")
        if backend and not backend.startswith(("keyring.backends.null", "unavailable")):
            raise CandidateError(f"the candidate can reach a keychain backend ({backend})")
        keys = facts.get("provider_keys")
        if keys:
            raise CandidateError(f"provider keys resolve inside the candidate: {keys}")
        store = str(facts.get("secret_store_home") or "")
        if store and not Path(store).resolve().is_relative_to(self.home):
            raise CandidateError("the candidate's secret store points outside its own home")
        if facts.get("secret_like_env"):
            raise CandidateError(
                f"secret-like variables reached the candidate: {facts['secret_like_env']}"
            )

    def probe(self) -> dict[str, Any]:
        """Start the candidate once without a run and return its isolation facts."""
        return dict(self._invoke({"mode": "probe"}).get("isolation") or {})

    def escape_probe(
        self,
        *,
        read: Sequence[str] = (),
        write: Sequence[str] = (),
        listing: Sequence[str] = (),
        connect: Sequence[tuple[str, int]] = (),
    ) -> dict[str, Any]:
        """Run the stdlib-only escape attempts inside the candidate's jail (or, for
        ``none``, as a plain child: the positive control) and return the report."""
        spec = {
            "read": list(read),
            "write": list(write),
            "list": list(listing),
            "connect": [[h, int(p)] for h, p in connect],
        }
        result = self._invoke({"mode": "escape-probe", "escape": spec}, check=False)
        return dict(result.get("escape") or {})

    def verify_isolation(
        self, *, external: tuple[str, int] | None = ("1.1.1.1", 443)
    ) -> dict[str, Any]:
        """Prove the jail from inside before scoring (raises :class:`CandidateError`).

        A fresh canary in a directory that is **not** granted must be unreadable and
        unwritable, the evaluator's home unlistable, a loopback listener and an
        external host unreachable, no OS credential store reachable and no
        secret-like variable visible. Returns the probe report (evidence)."""
        if not self.jailed:
            raise CandidateError("the candidate is not jailed (isolation 'none')")
        outside = Path(tempfile.mkdtemp(prefix="locus-canary-"))
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            canary = outside / "canary.txt"
            canary.write_text(secrets.token_hex(16), encoding="utf-8")
            before = canary.read_text(encoding="utf-8")
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            port = int(listener.getsockname()[1])
            targets: list[tuple[str, int]] = [("127.0.0.1", port)]
            if external is not None:
                targets.append(external)
            report = self.escape_probe(
                read=[str(canary)],
                write=[str(canary)],
                listing=[str(Path.home()), str(outside)],
                connect=targets,
            )
            escaped = escapes(report)
            if canary.read_text(encoding="utf-8") != before:
                escaped.append(f"write:{canary}")
            if _accepted_any(listener):
                escaped.append(f"connect:127.0.0.1:{port} (accepted)")
        finally:
            listener.close()
            shutil.rmtree(outside, ignore_errors=True)
        if escaped:
            raise CandidateError(f"the candidate jail leaked: {', '.join(sorted(set(escaped)))}")
        return report

    def run_sample(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One sample (see ``candidate_entry.run``); ``wall_seconds`` is measured here."""
        payload = {**dict(request), "output_dir": str(self.home / "runs" / str(request["run_id"]))}
        started = time.monotonic()
        workspace = Path(str(request.get("workspace") or "")).resolve()
        if self.jailed:
            if not workspace.is_dir():
                raise CandidateError("the sample's workspace does not exist")
            from locus_runtime.loop_runner.delivery import GitOps

            self._prepare()
            self._grant_workspace(workspace)
            if (workspace / ".git").exists():
                GitOps().seal(workspace)  # host git verifies it on every bridged call
            self._workspace = workspace
        try:
            result = self._invoke(payload)
        finally:
            self._workspace = None
        result["wall_seconds"] = round(time.monotonic() - started, 2)
        result["candidate_isolation"] = self.isolation
        if self.jailed:
            result["bridge"] = dict(self.bridge_stats)
        return result

    # ------------------------------------------------------------- lifecycle
    def export(self, target: Path) -> None:
        """Copy the candidate's evidence (telemetry DB, audit, trajectories) to ``target``."""
        target.mkdir(parents=True, exist_ok=True)
        db = self.home / "telemetry.db"
        if db.is_file():
            shutil.copy2(db, target / "telemetry.db")
        runs = self.home / "runs"
        if runs.is_dir():
            shutil.copytree(runs, target / "runs", dirs_exist_ok=True)

    def close(self) -> None:
        if self._opa is not None:
            self._opa.close()
            self._opa = None
        if self._http is not None:
            self._http.close()
            self._http = None
        if not self.keep_home:
            if self.code_dir.exists():
                jail_mod._force_rmtree(self.code_dir)  # noqa: SLF001 - same package
            if self._owns_home:
                shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self) -> CandidateInstance:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def escapes(report: Mapping[str, Any]) -> list[str]:
    """Every escape attempt in an ``escape_probe`` report that succeeded. Pure."""
    found: list[str] = []
    for kind in ("read", "write", "list", "connect"):
        for target, outcome in dict(report.get(kind) or {}).items():
            if outcome == "ok":
                found.append(f"{kind}:{target}")
    keychain = dict(report.get("keychain") or {})
    for name, outcome in keychain.items():
        if name == "keyring_backend":
            if not str(outcome).startswith(("keyring.backends.null", "unavailable")):
                found.append(f"keychain:{outcome}")
        elif outcome == "ok":
            found.append(f"keychain:{name}")
    for name in report.get("secret_like_env") or []:
        found.append(f"env:{name}")
    return found


def _accepted_any(listener: socket.socket) -> bool:
    listener.settimeout(0.2)
    try:
        conn, _addr = listener.accept()
    except OSError:
        return False
    conn.close()
    return True


class _Tail(threading.Thread):
    """Drain the child's stderr, keeping the last 64 KiB (never logged)."""

    def __init__(self, stream: Any) -> None:
        super().__init__(daemon=True, name="rsi-candidate-stderr")
        self._stream = stream
        self._data = bytearray()

    def run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(65536)
                if not chunk:
                    return
                self._data += chunk
                if len(self._data) > 65536:
                    del self._data[: len(self._data) - 65536]
        except (OSError, ValueError):
            return

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="replace")
