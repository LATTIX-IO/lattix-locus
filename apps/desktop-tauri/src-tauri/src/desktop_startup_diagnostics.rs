// LOCUS-475: native Windows startup telemetry independent of the webview UI.
// Never write URLs, query strings, prompts, tokens, response bodies, or JS
// exceptions. The log contains fixed event codes only and is capped at 64 KiB.
//
// A WebView2 PageLoadEvent::Finished means browser navigation completed. It
// does NOT establish React hydration, visible pixels, or operator acceptance.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Manager, Url};
use tauri::webview::PageLoadEvent;

const LOG_NAME: &str = "desktop-startup-events.log";
const MAX_LOG_SIZE: u64 = 64 * 1024;

pub(crate) fn classify_url(url: &Url) -> &'static str {
    if url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == Some(3000)
    {
        match url.path() {
            "/home" => "frontend-home",
            "/" => "frontend-root",
            _ => "frontend-other-route",
        }
    } else {
        "bundled-or-other-origin"
    }
}

pub(crate) fn record(app: &AppHandle, kind: &'static str) {
    // Failure to record diagnostics must never block startup or weaken the
    // readiness/version/security checks. Do not accept arbitrary log messages.
    let Ok(dir) = app.path().app_log_dir() else { return };
    if fs::create_dir_all(&dir).is_err() { return; }
    let path = dir.join(LOG_NAME);
    if fs::metadata(&path).is_ok_and(|meta| meta.len() > MAX_LOG_SIZE) {
        if fs::write(&path, b"").is_err() { return; }
    }
    let Ok(mut writer) = OpenOptions::new().create(true).append(true).open(path) else {
        return;
    };
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or_default();
    // Single-line fixed labels, no untrusted content.
    let _ = writeln!(writer, "{seconds} {kind}");
}

pub(crate) fn page_load(app: &AppHandle, url: &Url, event: PageLoadEvent) {
    let kind = classify_url(url);
    let code = match (kind, event) {
        ("frontend-home", PageLoadEvent::Started) => "frontend-home-started",
        ("frontend-home", PageLoadEvent::Finished) => "frontend-home-finished",
        ("frontend-root", PageLoadEvent::Started) => "frontend-root-started",
        ("frontend-root", PageLoadEvent::Finished) => "frontend-root-finished",
        ("frontend-other-route", PageLoadEvent::Started) => "frontend-route-started",
        ("frontend-other-route", PageLoadEvent::Finished) => "frontend-route-finished",
        (_, PageLoadEvent::Started) => "non-frontend-started",
        (_, PageLoadEvent::Finished) => "non-frontend-finished",
    };
    record(app, code);
}

#[cfg(test)]
mod tests {
    use super::classify_url;

    #[test]
    fn classify_exact_loopback_origin_without_logging_sensitive_query() {
        assert_eq!(
            classify_url(&"http://127.0.0.1:3000/home?session=private".parse().unwrap()),
            "frontend-home"
        );
        assert_eq!(
            classify_url(&"http://127.0.0.1:3000/".parse().unwrap()),
            "frontend-root"
        );
        assert_eq!(
            classify_url(&"http://127.0.0.1:3000/activity?session=private".parse().unwrap()),
            "frontend-other-route"
        );
        assert_eq!(
            classify_url(&"http://127.0.0.1:8000/home".parse().unwrap()),
            "bundled-or-other-origin"
        );
        assert_eq!(
            classify_url(&"https://example.test/home".parse().unwrap()),
            "bundled-or-other-origin"
        );
    }
}
