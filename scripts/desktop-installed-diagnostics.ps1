# LOCUS-475: read-only diagnostics for a blank installed Windows desktop.
# Does not stop processes, modify the installation, clear browser profiles, or
# read/print authentication cookies, user documents, application state or secrets.
# Run while the white Locus window is open:
#   powershell -NoProfile -File .\scripts\desktop-installed-diagnostics.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$install = Join-Path $env:LOCALAPPDATA "Lattix Locus"
$frontend = Join-Path $install "resources\frontend"
$static = Join-Path $frontend ".next\static"
$base = "http://127.0.0.1:3000"

Write-Output "Locus Windows installed frontend diagnostic"
Write-Output "Install exists: $(Test-Path -LiteralPath $install)"
Write-Output "Frontend exists: $(Test-Path -LiteralPath $frontend)"
Write-Output "Server exists: $(Test-Path -LiteralPath (Join-Path $frontend 'server.js'))"
Write-Output "Static exists: $(Test-Path -LiteralPath $static)"

$executables = @(
    "lattix-locus-desktop.exe",
    "locus-backend.exe",
    "locus-opa.exe",
    "bin\node.exe"
)
foreach ($name in $executables) {
    $file = Join-Path $install $name
    Write-Output ("Bundled {0}: {1}" -f $name, (Test-Path -LiteralPath $file))
}

if (Test-Path -LiteralPath $static) {
    $js = @(Get-ChildItem -LiteralPath $static -Recurse -File -Filter "*.js" -ErrorAction SilentlyContinue)
    $css = @(Get-ChildItem -LiteralPath $static -Recurse -File -Filter "*.css" -ErrorAction SilentlyContinue)
    Write-Output "Static JS count: $($js.Count); CSS count: $($css.Count)"
}
$buildFile = Join-Path $frontend ".next\BUILD_ID"
if (Test-Path -LiteralPath $buildFile) {
    $buildId = (Get-Content -LiteralPath $buildFile -Raw -ErrorAction SilentlyContinue).Trim()
    Write-Output ("Installed Next BUILD_ID: {0}" -f ($buildId -replace '[^a-zA-Z0-9_.-]',''))
}

foreach ($port in @(3000, 8000)) {
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) {
        Write-Output ("Port {0}: not listening" -f $port)
        continue
    }
    foreach ($connection in $connections) {
        $ownerPid = [int]$connection.OwningProcess
        $ownerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        if ($null -ne $ownerProcess) {
            Write-Output ("Port {0}: PID {1}; executable {2}; started {3}" -f $port, $ownerPid, $ownerProcess.ExecutablePath, $ownerProcess.CreationDate)
        } else {
            Write-Output ("Port {0}: PID {1}; process metadata unavailable" -f $port, $ownerPid)
        }
    }
}
$serverFile = Join-Path $frontend "server.js"
if (Test-Path -LiteralPath $serverFile) {
    Write-Output "Server.js modified: $((Get-Item -LiteralPath $serverFile).LastWriteTime)"
}

foreach ($url in @("http://127.0.0.1:8000/healthz", "$base/", "$base/home", "$base/api/auth/session")) {
    try {
        $res = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
        Write-Output "HTTP $($res.StatusCode): $url; Content-Type=$($res.Headers['Content-Type']); Desktop-Build=$($res.Headers['X-Locus-Desktop-Build'])"
    } catch {
        Write-Output "HTTP FAILED: $url; $($_.Exception.GetType().Name)"
    }
}

try {
    $home = Invoke-WebRequest -Uri "$base/home" -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
    $pattern = '/_next/static/[A-Za-z0-9._~%+/-]+?\.(?:js|css)(?:\?[A-Za-z0-9_.~%=&+-]*)?'
    $references = @([regex]::Matches([string]$home.Content, $pattern) |
        ForEach-Object { $_.Value -replace '&amp;', '&' } |
        Sort-Object -Unique)
    Write-Output "Referenced initial JS/CSS assets: $($references.Count)"
    $bad = 0
    foreach ($ref in $references) {
        $uriPath = ($ref -split '\?', 2)[0]
        $relative = $uriPath.Substring("/_next/static/".Length).Replace("/", [IO.Path]::DirectorySeparatorChar)
        $file = Join-Path $static $relative
        $exists = Test-Path -LiteralPath $file -PathType Leaf
        $status = "not tested"
        try {
            $res = Invoke-WebRequest -Uri "$base$ref" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            $status = [string]$res.StatusCode
        } catch {
            $status = $_.Exception.GetType().Name
        }
        if (-not $exists -or $status -ne "200") {
            $bad += 1
            Write-Output "ASSET FAILED: $uriPath; on_disk=$exists; http=$status"
        }
    }
    Write-Output "Missing/broken referenced assets: $bad / $($references.Count)"
    if ($references.Count -eq 0) {
        Write-Output "WARNING: page had no matching JS/CSS references; cannot confirm frontend health"
    }
} catch {
    Write-Output "ASSET SCAN UNAVAILABLE: $($_.Exception.GetType().Name)"
}

Write-Output "DIAGNOSTIC COMPLETE. No user data or application files were modified."
