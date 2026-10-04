"""``lattix evals sync``: the private RSI held-out split, installed read-only (LOCUS-382).

The held-out tasks (the exam the self-improvement loop is promoted on) live in a
separate private repository (:mod:`locus_tooling.evals_heldout`), never in the
main repository the loop's coding agent works in. This module:

1. **fetches** the pinned ref with ``git clone --depth 1 --branch <ref>`` through
   the hardened host :class:`~locus_runtime.loop_runner.delivery.GitOps` (hooks
   and fsmonitor off, no external diff, no ``ext::`` transport, no checkout:
   files are read as blobs, so no filter, attribute or symlink of the fetched
   repository runs). Credentials are the user's own (git's credential helper,
   ``gh auth``, SSH keys); nothing here reads, stores or prints them;
2. **verifies** ``MANIFEST.json``: exactly the listed ``heldout/<id>.yaml`` files
   (regular blobs, bounded sizes), each sha256 (LF-normalized) and the suite
   digest; the digest is the scorecard's ``split_digests.heldout``;
3. **verifies the tag signature** when the tag is signed and ``git verify-tag``
   can run here, and records whether it ran. A bad signature always fails;
   ``LOCUS_EVALS_REQUIRE_SIGNED=1`` also refuses unsigned or unverifiable tags;
4. **installs** the tasks read-only under ``<app_home>/evals/heldout/<digest>/``
   (staged, renamed into place, files read-only: the same sealing as the suite
   store) and records the active digest in ``<app_home>/evals/heldout/active.json``.

Idempotent: a verified install of the same digest is reused. The suite loader
resolves the held-out split with :func:`resolve_heldout`:
``LOCUS_EVAL_HELDOUT_DIR`` wins, else the recorded active digest (re-verified),
else nothing (the scorecard reports ``skipped: not synced`` and holds).

``<app_home>/evals/`` is granted to no jail: the RSI candidate and the tool jail
(LOCUS-379) cannot read it, and the loop's agent works in a repository clone
that no longer contains held-out tasks. This module and
:mod:`locus_tooling.evals_heldout` are D-22 protected paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from locus_runtime.rsi.readonly import force_rmtree, is_writable, make_read_only, make_writable
from locus_tooling.evals_heldout import (
    HELDOUT_DIR_ENV,
    HELDOUT_REF,
    HELDOUT_REPOSITORY,
    REF_ENV,
    REPOSITORY_ENV,
    REQUIRE_SIGNED_ENV,
)

SPLIT = "heldout"
MANIFEST = "MANIFEST.json"
SOURCE = "SOURCE.json"
STATE = "active.json"
MANIFEST_FORMAT = 1
MANIFEST_KIND = "locus.rsi_heldout"
STATE_VERSION = 1
MAX_TASKS = 500
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
CLONE_TIMEOUT = 300
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/@\s]+@")
_SIGNATURE_MARKERS = (
    "-----BEGIN PGP SIGNATURE-----",
    "-----BEGIN SSH SIGNATURE-----",
    "-----BEGIN SIGNED MESSAGE-----",
)
_BAD_SIGNATURE = re.compile(
    r"\bBADSIG\b|bad signature|incorrect signature|signature verification failed", re.I
)
_VERIFIER_MISSING = re.compile(
    r"cannot run|no such file|not found|allowedSignersFile|gpg\.ssh|unable to start", re.I
)

ErrorCode = Literal["config", "no_access", "git", "manifest", "signature", "install"]
SignatureStatus = Literal["verified", "bad", "unverifiable", "unsigned", "not_a_tag"]
Origin = Literal["argument", "env", "synced", "none"]


class HeldoutSyncError(RuntimeError):
    """The held-out split could not be synced (``code`` says why; never a secret)."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(redact(message))
        self.code = code


def redact(text: str) -> str:
    """Strip URL credentials and secret-shaped tokens from a message."""
    from locus_runtime.gateway import redact_text

    return redact_text(_URL_CREDENTIALS.sub(r"\1***@", str(text or "")), limit=600)


def repository_locator(url: str) -> str:
    """Return ``scheme://host[:port]/path`` for a URL, never userinfo, query or fragment.

    A local path or scp-style address (``git@host:org/repo``) keeps only what
    follows the last ``@``.
    """
    text = str(url or "")
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}{parts.path}"
    return text.rsplit("@", 1)[-1]


# --------------------------------------------------------------------------- #
# Manifest (pure)
# --------------------------------------------------------------------------- #
def content_sha(data: bytes) -> str:
    """sha256 with CRLF normalized to LF (``locus_evals.suite.loader.content_digest``)."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def suite_digest(files: Mapping[str, str]) -> str:
    """sha256 over ``heldout/<file>\\0<sha>\\n`` (``loader.split_digest`` of the split)."""
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(f"{name}\0{files[name]}\n".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class HeldoutManifest:
    digest: str
    tasks: tuple[str, ...]
    files: Mapping[str, str]
    suite_version: str = ""

    def to_json(self) -> str:
        return (
            json.dumps(
                {
                    "format": MANIFEST_FORMAT,
                    "kind": MANIFEST_KIND,
                    "split": SPLIT,
                    "suite_version": self.suite_version,
                    "digest": self.digest,
                    "tasks": list(self.tasks),
                    "files": dict(self.files),
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )


def _task_name(name: str) -> str:
    """The task id of ``heldout/<id>.yaml`` (raises on anything else)."""
    prefix, _, base = name.partition("/")
    if prefix != SPLIT or "/" in base or not base.endswith(".yaml"):
        raise HeldoutSyncError("manifest", f"unexpected file in the held-out split: {name!r}")
    task_id = base[: -len(".yaml")]
    if not _ID.fullmatch(task_id):
        raise HeldoutSyncError("manifest", f"invalid held-out task id {task_id!r}")
    return task_id


def parse_manifest(raw: bytes) -> HeldoutManifest:
    """Validate ``MANIFEST.json`` (shape only; contents are checked by :func:`verify_files`)."""
    if len(raw) > MAX_FILE_BYTES:
        raise HeldoutSyncError("manifest", f"{MANIFEST} is too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HeldoutSyncError("manifest", f"{MANIFEST} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise HeldoutSyncError("manifest", f"{MANIFEST} must be an object")
    if data.get("format") != MANIFEST_FORMAT or data.get("kind") != MANIFEST_KIND:
        raise HeldoutSyncError(
            "manifest", f"{MANIFEST}: unsupported format/kind (want {MANIFEST_FORMAT})"
        )
    if data.get("split") != SPLIT:
        raise HeldoutSyncError("manifest", f"{MANIFEST}: split must be {SPLIT!r}")
    digest, files, tasks = data.get("digest"), data.get("files"), data.get("tasks")
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise HeldoutSyncError("manifest", f"{MANIFEST}: digest must be 64 lower-case hex chars")
    if not isinstance(files, dict) or not files or len(files) > MAX_TASKS:
        raise HeldoutSyncError("manifest", f"{MANIFEST}: files must list 1..{MAX_TASKS} tasks")
    clean: dict[str, str] = {}
    for name, sha in files.items():
        if not isinstance(name, str) or not isinstance(sha, str) or not _DIGEST.fullmatch(sha):
            raise HeldoutSyncError("manifest", f"{MANIFEST}: bad entry for {name!r}")
        _task_name(name)
        clean[name] = sha
    if not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        raise HeldoutSyncError("manifest", f"{MANIFEST}: tasks must be a list of ids")
    if sorted(tasks) != sorted(_task_name(n) for n in clean):
        raise HeldoutSyncError("manifest", f"{MANIFEST}: tasks do not match files")
    if suite_digest(clean) != digest:
        raise HeldoutSyncError("manifest", f"{MANIFEST}: digest does not match its file hashes")
    version = data.get("suite_version")
    return HeldoutManifest(
        digest=digest,
        tasks=tuple(sorted(tasks)),
        files=clean,
        suite_version=version if isinstance(version, str) else "",
    )


def verify_files(manifest: HeldoutManifest, contents: Mapping[str, bytes]) -> None:
    """Exactly the manifest's files, each with its sha256, and the suite digest."""
    missing = sorted(set(manifest.files) - set(contents))
    extra = sorted(set(contents) - set(manifest.files))
    if missing or extra:
        raise HeldoutSyncError(
            "manifest",
            "held-out files do not match the manifest"
            + (f"; missing {missing[:5]}" if missing else "")
            + (f"; not listed {extra[:5]}" if extra else ""),
        )
    actual = {name: content_sha(data) for name, data in contents.items()}
    changed = sorted(n for n in manifest.files if actual[n] != manifest.files[n])
    if changed:
        raise HeldoutSyncError("manifest", f"sha256 mismatch for {changed[:5]}")
    if suite_digest(actual) != manifest.digest:
        raise HeldoutSyncError("manifest", "suite digest mismatch")


# --------------------------------------------------------------------------- #
# Locations and state
# --------------------------------------------------------------------------- #
def heldout_root(app_home: Path | None = None) -> Path:
    """``<app_home>/evals/heldout`` (the evaluator's app home, never a jail's)."""
    if app_home is None:
        from locus_runtime.win_toolchain import toolchain_app_home

        app_home = toolchain_app_home()
    return Path(app_home) / "evals" / SPLIT


@dataclass(frozen=True)
class SignatureCheck:
    #: The tag object carries a signature block.
    signed: bool
    #: ``git verify-tag`` ran with a working verifier (gpg / gpgsm / ssh-keygen).
    ran: bool
    status: SignatureStatus
    detail: str = ""


@dataclass(frozen=True)
class SyncResult:
    digest: str
    path: Path
    repository: str
    ref: str
    commit: str
    tasks: int
    installed: bool
    signature: SignatureCheck
    state_path: Path
    synced_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "installed" if self.installed else "already-installed",
            "digest": self.digest,
            "path": str(self.path),
            "repository": self.repository,
            "ref": self.ref,
            "commit": self.commit,
            "tasks": self.tasks,
            "signature": asdict(self.signature),
            "state": str(self.state_path),
            "synced_at": self.synced_at,
        }


def read_state(root: Path) -> dict[str, Any]:
    """The recorded active sync (``{}`` when there is none or it is unreadable)."""
    try:
        data = json.loads((root / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
        return {}
    if not _DIGEST.fullmatch(str(data.get("digest") or "")):
        return {}
    return data


def _write_state(root: Path, state: Mapping[str, Any]) -> Path:
    target = root / STATE
    tmp = root / f".{STATE}.{secrets.token_hex(4)}.tmp"
    tmp.write_text(json.dumps(dict(state), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------- #
# Install and verify (local)
# --------------------------------------------------------------------------- #
def _installed_files(dest: Path) -> dict[str, bytes]:
    split = dest / SPLIT
    out: dict[str, bytes] = {}
    if not split.is_dir():
        return out
    for path in sorted(split.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise HeldoutSyncError("install", f"unexpected entry in {split}: {path.name}")
        out[f"{SPLIT}/{path.name}"] = path.read_bytes()
    return out


def verify_installed(dest: Path, digest: str) -> HeldoutManifest:
    """Re-verify an install from its bytes: every file hashed, the digest equal to
    ``digest`` (the folder name and the recorded state), every file read-only."""
    if dest.name != digest:
        raise HeldoutSyncError("install", "held-out folder name does not match its digest")
    try:
        manifest = parse_manifest((dest / MANIFEST).read_bytes())
    except OSError as exc:
        raise HeldoutSyncError("install", f"no {MANIFEST} in {dest}") from exc
    if manifest.digest != digest:
        raise HeldoutSyncError("install", "installed manifest digest differs from the recorded one")
    verify_files(manifest, _installed_files(dest))
    writable = [p.name for p in [dest / MANIFEST, *(dest / SPLIT).iterdir()] if is_writable(p)]
    if writable:
        raise HeldoutSyncError("install", f"held-out files became writable: {writable[:5]}")
    return manifest


def install_heldout(
    root: Path,
    manifest: HeldoutManifest,
    contents: Mapping[str, bytes],
    source: Mapping[str, Any] | None = None,
) -> tuple[Path, bool]:
    """Install verified ``contents`` read-only at ``root/<digest>`` (idempotent).

    Returns ``(path, installed)``; ``installed`` is false when a verified install
    of the same digest was already there. A damaged install is replaced."""
    verify_files(manifest, contents)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / manifest.digest
    if dest.exists():
        try:
            verify_installed(dest, manifest.digest)
            return dest, False
        except HeldoutSyncError:
            make_writable(dest)
            force_rmtree(dest)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    try:
        (staging / SPLIT).mkdir()
        for name, data in contents.items():
            (staging / name).write_bytes(data)
        (staging / MANIFEST).write_text(manifest.to_json(), encoding="utf-8", newline="\n")
        if source is not None:
            (staging / SOURCE).write_text(
                json.dumps(dict(source), indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
        verify_files(manifest, _installed_files(staging))
        os.replace(staging, dest)
    except BaseException:
        make_writable(staging)
        force_rmtree(staging)
        raise
    make_read_only(dest)
    verify_installed(dest, manifest.digest)
    return dest, True


# --------------------------------------------------------------------------- #
# Resolution (what the suite loader uses)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HeldoutResolution:
    #: The folder holding ``<task-id>.yaml`` files (``None`` = no held-out split).
    path: Path | None
    origin: Origin
    digest: str = ""
    ref: str = ""
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    def describe(self) -> str:
        if self.origin == "synced":
            return f"synced {self.ref or '?'} ({self.digest[:12]})"
        if self.origin == "env":
            return f"{HELDOUT_DIR_ENV} ({self.path})"
        if self.origin == "argument":
            return f"private directory ({self.path})"
        return f"skipped: {self.reason or 'not synced'}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "origin": self.origin,
            "path": str(self.path) if self.path else "",
            "digest": self.digest,
            "ref": self.ref,
            "status": self.describe(),
        }


NOT_SYNCED = "not synced"


def resolve_heldout(
    *,
    explicit: Path | None = None,
    env: Mapping[str, str] | None = None,
    app_home: Path | None = None,
) -> HeldoutResolution:
    """Where the held-out split comes from: ``explicit`` (a caller's private folder),
    then ``LOCUS_EVAL_HELDOUT_DIR``, then the recorded synced digest (re-verified
    from its bytes). Otherwise ``origin == "none"`` with the reason (fail closed)."""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_dir():
            return HeldoutResolution(None, "none", reason=f"held-out directory not found: {path}")
        return HeldoutResolution(path, "argument")
    source = os.environ if env is None else env
    override = str(source.get(HELDOUT_DIR_ENV) or "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_dir():
            return HeldoutResolution(None, "none", reason=f"{HELDOUT_DIR_ENV} is not a directory")
        return HeldoutResolution(path, "env")
    root = heldout_root(app_home)
    state = read_state(root)
    if not state:
        return HeldoutResolution(None, "none", reason=NOT_SYNCED)
    digest = str(state["digest"])
    dest = root / digest
    try:
        verify_installed(dest, digest)
    except HeldoutSyncError as exc:
        return HeldoutResolution(
            None,
            "none",
            digest=digest,
            reason=f"synced held-out split failed verification ({exc}); run `lattix evals sync`",
        )
    return HeldoutResolution(dest / SPLIT, "synced", digest=digest, ref=str(state.get("ref") or ""))


# --------------------------------------------------------------------------- #
# Sync (network, the user's git credentials)
# --------------------------------------------------------------------------- #
def configured_source(
    repository: str | None = None, ref: str | None = None, env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    """``(repository, ref)``: the arguments, else the environment, else the pinned config."""
    source = os.environ if env is None else env
    repo = (repository or str(source.get(REPOSITORY_ENV) or "") or HELDOUT_REPOSITORY).strip()
    pinned = (ref or str(source.get(REF_ENV) or "") or HELDOUT_REF).strip()
    return repo, pinned


def require_signed(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(REQUIRE_SIGNED_ENV) or "").strip() == "1"


def _git_env(base: Mapping[str, str], *, interactive: bool) -> dict[str, str]:
    env = dict(base)
    # Never a pager; never prompt where nobody can answer.
    env["GIT_PAGER"] = "cat"
    if not interactive:
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"
        env["GIT_ASKPASS"] = ""
        env["SSH_ASKPASS"] = ""
    return env


def check_signature(git: Any, repo: Path, ref: str, env: Mapping[str, str]) -> SignatureCheck:
    """Verify ``ref``'s tag signature when it is signed and a verifier can run here."""
    kind, body = git.tag_object(repo, ref)
    if kind == "":
        return SignatureCheck(False, False, "not_a_tag", f"{ref} is not a tag")
    if kind != "tag" or not any(marker in body for marker in _SIGNATURE_MARKERS):
        return SignatureCheck(False, False, "unsigned", "lightweight or unsigned tag")
    code, output = git.verify_tag(repo, ref, env=env)
    detail = redact(output.strip().splitlines()[-1] if output.strip() else "")[:300]
    if code == 0:
        return SignatureCheck(True, True, "verified", detail)
    if _BAD_SIGNATURE.search(output):
        return SignatureCheck(True, True, "bad", detail)
    ran = not _VERIFIER_MISSING.search(output)
    return SignatureCheck(True, ran, "unverifiable", detail)


def _read_tree(git: Any, repo: Path) -> tuple[bytes, dict[str, bytes]]:
    manifest_raw = b""
    contents: dict[str, bytes] = {}
    total = 0
    for mode, sha, path in git.tree_blobs(repo):
        if path == MANIFEST:
            if mode != "100644":
                raise HeldoutSyncError("manifest", f"{MANIFEST} must be a regular file")
            manifest_raw = git.read_blob(repo, sha)
            continue
        if path != SPLIT and not path.startswith(f"{SPLIT}/"):
            continue  # documentation and tooling are not installed
        if mode != "100644":
            raise HeldoutSyncError(
                "manifest", f"{path}: only regular files belong in {SPLIT}/ (mode {mode})"
            )
        _task_name(path)
        if len(contents) >= MAX_TASKS:
            raise HeldoutSyncError("manifest", f"more than {MAX_TASKS} held-out files")
        data = git.read_blob(repo, sha)
        if len(data) > MAX_FILE_BYTES:
            raise HeldoutSyncError("manifest", f"{path} is larger than {MAX_FILE_BYTES} bytes")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise HeldoutSyncError("manifest", "the held-out split is too large")
        contents[path] = data
    if not manifest_raw:
        raise HeldoutSyncError("manifest", f"no {MANIFEST} at the root of the fetched ref")
    return manifest_raw, contents


def sync_heldout(
    *,
    repository: str | None = None,
    ref: str | None = None,
    app_home: Path | None = None,
    git: Any = None,
    env: Mapping[str, str] | None = None,
    interactive: bool = True,
    signed_required: bool | None = None,
    timeout: int = CLONE_TIMEOUT,
    now: datetime | None = None,
) -> SyncResult:
    """Fetch, verify and install the pinned held-out split; record it as active."""
    from locus_runtime.loop_runner.delivery import DeliveryError, GitOps

    base_env = dict(os.environ if env is None else env)
    repo_url, pinned = configured_source(repository, ref, base_env)
    must_sign = require_signed(base_env) if signed_required is None else signed_required
    ops = git or GitOps(timeout=timeout)
    root = heldout_root(app_home)
    root.mkdir(parents=True, exist_ok=True)
    git_env = _git_env(base_env, interactive=interactive)
    work = Path(tempfile.mkdtemp(prefix=".fetch-", dir=root))
    try:
        try:
            commit = ops.clone_ref(repo_url, work / "repo", pinned, env=git_env, timeout=timeout)
        except DeliveryError as exc:
            message = str(exc)
            refused = any(k in message for k in ("refusing", "timed out", "not available"))
            code: ErrorCode = "git" if refused else "no_access"
            raise HeldoutSyncError(
                code,
                f"could not fetch {pinned!r} from {repo_url}: {message} "
                "(the held-out repository is private: sign in with your own git "
                "credentials, e.g. `gh auth login`, or set LOCUS_EVAL_HELDOUT_DIR)",
            ) from exc
        clone = work / "repo"
        try:
            signature = check_signature(ops, clone, pinned, git_env)
            manifest_raw, contents = _read_tree(ops, clone)
        except DeliveryError as exc:
            raise HeldoutSyncError("git", f"reading the fetched ref failed: {exc}") from exc
        if signature.status == "bad":
            raise HeldoutSyncError(
                "signature", f"tag {pinned!r} has a BAD signature: {signature.detail}"
            )
        if must_sign and signature.status != "verified":
            raise HeldoutSyncError(
                "signature",
                f"{REQUIRE_SIGNED_ENV}=1 but tag {pinned!r} is {signature.status}"
                + (f" ({signature.detail})" if signature.detail else ""),
            )
        manifest = parse_manifest(manifest_raw)
        verify_files(manifest, contents)
        stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # The on-disk provenance record keeps no credential-bearing or free-text
        # fields: the repository is reduced to its locator (userinfo, query and
        # fragment dropped) and the signature to its status flags.
        source = {
            "repository": repository_locator(repo_url),
            "ref": pinned,
            "commit": commit,
            "signature": {
                "signed": bool(signature.signed),
                "ran": bool(signature.ran),
                "status": str(signature.status),
            },
            "synced_at": stamp,
        }
        try:
            dest, installed = install_heldout(root, manifest, contents, source)
        except OSError as exc:
            raise HeldoutSyncError(
                "install", f"installing the held-out split failed ({type(exc).__name__})"
            ) from exc
        state_path = _write_state(
            root,
            {
                "version": STATE_VERSION,
                "digest": manifest.digest,
                "tasks": len(manifest.tasks),
                "suite_version": manifest.suite_version,
                **source,
            },
        )
    finally:
        make_writable(work)
        force_rmtree(work)
    return SyncResult(
        digest=manifest.digest,
        path=dest,
        repository=redact(repo_url),
        ref=pinned,
        commit=commit,
        tasks=len(manifest.tasks),
        installed=installed,
        signature=signature,
        state_path=state_path,
        synced_at=stamp,
    )


def ensure_heldout(
    *,
    sync: bool = True,
    env: Mapping[str, str] | None = None,
    app_home: Path | None = None,
    syncer: Any = None,
    timeout: int = CLONE_TIMEOUT,
) -> tuple[HeldoutResolution, str]:
    """Before scoring (the loop) or at first run: the verified held-out source.

    ``LOCUS_EVAL_HELDOUT_DIR`` wins (no network). Otherwise the synced digest is
    re-verified; when nothing is synced, the install is damaged or the pinned ref
    changed, a non-interactive sync is attempted (``sync=True``). Never raises:
    returns the resolution and a note (``""``, or why a sync failed)."""
    source = os.environ if env is None else env
    current = resolve_heldout(env=source, app_home=app_home)
    if current.origin == "env" or not sync:
        return current, ""
    _repo, pinned = configured_source(env=source)
    if current.origin == "synced" and current.ref == pinned:
        return current, ""
    try:
        (syncer or sync_heldout)(env=source, app_home=app_home, interactive=False, timeout=timeout)
    except HeldoutSyncError as exc:
        return resolve_heldout(env=source, app_home=app_home), f"held-out sync: {exc}"
    except Exception as exc:  # noqa: BLE001 - a sync failure is a skipped held-out split
        return resolve_heldout(env=source, app_home=app_home), (
            f"held-out sync failed ({type(exc).__name__})"
        )
    return resolve_heldout(env=source, app_home=app_home), ""


def heldout_status(
    env: Mapping[str, str] | None = None, app_home: Path | None = None
) -> dict[str, Any]:
    """``lattix evals status``: the configured source, the active sync, the resolution."""
    source = os.environ if env is None else env
    repo, pinned = configured_source(env=source)
    root = heldout_root(app_home)
    state = read_state(root)
    resolution = resolve_heldout(env=source, app_home=app_home)
    installed: Sequence[str] = (
        sorted(p.name for p in root.iterdir() if p.is_dir() and _DIGEST.fullmatch(p.name))
        if root.is_dir()
        else []
    )
    return {
        "repository": redact(repo),
        "ref": pinned,
        "require_signed": require_signed(source),
        "root": str(root),
        "active": {k: state[k] for k in sorted(state) if k != "version"} if state else None,
        "installed_digests": list(installed),
        "resolution": resolution.to_dict(),
    }
