"""LOCUS-379: the RSI candidate instance runs in an OS jail.

Pure tests (every OS): tier selection, the explicit unjailed opt-out, the bwrap
argv and seatbelt profile (deny by default, no network, no user home), the
Windows runtime cache key, the code copy (never ``.git``), the AppContainer
helpers, and the escape report.

Real-jail tests (Windows only, AppContainer): a canary in a temp directory that
is **not** granted, the user's home, a loopback listener, an external host, the
OS credential store and a planted secret variable; every escape attempt must
fail from inside the jailed child. The positive control runs the same probe
unjailed and must read the canary and reach the listener, so the jailed result
is meaningful. They use a stdlib-only runtime copy (no site-packages) so they
stay fast; the full runtime is exercised by the opt-in end-to-end test below
and by the scorecard run.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from locus_runtime.rsi import jail
from locus_runtime.rsi.candidate import (
    CandidateError,
    CandidateInstance,
    escapes,
    resolve_isolation,
)
from locus_runtime.rsi.win_appcontainer import (
    CANDIDATE_PROFILE,
    TOOLS_PROFILE,
    environment_block,
    grant_commands,
    validate_profile_name,
)
from locus_runtime.sandbox import HostPlatform

REPO = Path(__file__).resolve().parents[2]
AC_SID = "S-1-15-2-4279608810-1338294037-1491860943-3187377624-924449257-3985269126-1905255198"
WINDOWS_JAIL = pytest.mark.skipif(
    sys.platform != "win32" or not jail.jail_availability().available,
    reason="real AppContainer execution needs Windows with the AppContainer APIs",
)


# --------------------------------------------------------------------------- #
# Tier selection and the opt-out
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("platform", "kwargs", "tier"),
    [
        (HostPlatform.WINDOWS, {"appcontainer_available": True}, "appcontainer"),
        (HostPlatform.WINDOWS, {"appcontainer_available": False}, None),
        (HostPlatform.MACOS, {"seatbelt_available": True}, "seatbelt"),
        (HostPlatform.MACOS, {"seatbelt_available": False}, None),
        (HostPlatform.LINUX, {"which": lambda name: "/usr/bin/bwrap"}, "bwrap"),
        (HostPlatform.LINUX, {"which": lambda name: None}, None),
    ],
)
def test_jail_availability_per_platform(
    platform: HostPlatform, kwargs: dict[str, object], tier: str | None
) -> None:
    found = jail.jail_availability(platform=platform, **kwargs)
    assert found.tier == tier
    assert found.available is (tier is not None)
    if tier is None:
        assert found.reason  # says what to install


def test_docker_is_not_a_candidate_jail() -> None:
    found = jail.jail_availability(
        platform=HostPlatform.LINUX,
        which=lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    assert found.tier is None and "bubblewrap" in found.reason


def test_unjailed_opt_out_is_exactly_one() -> None:
    assert jail.unjailed_requested({jail.UNJAILED_ENV: "1"})
    for value in ("", "0", "true", "yes", "2"):
        assert not jail.unjailed_requested({jail.UNJAILED_ENV: value})


def test_resolve_isolation_fails_closed_without_a_jail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    monkeypatch.setattr(
        jail, "jail_availability", lambda: jail.JailAvailability(None, "linux", "no bwrap")
    )
    with pytest.raises(CandidateError, match="no OS jail.*LOCUS_RSI_CANDIDATE_UNJAILED"):
        resolve_isolation()
    with pytest.raises(CandidateError, match="explicit opt-out"):
        resolve_isolation("none")
    monkeypatch.setenv(jail.UNJAILED_ENV, "1")
    assert resolve_isolation() == "none"
    assert resolve_isolation("bwrap") == "bwrap"
    with pytest.raises(CandidateError, match="unknown"):
        resolve_isolation("docker")


def test_the_platform_jail_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    monkeypatch.setattr(
        jail, "jail_availability", lambda: jail.JailAvailability("seatbelt", "macos", "x")
    )
    assert resolve_isolation() == "seatbelt"


def test_unjailed_instance_warns_loudly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv(jail.UNJAILED_ENV, "1")
    instance = CandidateInstance(
        REPO, model_base_url="http://127.0.0.1:1/v1", model="m", home=tmp_path / "c"
    )
    assert instance.isolation == "none" and not instance.jailed
    assert "WITHOUT an OS jail" in capsys.readouterr().err
    with pytest.raises(CandidateError, match="not jailed"):
        instance.verify_isolation()


def test_jailed_environment_uses_the_bridge_and_the_code_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:5/v1",
        model="m",
        home=tmp_path / "cand",
        opa_bin="opa.exe",
        toolchain_home="T",
        isolation="bwrap",
        parent_env={"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-real"},
    )
    env = instance.environment()
    assert env["LOCUS_RSI_BRIDGE"] == "stdio"
    assert env["LOCUS_RSI_ISOLATION"] == "bwrap"
    assert Path(env["PYTHONPATH"]) == tmp_path / "cand-code" / "src"
    assert Path(env["LOCUS_POLICY_DIR"]) == tmp_path / "cand-code" / "src" / "policies"
    # Policy decisions and agent commands are the parent's: no OPA binary or
    # toolchain path inside, and still no secret.
    assert "LOCUS_OPA_BIN" not in env and "LOCUS_TOOLCHAIN_HOME" not in env
    assert "OPENAI_API_KEY" not in env
    assert instance.entry_script == tmp_path / "cand-code" / "trusted" / "candidate_entry.py"
    facts = instance.tool_jail_facts()
    assert facts["strategy"] == "kernel-bwrap" and facts["allow_network"] is False
    assert facts["readonly_rootfs"] is True


def test_windows_tool_jail_facts_satisfy_tool_jail(tmp_path: Path) -> None:
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:5/v1",
        model="m",
        home=tmp_path / "c",
        isolation="appcontainer",
    )
    assert instance.tool_jail_facts() == {
        "strategy": "windows-appcontainer",
        "allow_network": False,
        "appcontainer": True,
        "job_object": True,
        "require_appcontainer": True,
    }
    assert instance._hello({})["shell"] == "sh"  # noqa: SLF001


# --------------------------------------------------------------------------- #
# bwrap and seatbelt (pure)
# --------------------------------------------------------------------------- #
def test_bwrap_argv_unshares_everything_and_never_mounts_the_home() -> None:
    layout = jail.JailLayout(read=("/tmp/c-code", "/opt/py"), write=("/tmp/c", "/tmp/ws"))
    argv = jail.bwrap_argv(
        layout,
        ["/opt/py/bin/python", "-s", "entry.py"],
        cwd="/tmp/c",
        system_paths=("/usr", "/bin"),
        is_symlink=lambda p: p == "/bin",
        readlink=lambda p: "usr/bin",
    )
    assert argv[0] == "bwrap" and "--unshare-all" in argv
    assert "--share-net" not in argv  # no network namespace escape
    assert argv[argv.index("--tmpfs") + 1] == "/tmp"
    # The tmpfs comes before every bind below /tmp.
    assert argv.index("--tmpfs") < argv.index("--bind")
    assert ["--symlink", "usr/bin", "/bin"] == argv[
        argv.index("--symlink") : argv.index("--symlink") + 3
    ]
    assert ["--ro-bind-try", "/usr", "/usr"] == argv[
        argv.index("--ro-bind-try") : argv.index("--ro-bind-try") + 3
    ]
    joined = " ".join(argv)
    assert "--ro-bind /tmp/c-code /tmp/c-code" in joined
    assert "--bind /tmp/c /tmp/c" in joined and "--bind /tmp/ws /tmp/ws" in joined
    assert "--ro-bind / /" not in joined and "/home" not in joined
    assert argv[argv.index("--") + 1 :] == ["/opt/py/bin/python", "-s", "entry.py"]


def test_seatbelt_profile_denies_by_default_without_network_or_services() -> None:
    profile = jail.seatbelt_profile(
        jail.JailLayout(read=("/private/tmp/c-code",), write=('/private/tmp/c "x"',)),
        system_paths=("/System",),
        exec_paths=("/opt/py",),
    )
    lines = profile.splitlines()
    assert lines[:2] == ["(version 1)", "(deny default)"]
    assert "network" not in profile  # no network* rule at all: every socket denied
    assert "mach-lookup" not in profile  # no keychain / system services
    assert "ipc" not in profile
    assert '(allow file-read* (subpath "/System") (subpath "/private/tmp/c-code"))' in profile
    assert '(allow file-read* file-write* (subpath "/private/tmp/c \\"x\\""))' in profile
    assert '(allow process-exec (subpath "/opt/py"))' in profile
    assert "/Users" not in profile


def test_seatbelt_argv_resolves_real_paths(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    argv = jail.seatbelt_argv(jail.JailLayout(write=(str(real),)), ["python"])
    assert argv[:2] == [jail.SEATBELT_BIN, "-p"] and argv[-1] == "python"
    assert os.path.realpath(real).replace("\\", "\\\\") in argv[2]


def test_default_system_paths_never_include_user_homes() -> None:
    for path in (*jail.LINUX_SYSTEM_PATHS, *jail.MACOS_SYSTEM_PATHS):
        assert not path.startswith(("/home", "/Users", "/root"))


# --------------------------------------------------------------------------- #
# Code copy and the Windows runtime cache key
# --------------------------------------------------------------------------- #
def test_copy_code_takes_the_packages_but_never_git_or_caches(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    for rel in (
        "locus_runtime/__init__.py",
        "locus_runtime/__pycache__/x.cpython-312.pyc",
        "locus_tooling/common.py",
        "policies/tool_jail.rego",
        ".git/config",
        "secrets.env",
        "docs/x.md",
    ):
        path = checkout / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    dest = jail.copy_code(checkout, tmp_path / "copy")
    copied = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
    assert copied == [
        "locus_runtime/__init__.py",
        "locus_tooling/common.py",
        "policies/tool_jail.rego",
    ]


def test_runtime_key_changes_with_the_installed_packages(tmp_path: Path) -> None:
    site = tmp_path / "Lib" / "site-packages"
    site.mkdir(parents=True)
    info = jail.InterpreterInfo("python.exe", "3.12.10", str(tmp_path), "C:/Py312", (str(site),))
    first = jail.runtime_key(info, site_packages=True)
    assert first == jail.runtime_key(info, site_packages=True)
    (site / "newpkg").mkdir()
    assert jail.runtime_key(info, site_packages=True) != first
    assert jail.runtime_key(info, site_packages=False) != jail.runtime_key(info, site_packages=True)


def test_package_dirs_never_include_the_prefix_itself() -> None:
    dirs = jail._package_dirs(  # noqa: SLF001
        ["C:/venv", "C:/venv/Lib/site-packages", "C:/other/Lib/site-packages"], "C:/venv"
    )
    assert dirs == ["C:/venv/Lib/site-packages"]


# --------------------------------------------------------------------------- #
# AppContainer helpers (pure)
# --------------------------------------------------------------------------- #
def test_profile_names_are_locus_namespaced() -> None:
    assert validate_profile_name(CANDIDATE_PROFILE) == CANDIDATE_PROFILE
    assert validate_profile_name(TOOLS_PROFILE) == TOOLS_PROFILE
    assert CANDIDATE_PROFILE != TOOLS_PROFILE != "com.lattix.locus.agent"
    for bad in ("", "Microsoft.Windows", "com.lattix.locus.", "com.lattix.locus.A B"):
        with pytest.raises(ValueError):
            validate_profile_name(bad)


def test_environment_block_is_sorted_and_rejects_injection() -> None:
    block = environment_block({"b": "2", "A": "1", "Path": "C:/x"})
    assert block == "A=1\0b=2\0Path=C:/x\0\0"
    for bad in ({"A=B": "1"}, {"A": "1\0B=2"}, {"": "x"}):
        with pytest.raises(ValueError):
            environment_block(bad)


def test_grants_only_ever_name_an_appcontainer_sid() -> None:
    cmds = grant_commands(AC_SID, read=["C:/code"], write=["C:/home"])
    assert cmds == [
        ["icacls", "C:/home", "/grant", f"*{AC_SID}:(OI)(CI)M", "/T", "/C", "/Q"],
        ["icacls", "C:/code", "/grant", f"*{AC_SID}:(OI)(CI)RX", "/T", "/C", "/Q"],
    ]
    for sid in ("S-1-15-2-1", "S-1-15-2-2", "S-1-5-21-1-2-3-1001", "S-1-1-0"):
        with pytest.raises(ValueError):
            grant_commands(sid, read=["C:/x"])


# --------------------------------------------------------------------------- #
# The escape report (pure)
# --------------------------------------------------------------------------- #
def test_escapes_lists_every_successful_attempt() -> None:
    report = {
        "read": {"C:/canary": "ok", "C:/other": "blocked:PermissionError"},
        "write": {"C:/canary": "blocked:PermissionError"},
        "list": {"C:/Users/me": "ok"},
        "connect": {"127.0.0.1:5": "blocked:TimeoutError", "1.1.1.1:443": "ok"},
        "keychain": {
            "windows_credential_manager": "ok",
            "keyring_backend": "keyring.backends.Windows",
        },
        "secret_like_env": ["NVIDIA_API_KEY"],
    }
    assert sorted(escapes(report)) == sorted(
        [
            "read:C:/canary",
            "list:C:/Users/me",
            "connect:1.1.1.1:443",
            "keychain:windows_credential_manager",
            "keychain:keyring.backends.Windows",
            "env:NVIDIA_API_KEY",
        ]
    )
    clean = {"read": {"x": "blocked:OSError"}, "keychain": {"keyring_backend": "unavailable:X"}}
    assert escapes(clean) == []


# --------------------------------------------------------------------------- #
# Real AppContainer execution (Windows)
# --------------------------------------------------------------------------- #
def _probe(instance: CandidateInstance, canary: Path, port: int) -> dict[str, Any]:
    return instance.escape_probe(
        read=[str(canary)],
        write=[str(canary)],
        listing=[str(Path.home()), str(canary.parent)],
        connect=[("127.0.0.1", port), ("1.1.1.1", 443)],
    )


def _accepted(listener: socket.socket) -> bool:
    listener.settimeout(0.5)
    try:
        conn, _ = listener.accept()
    except OSError:
        return False
    conn.close()
    return True


@pytest.fixture
def canary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # A user-profile-like location the jail was never granted.
    outside = tmp_path_factory.mktemp("outside-not-granted")
    path = outside / "canary.txt"
    path.write_text("LOCUS-379-CANARY", encoding="utf-8")
    return path


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    yield sock
    sock.close()


@WINDOWS_JAIL
def test_appcontainer_candidate_cannot_escape(
    tmp_path: Path, canary: Path, listener: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    # A secret in the evaluator's own environment must not reach the child.
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-should-not-leak")
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        runtime_cache=tmp_path / "runtime",
        runtime_site_packages=False,
    )
    try:
        assert instance.isolation == "appcontainer"
        port = int(listener.getsockname()[1])
        report = _probe(instance, canary, port)
    finally:
        instance.close()
    # Proven from inside the jailed child: every attempt failed.
    assert all(v.startswith("blocked:") for v in report["read"].values()), report["read"]
    assert all(v.startswith("blocked:") for v in report["write"].values()), report["write"]
    assert all(v.startswith("blocked:") for v in report["list"].values()), report["list"]
    assert all(v.startswith("blocked:") for v in report["connect"].values()), report["connect"]
    keychain = report["keychain"]
    assert keychain["windows_credential_manager"].startswith("blocked:")
    assert report["secret_like_env"] == []
    assert "NVIDIA_API_KEY" not in report["env"]
    assert escapes(report) == []
    # ... and the host side agrees: nothing written, nothing connected.
    assert canary.read_text(encoding="utf-8") == "LOCUS-379-CANARY"
    assert not _accepted(listener)
    assert Path(str(report["executable"])).is_relative_to(tmp_path / "runtime")


@WINDOWS_JAIL
def test_positive_control_the_same_probe_unjailed_escapes(
    tmp_path: Path, canary: Path, listener: socket.socket, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(jail.UNJAILED_ENV, "1")
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="none",
        python=sys.executable,
    )
    try:
        report = _probe(instance, canary, int(listener.getsockname()[1]))
    finally:
        instance.close()
    assert report["read"] == {str(canary): "ok"}
    assert report["write"] == {str(canary): "ok"}
    assert report["list"][str(canary.parent)] == "ok"
    assert report["connect"][f"127.0.0.1:{listener.getsockname()[1]}"] == "ok"
    assert _accepted(listener)
    assert canary.read_text(encoding="utf-8").endswith("escape")


@WINDOWS_JAIL
def test_verify_isolation_passes_in_the_appcontainer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        runtime_cache=tmp_path / "runtime",
        runtime_site_packages=False,
    )
    try:
        report = instance.verify_isolation()
    finally:
        instance.close()
    assert escapes(report) == []
    assert len(report["read"]) == len(report["write"]) == 1


@WINDOWS_JAIL
def test_verify_isolation_fails_closed_on_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="appcontainer",
    )
    monkeypatch.setattr(
        instance,
        "escape_probe",
        lambda **_kw: {"read": {"C:/canary": "ok"}, "keychain": {}, "secret_like_env": []},
    )
    with pytest.raises(CandidateError, match="leaked: read:C:/canary"):
        instance.verify_isolation(external=None)
    instance.close()


@WINDOWS_JAIL
def test_tool_jail_runs_commands_confined_to_the_workspace(
    tmp_path: Path, canary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.win_toolchain import discover_toolchain

    if discover_toolchain() is None:
        pytest.skip("the Windows agent toolchain (BusyBox) is not installed here")
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="appcontainer",
    )
    try:
        instance._grant_workspace(workspace)  # noqa: SLF001
        instance._workspace = workspace  # noqa: SLF001
        ok = instance._exec({"command": ["sh", "-c", "echo hi > f.txt && cat f.txt"]})  # noqa: SLF001
        leak = instance._exec({"command": ["sh", "-c", f"cat '{canary.as_posix()}'"]})  # noqa: SLF001
        home = instance._exec({"command": ["sh", "-c", f"ls '{instance.home.as_posix()}'"]})  # noqa: SLF001
    finally:
        instance.close()
    assert ok["exit_code"] == 0 and "hi" in ok["stdout"], ok
    assert (workspace / "f.txt").read_text(encoding="utf-8").strip() == "hi"
    assert leak["exit_code"] != 0 and "LOCUS-379-CANARY" not in leak["stdout"]
    # The tool profile cannot read the candidate's own home (audit, telemetry).
    assert home["exit_code"] != 0, home
    assert str(ok["backend"]).endswith("appcontainer")


@pytest.mark.skipif(
    os.environ.get("LOCUS_RSI_JAIL_FULL_TEST") != "1" or sys.platform != "win32",
    reason="opt-in: builds the full candidate runtime copy (about 800 MB, once)",
)
def test_full_runtime_probe_loads_the_candidates_code_in_the_jail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    instance = CandidateInstance(
        REPO, model_base_url="http://127.0.0.1:1/v1", model="m", home=tmp_path / "cand"
    )
    try:
        facts = instance.probe()
    finally:
        instance.close()
    assert Path(str(facts["locus_runtime"])).is_relative_to(tmp_path / "cand-code" / "src")
    assert str(facts["keyring_backend"]).startswith("keyring.backends.null")
    assert facts["provider_keys"] == [] and facts["secret_like_env"] == []


# --------------------------------------------------------------------------- #
# LOCUS-382: the synced private held-out split is unreadable from every jail
# --------------------------------------------------------------------------- #
STUB_HELDOUT = REPO / "tests" / "evals" / "fixtures" / "heldout_stub"


@pytest.fixture
def synced_heldout(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """A real read-only install at ``<app_home>/evals/heldout/<digest>/`` (the public
    test stub stands in for the private tasks), next to the app home's jail-granted
    candidate-runtime cache, exactly as on a runner."""
    from locus_tooling import evals_sync as es

    app_home = tmp_path / "app-home"
    files = {f"heldout/{p.name}": p.read_bytes() for p in sorted(STUB_HELDOUT.glob("*.yaml"))}
    hashes = {name: es.content_sha(data) for name, data in files.items()}
    manifest = es.HeldoutManifest(
        digest=es.suite_digest(hashes),
        tasks=tuple(sorted(n.split("/")[1][: -len(".yaml")] for n in files)),
        files=hashes,
    )
    dest, installed = es.install_heldout(es.heldout_root(app_home), manifest, files)
    assert installed
    yield app_home, dest
    es.make_writable(dest)


def _heldout_targets(dest: Path) -> tuple[list[str], list[str]]:
    task = next(iter(sorted((dest / "heldout").iterdir())))
    return [str(task), str(dest / "MANIFEST.json")], [str(dest.parent), str(dest), str(task.parent)]


@WINDOWS_JAIL
def test_appcontainer_candidate_cannot_read_the_synced_heldout(
    tmp_path: Path, synced_heldout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    app_home, dest = synced_heldout
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    reads, listings = _heldout_targets(dest)
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        # The interpreter copy lives in the same app home and IS granted to the jail.
        runtime_cache=app_home / "rsi" / "candidate-runtime",
        runtime_site_packages=False,
    )
    try:
        assert instance.isolation == "appcontainer"
        report = instance.escape_probe(read=reads, listing=listings)
        # The run-time proof lists the held-out folder too and must pass.
        proof = instance.verify_isolation(external=None, protected=[dest])
    finally:
        instance.close()
    assert set(report["read"]) == set(reads) and set(report["list"]) == set(listings)
    assert all(v.startswith("blocked:") for v in report["read"].values()), report["read"]
    assert all(v.startswith("blocked:") for v in report["list"].values()), report["list"]
    assert escapes(report) == [] and escapes(proof) == []
    assert str(dest) in proof["list"] and len(proof["read"]) == 2


@WINDOWS_JAIL
def test_tool_jail_cannot_read_the_synced_heldout(
    tmp_path: Path, synced_heldout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from locus_runtime.win_toolchain import discover_toolchain

    if discover_toolchain() is None:
        pytest.skip("the Windows agent toolchain (BusyBox) is not installed here")
    _app_home, dest = synced_heldout
    monkeypatch.delenv(jail.UNJAILED_ENV, raising=False)
    (task, _manifest), (root, _dest, _split) = _heldout_targets(dest)
    secret_line = Path(task).read_text(encoding="utf-8").splitlines()[0]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="appcontainer",
    )
    try:
        instance._grant_workspace(workspace)  # noqa: SLF001
        instance._workspace = workspace  # noqa: SLF001
        ok = instance._exec({"command": ["sh", "-c", "echo hi"]})  # noqa: SLF001
        read = instance._exec({"command": ["sh", "-c", f"cat '{Path(task).as_posix()}'"]})  # noqa: SLF001
        listing = instance._exec({"command": ["sh", "-c", f"ls '{Path(root).as_posix()}'"]})  # noqa: SLF001
    finally:
        instance.close()
    assert ok["exit_code"] == 0 and "hi" in ok["stdout"], ok  # the jail itself works
    assert read["exit_code"] != 0 and secret_line not in read["stdout"], read
    assert listing["exit_code"] != 0 and dest.name not in listing["stdout"], listing


@WINDOWS_JAIL
def test_positive_control_unjailed_reads_the_synced_heldout(
    tmp_path: Path, synced_heldout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same probes without a jail succeed, so the blocked results above are meaningful."""
    from locus_runtime.sandbox import minimal_agent_env
    from locus_runtime.win_toolchain import discover_toolchain

    _app_home, dest = synced_heldout
    reads, listings = _heldout_targets(dest)
    monkeypatch.setenv(jail.UNJAILED_ENV, "1")
    instance = CandidateInstance(
        REPO,
        model_base_url="http://127.0.0.1:1/v1",
        model="m",
        home=tmp_path / "cand",
        isolation="none",
        python=sys.executable,
    )
    try:
        report = instance.escape_probe(read=reads, listing=listings)
    finally:
        instance.close()
    assert report["read"] == {path: "ok" for path in reads}
    assert all(v == "ok" for v in report["list"].values()), report["list"]
    toolchain = discover_toolchain()
    if toolchain is None:
        return
    task = reads[0]
    command = toolchain.resolve(["sh", "-c", f"cat '{Path(task).as_posix()}'"])
    result = jail.run_tool(
        "none",
        command,
        layout=jail.JailLayout(write=(str(tmp_path),)),
        env=minimal_agent_env(None, path_prepend=toolchain.path_dirs()),
        cwd=str(tmp_path),
        timeout=60.0,
    )
    assert result.exit_code == 0, result
    assert Path(task).read_text(encoding="utf-8").splitlines()[0] in result.stdout
