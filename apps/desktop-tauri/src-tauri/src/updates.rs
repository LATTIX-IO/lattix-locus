// Update channels (LOCUS-349, D-26).
//
// * Channel setting `dev | stable`, persisted in the app config dir
//   (`update-settings.json`, default stable). It selects one of exactly two
//   compiled-in GitHub URLs via `updater_builder().endpoints(...)`; a URL is
//   never read from the settings file, the UI or the environment.
// * Stable: background check on start and every few hours; an available update
//   only emits `update-available` / `update-status` so the UI shows a banner.
//   Installing is the user's click (`install_update_and_restart`).
// * Dev: background check, auto-download, then wait until the backend reports
//   no agent run in progress and holds the self-improvement loop
//   (POST /system/update/prepare takes the loop's single-run lock, so a loop
//   run is never killed), then stop the sidecar, install and restart.
// * tauri#15134 (an NSIS update can keep a stale, locked sidecar): the sidecar
//   is stopped through the normal teardown before installing, and after every
//   start the shell checks the backend's stamped build version
//   (`backend_handshake`); a mismatch stops the app from loading.
//
// Updater signature verification is the plugin's and stays mandatory: the
// pubkey is in tauri.conf.json and `install` refuses unsigned or foreign bytes.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, Url};
use tauri_plugin_updater::{Update, Updater, UpdaterExt};

use crate::computer_use;

/// The only two update endpoints the app will ever use.
pub const DEV_ENDPOINT: &str =
    "https://github.com/LATTIX-IO/lattix-locus/releases/download/channel-dev/latest.json";
pub const STABLE_ENDPOINT: &str =
    "https://github.com/LATTIX-IO/lattix-locus/releases/download/channel-stable/latest.json";

pub const STATUS_EVENT: &str = "update-status";
pub const AVAILABLE_EVENT: &str = "update-available";
pub const VERSION_MISMATCH_EVENT: &str = "backend-version-mismatch";

const SETTINGS_FILE: &str = "update-settings.json";
const STATUS_PATH: &str = "/system/update/status";
const PREPARE_PATH: &str = "/system/update/prepare";
const CANCEL_PATH: &str = "/system/update/cancel";
const SHUTDOWN_PATH: &str = "/system/shutdown";
const FIRST_CHECK_DELAY: Duration = Duration::from_secs(120);
const CHECK_INTERVAL: Duration = Duration::from_secs(4 * 60 * 60);
const IDLE_POLL: Duration = Duration::from_secs(60);
const SHUTDOWN_WAIT: Duration = Duration::from_secs(20);
const HANDSHAKE_ATTEMPTS: u32 = 10;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Channel {
    Dev,
    Stable,
}

impl Channel {
    pub fn parse(raw: &str) -> Option<Channel> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "dev" => Some(Channel::Dev),
            "stable" => Some(Channel::Stable),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Channel::Dev => "dev",
            Channel::Stable => "stable",
        }
    }

    pub fn endpoint(self) -> &'static str {
        match self {
            Channel::Dev => DEV_ENDPOINT,
            Channel::Stable => STABLE_ENDPOINT,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct UpdateStatus {
    pub channel: Channel,
    /// idle | checking | up_to_date | available | downloading | waiting_for_idle
    /// | installing | error
    pub state: &'static str,
    pub current_version: String,
    pub version: Option<String>,
    pub detail: String,
}

pub struct UpdateState {
    channel: Mutex<Channel>,
    status: Mutex<Option<UpdateStatus>>,
    busy: AtomicBool,
}

impl UpdateState {
    pub fn new(channel: Channel) -> Self {
        UpdateState {
            channel: Mutex::new(channel),
            status: Mutex::new(None),
            busy: AtomicBool::new(false),
        }
    }

    fn channel(&self) -> Channel {
        self.channel.lock().map(|c| *c).unwrap_or(Channel::Stable)
    }
}

// --------------------------------------------------------------------------
// Settings (app config dir)
// --------------------------------------------------------------------------
#[derive(Default, Serialize, Deserialize)]
struct SettingsFile {
    #[serde(default)]
    channel: Option<String>,
}

fn settings_path(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join(SETTINGS_FILE))
}

/// The persisted channel; missing or unreadable settings mean Stable.
pub fn load_channel(app: &AppHandle) -> Channel {
    settings_path(app)
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<SettingsFile>(&text).ok())
        .and_then(|settings| settings.channel)
        .and_then(|raw| Channel::parse(&raw))
        .unwrap_or(Channel::Stable)
}

fn save_channel(app: &AppHandle, channel: Channel) -> Result<(), String> {
    let path = settings_path(app).ok_or_else(|| "no app config directory".to_string())?;
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let body = serde_json::to_string_pretty(&SettingsFile {
        channel: Some(channel.as_str().to_string()),
    })
    .map_err(|e| e.to_string())?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, body).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

fn current_channel(app: &AppHandle) -> Channel {
    app.state::<UpdateState>().channel()
}

fn set_status(app: &AppHandle, state: &'static str, version: Option<String>, detail: impl Into<String>) {
    let status = UpdateStatus {
        channel: current_channel(app),
        state,
        current_version: app.package_info().version.to_string(),
        version,
        detail: detail.into(),
    };
    if let Ok(mut slot) = app.state::<UpdateState>().status.lock() {
        *slot = Some(status.clone());
    }
    let _ = app.emit(STATUS_EVENT, status);
}

fn status_snapshot(app: &AppHandle) -> UpdateStatus {
    let stored = app
        .state::<UpdateState>()
        .status
        .lock()
        .ok()
        .and_then(|slot| slot.clone());
    let channel = current_channel(app);
    match stored {
        Some(status) if status.channel == channel => status,
        _ => UpdateStatus {
            channel,
            state: "idle",
            current_version: app.package_info().version.to_string(),
            version: None,
            detail: String::new(),
        },
    }
}

/// The updater for a channel: the configured pubkey, one allow-listed endpoint.
fn updater_for(app: &AppHandle, channel: Channel) -> Result<Updater, String> {
    let url = Url::parse(channel.endpoint()).map_err(|e| e.to_string())?;
    app.updater_builder()
        .endpoints(vec![url])
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())
}

// --------------------------------------------------------------------------
// Backend coordination (loopback, see computer_use::request)
// --------------------------------------------------------------------------
#[derive(Default, Deserialize)]
struct Readiness {
    #[serde(default)]
    ready: bool,
    #[serde(default)]
    reason: String,
}

fn backend_prepare() -> Result<Readiness, String> {
    match computer_use::request("POST", PREPARE_PATH) {
        Some((200, body)) => {
            serde_json::from_str(body.trim()).map_err(|e| format!("unreadable readiness: {e}"))
        }
        Some((code, _)) => Err(format!("the backend answered HTTP {code}")),
        None => Err("the backend is unreachable".to_string()),
    }
}

fn backend_cancel() {
    let _ = computer_use::request("POST", CANCEL_PATH);
}

/// Stop the sidecar through the normal teardown (the supervisor stops its
/// children, the loop included, then exits), then the PID-tree kill as a
/// backstop, so no file in the install dir is locked when the installer runs.
fn stop_backend_for_install() {
    let _ = computer_use::request("POST", SHUTDOWN_PATH);
    let deadline = Instant::now() + SHUTDOWN_WAIT;
    while crate::backend_running() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(250));
    }
    crate::kill_backend_tree();
    // Give the OS a moment to release image and file handles.
    std::thread::sleep(Duration::from_secs(2));
}

/// Install the downloaded bytes (the plugin verifies the signature) and restart.
/// On failure the old backend is started again and the error returned.
fn install_and_restart(app: &AppHandle, update: &Update, bytes: Vec<u8>) -> Result<(), String> {
    set_status(app, "installing", Some(update.version.clone()), "stopping the backend");
    stop_backend_for_install();
    match update.install(&bytes) {
        Ok(()) => app.restart(),
        Err(e) => {
            if let Err(start_err) = crate::start_backend(app) {
                eprintln!("[update] could not restart the backend: {start_err}");
            }
            Err(format!("install failed: {e}"))
        }
    }
}

// --------------------------------------------------------------------------
// Background cycle (runs on its own thread; blocking calls are fine there)
// --------------------------------------------------------------------------
struct BusyGuard(AppHandle);

impl Drop for BusyGuard {
    fn drop(&mut self) {
        self.0.state::<UpdateState>().busy.store(false, Ordering::SeqCst);
    }
}

fn dev_install(app: &AppHandle, update: Update) {
    let version = update.version.clone();
    set_status(app, "downloading", Some(version.clone()), "");
    let bytes = match tauri::async_runtime::block_on(update.download(|_, _| {}, || {})) {
        Ok(bytes) => bytes,
        Err(e) => {
            set_status(app, "error", Some(version), format!("download failed: {e}"));
            return;
        }
    };
    loop {
        if current_channel(app) != Channel::Dev {
            backend_cancel();
            set_status(app, "idle", None, "channel changed; the downloaded Dev update was dropped");
            return;
        }
        match backend_prepare() {
            Ok(readiness) if readiness.ready => break,
            Ok(readiness) => set_status(app, "waiting_for_idle", Some(version.clone()), readiness.reason),
            Err(e) => set_status(app, "waiting_for_idle", Some(version.clone()), e),
        }
        std::thread::sleep(IDLE_POLL);
    }
    if let Err(e) = install_and_restart(app, &update, bytes) {
        set_status(app, "error", Some(version), e);
    }
}

fn run_cycle(app: &AppHandle) {
    if app.state::<UpdateState>().busy.swap(true, Ordering::SeqCst) {
        return; // a check, download or wait is already in progress
    }
    let _guard = BusyGuard(app.clone());
    let channel = current_channel(app);
    set_status(app, "checking", None, "");
    let checked = updater_for(app, channel)
        .and_then(|updater| tauri::async_runtime::block_on(updater.check()).map_err(|e| e.to_string()));
    match checked {
        Ok(None) => set_status(app, "up_to_date", None, ""),
        Ok(Some(update)) => match channel {
            Channel::Stable => {
                let version = update.version.clone();
                set_status(app, "available", Some(version.clone()), "");
                let _ = app.emit(AVAILABLE_EVENT, version);
            }
            Channel::Dev => dev_install(app, update),
        },
        // No signed metadata published yet, offline, or rate limited: report it
        // in the status; the UI stays quiet.
        Err(e) => set_status(app, "error", None, e),
    }
}

/// Start the background checks (first one shortly after start, then periodic).
pub fn start(app: AppHandle) {
    std::thread::spawn(move || {
        std::thread::sleep(FIRST_CHECK_DELAY);
        loop {
            run_cycle(&app);
            std::thread::sleep(CHECK_INTERVAL);
        }
    });
}

fn trigger(app: &AppHandle) {
    let app = app.clone();
    std::thread::spawn(move || run_cycle(&app));
}

// --------------------------------------------------------------------------
// Version handshake (tauri#15134)
// --------------------------------------------------------------------------
#[derive(Debug, PartialEq, Eq)]
pub enum Handshake {
    Match,
    /// Local or source build: no stamp to compare.
    Unstamped,
    Mismatch(String),
}

fn normalize(version: &str) -> &str {
    let trimmed = version.trim();
    trimmed.strip_prefix('v').unwrap_or(trimmed)
}

pub fn classify(app_version: &str, backend_version: &str) -> Handshake {
    let app = normalize(app_version);
    let backend = normalize(backend_version);
    if backend.is_empty() {
        Handshake::Unstamped
    } else if app == backend {
        Handshake::Match
    } else {
        Handshake::Mismatch(format!(
            "the backend is version {backend} but the app is {app}; the update did not replace the backend"
        ))
    }
}

/// Ask the backend which build it is. Blocking; call off the async runtime.
pub fn backend_handshake(app_version: &str) -> Handshake {
    for _ in 0..HANDSHAKE_ATTEMPTS {
        match computer_use::request("GET", STATUS_PATH) {
            Some((200, body)) => {
                let parsed: Option<serde_json::Value> = serde_json::from_str(body.trim()).ok();
                return match parsed {
                    Some(value) => classify(
                        app_version,
                        value.get("build_version").and_then(|v| v.as_str()).unwrap_or(""),
                    ),
                    None => Handshake::Mismatch("the backend version answer is unreadable".to_string()),
                };
            }
            Some((404, _)) => {
                return Handshake::Mismatch(
                    "the backend predates the version check; the update did not replace it".to_string(),
                )
            }
            Some((code, _)) if code < 500 => {
                return Handshake::Mismatch(format!("the backend version check failed (HTTP {code})"))
            }
            _ => std::thread::sleep(Duration::from_secs(1)),
        }
    }
    Handshake::Mismatch("the backend did not answer the version check".to_string())
}

pub fn report_mismatch(app: &AppHandle, detail: &str) {
    eprintln!("[update] backend version mismatch: {detail}");
    let _ = app.emit(VERSION_MISMATCH_EVENT, detail.to_string());
    let _ = app.emit(
        "firstrun-progress",
        format!(
            "⚠ Backend version mismatch: {detail}. Lattix Locus stopped it instead of running \
             stale code. Quit from the tray and reinstall the latest release."
        ),
    );
}

// --------------------------------------------------------------------------
// Commands
// --------------------------------------------------------------------------
#[tauri::command]
pub fn get_update_status(app: AppHandle) -> UpdateStatus {
    status_snapshot(&app)
}

/// Switch channel (only `dev` or `stable`), persist it and check right away.
#[tauri::command]
pub fn set_update_channel(app: AppHandle, channel: String) -> Result<UpdateStatus, String> {
    let channel = Channel::parse(&channel)
        .ok_or_else(|| "unknown update channel (expected \"dev\" or \"stable\")".to_string())?;
    save_channel(&app, channel)?;
    if let Ok(mut slot) = app.state::<UpdateState>().channel.lock() {
        *slot = channel;
    }
    set_status(&app, "idle", None, format!("channel set to {}", channel.as_str()));
    trigger(&app);
    Ok(status_snapshot(&app))
}

/// The available version on the current channel, or None.
#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<Option<String>, String> {
    let updater = updater_for(&app, current_channel(&app))?;
    match updater.check().await {
        Ok(Some(update)) => Ok(Some(update.version)),
        Ok(None) => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}

/// The user's click: download, stop the backend, install, restart. The UI
/// confirms first and shows any agent runs in progress.
#[tauri::command]
pub async fn install_update_and_restart(app: AppHandle) -> Result<(), String> {
    let updater = updater_for(&app, current_channel(&app))?;
    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "No update available".to_string())?;
    set_status(&app, "downloading", Some(update.version.clone()), "");
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|e| e.to_string())?;
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        // Best effort: hold the loop so no new loop run starts during the install.
        let _ = backend_prepare();
        install_and_restart(&handle, &update, bytes)
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_two_channels_parse() {
        assert_eq!(Channel::parse("dev"), Some(Channel::Dev));
        assert_eq!(Channel::parse(" Stable "), Some(Channel::Stable));
        assert_eq!(Channel::parse("https://example.com/latest.json"), None);
        assert_eq!(Channel::parse(""), None);
    }

    #[test]
    fn endpoints_are_the_allow_listed_github_urls() {
        for channel in [Channel::Dev, Channel::Stable] {
            assert!(channel
                .endpoint()
                .starts_with("https://github.com/LATTIX-IO/lattix-locus/releases/download/channel-"));
            assert!(Url::parse(channel.endpoint()).is_ok());
        }
    }

    #[test]
    fn handshake_classification() {
        assert_eq!(classify("0.1.0-dev.4", "0.1.0-dev.4"), Handshake::Match);
        assert_eq!(classify("0.1.0-dev.4", "v0.1.0-dev.4"), Handshake::Match);
        assert_eq!(classify("0.1.0-dev.4", ""), Handshake::Unstamped);
        assert!(matches!(classify("0.1.0-dev.5", "0.1.0-dev.4"), Handshake::Mismatch(_)));
    }
}
