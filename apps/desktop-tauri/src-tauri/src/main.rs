// Lattix xFrontier desktop shell (Tauri v2).
//
// Topology: this shell spawns ONE backend sidecar — the packaged supervisor
// (`frontier-backend`, see frontier_tooling/desktop_main.py) — which in turn
// brings up every native service (Postgres+pgvector, Neo4j world models, NATS,
// Ollama, the confined agents, the FastAPI backend, and the Next.js frontend).
// Once the backend reports healthy, we navigate the webview to the local UI.
//
// We intentionally keep the heavy lifting in Python (the supervisor) so the Rust
// layer stays a thin, auditable window + lifecycle manager.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{include_image, Emitter, Manager, WebviewUrl, WindowEvent};
use tauri_plugin_shell::process::CommandEvent;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::UpdaterExt;

const BACKEND_HEALTH_URL: &str = "http://127.0.0.1:8000/healthz";
const FRONTEND_URL: &str = "http://127.0.0.1:3000";
const HEALTH_TIMEOUT_SECS: u64 = 180;

/// PID of the spawned backend supervisor sidecar. The supervisor in turn spawns
/// every native service (Postgres, NATS, Node, …), so killing its process TREE
/// reaps them all.
static BACKEND_PID: AtomicU32 = AtomicU32::new(0);

/// Force-kill the backend supervisor sidecar AND its entire process subtree.
/// Idempotent (clears the PID), so it's safe to call from both the quit command
/// and the app's Exit event. Without this, quitting from the tray could leave
/// orphaned Postgres/Node/sidecar processes holding files — which blocks reinstall.
fn kill_backend_tree() {
    let pid = BACKEND_PID.swap(0, Ordering::SeqCst);
    if pid == 0 {
        return;
    }
    #[cfg(windows)]
    {
        // /T = whole tree (supervisor + its children), /F = force.
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/T", "/PID", &pid.to_string()])
            .output();
    }
    #[cfg(not(windows))]
    {
        // SIGTERM the supervisor; its signal handlers tear down the child group.
        let _ = std::process::Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .output();
    }
}

/// Show + focus the main window (from the tray).
fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Exit the whole app — invoked by the frontend after it has confirmed quit and
/// asked the backend to tear down its child processes (/api/system/shutdown).
/// We force-kill the backend tree here as a deterministic backstop (in case the
/// graceful /system/shutdown didn't run or the backend was unreachable), then the
/// Exit event runs the same kill once more (idempotent).
#[tauri::command]
fn quit_now(app: tauri::AppHandle) {
    kill_backend_tree();
    app.exit(0);
}

/// Returns the available update version (or None). Errors are mapped to a string;
/// the UI treats "no updater configured" as simply "no update".
#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    match updater.check().await {
        Ok(Some(update)) => Ok(Some(update.version)),
        Ok(None) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// Silently download + install the pending update, then relaunch the app. The
/// UI confirms with the user (and warns about the restart) before calling this.
#[tauri::command]
async fn install_update_and_restart(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|e| e.to_string())?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No update available".to_string())?;
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    app.restart();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            quit_now,
            check_for_update,
            install_update_and_restart
        ])
        // Closing the window hides to the tray instead of quitting; real quit is
        // the tray "Quit" item, which runs the agent-running validation first.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();

            // System tray: logo icon + a menu (Open / Quit). Left-click opens the
            // window; "Quit" asks the UI to validate running agents, then exits.
            //
            // The tray is the ONLY way to fully quit the app (closing the window
            // just hides it). So the tray MUST always be created — we embed the
            // icon at compile time (`include_image!`) rather than relying on
            // `default_window_icon()`, which can be None and would otherwise leave
            // the user with a hidden, unquittable process holding files in use.
            let open_item = MenuItem::with_id(app, "open", "Open Lattix xFrontier", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let tray_menu = Menu::with_items(app, &[&open_item, &quit_item])?;
            let tray_icon = include_image!("icons/32x32.png");
            if let Err(e) = TrayIconBuilder::with_id("lattix-tray")
                .icon(tray_icon)
                .tooltip("Lattix xFrontier")
                .menu(&tray_menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => {
                        // Quit MUST always work and must NOT depend on a frontend
                        // round-trip (the webview may be on the loading page or
                        // unresponsive). Notify the UI (best-effort), then force-kill
                        // the backend process tree and exit. RunEvent::Exit reaps the
                        // tree again (idempotent) as a final backstop.
                        let _ = app.emit("app-quit-requested", ());
                        kill_backend_tree();
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(&tray.app_handle().clone());
                    }
                })
                .build(app)
            {
                eprintln!("failed to create system tray icon: {e}");
            }

            // Spawn the packaged supervisor sidecar. The binary is resolved from
            // the bundle's `externalBin` (name + target triple suffix).
            let sidecar = app
                .shell()
                .sidecar("frontier-backend")
                .expect("frontier-backend sidecar is missing from the bundle")
                // Single source of truth for the version: the Tauri app/package
                // version. The backend reports this (FRONTIER_APP_VERSION wins in
                // _platform_version), so the UI no longer shows a stale 0.0.0.
                .env("FRONTIER_APP_VERSION", app.package_info().version.to_string());
            let (mut rx, _child) = sidecar
                .spawn()
                .expect("failed to spawn the frontier-backend sidecar");
            // Remember the supervisor PID so we can kill its whole tree on quit.
            BACKEND_PID.store(_child.pid(), Ordering::SeqCst);

            // Drain sidecar stdout/stderr; surface first-run progress to the splash.
            let drain_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let text = String::from_utf8_lossy(&line).to_string();
                            println!("[backend] {text}");
                            // The supervisor prefixes provisioning lines with
                            // "[firstrun]"; forward them to the loading page.
                            if let Some(msg) = text.split("[firstrun]").nth(1) {
                                let _ = drain_handle.emit("firstrun-progress", msg.trim().to_string());
                            }
                        }
                        CommandEvent::Stderr(line) => {
                            let text = String::from_utf8_lossy(&line).to_string();
                            eprintln!("[backend] {text}");
                            if let Some(msg) = text.split("[firstrun]").nth(1) {
                                let _ = drain_handle.emit("firstrun-progress", msg.trim().to_string());
                            }
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[backend] terminated: {:?}", payload);
                            break;
                        }
                        _ => {}
                    }
                }
            });

            // Drive visible, staged progress on the splash (independent of the
            // sidecar's stdout) and navigate once the UI port is reachable.
            tauri::async_runtime::spawn(async move {
                let _ = handle.emit("firstrun-progress", "Starting backend…".to_string());
                let backend_up = wait_for_health(BACKEND_HEALTH_URL, HEALTH_TIMEOUT_SECS).await;
                let _ = handle.emit(
                    "firstrun-progress",
                    if backend_up { "Backend ready — loading interface…" } else { "Loading interface…" }
                        .to_string(),
                );
                if wait_for_health(FRONTEND_URL, HEALTH_TIMEOUT_SECS).await {
                    if let Some(window) = handle.get_webview_window("main") {
                        let _ = window.navigate(FRONTEND_URL.parse().unwrap());
                    }
                } else {
                    let _ = handle.emit(
                        "firstrun-progress",
                        "⚠ The interface didn't start in time. See logs in %LOCALAPPDATA%\\Lattix\\xFrontier."
                            .to_string(),
                    );
                    eprintln!("frontend did not become reachable within {HEALTH_TIMEOUT_SECS}s");
                }
            });

            // The main window starts on the bundled `loading` page (frontendDist).
            let _ = WebviewUrl::default();
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Lattix xFrontier desktop shell")
        .run(|_app_handle, event| {
            // Final backstop: whenever the app exits (tray Quit, window-driven
            // quit, OS signal, updater restart), reap the backend supervisor and
            // every process it spawned so nothing is left holding files.
            if let tauri::RunEvent::Exit = event {
                kill_backend_tree();
            }
        });
}

/// Poll the backend health endpoint until it responds or the timeout elapses.
async fn wait_for_health(url: &str, timeout_secs: u64) -> bool {
    let deadline = std::time::Instant::now() + Duration::from_secs(timeout_secs);
    let client = match reqwest_like_get() {
        Some(c) => c,
        None => return false,
    };
    while std::time::Instant::now() < deadline {
        if client(url) {
            return true;
        }
        tokio_sleep(Duration::from_millis(750)).await;
    }
    false
}

// The desktop shell avoids an extra HTTP dependency: a tiny std TCP probe is
// enough to know the backend port is accepting connections. Swap for a real
// HTTP client if a status-code check is needed.
fn reqwest_like_get() -> Option<fn(&str) -> bool> {
    Some(|url: &str| {
        // url is http://host:port/path — extract host:port and TCP-probe it.
        let trimmed = url.trim_start_matches("http://");
        let authority = trimmed.split('/').next().unwrap_or("");
        std::net::TcpStream::connect_timeout(
            &match authority.to_socket_addrs_first() {
                Some(addr) => addr,
                None => return false,
            },
            Duration::from_millis(500),
        )
        .is_ok()
    })
}

trait ToSocketAddrsFirst {
    fn to_socket_addrs_first(&self) -> Option<std::net::SocketAddr>;
}

impl ToSocketAddrsFirst for str {
    fn to_socket_addrs_first(&self) -> Option<std::net::SocketAddr> {
        use std::net::ToSocketAddrs;
        self.to_socket_addrs().ok().and_then(|mut it| it.next())
    }
}

async fn tokio_sleep(d: Duration) {
    tauri::async_runtime::spawn_blocking(move || std::thread::sleep(d))
        .await
        .ok();
}
