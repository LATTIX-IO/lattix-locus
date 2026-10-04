"""Track B4: desktop (Tauri) packaging integration — frozen-mode resolution,
desktop config, supervisor serve() lifecycle, and Tauri config validity.
Pure/unit; no Rust/PyInstaller build required."""

from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from locus_tooling import desktop as dt  # noqa: E402
from locus_tooling import native_launcher as nl  # noqa: E402

_TAURI_DIR = _REPO_ROOT / "apps" / "desktop-tauri" / "src-tauri"


# --- frozen-mode resolution --------------------------------------------------
def test_is_frozen_default_false():
    assert dt.is_frozen() is False


def test_bundled_root_frozen_is_exe_dir(monkeypatch):
    # Tauri resources/bin sit next to the installed exe (not in _MEIPASS).
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "C:/some/meipass/temp", raising=False)
    exe_parent = Path(sys.executable).resolve().parent
    assert dt.bundled_root() == exe_parent
    assert dt.bundled_bin_dir() == exe_parent / "bin"


def test_bundled_root_from_checkout_is_repo_root(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    # source_repo_root() is the package parent (repo root).
    assert (dt.bundled_root() / "locus_tooling").exists()


def test_desktop_app_home_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    assert dt.desktop_app_home() == tmp_path


# --- desktop NativeConfig ----------------------------------------------------
def test_desktop_config_uses_writable_bin_and_degrades(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config()
    assert cfg.app_home == tmp_path
    # First-run fetch lands in the writable app-home bin (not the read-only bundle).
    assert cfg.bin_dir == tmp_path / "bin"
    # Desktop degrades (boots before sidecars are fetched) rather than raising.
    assert cfg.degrade_when_missing is True


def test_desktop_config_overrides_pass_through(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config(enable_world_models=False)
    assert cfg.enable_world_models is False


def test_desktop_config_serves_backend_in_process(monkeypatch, tmp_path):
    # The frozen exe IS the backend (in-proc uvicorn) and runs agents in-proc,
    # so the supervisor must not spawn `python -m uvicorn` subprocesses.
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    cfg = dt.desktop_config()
    assert cfg.manage_backend is False
    assert cfg.enable_agents is False


def test_desktop_plan_excludes_backend_and_agent_services(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv("LOCUS_APP_HOME", str(tmp_path))
    plan = dt.build_desktop_plan()  # degrade mode → no raise even with no sidecars present
    assert "backend" not in plan.service_names()
    assert not any(n.startswith("agent-") for n in plan.service_names())


# --- supervisor serve() lifecycle -------------------------------------------
def test_serve_starts_then_stops_on_interrupt():
    terminated: list[str] = []

    class _Proc:
        def __init__(self, name):
            self.name = name

        def poll(self):
            return None  # stays running

        def terminate(self):
            terminated.append(self.name)

    plan = nl.NativePlan(
        services=[
            nl.ServiceSpec(name="svc", argv=["svc"], health=nl.HealthCheck("none"), required=True)
        ],
        env={},
        warnings=[],
    )

    def _sleep(_s):
        raise KeyboardInterrupt  # first poll tick → simulate shell shutdown

    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: _Proc(argv[0]),
        run=lambda argv, *, env: 0,
        probe=lambda check: True,
        sleep=_sleep,
    )
    sup.serve()  # should not raise; start_all then stop_all
    assert terminated == ["svc"]


def test_serve_stops_when_required_service_dies():
    terminated: list[str] = []

    class _DeadProc:
        def poll(self):
            return 1  # already exited

        def terminate(self):
            terminated.append("svc")

    plan = nl.NativePlan(
        services=[
            nl.ServiceSpec(name="svc", argv=["svc"], health=nl.HealthCheck("none"), required=True)
        ],
        env={},
        warnings=[],
    )
    sup = nl.NativeSupervisor(
        plan,
        spawn=lambda argv, *, env, cwd: _DeadProc(),
        run=lambda argv, *, env: 0,
        probe=lambda check: True,
        sleep=lambda s: None,  # poll loop detects the dead required service
    )
    sup.serve()
    assert terminated == ["svc"]


# --- Tauri config validity ---------------------------------------------------
def test_tauri_conf_is_valid_and_complete():
    conf = json.loads((_TAURI_DIR / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["identifier"] == "com.lattix.locus"
    assert conf["bundle"]["externalBin"] == ["bin/locus-backend"]
    # The one-click updater pulls signed release manifests from GitHub releases.
    updater = conf.get("plugins", {}).get("updater", {})
    assert updater.get("pubkey")
    assert all(url.startswith("https://github.com/") for url in updater.get("endpoints", []))
    # macOS hardened runtime + Windows timestamp server are configured for signing.
    assert conf["bundle"]["macOS"]["hardenedRuntime"] is True
    assert conf["bundle"]["windows"]["timestampUrl"]


def test_tauri_capabilities_allow_sidecar_spawn():
    cap = json.loads((_TAURI_DIR / "capabilities" / "default.json").read_text(encoding="utf-8"))
    spawn_perms = [
        p
        for p in cap["permissions"]
        if isinstance(p, dict) and p.get("identifier") == "shell:allow-spawn"
    ]
    assert spawn_perms, "sidecar spawn permission must be granted"
    assert spawn_perms[0]["allow"][0]["sidecar"] is True


def test_pyinstaller_spec_targets_desktop_main():
    spec = (_REPO_ROOT / "packaging" / "locus-backend.spec").read_text(encoding="utf-8")
    assert "desktop_main.py" in spec
    assert "locus-backend" in spec


# --- computer use in the desktop bundle (LOCUS-346) --------------------------
def test_pyinstaller_spec_collects_playwright_driver():
    spec = (_REPO_ROOT / "packaging" / "locus-backend.spec").read_text(encoding="utf-8")
    # collect_all over _DYNAMIC_PKGS ships playwright/driver (node + cli.js).
    assert '"playwright",' in spec and "collect_all(pkg)" in spec
    assert '"locus_runtime.computer_use.browser"' in spec
    assert '"greenlet"' in spec


def test_desktop_supervisor_points_playwright_at_app_home():
    source = (_REPO_ROOT / "locus_tooling" / "desktop.py").read_text(encoding="utf-8")
    assert "PLAYWRIGHT_BROWSERS_PATH" in source
    assert "ensure_playwright_chromium(desktop_app_home()" in source


def test_tauri_panic_hotkey_is_wired_from_rust():
    cargo = (_TAURI_DIR / "Cargo.toml").read_text(encoding="utf-8")
    assert 'tauri-plugin-global-shortcut = "2"' in cargo
    cap = json.loads((_TAURI_DIR / "capabilities" / "default.json").read_text(encoding="utf-8"))
    assert any(str(p).startswith("global-shortcut:") for p in cap["permissions"])
    main_rs = (_TAURI_DIR / "src" / "main.rs").read_text(encoding="utf-8")
    assert "tauri_plugin_global_shortcut::Builder::new()" in main_rs
    assert "computer_use::trigger_panic()" in main_rs
    assert "computer_use::start_status_indicator" in main_rs
    cu_rs = (_TAURI_DIR / "src" / "computer_use.rs").read_text(encoding="utf-8")
    assert 'PANIC_PATH: &str = "/computer-use/panic"' in cu_rs
    assert 'STATUS_PATH: &str = "/computer-use/status"' in cu_rs
    assert "Code::Escape" in cu_rs and "Modifiers::SUPER" in cu_rs
    # The token is sent, never printed.
    assert "eprintln!" in cu_rs and "token" not in "".join(
        line for line in cu_rs.splitlines() if "eprintln!" in line or "println!" in line
    )


# --- out-of-band browser-tier confirmation (LOCUS-350) ------------------------
def test_shell_confirmation_is_wired_from_rust_and_matches_python():
    import re

    from locus_runtime.computer_use.user_browser.tiers import TIER_RISKS
    from locus_tooling import shell_confirmation as sc

    cargo = (_TAURI_DIR / "Cargo.toml").read_text(encoding="utf-8")
    for dep in ('tauri-plugin-dialog = "2"', 'hmac = "0.12"', 'sha2 = "0.10"', 'getrandom = "0.2"'):
        assert dep in cargo
    main_rs = (_TAURI_DIR / "src" / "main.rs").read_text(encoding="utf-8")
    assert ".plugin(tauri_plugin_dialog::init())" in main_rs
    assert "browser_tier::confirm_browser_tier" in main_rs
    assert "browser_tier::confirm_browser_pairing" in main_rs
    # The secret goes over stdin; only the flag is in the environment.
    assert '.env(browser_tier::SHELL_CONFIRMATION_ENV, "stdin")' in main_rs
    assert "child.write(line.as_bytes())" in main_rs
    assert "secret_line_for_backend" not in re.sub(
        r"match browser_tier::secret_line_for_backend\(\)", "", main_rs
    )
    tier_rs = (_TAURI_DIR / "src" / "browser_tier.rs").read_text(encoding="utf-8")
    for name, text in TIER_RISKS.items():
        assert f'const RISK_{name.upper()}: &str = "{text}";' in tier_rs, name
    assert f'const MESSAGE_PREFIX: &str = "{sc.MESSAGE_PREFIX}";' in tier_rs
    assert f'const SECRET_LINE_PREFIX: &str = "{sc.SECRET_LINE_PREFIX}";' in tier_rs
    assert "{MESSAGE_PREFIX}|browser-tier|{tier}|{}|{}|{nonce}|{ts}" in tier_rs
    assert "{MESSAGE_PREFIX}|browser-pair|{nonce}|{ts}" in tier_rs
    assert "X-Locus-Shell-Proof: {proof_header}" in tier_rs
    assert "getrandom::getrandom" in tier_rs
    # The webview gets no dialog permission (it could fake confirmations).
    cap = json.loads((_TAURI_DIR / "capabilities" / "default.json").read_text(encoding="utf-8"))
    assert not any(str(p).startswith("dialog:") for p in cap["permissions"])
    # The frozen backend reads the secret before the supervisor starts children.
    desktop_main = (_REPO_ROOT / "locus_tooling" / "desktop_main.py").read_text(encoding="utf-8")
    assert desktop_main.index("receive_from_stdin()") < desktop_main.index(
        "run_desktop_supervisor()"
    )


# --- out-of-band confirmation for every widening request (LOCUS-357) ---------
def _shell_rules():  # type: ignore[no-untyped-def]
    backend = str(_REPO_ROOT / "apps" / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.request_security import ShellProofFormat, shell_proof_rules

    return [
        rule
        for rule in shell_proof_rules()
        if rule.may_need_proof and rule.proof == ShellProofFormat.REQUEST
    ]


def test_shell_actions_mirror_the_backend_rules_byte_for_byte():
    actions_rs = (_TAURI_DIR / "src" / "shell_actions.rs").read_text(encoding="utf-8")
    rules = _shell_rules()
    assert rules
    for rule in rules:
        current = f'Some("{rule.current}")' if rule.current else "None"
        block = (
            "    ShellAction {\n"
            f'        id: "{rule.action}",\n'
            f'        method: "{rule.method}",\n'
            f'        path: "{rule.path_template}",\n'
            f'        title: "{rule.title}",\n'
            f'        risk: "{rule.risk}",\n'
            f"        current: {current},\n"
            "    },\n"
        )
        assert block in actions_rs, rule.action
    # No action in the shell that the backend does not classify.
    assert actions_rs.count("    ShellAction {\n        id: ") == len(rules)


def test_shell_actions_sign_the_generic_request_bound_message():
    from locus_tooling import shell_confirmation as sc

    actions_rs = (_TAURI_DIR / "src" / "shell_actions.rs").read_text(encoding="utf-8")
    # Message, digest input and canonical body match locus_tooling/shell_confirmation.py.
    assert sc.MESSAGE_PREFIX == "locus-shell-proof/v1"
    assert 'format!("{MESSAGE_PREFIX}|{action}|{digest}|{nonce}|{ts}")' in actions_rs
    assert 'format!("{method}\\n{path}\\n{canonical_body}")' in actions_rs
    assert "entries.sort_by(|a, b| a.0.cmp(b.0));" in actions_rs
    assert "only whole numbers can be confirmed" in actions_rs
    # The shell sends the request itself; the proof never goes back to the webview.
    assert "send(method, &path, &canonical, Some(header.as_str()))" in actions_rs
    assert "Ok(response)" in actions_rs and "Ok(header" not in actions_rs
    # Dialog text comes from the request and the backend's state, never from
    # text the webview supplies; secrets are masked.
    signature = actions_rs[actions_rs.index("pub async fn confirm_action(") :]
    signature = signature[: signature.index(")")]
    assert signature.split("(", 1)[1].split() == [
        "app:",
        "tauri::AppHandle,",
        "action:",
        "String,",
        "path:",
        "String,",
        "body:",
        "Option<Value>,",
    ]
    assert "fn masked(" in actions_rs and "fn clean(" in actions_rs
    main_rs = (_TAURI_DIR / "src" / "main.rs").read_text(encoding="utf-8")
    assert "mod shell_actions;" in main_rs
    assert "shell_actions::confirm_action" in main_rs
    cap = json.loads((_TAURI_DIR / "capabilities" / "default.json").read_text(encoding="utf-8"))
    assert not any(str(p).startswith("dialog:") for p in cap["permissions"])
