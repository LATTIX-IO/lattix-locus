// Computer-use shell controls (LOCUS-346): the global panic hotkey and the
// "takeover active" tray indicator.
//
// Both run on plain std threads and talk to the local backend over a raw
// loopback HTTP/1.1 socket, so they keep working when the UI webview is frozen
// or still on the loading page (P5: the human can always stop the agent).
//
// Authentication: the desktop backend runs the `local-native` profile with the
// single-user local-operator bootstrap (loopback only), which is how the UI
// itself is authenticated. If `LOCUS_API_BEARER_TOKEN` is present in this
// shell's own environment (dev / managed installs), it is sent as a bearer
// token as well. The token is never logged.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::str::FromStr;
use std::time::Duration;

use tauri::AppHandle;
use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut};

const BACKEND_ADDR: &str = "127.0.0.1:8000";
const PANIC_PATH: &str = "/computer-use/panic";
const STATUS_PATH: &str = "/computer-use/status";
const IO_TIMEOUT: Duration = Duration::from_millis(1500);
const STATUS_POLL: Duration = Duration::from_secs(1);
/// Env override for the panic hotkey, e.g. "ctrl+alt+shift+F12".
pub const HOTKEY_ENV: &str = "LOCUS_PANIC_HOTKEY";
pub const TRAY_ID: &str = "lattix-tray";
const TRAY_IDLE_TOOLTIP: &str = "Lattix Locus";

/// Default: Ctrl+Alt+Shift+Escape (Cmd+Alt+Shift+Escape on macOS).
pub fn default_panic_shortcut() -> Shortcut {
    #[cfg(target_os = "macos")]
    let primary = Modifiers::SUPER;
    #[cfg(not(target_os = "macos"))]
    let primary = Modifiers::CONTROL;
    Shortcut::new(Some(primary | Modifiers::ALT | Modifiers::SHIFT), Code::Escape)
}

/// The configured panic hotkey: `LOCUS_PANIC_HOTKEY` when it parses, else the default.
pub fn panic_shortcut() -> Shortcut {
    match std::env::var(HOTKEY_ENV) {
        Ok(raw) if !raw.trim().is_empty() => match Shortcut::from_str(raw.trim()) {
            Ok(shortcut) => shortcut,
            Err(e) => {
                eprintln!("[computer-use] invalid {HOTKEY_ENV} ({e}); using the default hotkey");
                default_panic_shortcut()
            }
        },
        _ => default_panic_shortcut(),
    }
}

pub fn hotkey_label() -> String {
    match std::env::var(HOTKEY_ENV) {
        Ok(raw) if Shortcut::from_str(raw.trim()).is_ok() => raw.trim().to_string(),
        _ if cfg!(target_os = "macos") => "Cmd+Alt+Shift+Esc".to_string(),
        _ => "Ctrl+Alt+Shift+Esc".to_string(),
    }
}

fn bearer_header() -> String {
    match std::env::var("LOCUS_API_BEARER_TOKEN") {
        Ok(token) if !token.trim().is_empty() => {
            format!("Authorization: Bearer {}\r\n", token.trim())
        }
        _ => String::new(),
    }
}

/// One loopback request; returns (status code, body). Never logs the request.
/// Also used by the update channels (`updates.rs`, LOCUS-349).
pub(crate) fn request(method: &str, path: &str) -> Option<(u16, String)> {
    let addr: SocketAddr = BACKEND_ADDR.parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, IO_TIMEOUT).ok()?;
    stream.set_read_timeout(Some(IO_TIMEOUT)).ok()?;
    stream.set_write_timeout(Some(IO_TIMEOUT)).ok()?;
    let head = format!(
        "{method} {path} HTTP/1.1\r\nHost: {BACKEND_ADDR}\r\nUser-Agent: locus-desktop-shell\r\n\
         Accept: application/json\r\nContent-Length: 0\r\nConnection: close\r\n{}\r\n",
        bearer_header()
    );
    stream.write_all(head.as_bytes()).ok()?;
    let mut raw = Vec::new();
    let _ = stream.read_to_end(&mut raw);
    let text = String::from_utf8_lossy(&raw).to_string();
    let status = text
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())?;
    let body = text.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
    Some((status, body))
}

/// Fire the panic on a fresh thread (never blocks the hotkey handler / UI).
pub fn trigger_panic() {
    std::thread::spawn(|| {
        // Retry once quickly: the panic must land even if the first connect races.
        for attempt in 0..2 {
            match request("POST", PANIC_PATH) {
                Some((status, _)) => {
                    eprintln!("[computer-use] panic hotkey -> HTTP {status}");
                    return;
                }
                None if attempt == 0 => std::thread::sleep(Duration::from_millis(150)),
                None => eprintln!("[computer-use] panic hotkey: backend unreachable"),
            }
        }
    });
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Indicator {
    Idle,
    Takeover,
    Stopped,
}

fn indicator_from(body: &str) -> Indicator {
    let parsed: Option<serde_json::Value> = serde_json::from_str(body.trim()).ok();
    let (mode, panicked) = match parsed {
        Some(v) => (
            v.get("mode").and_then(|m| m.as_str()).unwrap_or("").to_string(),
            v.get("panicked").and_then(|p| p.as_bool()).unwrap_or(false),
        ),
        None => (String::new(), false),
    };
    if panicked {
        Indicator::Stopped
    } else if mode == "takeover" {
        Indicator::Takeover
    } else {
        Indicator::Idle
    }
}

fn apply_indicator(app: &AppHandle, state: Indicator) {
    let Some(tray) = app.tray_by_id(TRAY_ID) else {
        return;
    };
    let tooltip = match state {
        Indicator::Idle => TRAY_IDLE_TOOLTIP.to_string(),
        Indicator::Takeover => format!(
            "Lattix Locus - AGENT IS CONTROLLING THIS COMPUTER ({} to stop)",
            hotkey_label()
        ),
        Indicator::Stopped => "Lattix Locus - computer use stopped (panic)".to_string(),
    };
    let _ = tray.set_tooltip(Some(tooltip));
    // macOS shows the title next to the menu-bar icon; other OSes ignore it.
    let title = match state {
        Indicator::Takeover => Some("AGENT IN CONTROL"),
        _ => None,
    };
    let _ = tray.set_title(title);
}

/// Poll /computer-use/status about once a second and reflect takeover on the tray.
pub fn start_status_indicator(app: AppHandle) {
    std::thread::spawn(move || {
        let mut current = Indicator::Idle;
        loop {
            let next = match request("GET", STATUS_PATH) {
                Some((200, body)) => indicator_from(&body),
                _ => Indicator::Idle,
            };
            if next != current {
                apply_indicator(&app, next);
                current = next;
            }
            std::thread::sleep(STATUS_POLL);
        }
    });
}
