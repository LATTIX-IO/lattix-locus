// Out-of-band principal confirmation for the user browser (LOCUS-350, D-25).
//
// On the desktop install the backend authenticates every loopback request as
// the operator (local-operator bootstrap), so a local process could otherwise
// widen the agent's access to the principal's signed-in browser on its own.
// Widening (a wider browser tier, more allowlisted / granted sites, pairing a
// browser) therefore needs a proof that only this shell can produce, and only
// after the human confirms a native OS dialog:
//
//   1. At sidecar spawn the shell draws a 32-byte secret from the OS CSPRNG and
//      writes it to the backend's stdin (never env, argv, files or logs). The
//      backend keeps it in memory and detaches stdin so no child inherits it
//      (locus_tooling/shell_confirmation.py).
//   2. The webview UI invokes `confirm_browser_tier` / `confirm_browser_pairing`.
//      The shell shows a native dialog (tauri-plugin-dialog) with the exact risk
//      text and site lists. Only on "Allow" does it sign
//      HMAC-SHA256(secret, canonical request) with a fresh nonce and timestamp,
//      and send the request to the backend itself. The webview never sees the
//      secret or the proof.
//
// The canonical messages and risk texts below must match
// locus_tooling/shell_confirmation.py and
// locus_runtime/computer_use/user_browser/tiers.py::TIER_RISKS byte for byte
// (tests/backend/test_desktop_packaging.py checks the texts).
//
// The secret, proof, dialog and request helpers here are shared with
// shell_actions.rs, which confirms every other capability-widening request
// (LOCUS-357) with the generic request-bound message format.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use sha2::Sha256;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

type HmacSha256 = Hmac<Sha256>;

const BACKEND_ADDR: &str = "127.0.0.1:8000";
const TIER_PATH: &str = "/user-browser/tier";
const PAIRING_PATH: &str = "/user-browser/pairing";
const IO_TIMEOUT: Duration = Duration::from_secs(10);
pub(crate) const MESSAGE_PREFIX: &str = "locus-shell-proof/v1";
const SECRET_LINE_PREFIX: &str = "locus-shell-secret:v1:";
/// Flag (not the secret) telling the backend to read the secret from stdin.
pub const SHELL_CONFIRMATION_ENV: &str = "LOCUS_SHELL_CONFIRMATION";
const MAX_SITES: usize = 200;
const MAX_SITE_CHARS: usize = 253;

const RISK_STRICT: &str = "Every action asks; the agent only reads tabs you share.";
const RISK_ASSISTED: &str = "On allowlisted sites the agent reads pages and navigates without asking, using your signed-in sessions. Clicks and typing still ask.";
const RISK_TRUSTED: &str = "On granted sites the agent clicks and types in your signed-in sessions without asking. Only irreversible actions (send, pay, purchase, delete, account or security settings) ask.";
const RISK_OPEN: &str = "The agent acts in every tab and site of your signed-in browser without asking, including irreversible actions such as sending and deleting. A prompt injection on any page can make it do so. Payments, purchases and account-security changes still ask. Secret fields, panic and audit still apply.";

/// The per-launch secret. Set once at sidecar spawn; never logged or exposed.
static SHELL_SECRET: OnceLock<[u8; 32]> = OnceLock::new();

pub(crate) fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push(DIGITS[(b >> 4) as usize] as char);
        out.push(DIGITS[(b & 0x0f) as usize] as char);
    }
    out
}

fn random_bytes<const N: usize>() -> Result<[u8; N], String> {
    let mut buf = [0u8; N];
    getrandom::getrandom(&mut buf).map_err(|_| "the OS random source is unavailable".to_string())?;
    Ok(buf)
}

/// Generate the secret (once per launch) and return the line to write to the
/// backend's stdin. Returns None if the OS CSPRNG failed: the backend then has
/// no secret and refuses every widening on the desktop profile (fail closed).
pub fn secret_line_for_backend() -> Option<String> {
    let secret = match SHELL_SECRET.get() {
        Some(s) => *s,
        None => {
            let fresh = random_bytes::<32>().ok()?;
            let _ = SHELL_SECRET.set(fresh);
            *SHELL_SECRET.get()?
        }
    };
    Some(format!("{SECRET_LINE_PREFIX}{}\n", hex(&secret)))
}

fn risk_text(tier: &str) -> Option<&'static str> {
    match tier {
        "strict" => Some(RISK_STRICT),
        "assisted" => Some(RISK_ASSISTED),
        "trusted" => Some(RISK_TRUSTED),
        "open" => Some(RISK_OPEN),
        _ => None,
    }
}

fn valid_item(item: &str) -> bool {
    !item.is_empty()
        && item.len() <= MAX_SITE_CHARS
        && !item.chars().any(|c| matches!(c, '|' | ',' | '~') || c.is_control())
}

fn validate_sites(label: &str, sites: &[String]) -> Result<(), String> {
    if sites.len() > MAX_SITES {
        return Err(format!("at most {MAX_SITES} {label}"));
    }
    if let Some(bad) = sites.iter().find(|s| !valid_item(s)) {
        return Err(format!("unsupported characters in {label}: {bad:?}"));
    }
    Ok(())
}

fn tier_message(tier: &str, allow: &[String], grant: &[String], nonce: &str, ts: u64) -> String {
    format!(
        "{MESSAGE_PREFIX}|browser-tier|{tier}|{}|{}|{nonce}|{ts}",
        allow.join(","),
        grant.join(",")
    )
}

fn pairing_message(nonce: &str, ts: u64) -> String {
    format!("{MESSAGE_PREFIX}|browser-pair|{nonce}|{ts}")
}

/// `v1:<ts>:<nonce>:<hmac>` for the canonical message built by `message_for`.
pub(crate) fn proof(message_for: impl Fn(&str, u64) -> String) -> Result<String, String> {
    let secret = SHELL_SECRET
        .get()
        .ok_or_else(|| "the backend was not started by this shell".to_string())?;
    let nonce = hex(&random_bytes::<16>()?);
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| "system clock is before 1970".to_string())?
        .as_secs();
    let mut mac = HmacSha256::new_from_slice(secret).map_err(|_| "bad key".to_string())?;
    mac.update(message_for(&nonce, ts).as_bytes());
    let tag = hex(&mac.finalize().into_bytes());
    Ok(format!("v1:{ts}:{nonce}:{tag}"))
}

fn bearer_header() -> String {
    match std::env::var("LOCUS_API_BEARER_TOKEN") {
        Ok(token) if !token.trim().is_empty() => {
            format!("Authorization: Bearer {}\r\n", token.trim())
        }
        _ => String::new(),
    }
}

/// One loopback request with a JSON body (possibly empty) and, for a
/// confirmed change, the proof header; returns (status, body). Never logs the
/// request (it may carry the proof and a bearer token).
pub(crate) fn send(
    method: &str,
    path: &str,
    body: &str,
    proof_header: Option<&str>,
) -> Result<(u16, String), String> {
    let addr: SocketAddr = BACKEND_ADDR.parse().map_err(|_| "bad backend address".to_string())?;
    let mut stream = TcpStream::connect_timeout(&addr, IO_TIMEOUT)
        .map_err(|_| "the Locus backend is not reachable".to_string())?;
    let _ = stream.set_read_timeout(Some(IO_TIMEOUT));
    let _ = stream.set_write_timeout(Some(IO_TIMEOUT));
    let proof_line = match proof_header {
        Some(proof_header) => format!("X-Locus-Shell-Proof: {proof_header}\r\n"),
        None => String::new(),
    };
    let head = format!(
        "{method} {path} HTTP/1.1\r\nHost: {BACKEND_ADDR}\r\nUser-Agent: locus-desktop-shell\r\n\
         Accept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\
         {proof_line}Connection: close\r\n{}\r\n",
        body.len(),
        bearer_header()
    );
    stream
        .write_all(head.as_bytes())
        .and_then(|_| stream.write_all(body.as_bytes()))
        .map_err(|_| "could not send the request to the backend".to_string())?;
    let mut raw = Vec::new();
    let _ = stream.read_to_end(&mut raw);
    parse_response(&raw)
}

/// Status and body of a raw HTTP/1.1 response (`Connection: close`). Chunked
/// transfer encoding is undone on the bytes, before UTF-8 decoding.
fn parse_response(raw: &[u8]) -> Result<(u16, String), String> {
    let split = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| "the backend sent no HTTP response".to_string())?;
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or_else(|| "the backend sent no HTTP status".to_string())?;
    let chunked = head.lines().any(|line| {
        let line = line.to_ascii_lowercase();
        line.starts_with("transfer-encoding:") && line.contains("chunked")
    });
    let payload = &raw[split + 4..];
    let body = if chunked {
        dechunk(payload).ok_or_else(|| "the backend sent a malformed response".to_string())?
    } else {
        payload.to_vec()
    };
    Ok((status, String::from_utf8_lossy(&body).to_string()))
}

fn dechunk(mut rest: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let line_end = rest.windows(2).position(|window| window == b"\r\n")?;
        let size_text = std::str::from_utf8(&rest[..line_end]).ok()?;
        let size = usize::from_str_radix(size_text.split(';').next()?.trim(), 16).ok()?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            return Some(out);
        }
        let end = size.checked_add(2)?;
        if rest.len() < end {
            return None;
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[end..];
    }
}

pub(crate) async fn ask_human(app: tauri::AppHandle, title: String, text: String) -> bool {
    // blocking_show must not run on the main thread: use a blocking worker.
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .message(text)
            .title(title)
            .kind(MessageDialogKind::Warning)
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Allow".to_string(),
                "Cancel".to_string(),
            ))
            .blocking_show()
    })
    .await
    .unwrap_or(false)
}

fn list_text(sites: &[String]) -> String {
    if sites.is_empty() {
        "(none)".to_string()
    } else {
        sites.join(", ")
    }
}

/// Ask the principal, in a native dialog, to confirm a browser-tier change;
/// on "Allow", sign it and send it to the backend. Returns the backend's JSON.
/// Both site lists are required so the dialog shows every site the tier covers.
#[tauri::command]
pub async fn confirm_browser_tier(
    app: tauri::AppHandle,
    tier: String,
    allowlisted_sites: Vec<String>,
    granted_sites: Vec<String>,
) -> Result<String, String> {
    let risk = risk_text(&tier).ok_or_else(|| "unknown browser tier".to_string())?;
    validate_sites("allowlisted sites", &allowlisted_sites)?;
    validate_sites("granted sites", &granted_sites)?;
    let text = format!(
        "Set the Locus agent's access to your signed-in browser to: {}\n\n{risk}\n\n\
         Allowlisted sites (read and navigate): {}\nGranted sites (act): {}\n\n\
         Only allow this if you asked for it.",
        tier.to_uppercase(),
        list_text(&allowlisted_sites),
        list_text(&granted_sites),
    );
    if !ask_human(app, "Lattix Locus: confirm browser access".to_string(), text).await {
        return Err("cancelled".to_string());
    }
    let header = proof(|nonce, ts| {
        tier_message(&tier, &allowlisted_sites, &granted_sites, nonce, ts)
    })?;
    let body = serde_json::json!({
        "tier": tier,
        "allowlisted_sites": allowlisted_sites,
        "granted_sites": granted_sites,
        "acknowledge_risk": true,
    })
    .to_string();
    let (status, response) = tauri::async_runtime::spawn_blocking(move || {
        send("PUT", TIER_PATH, &body, Some(header.as_str()))
    })
    .await
    .map_err(|_| "request task failed".to_string())??;
    if status == 200 {
        Ok(response)
    } else {
        Err(format!("the backend refused the change (HTTP {status})"))
    }
}

/// Ask the principal to confirm pairing (or re-pairing) a browser; on "Allow",
/// sign the request and send it to the backend.
#[tauri::command]
pub async fn confirm_browser_pairing(app: tauri::AppHandle) -> Result<String, String> {
    let text = "Pair your browser with Lattix Locus?\n\nThe Locus browser extension will let \
                the agent use your signed-in browser under the browser tier you choose \
                (Strict by default: it only reads tabs you share and asks before anything \
                else). Pairing again replaces the previous pairing.\n\nOnly allow this if \
                you asked for it."
        .to_string();
    if !ask_human(app, "Lattix Locus: pair browser".to_string(), text).await {
        return Err("cancelled".to_string());
    }
    let header = proof(pairing_message)?;
    let (status, response) = tauri::async_runtime::spawn_blocking(move || {
        send("POST", PAIRING_PATH, "{}", Some(header.as_str()))
    })
    .await
    .map_err(|_| "request task failed".to_string())??;
    if status == 200 {
        Ok(response)
    } else {
        Err(format!("the backend refused pairing (HTTP {status})"))
    }
}
