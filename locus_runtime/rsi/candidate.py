"""Run the eval against CANDIDATE code in a separate, secret-free instance (LOCUS-351).

A :class:`CandidateInstance` executes one eval sample at a time with the
candidate checkout's Python code (the loop's working copy or a branch), never
with the installed instance and never inside the evaluator's process:

* **Code.** A child process: ``<python> -s <trusted candidate_entry.py>`` with
  ``PYTHONPATH`` = the candidate checkout (first) and ``PYTHONSAFEPATH`` /
  ``PYTHONNOUSERSITE`` set. The entry reports where ``locus_runtime`` was loaded
  from; a run that did not load the candidate's code is an error, not a result.
  ``python`` may be a venv built from the checkout or ``sys.executable``.
* **Separate app home.** A fresh temp directory is ``LOCUS_APP_HOME``,
  ``LOCUS_LOOP_HOME``, ``HOME`` / ``USERPROFILE`` / ``APPDATA`` / ``LOCALAPPDATA``
  and ``TEMP``. Telemetry goes to its own SQLite there (``LOCUS_TELEMETRY_DB``).
  No user data is copied in. Only the read-only Windows agent toolchain is
  shared (``LOCUS_TOOLCHAIN_HOME``: BusyBox + embedded Python, no secrets).
* **No secrets.** The environment is built from an allowlist, not copied: no
  ``*_API_KEY``, tokens, ``GH_*``, cloud or Linear credentials. The OS keychain is
  disabled (``PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring``, which the
  secret store rejects as insecure, so it resolves nothing) and the DPAPI
  fallback reads only the empty temp app home. Only keyless model endpoints
  (local Ollama) can be used.
* **Egress.** The only model endpoint is the evaluator's metering proxy
  (:mod:`.metering`) on loopback. ``HTTP(S)_PROXY`` / ``ALL_PROXY`` point at a
  closed loopback port so library traffic to anything but loopback fails;
  LangSmith tracing is forced off. Agent processes run in the platform jail
  (AppContainer / seatbelt / bubblewrap) without network, and every agent
  action and model call goes through the candidate's gateway with the trusted
  policy bundle (``LOCUS_POLICY_DIR``) on the real OPA engine.
* **Never the installed instance.** No shared ports (OPA starts on its own
  ephemeral port; the proxy is the evaluator's), no shared state directories.

Limits (documented in ``docs/development/rsi-scorecard.md``): the candidate's
Python process is not OS-firewalled (loopback stays reachable and the proxy
environment is advisory to code that ignores it); the candidate's in-process
gateway is the candidate's own code, so the D-22 protected-path rule, not the
scorecard, is what keeps gateway and policy changes out of auto-merge; the
candidate runs as the same OS user.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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


class CandidateError(RuntimeError):
    """The candidate instance could not produce a trustworthy result."""


def is_secret_like(name: str) -> bool:
    return bool(_SECRET_NAME.search(name)) and name != "PYTHON_KEYRING_BACKEND"


class CandidateInstance:
    """A separate, secret-free instance of the candidate checkout (one per eval)."""

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
    ) -> None:
        self.checkout = Path(checkout).resolve()
        if not (self.checkout / "locus_runtime").is_dir():
            raise CandidateError(f"not a Locus checkout (no locus_runtime/): {self.checkout}")
        if provider != "ollama":
            # Keyed providers would need a secret inside the candidate (out of scope).
            raise CandidateError(
                f"the candidate instance supports keyless providers only, not {provider!r}"
            )
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
        self._parent_env = dict(parent_env if parent_env is not None else os.environ)
        self._owns_home = home is None
        self.home = Path(home or tempfile.mkdtemp(prefix="locus-candidate-")).resolve()
        for sub in ("app", "loop", "tmp", "profile", "appdata", "localappdata", "runs"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)

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
                "PYTHONPATH": str(self.checkout),
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
            }
        )
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

    # ------------------------------------------------------------- execution
    def _invoke(self, request: Mapping[str, Any]) -> dict[str, Any]:
        tag = secrets.token_hex(6)
        req_path = self.home / "runs" / f"{tag}.request.json"
        out_path = self.home / "runs" / f"{tag}.result.json"
        req_path.write_text(json.dumps(dict(request), sort_keys=True), encoding="utf-8")
        argv = [self.python, "-s", str(ENTRY_SCRIPT), str(req_path), str(out_path)]
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
        if done.returncode != 0 or not out_path.is_file():
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
            raise CandidateError(
                f"the candidate exited {done.returncode}: {' | '.join(tail)[:400]}"
            )
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandidateError("the candidate wrote no readable result") from exc
        if not isinstance(result, dict):
            raise CandidateError("the candidate result is not an object")
        self.check_isolation(result.get("isolation") or {})
        return result

    def check_isolation(self, facts: Mapping[str, Any]) -> None:
        """Fail unless the run used the candidate's code, this home and no keychain."""
        loaded = str(facts.get("locus_runtime") or "")
        try:
            under_checkout = Path(loaded).resolve().is_relative_to(self.checkout)
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

    def run_sample(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """One sample (see ``candidate_entry.run``); ``wall_seconds`` is measured here."""
        payload = {**dict(request), "output_dir": str(self.home / "runs" / str(request["run_id"]))}
        started = time.monotonic()
        result = self._invoke(payload)
        result["wall_seconds"] = round(time.monotonic() - started, 2)
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
        if self._owns_home and not self.keep_home:
            shutil.rmtree(self.home, ignore_errors=True)

    def __enter__(self) -> CandidateInstance:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
