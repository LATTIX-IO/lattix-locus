# LOCUS-475 — Windows installed UI blank-screen incident and UAT

**Incident, October 25, 2026.** Owner manually upgraded from Dev v0.4.0 to
official Dev v0.10.3 (Windows NSIS). The Tauri window opens with a white
content area and no visible UI, blocking owner acceptance.

**Do not infer the exact root cause from the screenshot.** The bundled loading
page is dark, so a fully white webview suggests the shell attempted navigation
to an unrendered frontend. Prior native startup accepted any open TCP listener
on ports 8000 and 3000, including a crashed/incorrect HTTP server. A white
page can also result from failed JavaScript hydration, unavailable static
chunks, stale WebView2 storage or a WebView2 runtime problem. Distinguish these
with the operator's outputs below before claiming a fix is verified.

## Non-destructive local triage

Run PowerShell while the blank Locus window remains open:

```powershell
$urls = @(
  'http://127.0.0.1:8000/healthz',
  'http://127.0.0.1:3000/',
  'http://127.0.0.1:3000/home'
)
foreach ($url in $urls) {
  try {
    $result = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
    "$url -> HTTP $($result.StatusCode)"
  } catch {
    "$url -> ERROR: $($_.Exception.Message)"
  }
}
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in @(3000, 8000) } |
  Select-Object LocalAddress, LocalPort, OwningProcess
```

Then open `http://127.0.0.1:3000/home` in Edge. A working Edge page
but blank Tauri window isolates the problem to the WebView/window profile;
a broken Edge page indicates the frontend process/content.

If a conflicting listener is identified, save work and quit Locus **from the
system tray**. Inspect its owning executable before terminating any process.
Do not delete `%APPDATA%\com.lattix.locus`, `%LOCALAPPDATA%\Lattix`, or
the per-user `locus` data root. Never uninstall/remove agent state as a
diagnostic step. Limit diagnostic captures to HTTP status codes and process
name/PID; do not paste auth tokens, local secrets or personal data.

## Repair branch

The desktop shell now requires a real HTTP 200 `/healthz`, a 200 HTML `/home`
and `X-Locus-Desktop-Build` matching Tauri's exact package version. A wrong
listener, 404/500 or a stale installed version cannot force a navigation to
an arbitrary white webview. Instead the bundled dark loading page remains
visible with a bounded explanation. The Next root has a standalone error
boundary if rendering throws.

Both `desktop-dev` and `desktop-release` stamp the validated version into
the Next frontend and run a real Node standalone smoke test on the **packaged
resource tree**. That smoke test validates the HTML page, build header and one
bundled JavaScript chunk before allowing an installer build.

This is a containment and diagnostic improvement, **not a claim that the
specific user's white screen has been reproduced or repaired**. The next
on-device UAT still must verify the v0.4.0 migration and both localhost routes,
that the actual app window renders, that memory/settings survived and that
front-end errors leave a visible diagnostic.

## Release gate

Merge only after exact-head frontend/backend CI, code review and applicable
security checks. The trusted main-only `desktop-dev` Windows build must pass
its standalone smoke and Rust/Tauri packaging; verify the resulting release
before recommending another installer to the owner. Do not trigger the
credential-bearing desktop release workflow on an untrusted PR branch, change
signing keys or publish an unmerged PR to the automatic Dev channel.
