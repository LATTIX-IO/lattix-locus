"""Windows agent toolchain (LOCUS-333): pinned specs, install layout, PATH ordering,
command mapping, ACL grant construction and executor wiring. No network: downloads
are faked; the real AppContainer run lives in tests/policy/test_jail_tiers_opa.py."""

from __future__ import annotations

import hashlib
import io
import re
import subprocess
import sys
import zipfile
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

import pytest

from locus_runtime import sandbox as sb
from locus_runtime import win_sandbox as ws
from locus_runtime import win_toolchain as wt
from locus_runtime.harness import executor as executor_module
from locus_runtime.harness.executor import LocalSandboxExecutor
from locus_tooling import desktop_firstrun
from locus_tooling import native_binaries as nb

SID = "S-1-15-2-3401317118-3897952976-696262754-2784590042-808074871-4138624016-2029712207"
ALL_APP_PACKAGES = "S-1-15-2-1"
ALL_RESTRICTED_APP_PACKAGES = "S-1-15-2-2"


# --- pinned specs ------------------------------------------------------------------------------


@pytest.mark.parametrize("arch", ["amd64", "arm64"])
def test_python_embed_spec_is_pinned_official_and_dir_layout(arch: str) -> None:
    spec = nb.resolve_toolchain_spec("python-embed", "windows", arch)
    v = nb.PYTHON_EMBED_VERSION
    assert spec.url == f"https://www.python.org/ftp/python/{v}/python-{v}-embed-{arch}.zip"
    assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256 or "")
    assert (spec.kind, spec.archive, spec.layout) == ("auto", "zip", "dir")
    assert spec.install_subdir == f"python-{v}"
    assert spec.member_rel == "python.exe"
    assert spec.write_shims is False


@pytest.mark.parametrize(("arch", "flavour"), [("amd64", "w64u"), ("arm64", "w64a")])
def test_busybox_spec_is_pinned_official_single_binary(arch: str, flavour: str) -> None:
    spec = nb.resolve_toolchain_spec("busybox", "windows", arch)
    assert spec.url == (
        f"https://frippery.org/files/busybox/busybox-{flavour}-{nb.BUSYBOX_BUILD}.exe"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256 or "")
    assert (spec.kind, spec.archive, spec.layout, spec.exe) == (
        "auto",
        "raw",
        "single",
        "busybox.exe",
    )


@pytest.mark.parametrize("name", nb.TOOLCHAIN_COMPONENTS)
@pytest.mark.parametrize(("os_name", "arch"), [("linux", "amd64"), ("darwin", "arm64")])
def test_toolchain_is_windows_only(name: str, os_name: str, arch: str) -> None:
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_toolchain_spec(name, os_name, arch)


def test_toolchain_rejects_unknown_arch_and_component() -> None:
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_toolchain_spec("busybox", "windows", "x86")
    with pytest.raises(nb.UnsupportedPlatformError):
        nb.resolve_toolchain_spec("perl", "windows", "amd64")


def test_toolchain_components_are_not_generic_sidecars() -> None:
    # native-fetch's PATH-skip logic must never "find" a host python/busybox instead.
    for name in nb.TOOLCHAIN_COMPONENTS:
        with pytest.raises(nb.UnsupportedPlatformError):
            nb.resolve_spec(name, "windows", "amd64")


# --- provisioning (fake downloads) -------------------------------------------------------------


def _embed_zip() -> bytes:
    tag = "python" + "".join(nb.PYTHON_EMBED_VERSION.split(".")[:2])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("python.exe", b"MZ-python")
        zf.writestr(f"{tag}.zip", b"stdlib")
        zf.writestr(f"{tag}._pth", f"{tag}.zip\n.\n\n#import site\n")
    return buf.getvalue()


class _Downloads:
    def __init__(self) -> None:
        self.payloads = {"python-embed": _embed_zip(), "busybox": b"MZ-busybox"}
        self.urls: list[str] = []

    def __call__(self, url: str, dest: Path) -> None:
        self.urls.append(url)
        name = "busybox" if urlsplit(url).hostname == "frippery.org" else "python-embed"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.payloads[name])

    def verify(self, path: Path, expected: str) -> None:
        # Pinned digests are for the real artifacts; check against the fakes instead.
        name = "busybox" if path.name.startswith(".busybox") else "python-embed"
        nb._default_verify(path, hashlib.sha256(self.payloads[name]).hexdigest())


def _provision(tmp_path: Path, downloads: _Downloads, grants: list[Path] | None = None, **kw: Any):
    return nb.provision_toolchain(
        tmp_path,
        os_name="windows",
        arch="amd64",
        download=downloads,
        verify=downloads.verify,
        grant=(lambda root: grants.append(root)) if grants is not None else None,
        **kw,
    )


def test_provision_installs_layout_configures_python_and_grants(tmp_path: Path) -> None:
    downloads, grants = _Downloads(), []
    report = _provision(tmp_path, downloads, grants)
    root = tmp_path / "toolchain"
    assert report.ok and report.granted, report.failed
    assert set(report.installed) == {"python-embed", "busybox"}
    assert grants == [root]
    assert all(
        u.startswith(("https://www.python.org/", "https://frippery.org/")) for u in downloads.urls
    )
    toolchain = wt.WindowsToolchain(root=root)
    assert toolchain.busybox_exe.read_bytes() == b"MZ-busybox"
    assert toolchain.python_exe.read_bytes() == b"MZ-python"
    assert (root / nb.TOOLCHAIN_MARKER).is_file()
    tag = "python" + "".join(nb.PYTHON_EMBED_VERSION.split(".")[:2])
    pth = (toolchain.python_dir / f"{tag}._pth").read_text(encoding="utf-8").splitlines()
    assert pth == [f"{tag}.zip", ".", "import site"]
    assert (toolchain.python_dir / "sitecustomize.py").is_file()
    # No .cmd shims anywhere: the sandbox runs the binaries directly.
    assert not list(root.rglob("*.cmd"))
    assert toolchain.is_installed(arch="amd64")


def test_provision_is_idempotent(tmp_path: Path) -> None:
    downloads, grants = _Downloads(), []
    _provision(tmp_path, downloads, grants)
    first = list(downloads.urls)
    report = _provision(tmp_path, downloads, grants)
    assert downloads.urls == first  # nothing fetched again
    assert set(report.present) == {"python-embed", "busybox"} and not report.installed
    assert report.granted and len(grants) == 2  # the grant itself is idempotent


def test_provision_fails_closed_on_sha256_mismatch(tmp_path: Path) -> None:
    downloads, grants = _Downloads(), []
    report = nb.provision_toolchain(
        tmp_path,
        os_name="windows",
        arch="amd64",
        download=downloads,
        grant=lambda root: grants.append(root),
    )  # real verify against the real pins: the fakes cannot match
    assert set(report.failed) == {"python-embed", "busybox"}
    assert all("sha256 mismatch" in msg for msg in report.failed.values())
    assert grants == [] and not report.granted
    toolchain = wt.WindowsToolchain(root=tmp_path / "toolchain")
    assert not toolchain.python_dir.exists() and not toolchain.busybox_exe.exists()
    assert not list((tmp_path / "toolchain").rglob("*.download"))
    assert not toolchain.is_installed(arch="amd64")


def test_provision_reports_grant_failure(tmp_path: Path) -> None:
    def boom(_root: Path) -> None:
        raise OSError("icacls failed")

    downloads = _Downloads()
    report = nb.provision_toolchain(
        tmp_path,
        os_name="windows",
        arch="amd64",
        download=downloads,
        verify=downloads.verify,
        grant=boom,
    )
    assert not report.ok and "icacls failed" in report.failed["grant"]


def test_provision_off_windows_does_nothing(tmp_path: Path) -> None:
    report = nb.provision_toolchain(tmp_path, os_name="linux", arch="amd64", grant=None)
    assert "toolchain" in report.failed
    assert not (tmp_path / "toolchain").exists()


def test_reinstall_after_stamp_mismatch(tmp_path: Path) -> None:
    downloads = _Downloads()
    _provision(tmp_path, downloads)
    stamp = tmp_path / "toolchain" / "busybox" / nb.INSTALL_STAMP
    stamp.write_text("https://example.invalid other\n", encoding="utf-8")
    report = _provision(tmp_path, downloads)
    assert "busybox" in report.installed and "python-embed" in report.present


# --- sitecustomize: normal sys.path[0] / PYTHONPATH semantics ----------------------------------


@pytest.mark.parametrize(
    ("argv", "orig", "expect_first"),
    [
        (["-c"], ["python", "-c", "x"], ""),
        ([""], ["python"], ""),
        (["-m"], ["python", "-m", "pkg"], "CWD"),
        (["SCRIPT"], ["python", "SCRIPT"], "SCRIPTDIR"),
        (["SCRIPT"], ["python", "-u", "SCRIPT"], "SCRIPTDIR"),
        (["-c"], ["python", "-P", "-c", "x"], None),
        (["-c"], ["python", "-I", "-c", "x"], None),
    ],
)
def test_sitecustomize_restores_path0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    orig: list[str],
    expect_first: str | None,
) -> None:
    script = tmp_path / "proj" / "main.py"
    script.parent.mkdir()
    script.write_text("", encoding="utf-8")
    argv = [str(script) if a == "SCRIPT" else a for a in argv]
    orig = [str(script) if a == "SCRIPT" else a for a in orig]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(sys, "orig_argv", orig)
    monkeypatch.setattr(sys, "path", ["stdlib.zip", "home"])
    monkeypatch.setenv("PYTHONPATH", f"src{__import__('os').pathsep}lib")
    exec(compile(nb._SITECUSTOMIZE, "sitecustomize.py", "exec"), {})  # noqa: S102
    if expect_first is None:
        assert sys.path == ["stdlib.zip", "home"]
        return
    want = {"CWD": str(tmp_path), "SCRIPTDIR": str(script.parent), "": ""}[expect_first]
    assert sys.path[:3] == [want, "src", "lib"]
    assert sys.path[3:] == ["stdlib.zip", "home"]


# --- paths, PATH ordering, command mapping ------------------------------------------------------


def test_toolchain_paths_and_path_dirs(tmp_path: Path) -> None:
    tc = wt.toolchain_for(tmp_path)
    assert tc.root == tmp_path / "toolchain"
    assert tc.busybox_exe == tmp_path / "toolchain" / "busybox" / "busybox.exe"
    assert (
        tc.python_exe == tmp_path / "toolchain" / f"python-{nb.PYTHON_EMBED_VERSION}" / "python.exe"
    )
    assert tc.path_dirs() == [str(tc.busybox_dir), str(tc.python_dir)]


def test_discover_toolchain_requires_a_complete_install(tmp_path: Path) -> None:
    assert wt.discover_toolchain(tmp_path) is None
    (tmp_path / "toolchain").mkdir()
    assert wt.discover_toolchain(tmp_path) is None  # no marker / stamps


def test_toolchain_app_home_honours_locus_app_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    assert wt.toolchain_app_home() == tmp_path
    assert wt.toolchain_for().root == tmp_path / "toolchain"


def test_resolve_maps_logical_names_only(tmp_path: Path) -> None:
    tc = wt.toolchain_for(tmp_path)
    bb, py = str(tc.busybox_exe), str(tc.python_exe)
    assert tc.resolve(["sh", "-c", "echo hi"]) == [bb, "sh", "-c", wt.wrap_shell_script("echo hi")]
    assert tc.resolve(["bash", "-lc", "ls"]) == [bb, "bash", "-lc", wt.wrap_shell_script("ls")]
    assert tc.resolve(["sh", "script.sh", "a"]) == [bb, "sh", "script.sh", "a"]
    assert tc.resolve(["python", "-m", "pytest"]) == [py, "-m", "pytest"]
    assert tc.resolve(["python3", "x.py"]) == [py, "x.py"]
    for other in (["git", "status"], ["cmd", "/c", "dir"], [bb, "sh"], ["C:/x/python.exe"]):
        assert tc.resolve(other) == other
    assert tc.resolve([]) == []


def test_wrap_shell_script_keeps_last_command_out_of_exec() -> None:
    assert wt.wrap_shell_script("python -c 'print(1)'") == "{\npython -c 'print(1)'\n}; exit $?"


def test_prepend_path_orders_toolchain_first_and_dedupes() -> None:
    import os

    env = {"Path": os.pathsep.join(["/windows/system32", "/tc/busybox"]), "TEMP": "t"}
    out = sb.prepend_path(env, ["/tc/busybox", "/tc/python"])
    assert set(out) == {"Path", "TEMP"}  # reuses the existing key (case-insensitive)
    assert out["Path"].split(os.pathsep) == ["/tc/busybox", "/tc/python", "/windows/system32"]


def test_minimal_agent_env_path_prepend_keeps_secret_filtering() -> None:
    import os

    base = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk", "LOCUS_X": "1", "TEMP": "t"}
    env = sb.minimal_agent_env({"PATH": "/agent/bin"}, base=base, path_prepend=["/tc/bb", "/tc/py"])
    assert env["PATH"].split(os.pathsep) == ["/tc/bb", "/tc/py", "/agent/bin"]
    assert "OPENAI_API_KEY" not in env and "LOCUS_X" not in env
    assert sb.minimal_agent_env(base=base)["PATH"] == "/usr/bin"


# --- ACL grant: Locus-owned directory, Locus AppContainer SID only ------------------------------


def _owned_root(tmp_path: Path) -> Path:
    root = tmp_path / "toolchain"
    nb.ensure_toolchain_marker(root)
    return root


def test_toolchain_grant_command_is_read_execute_for_the_container_sid(tmp_path: Path) -> None:
    argv = wt.toolchain_grant_command(SID, tmp_path / "toolchain")
    assert argv == [
        "icacls",
        str(tmp_path / "toolchain"),
        "/grant",
        f"*{SID}:(OI)(CI)RX",
        "/T",
        "/C",
        "/Q",
    ]


@pytest.mark.parametrize(
    "sid",
    [ALL_APP_PACKAGES, ALL_RESTRICTED_APP_PACKAGES, "S-1-1-0", "S-1-5-32-545", "*S-1-15-2-1", ""],
)
def test_toolchain_grant_refuses_group_and_non_appcontainer_sids(tmp_path: Path, sid: str) -> None:
    assert not wt.is_appcontainer_sid(sid)
    with pytest.raises(ValueError):
        wt.toolchain_grant_command(sid, tmp_path)


def test_ensure_toolchain_grant_is_idempotent(tmp_path: Path) -> None:
    root = _owned_root(tmp_path)
    calls: list[list[str]] = []

    def run(argv, **_kw):  # noqa: ANN001, ANN003, ANN202
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    assert wt.ensure_toolchain_grant(root, SID, run=run) is True
    assert wt.ensure_toolchain_grant(root, SID, run=run) is False
    assert calls == [wt.toolchain_grant_command(SID, root)]
    assert wt.grant_stamp(root, SID).is_file()


def test_ensure_toolchain_grant_fails_closed_without_stamp(tmp_path: Path) -> None:
    root = _owned_root(tmp_path)

    def run(argv, **_kw):  # noqa: ANN001, ANN003, ANN202
        return subprocess.CompletedProcess(argv, 5, stdout="", stderr="Access is denied.")

    with pytest.raises(OSError, match="Access is denied"):
        wt.ensure_toolchain_grant(root, SID, run=run)
    assert not wt.grant_stamp(root, SID).exists()


@pytest.mark.parametrize("setup", ["no-marker", "wrong-name", "missing"])
def test_ensure_toolchain_grant_refuses_dirs_locus_does_not_own(tmp_path: Path, setup: str) -> None:
    if setup == "no-marker":
        root = tmp_path / "toolchain"
        root.mkdir()
    elif setup == "wrong-name":
        root = tmp_path / "Documents"
        root.mkdir()
        (root / nb.TOOLCHAIN_MARKER).write_text("planted", encoding="utf-8")
    else:
        root = tmp_path / "toolchain"

    def run(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("icacls must not run")

    with pytest.raises(PermissionError):
        wt.ensure_toolchain_grant(root, SID, run=run)


# --- launcher + strategy wiring ------------------------------------------------------------------


def test_launcher_parses_toolchain_root() -> None:
    parsed = ws._parse_args(["run", "--toolchain-root", r"C:\tc", "--", "busybox.exe", "sh"])
    assert parsed.toolchain_root == r"C:\tc"
    assert parsed.command == ["busybox.exe", "sh"]
    assert ws._parse_args(["run", "--", "x"]).toolchain_root == ""


def test_appcontainer_strategy_passes_toolchain_root(tmp_path: Path) -> None:
    mgr = sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER)
    policy = sb.SandboxPolicy(
        platform=sb.HostPlatform.WINDOWS,
        allowed_executables=["busybox.exe"],
        toolchain_root=str(tmp_path / "toolchain"),
    )
    plan = mgr.plan(sb.ExecutionSpec(tool_id="coding", command=["busybox.exe", "sh"]), policy)
    idx = plan.command.index("--toolchain-root")
    assert plan.command[idx + 1] == str((tmp_path / "toolchain").resolve())
    assert idx < plan.command.index("--")
    no_tc = sb.SandboxPolicy(platform=sb.HostPlatform.WINDOWS, allowed_executables=["x"])
    plan2 = mgr.plan(sb.ExecutionSpec(tool_id="coding", command=["x"]), no_tc)
    assert "--toolchain-root" not in plan2.command


@pytest.mark.skipif(sys.platform != "win32", reason="the launcher's ctypes path is Windows-only")
def test_appcontainer_launch_grants_toolchain_before_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher hands the toolchain root to the stamped, ownership-checked grant."""
    seen: list[tuple[Path, str]] = []
    monkeypatch.setattr(wt, "ensure_toolchain_grant", lambda root, sid: seen.append((root, sid)))
    monkeypatch.setattr(ws, "_derive_appcontainer_sid", lambda _name: (object(), SID))

    class _Stop(Exception):
        pass

    def stop(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
        raise _Stop

    monkeypatch.setattr(ws, "_derive_capability_sids", stop)
    monkeypatch.setattr(ws.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(_Stop):
        ws._run_in_appcontainer(
            ["x"],
            ws.JobLimits(),
            allow_network=False,
            read_paths=[],
            write_paths=[],
            toolchain_root=r"C:\apphome\toolchain",
        )
    assert seen == [(Path(r"C:\apphome\toolchain"), SID)]


# --- executor ----------------------------------------------------------------------------------


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003, ANN202
        calls.append({"cmd": list(cmd), **kwargs})
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(executor_module.subprocess, "run", fake_run)
    return calls


def _appcontainer_executor(tmp_path: Path, toolchain: Any) -> LocalSandboxExecutor:
    return LocalSandboxExecutor(
        tmp_path / "ws",
        manager=sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER),
        toolchain=toolchain,
    )


def test_run_shell_uses_busybox_sh_after_gating_the_logical_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permissive_gateway: Any
) -> None:
    import os

    calls = _capture(monkeypatch)
    tc = wt.toolchain_for(tmp_path)
    ex = _appcontainer_executor(tmp_path, tc)
    result = ex.run_shell("echo hi && python -c 'print(2+2)'")
    assert result.exit_code == 0
    action = permissive_gateway.actions[-1]
    assert action.executable == "sh"  # tool_jail sees the allowlisted logical name
    cmd = calls[0]["cmd"]
    tail = cmd[cmd.index("--") + 1 :]
    assert tail == [
        str(tc.busybox_exe),
        "sh",
        "-c",
        wt.wrap_shell_script("echo hi && python -c 'print(2+2)'"),
    ]
    assert cmd[cmd.index("--toolchain-root") + 1] == str(tc.root.resolve())
    path_key = next(k for k in calls[0]["env"] if k.upper() == "PATH")
    assert calls[0]["env"][path_key].split(os.pathsep)[:2] == tc.path_dirs()


def test_python_maps_to_the_toolchain_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)
    tc = wt.toolchain_for(tmp_path)
    _appcontainer_executor(tmp_path, tc).run(["python", "-m", "pytest", "-q"])
    cmd = calls[0]["cmd"]
    assert cmd[cmd.index("--") + 1 :] == [str(tc.python_exe), "-m", "pytest", "-q"]


def test_missing_toolchain_fails_with_actionable_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _capture(monkeypatch)
    ex = _appcontainer_executor(tmp_path, None)
    result = ex.run_shell("echo hi")
    assert result.exit_code == executor_module.TOOLCHAIN_MISSING_EXIT_CODE
    assert "native-fetch-toolchain" in result.stderr
    assert calls == []
    # Commands the toolchain does not serve still run (cmd/git are readable there).
    ex.run(["git", "status"])
    assert len(calls) == 1 and "--toolchain-root" not in calls[0]["cmd"]


def test_auto_toolchain_discovers_under_the_app_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path / "home"))
    ex = LocalSandboxExecutor(
        tmp_path / "ws",
        manager=sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER),
    )
    assert ex.toolchain is None  # nothing installed there
    downloads = _Downloads()
    _provision(tmp_path / "home", downloads)
    monkeypatch.setattr(nb, "current_platform", lambda: ("windows", "amd64"))
    monkeypatch.setattr(wt, "current_platform", lambda: ("windows", "amd64"))
    fresh = LocalSandboxExecutor(
        tmp_path / "ws",
        manager=sb.SandboxManager(force_strategy=sb.IsolationStrategy.WINDOWS_APPCONTAINER),
    )
    assert fresh.toolchain is not None
    assert fresh.toolchain.root == tmp_path / "home" / "toolchain"


def test_non_windows_tiers_keep_bash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch)
    ex = LocalSandboxExecutor(
        tmp_path / "ws",
        manager=sb.SandboxManager(force_strategy=sb.IsolationStrategy.KERNEL_BWRAP),
        toolchain=wt.toolchain_for(tmp_path),
    )
    assert ex.toolchain is None
    ex.run_shell("echo hi")
    cmd = calls[0]["cmd"]
    assert cmd[-3:] == ["bash", "-lc", "echo hi"]
    assert "busybox" not in " ".join(cmd)


# --- first run ---------------------------------------------------------------------------------


def test_first_run_provisions_toolchain_on_windows_only(tmp_path: Path) -> None:
    lines: list[str] = []
    calls: list[Path] = []

    def fake(app_home: Path) -> nb.ToolchainReport:
        calls.append(app_home)
        return nb.ToolchainReport(root=str(app_home / "toolchain"), installed={"busybox": "x"})

    assert (
        desktop_firstrun.ensure_agent_toolchain(
            tmp_path, progress=lines.append, provision_toolchain=fake, os_name="linux"
        )
        is None
    )
    assert calls == []
    report = desktop_firstrun.ensure_agent_toolchain(
        tmp_path, progress=lines.append, provision_toolchain=fake, os_name="windows"
    )
    assert report is not None and calls == [tmp_path]
    assert "installed busybox" in lines


def test_first_run_toolchain_failure_never_raises(tmp_path: Path) -> None:
    lines: list[str] = []

    def boom(_home: Path) -> nb.ToolchainReport:
        raise RuntimeError("offline")

    assert (
        desktop_firstrun.ensure_agent_toolchain(
            tmp_path, progress=lines.append, provision_toolchain=boom, os_name="windows"
        )
        is None
    )
    assert any("FAILED agent toolchain: offline" in line for line in lines)
