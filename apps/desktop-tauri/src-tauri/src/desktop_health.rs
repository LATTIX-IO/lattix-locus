// LOCUS-475: fail-closed local service readiness for the installed desktop.
//
// The old shell navigated once a port accepted a TCP socket. A stale Node
// server, a foreign service, or HTTP 500 could therefore show a blank webview.
// This module probes the actual HTTP route and verifies the frontend's release
// build identity, with fixed loopback targets and no new network dependencies.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

const MAX_HEADERS: usize = 16 * 1024;
const IO_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Service {
    Backend,
    Frontend,
}

impl Service {
    fn port(self) -> u16 {
        match self {
            Service::Backend => 8000,
            Service::Frontend => 3000,
        }
    }

    fn path(self) -> &'static str {
        match self {
            Service::Backend => "/healthz",
            Service::Frontend => "/home",
        }
    }

    pub(crate) fn label(self) -> &'static str {
        match self {
            Service::Backend => "backend",
            Service::Frontend => "frontend",
        }
    }
}

fn response_header<'a>(response: &'a str, name: &str) -> Option<&'a str> {
    response
        .lines()
        .skip(1)
        .filter_map(|line| line.split_once(':'))
        .find_map(|(key, value)| key.eq_ignore_ascii_case(name).then_some(value.trim()))
}

/// Only successful HTTP 200 on the expected route is healthy. The frontend
/// must also serve HTML and identify the *exact installed build*. An arbitrary
/// old Next.js process on port 3000 cannot pass the version check.
fn validate_response(
    headers: &str,
    service: Service,
    expected_build: Option<&str>,
) -> Result<(), String> {
    let status = headers
        .lines()
        .next()
        .and_then(|line| {
            let mut parts = line.split_whitespace();
            let version = parts.next()?;
            if version != "HTTP/1.1" && version != "HTTP/1.0" {
                return None;
            }
            parts.next()?.parse::<u16>().ok()
        })
        .ok_or_else(|| "invalid local HTTP response".to_string())?;
    if status != 200 {
        return Err(format!("{} returned HTTP {status} (expected 200)", service.label()));
    }

    if service == Service::Frontend {
        let content_type = response_header(headers, "content-type").unwrap_or("");
        if !content_type.to_ascii_lowercase().starts_with("text/html") {
            return Err("frontend did not return an HTML page".to_string());
        }
        if let Some(version) = expected_build {
            let announced = response_header(headers, "x-locus-desktop-build");
            if announced != Some(version) {
                // The listener is untrusted; bound user-visible diagnostics.
                let observed: String = announced
                    .unwrap_or("no Locus build header")
                    .chars()
                    .take(96)
                    .collect();
                return Err(format!(
                    "frontend build mismatch: expected v{version}, received {observed}"
                ));
            }
        }
    }
    Ok(())
}

/// Probe an explicitly pinned loopback service. No DNS, redirects, remote host
/// input, or user-defined port is accepted. Read at most 16 KiB of headers.
pub(crate) fn probe(service: Service, expected_build: Option<&str>) -> Result<(), String> {
    let addr = SocketAddr::from(([127, 0, 0, 1], service.port()));
    let mut stream = TcpStream::connect_timeout(&addr, IO_TIMEOUT)
        .map_err(|e| format!("{} is unreachable: {e}", service.label()))?;
    stream
        .set_read_timeout(Some(IO_TIMEOUT))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(IO_TIMEOUT))
        .map_err(|e| e.to_string())?;
    let request = format!(
        "GET {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAccept: text/html, application/json\r\nConnection: close\r\n\r\n",
        service.path(),
        service.port()
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("{} health request failed: {e}", service.label()))?;
    let mut bytes = Vec::with_capacity(2048);
    let mut buf = [0_u8; 2048];
    loop {
        let count = stream
            .read(&mut buf)
            .map_err(|e| format!("{} health response failed: {e}", service.label()))?;
        if count == 0 {
            return Err(format!("{} ended its HTTP response before the headers", service.label()));
        }
        bytes.extend_from_slice(&buf[..count]);
        if let Some(end) = bytes.windows(4).position(|part| part == b"\r\n\r\n") {
            let text = std::str::from_utf8(&bytes[..end])
                .map_err(|_| format!("{} sent invalid HTTP headers", service.label()))?;
            return validate_response(text, service, expected_build);
        }
        if bytes.len() >= MAX_HEADERS {
            return Err(format!("{} sent oversized HTTP headers", service.label()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEALTHY_BACKEND: &str = "HTTP/1.1 200 OK\r\ncontent-type: application/json";
    const HEALTHY_FRONTEND: &str =
        "HTTP/1.1 200 OK\r\ncontent-type: text/html; charset=utf-8\r\nX-Locus-Desktop-Build: 0.10.4";

    #[test]
    fn backend_requires_real_200_not_just_open_port() {
        assert!(validate_response(HEALTHY_BACKEND, Service::Backend, None).is_ok());
        for response in [
            "HTTP/1.1 500 Internal Server Error\r\n",
            "HTTP/1.1 404 Not Found\r\n",
            "HTTP/1.1 302 Found\r\nLocation: /auth",
            "garbage",
        ] {
            assert!(validate_response(response, Service::Backend, None).is_err());
        }
    }

    #[test]
    fn frontend_requires_html_and_matching_build() {
        assert!(validate_response(HEALTHY_FRONTEND, Service::Frontend, Some("0.10.4")).is_ok());
        assert!(validate_response(HEALTHY_FRONTEND, Service::Frontend, Some("0.10.3")).is_err());
        assert!(validate_response(
            "HTTP/1.1 200 OK\r\ncontent-type: text/html",
            Service::Frontend, Some("0.10.4"),
        ).is_err());
        assert!(validate_response(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\nX-Locus-Desktop-Build: 0.10.4",
            Service::Frontend, Some("0.10.4"),
        ).is_err());
        assert!(validate_response(
            "HTTP/1.1 500 Error\r\ncontent-type: text/html\r\nX-Locus-Desktop-Build: 0.10.4",
            Service::Frontend, Some("0.10.4"),
        ).is_err());
    }

    #[test]
    fn debug_allows_missing_build_header_but_still_requires_valid_html() {
        assert!(validate_response(
            "HTTP/1.1 200 OK\r\ncontent-type: text/html",
            Service::Frontend, None,
        ).is_ok());
        assert!(validate_response(
            "HTTP/1.1 200 OK\r\ncontent-type: application/json",
            Service::Frontend, None,
        ).is_err());
    }

    #[test]
    fn fixed_ports_and_paths_cannot_be_redirected_by_caller() {
        assert_eq!(Service::Backend.port(), 8000);
        assert_eq!(Service::Frontend.port(), 3000);
        assert_eq!(Service::Frontend.path(), "/home");
        assert_eq!(Service::Backend.path(), "/healthz");
    }
}
