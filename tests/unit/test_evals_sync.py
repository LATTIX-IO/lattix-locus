"""LOCUS-382: ``lattix evals sync`` and the private held-out split.

A local bare repository stands in for the private ``locus-evals-private``
repository (no network): it holds the public **test stub** tasks
(``tests/evals/fixtures/heldout_stub``), never real held-out tasks.

* sync: fetch a pinned tag through the hardened GitOps, verify the manifest,
  record the tag signature, install read-only under
  ``<app_home>/evals/heldout/<digest>/``, record the active digest; idempotent;
* refusals: a manifest that does not match, files the manifest does not list, a
  symlink, an unsigned tag with ``LOCUS_EVALS_REQUIRE_SIGNED=1``, no access
  (and credentials never echoed);
* signatures: SSH-signed tags verified with ``git verify-tag`` when a verifier is
  configured; bad / unverifiable classification;
* resolution: ``LOCUS_EVAL_HELDOUT_DIR`` wins, then the synced digest (re-verified),
  else ``not synced``; a tampered install is not used;
* wiring: the loop syncs (non-interactive) before scoring, the desktop first run
  skips quietly, the CLI prints the result.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from locus_runtime.loop_runner.delivery import DeliveryError, GitOps
from locus_tooling import evals_sync as es

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "apps" / "evals") not in sys.path:
    sys.path.insert(0, str(REPO / "apps" / "evals"))
STUB = REPO / "tests" / "evals" / "fixtures" / "heldout_stub"
GIT_ID = [
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@example.invalid",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "core.autocrlf=false",
]


def _git(cwd: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    done = subprocess.run(
        ["git", *GIT_ID, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=None if env is None else dict(env),
    )
    return done.stdout.strip()


def _stub_files() -> dict[str, bytes]:
    return {f"heldout/{p.name}": p.read_bytes() for p in sorted(STUB.glob("*.yaml"))}


def _manifest(files: Mapping[str, bytes]) -> es.HeldoutManifest:
    hashes = {name: es.content_sha(data) for name, data in files.items()}
    return es.HeldoutManifest(
        digest=es.suite_digest(hashes),
        tasks=tuple(sorted(n.split("/")[1][: -len(".yaml")] for n in files)),
        files=hashes,
        suite_version="test",
    )


def _publish(
    root: Path,
    files: Mapping[str, bytes],
    *,
    manifest: str | None = None,
    tags: tuple[str, ...] = ("v1",),
) -> Path:
    """A source repository with ``files`` + MANIFEST.json, annotated tags, and its bare clone."""
    src = root / "src"
    src.mkdir(parents=True)
    _git(src, "init", "-q", "-b", "main")
    for name, data in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_bytes(data)
    (src / "MANIFEST.json").write_text(
        manifest if manifest is not None else _manifest(files).to_json(),
        encoding="utf-8",
        newline="\n",
    )
    (src / "README.md").write_text("private held-out split (test double)\n", encoding="utf-8")
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "tasks")
    for tag in tags:
        _git(src, "tag", "-a", tag, "-m", f"release {tag}")
    bare = root / "private.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


def _env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("LOCUS_EVALS_", "LOCUS_EVAL_HELDOUT")) and not k.startswith("GIT_")
    }
    env.update(extra)
    return env


@pytest.fixture
def private_repo(tmp_path: Path) -> Path:
    return _publish(tmp_path / "remote", _stub_files())


@pytest.fixture
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    home = tmp_path / "app"
    monkeypatch.setenv("LOCUS_APP_HOME", str(home))
    monkeypatch.delenv("LOCUS_EVAL_HELDOUT_DIR", raising=False)
    for name in (es.REF_ENV, es.REPOSITORY_ENV, es.REQUIRE_SIGNED_ENV):
        monkeypatch.delenv(name, raising=False)
    yield home
    root = home / "evals" / "heldout"
    if root.exists():
        es.make_writable(root)


def _sync(repo: Path, app_home: Path, **kw: Any) -> es.SyncResult:
    kw.setdefault("env", _env())
    return es.sync_heldout(repository=str(repo), app_home=app_home, interactive=False, **kw)


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
def test_sync_installs_the_pinned_tag_read_only_and_records_it(
    private_repo: Path, app_home: Path, tmp_path: Path
) -> None:
    from locus_evals.suite.loader import split_digest

    result = _sync(private_repo, app_home, ref="v1")
    root = app_home / "evals" / "heldout"
    assert result.installed and result.path == root / result.digest
    assert result.tasks == len(list(STUB.glob("*.yaml")))
    # The digest is the scorecard's held-out split digest of the same tasks.
    shutil.copytree(STUB, tmp_path / "suite" / "heldout")
    assert result.digest == split_digest(tmp_path / "suite", "heldout")
    installed = sorted(p.name for p in (result.path / "heldout").iterdir())
    assert installed == sorted(p.name for p in STUB.glob("*.yaml"))
    for path in [*(result.path / "heldout").iterdir(), result.path / "MANIFEST.json"]:
        assert not os.access(path, os.W_OK), path
    # Only the split and the manifest are installed (not README, not the git dir).
    assert sorted(p.name for p in result.path.iterdir()) == [
        "MANIFEST.json",
        "SOURCE.json",
        "heldout",
    ]
    state = es.read_state(root)
    assert state["digest"] == result.digest and state["ref"] == "v1"
    assert state["commit"] == result.commit and len(result.commit) == 40
    assert state["signature"]["status"] == "unsigned" and not state["signature"]["ran"]
    # The on-disk source record holds no free-text signature detail.
    source = json.loads((result.path / "SOURCE.json").read_text(encoding="utf-8"))
    assert set(source["signature"]) == {"signed", "ran", "status"}
    # No temp clone left behind.
    assert sorted(p.name for p in root.iterdir()) == sorted(["active.json", result.digest])


def test_sync_is_idempotent_and_repairs_a_damaged_install(
    private_repo: Path, app_home: Path
) -> None:
    first = _sync(private_repo, app_home, ref="v1")
    again = _sync(private_repo, app_home, ref="v1")
    assert not again.installed and again.path == first.path and again.digest == first.digest
    target = next((first.path / "heldout").iterdir())
    os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    target.write_text("id: easier\n", encoding="utf-8")
    assert es.resolve_heldout(app_home=app_home).origin == "none"
    repaired = _sync(private_repo, app_home, ref="v1")
    assert repaired.installed
    assert es.resolve_heldout(app_home=app_home).origin == "synced"


def test_sync_refuses_a_manifest_that_does_not_match(tmp_path: Path, app_home: Path) -> None:
    files = _stub_files()
    manifest = _manifest(files).to_json()
    name = next(iter(files))
    tampered = dict(files)
    tampered[name] = files[name] + b"# easier\n"
    repo = _publish(tmp_path / "remote", tampered, manifest=manifest)
    with pytest.raises(es.HeldoutSyncError, match="sha256 mismatch") as info:
        _sync(repo, app_home, ref="v1")
    assert info.value.code == "manifest"
    assert not es.read_state(app_home / "evals" / "heldout")


def test_sync_refuses_files_the_manifest_does_not_list(tmp_path: Path, app_home: Path) -> None:
    files = _stub_files()
    manifest = _manifest(files).to_json()
    extra = {**files, "heldout/stub-extra.yaml": b"id: stub-extra\n"}
    repo = _publish(tmp_path / "remote", extra, manifest=manifest)
    with pytest.raises(es.HeldoutSyncError, match="not listed"):
        _sync(repo, app_home, ref="v1")


def test_sync_refuses_a_symlink_in_the_split(tmp_path: Path, app_home: Path) -> None:
    files = _stub_files()
    src = tmp_path / "remote" / "src"
    repo = _publish(tmp_path / "remote", files)
    # A symlink entry (mode 120000) added to the tree without a filesystem symlink.
    blob = subprocess.run(
        ["git", "-C", str(src), "hash-object", "-w", "--stdin"],
        input="../../outside",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(src, "update-index", "--add", "--cacheinfo", f"120000,{blob},heldout/link.yaml")
    _git(src, "commit", "-q", "-m", "symlink")
    _git(src, "tag", "-a", "v2", "-m", "v2")
    _git(src, "push", "-q", str(repo), "refs/tags/v2")
    with pytest.raises(es.HeldoutSyncError, match="only regular files"):
        _sync(repo, app_home, ref="v2")


def test_sync_without_access_fails_clearly_and_never_echoes_credentials(
    app_home: Path,
) -> None:
    secret = "ghp_" + "A1b2C3d4" * 5
    url = f"https://x-access-token:{secret}@127.0.0.1:9/LATTIX-IO/locus-evals-private.git"
    with pytest.raises(es.HeldoutSyncError) as info:
        es.sync_heldout(
            repository=url,
            ref="v1",
            app_home=app_home,
            interactive=False,
            env=_env(),
            timeout=60,
        )
    assert info.value.code == "no_access"
    assert secret not in str(info.value) and "x-access-token" not in str(info.value)
    assert "gh auth login" in str(info.value)
    assert not es.read_state(app_home / "evals" / "heldout")


def test_sync_refuses_unsigned_tags_when_signatures_are_required(
    private_repo: Path, app_home: Path
) -> None:
    with pytest.raises(es.HeldoutSyncError, match="unsigned") as info:
        _sync(private_repo, app_home, ref="v1", env=_env(LOCUS_EVALS_REQUIRE_SIGNED="1"))
    assert info.value.code == "signature"
    # A branch is not a tag: recorded as such, refused when signatures are required.
    branch = _sync(private_repo, app_home, ref="main")
    assert branch.signature.status == "not_a_tag"


def test_the_pinned_ref_comes_from_the_environment_or_the_config(
    private_repo: Path, app_home: Path
) -> None:
    assert es.configured_source(env={}) == (es.HELDOUT_REPOSITORY, es.HELDOUT_REF)
    assert es.configured_source(env={es.REF_ENV: "v9"})[1] == "v9"
    result = es.sync_heldout(
        app_home=app_home,
        interactive=False,
        env=_env(LOCUS_EVALS_REPO=str(private_repo), LOCUS_EVALS_REF="v1"),
    )
    assert result.ref == "v1"


@pytest.mark.parametrize(
    ("ref", "url"),
    [("-upload-pack=x", "repo"), ("a..b", "repo"), ("v1", "ext::sh -c x"), ("v1", "--evil")],
)
def test_clone_ref_refuses_option_like_refs_and_unsafe_transports(
    tmp_path: Path, ref: str, url: str
) -> None:
    with pytest.raises(DeliveryError, match="refusing"):
        GitOps().clone_ref(url, tmp_path / "c", ref)


# --------------------------------------------------------------------------- #
# Signatures
# --------------------------------------------------------------------------- #
class FakeTagGit:
    def __init__(self, kind: str, body: str, code: int = 0, output: str = "") -> None:
        self.kind, self.body, self.code, self.output = kind, body, code, output

    def tag_object(self, repo: Path, tag: str) -> tuple[str, str]:
        return self.kind, self.body

    def verify_tag(self, repo: Path, tag: str, *, env: Mapping[str, str]) -> tuple[int, str]:
        return self.code, self.output


SIGNED = "object abc\ntype commit\ntag v1\n\nrelease\n-----BEGIN PGP SIGNATURE-----\nx\n"


@pytest.mark.parametrize(
    ("git", "status", "ran"),
    [
        (FakeTagGit("", ""), "not_a_tag", False),
        (FakeTagGit("commit", ""), "unsigned", False),
        (FakeTagGit("tag", "object abc\n\nrelease\n"), "unsigned", False),
        (FakeTagGit("tag", SIGNED, 0, "[GNUPG:] GOODSIG 1234 principal"), "verified", True),
        (FakeTagGit("tag", SIGNED, 1, "[GNUPG:] BADSIG 1234 principal"), "bad", True),
        (
            FakeTagGit("tag", SIGNED, 1, "[GNUPG:] ERRSIG 1234\n[GNUPG:] NO_PUBKEY 1234"),
            "unverifiable",
            True,
        ),
        (
            FakeTagGit("tag", SIGNED, 1, "error: cannot run gpg: No such file or directory"),
            "unverifiable",
            False,
        ),
    ],
)
def test_signature_classification(git: FakeTagGit, status: str, ran: bool) -> None:
    check = es.check_signature(git, Path("."), "v1", {})
    assert (check.status, check.ran) == (status, ran)
    assert check.signed is (status in {"verified", "bad", "unverifiable"})


def _ssh_signing_key(tmp_path: Path) -> Path | None:
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        return None
    key = tmp_path / "principal_ed25519"
    done = subprocess.run(
        [keygen, "-q", "-t", "ed25519", "-N", "", "-C", "principal", "-f", str(key)],
        capture_output=True,
    )
    return key if done.returncode == 0 else None


def test_a_signed_tag_is_verified_and_a_bad_one_refused(tmp_path: Path, app_home: Path) -> None:
    key = _ssh_signing_key(tmp_path)
    if key is None:
        pytest.skip("ssh-keygen is not available to sign a test tag")
    files = _stub_files()
    repo = _publish(tmp_path / "remote", files, tags=())
    src = tmp_path / "remote" / "src"
    signed = subprocess.run(
        [
            "git",
            *GIT_ID,
            "-c",
            "gpg.format=ssh",
            "-c",
            f"user.signingkey={key.as_posix()}",
            "-C",
            str(src),
            "tag",
            "-s",
            "v1",
            "-m",
            "release v1",
        ],
        capture_output=True,
        text=True,
    )
    if signed.returncode != 0:
        pytest.skip(f"git could not SSH-sign a tag here: {signed.stderr.strip()[-200:]}")
    _git(src, "push", "-q", str(repo), "refs/tags/v1")
    pub = key.with_suffix(".pub").read_text(encoding="utf-8").split()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f"t@example.invalid {pub[0]} {pub[1]}\n", encoding="utf-8")
    verifier = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "gpg.ssh.allowedSignersFile",
        "GIT_CONFIG_VALUE_0": allowed.as_posix(),
    }
    # Without a verifier configured: signed, verification could not run, still installed.
    plain = _sync(repo, app_home, ref="v1")
    assert plain.signature.signed and plain.signature.status == "unverifiable"
    assert not plain.signature.ran
    with pytest.raises(es.HeldoutSyncError, match="unverifiable"):
        _sync(repo, app_home, ref="v1", env=_env(LOCUS_EVALS_REQUIRE_SIGNED="1"))
    # With the principal's key as an allowed signer: verified (also when required).
    good = _sync(repo, app_home, ref="v1", env=_env(LOCUS_EVALS_REQUIRE_SIGNED="1", **verifier))
    assert good.signature.status == "verified" and good.signature.ran
    assert es.read_state(app_home / "evals" / "heldout")["signature"]["status"] == "verified"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_resolution_order_env_then_synced_then_not_synced(
    private_repo: Path, app_home: Path, tmp_path: Path
) -> None:
    nothing = es.resolve_heldout(env={}, app_home=app_home)
    assert (nothing.origin, nothing.reason, nothing.path) == ("none", "not synced", None)
    assert nothing.describe() == "skipped: not synced"
    synced = _sync(private_repo, app_home, ref="v1")
    found = es.resolve_heldout(env={}, app_home=app_home)
    assert found.origin == "synced" and found.path == synced.path / "heldout"
    assert found.digest == synced.digest and found.ref == "v1"
    private = tmp_path / "local-private"
    private.mkdir()
    override = es.resolve_heldout(env={es.HELDOUT_DIR_ENV: str(private)}, app_home=app_home)
    assert override.origin == "env" and override.path == private
    missing = es.resolve_heldout(env={es.HELDOUT_DIR_ENV: str(tmp_path / "nope")})
    assert missing.origin == "none" and "not a directory" in missing.reason


@pytest.mark.parametrize("attack", ["state-points-elsewhere", "file-added", "made-writable"])
def test_a_tampered_synced_install_is_never_used(
    private_repo: Path, app_home: Path, attack: str
) -> None:
    synced = _sync(private_repo, app_home, ref="v1")
    root = app_home / "evals" / "heldout"
    if attack == "state-points-elsewhere":
        state = (root / "active.json").read_text(encoding="utf-8")
        (root / "active.json").write_text(state.replace(synced.digest, "0" * 64), encoding="utf-8")
    elif attack == "file-added":
        es.make_writable(synced.path)
        (synced.path / "heldout" / "stub-extra.yaml").write_text("id: x\n", encoding="utf-8")
        es.make_read_only(synced.path)
    else:
        os.chmod(next((synced.path / "heldout").iterdir()), stat.S_IWRITE | stat.S_IREAD)
    resolved = es.resolve_heldout(env={}, app_home=app_home)
    assert resolved.origin == "none" and resolved.path is None
    assert "lattix evals sync" in resolved.reason


def test_the_suite_store_takes_the_heldout_split_from_the_sync(
    private_repo: Path, app_home: Path, tmp_path: Path
) -> None:
    from locus_evals.suite import TASKS_DIR
    from locus_evals.suite.store import install

    before = install(TASKS_DIR, tmp_path / "store")
    assert set(before.split_digests) == {"dev"} and before.heldout.reason == "not synced"
    synced = _sync(private_repo, app_home, ref="v1")
    after = install(TASKS_DIR, tmp_path / "store")
    assert after.split_digests["heldout"] == synced.digest
    assert after.heldout.origin == "synced"


# --------------------------------------------------------------------------- #
# Wiring: loop, desktop first run, CLI
# --------------------------------------------------------------------------- #
def test_ensure_heldout_syncs_only_when_needed(
    private_repo: Path, app_home: Path, tmp_path: Path
) -> None:
    calls: list[dict[str, Any]] = []

    def syncer(**kw: Any) -> None:
        calls.append(kw)
        _sync(private_repo, app_home, ref="v1")

    env = _env(LOCUS_EVALS_REF="v1")
    resolution, note = es.ensure_heldout(env=env, app_home=app_home, syncer=syncer)
    assert resolution.origin == "synced" and note == ""
    assert len(calls) == 1 and calls[0]["interactive"] is False
    # Synced at the pinned ref: verified, no new fetch.
    es.ensure_heldout(env=env, app_home=app_home, syncer=syncer)
    assert len(calls) == 1
    # The pinned ref moved: sync again.
    es.ensure_heldout(env=_env(LOCUS_EVALS_REF="v2"), app_home=app_home, syncer=syncer)
    assert len(calls) == 2
    # LOCUS_EVAL_HELDOUT_DIR wins without any network.
    local = tmp_path / "local"
    local.mkdir()
    over, _ = es.ensure_heldout(
        env={**env, es.HELDOUT_DIR_ENV: str(local)}, app_home=app_home, syncer=syncer
    )
    assert over.origin == "env" and len(calls) == 2


def test_ensure_heldout_never_raises_without_access(app_home: Path) -> None:
    def denied(**_kw: Any) -> None:
        raise es.HeldoutSyncError("no_access", "could not fetch: Repository not found")

    resolution, note = es.ensure_heldout(env=_env(), app_home=app_home, syncer=denied)
    assert resolution.origin == "none" and resolution.reason == "not synced"
    assert note.startswith("held-out sync:")


def test_the_loop_syncs_non_interactively_before_scoring(
    app_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from locus_runtime.loop_runner import scorecard_gate as sg

    seen: dict[str, Any] = {}

    def fake_sync(**kw: Any) -> None:
        seen["sync"] = kw
        raise es.HeldoutSyncError("no_access", "Repository not found")

    class FakeSuite:
        SuiteUnavailable = RuntimeError

        @staticmethod
        def SuiteRunConfig(**kw: Any) -> dict[str, Any]:  # noqa: N802 - mimics the class
            seen["config"] = kw
            return kw

        @staticmethod
        def run_suite(config: Any) -> Any:
            seen["ran_after_sync"] = "sync" in seen
            raise RuntimeError("stop here")

    monkeypatch.setattr(es, "sync_heldout", fake_sync)
    monkeypatch.setattr(sg, "_import_suite_runner", lambda _repo: FakeSuite)
    request = sg.ScorecardRequest(
        candidate_checkout=tmp_path,
        repo_path=tmp_path,
        output_dir=tmp_path / "out",
        git_sha="",
        branch="b",
        python=sys.executable,
    )
    with pytest.raises(sg.ScorecardUnavailable):
        sg.default_scorecard_runner(request)
    assert seen["ran_after_sync"] and seen["sync"]["interactive"] is False
    assert any("held-out sync" in n for n in seen["config"]["extra_notes"])
    # Dev only: no held-out sync at all.
    seen.clear()
    with pytest.raises(sg.ScorecardUnavailable):
        sg.default_scorecard_runner(
            sg.ScorecardRequest(
                candidate_checkout=tmp_path,
                repo_path=tmp_path,
                output_dir=tmp_path / "o",
                git_sha="",
                branch="b",
                python=sys.executable,
                splits=("dev",),
            )
        )
    assert "sync" not in seen


def test_desktop_first_run_skips_quietly_without_access(app_home: Path) -> None:
    from locus_tooling.desktop_firstrun import ensure_heldout_suite

    lines: list[str] = []

    def denied(_home: Path) -> tuple[Any, str]:
        return es.HeldoutResolution(None, "none", reason="not synced"), "held-out sync: denied"

    def crashing(_home: Path) -> tuple[Any, str]:
        raise OSError("no git")

    assert ensure_heldout_suite(app_home, progress=lines.append, ensure=denied) is False
    assert ensure_heldout_suite(app_home, progress=lines.append, ensure=crashing) is False
    assert len(lines) == 2 and all(
        line.startswith("held-out eval suite: skipped") for line in lines
    )
    assert not any("denied" in line for line in lines)


def test_desktop_first_run_reports_a_synced_split(private_repo: Path, app_home: Path) -> None:
    from locus_tooling.desktop_firstrun import ensure_heldout_suite

    lines: list[str] = []

    def ensure(home: Path) -> tuple[Any, str]:
        _sync(private_repo, home, ref="v1")
        return es.resolve_heldout(env={}, app_home=home), ""

    assert ensure_heldout_suite(app_home, progress=lines.append, ensure=ensure) is True
    assert lines and lines[0].startswith("held-out eval suite: synced v1")


def test_cli_evals_sync_and_status(private_repo: Path, app_home: Path) -> None:
    from locus_tooling.cli import cli

    runner = CliRunner()
    done = runner.invoke(
        cli, ["evals", "sync", "--repo", str(private_repo), "--ref", "v1", "--non-interactive"]
    )
    assert done.exit_code == 0, done.output
    assert '"status": "installed"' in done.output and '"ref": "v1"' in done.output
    status = runner.invoke(cli, ["evals", "status"])
    assert status.exit_code == 0 and '"origin": "synced"' in status.output
    failed = runner.invoke(
        cli, ["evals", "sync", "--repo", str(private_repo), "--ref", "v404", "--non-interactive"]
    )
    assert failed.exit_code == 1 and "no_access" in failed.output


def test_redact_strips_url_credentials() -> None:
    text = es.redact("fatal: https://user:hunter2-secret-value@github.com/x.git not found")
    assert "hunter2" not in text and "https://***@github.com/x.git" in text


def test_desktop_first_run_retries_a_denied_fetch_at_most_daily(
    app_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_tooling import desktop_firstrun as fr

    attempts: list[dict[str, Any]] = []

    def denied(**kw: Any) -> None:
        attempts.append(kw)
        raise es.HeldoutSyncError("no_access", "Repository not found")

    monkeypatch.setattr(es, "sync_heldout", denied)
    lines: list[str] = []
    assert fr.ensure_heldout_suite(app_home, progress=lines.append) is False
    assert fr.ensure_heldout_suite(app_home, progress=lines.append) is False
    assert len(attempts) == 1
    assert attempts[0]["interactive"] is False
    assert attempts[0]["timeout"] == fr.HELDOUT_FETCH_TIMEOUT
    marker = es.heldout_root(app_home) / ".firstrun-attempt"
    os.utime(marker, (0, 0))  # a day later
    fr.ensure_heldout_suite(app_home, progress=lines.append)
    assert len(attempts) == 2


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:hunter2-secret@github.com/LATTIX-IO/locus-evals-private.git?t=x#f",
            "https://github.com/LATTIX-IO/locus-evals-private.git",
        ),
        ("https://github.com:8443/a/b.git", "https://github.com:8443/a/b.git"),
        ("git@github.com:a/b.git", "github.com:a/b.git"),
        ("E:/lattix/locus-evals-private", "E:/lattix/locus-evals-private"),
    ],
)
def test_repository_locator_never_keeps_userinfo(url: str, expected: str) -> None:
    assert es.repository_locator(url) == expected
