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

## October 25 owner asset evidence (installed v0.10.4)

Confirmed from the owner's Windows PowerShell and Edge tests:

- The Next.js frontend at `http://127.0.0.1:3000/home` is completely white
  in Edge **and** the installed Tauri window. It responds HTTP 200,
  `text/html; charset=utf-8`, with 12,011 HTML characters.
- Installed `%LOCALAPPDATA%\\Lattix Locus\\resources\\frontend\\server.js`
  and `.next\\static` exist; the static tree contains 52 JavaScript and 3 CSS files.
- HTML references 14 JS/CSS assets. **6 of the first 12 HTTP requests returned
  404**: one CSS chunk (`3vgvgj9_bqlsc.css`) and five JS chunks
  (`38yxu_eyyt1fs.js`, `0fo546wxpcpmm.js`, `1a8fdlddd8icn.js`,
  `2e0etuy-dhsrf.js`, `2rrrnh9yjjav3.js`).
- PID 99020 on port 3000 is bundled `bin\\node.EXE`; PID 80912
  on port 8000 is bundled `locus-backend.exe`. The installed shell path is
  `%LOCALAPPDATA%\\Lattix Locus\\lattix-locus-desktop.exe`.

**Confirmed failure class:** the installed Node server does not serve all
required static assets, which can prevent React hydration. **Not yet proven:**
whether those files are absent from disk (installer/build staging defect),
or a previous Node server is still running against a newer installed tree
(stale process/build mismatch), or an unexpected route is used.

The older smoke probe validated only the alphabetically first arbitrary
JS file. The revised `scripts/desktop_frontend_smoke.py` now tests *every*
page-referenced JS/CSS file against both the packaged tree and HTTP response.
The three CI installer recipes now clean their staged Next.js resource tree
before copying, and a focused regression suite covers 404, missing files,
bad MIME types and empty references. No passing exact-head results or owner
Windows repair can be claimed until those validations complete.

### Read-only local discriminator

With the white Locus app **still running**, use PowerShell:

```powershell
$install = Join-Path $env:LOCALAPPDATA 'Lattix Locus'
$static = Join-Path $install 'resources\\frontend\\.next\\static\\chunks'
$missing = @(
  '3vgvgj9_bqlsc.css',
  '38yxu_eyyt1fs.js',
  '0fo546wxpcpmm.js',
  '1a8fdlddd8icn.js',
  '2e0etuy-dhsrf.js',
  '2rrrnh9yjjav3.js'
)
$missing | ForEach-Object {
  [pscustomobject]@{
    Chunk = $_
    Exists = Test-Path (Join-Path $static $_)
  }
} | Format-Table -AutoSize

$listener = Get-NetTCPConnection -LocalPort 3000 -State Listen |
  Select-Object -First 1
$nodeInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
$serverInfo = Get-Item (Join-Path $install 'resources\\frontend\\server.js')
[pscustomobject]@{
  NodePID = $nodeInfo.ProcessId
  NodeParentPID = $nodeInfo.ParentProcessId
  NodeStarted = $nodeInfo.CreationDate
  ServerJSModified = $serverInfo.LastWriteTime
} | Format-List
```

If files are missing from disk, investigate NSIS resource inclusion and
unified staging; if files exist but return 404, investigate the serving root
and running-process identity. If Node predates a newer installed
`server.js`, a stale process/build mismatch is strongly indicated. Save
work, use tray **Quit**, verify ports are released before relaunching; do
not manually kill active worker processes or uninstall/delete app data.

## Owner follow-up: missing files and restart (2026-10-25)

All six 404 assets were confirmed **absent from their installed expected
locations** under `resources/frontend/.next/static/chunks`; the white screen
remained after a tray Quit, verification that ports 3000/8000 were no longer
listening, and an explicit restart. Node was started at 12:08:15 PM, while
`server.js` had been modified at 11:01:22 AM. This substantially weakens a
stale-before-upgrade Node explanation, but does not yet prove which build or
installer step omitted the files. The owner data contains no evidence that
WebView2 is the unique cause: Edge reproduced the white page as well.

**One bounded, read-only discriminator** that can establish a nested packaging
path, without needing another reinstall or sharing app secrets:

```powershell
$base = Join-Path $env:LOCALAPPDATA "Lattix Locus"
$names = @("3vgvgj9_bqlsc.css", "38yxu_eyyt1fs.js",
  "0fo546wxpcpmm.js", "1a8fdlddd8icn.js",
  "2e0etuy-dhsrf.js", "2rrrnh9yjjav3.js")
Get-ChildItem (Join-Path $base "resources/frontend") -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $names -contains $_.Name } |
  Select-Object Name, FullName
```

If these files appear under `.next/static/static/chunks`, staging likely
created an accidental nested directory. If no match exists, inspect the NSIS
archive (instead of speculatively copying files into the live install). If
they appear elsewhere, retain those paths as evidence for release packaging
triage.

## Installer-level regression gate

`scripts/desktop_installer_smoke.py` extracts the actual NSIS build to a
temporary directory using 7-Zip, verifies every `.next/static` file against
the independently smoke-checked stage by SHA-256 and relative name, verifies
the bundled Node runtime, and launches the extracted standalone server to
check all route-referenced JS/CSS HTTP assets. This extends the existing
staged-only browser smoke to cover the gap observed on the owner's v0.10.4
installation. Both main-channel release workflows must pass this check
**before publishing**. A separate secretless PR workflow builds an ephemeral
unsigned NSIS with never-executed placeholder external binaries solely for
resource-inspection QA. It does not publish or sign any candidate.

Release qualification still requires a genuine new Windows installation and
operator verification; the premerge smoke cannot validate actual migration of
the owner's local state.
