#requires -version 5
<#
.SYNOPSIS
  Build the Lattix xFrontier desktop installer locally on Windows (unsigned —
  fine for testing the install/first-run UX). Produces an .msi + .exe under
  apps/desktop-tauri/src-tauri/target/<triple>/release/bundle/.

.PREREQUISITES (install once)
  - Rust + Tauri CLI:   winget install Rustlang.Rustup ;  cargo install tauri-cli --version "^2"
  - Python 3.12 + PyInstaller:  pip install pyinstaller -e .
  - Node 20+ (on PATH)
  - Microsoft C++ Build Tools (MSVC) + WebView2 runtime (usually preinstalled on Win11)

.USAGE
  pwsh scripts/build-desktop.ps1
#>
$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot/..").Path
$tauri = Join-Path $root "apps/desktop-tauri/src-tauri"
$bin = Join-Path $tauri "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null

function Need($name) { if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { throw "Missing prerequisite on PATH: $name" } }
function CheckExit($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE) — see output above." } }
Need node; Need cargo

# Preflight: the backend sidecar needs Python 3.12 (3.13/3.14 lack wheels for some
# deps). Auto-resolve a 3.12 interpreter instead of forcing whatever `python` is on
# PATH — prefer the project's .venv-desktop (already has PyInstaller + deps), then
# the py -3.12 launcher target, then a 3.12 on PATH.
function Test-Py312($exe) {
  if (-not $exe -or -not (Test-Path $exe)) { return $false }
  $v = (& $exe -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null)
  return ($v -eq "3.12")
}
$Py = $null
$venvPy = Join-Path $root ".venv-desktop/Scripts/python.exe"
if (Test-Py312 $venvPy) { $Py = $venvPy }
if (-not $Py -and (Get-Command py -ErrorAction SilentlyContinue)) {
  $launcherPy = (& py -3.12 -c "import sys;print(sys.executable)" 2>$null)
  if ($LASTEXITCODE -eq 0 -and (Test-Py312 $launcherPy)) { $Py = $launcherPy.Trim() }
}
if (-not $Py) {
  $pathPy = (Get-Command python -ErrorAction SilentlyContinue).Source
  if (Test-Py312 $pathPy) { $Py = $pathPy }
}
if (-not $Py) {
  throw "No Python 3.12 found. Create one and re-run:  py -3.12 -m venv .venv-desktop ; .venv-desktop\Scripts\python -m pip install pyinstaller -e .   (or use the CI build: Actions -> desktop-release)."
}
Write-Host "== using Python 3.12: $Py =="

# Ensure PyInstaller is present in the chosen interpreter (install into it if not).
& $Py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "== installing PyInstaller + project deps into $Py =="
  & $Py -m pip install --upgrade pip pyinstaller -e $root
  CheckExit "pip install pyinstaller -e ."
}

# Resolve the Rust target triple (Tauri externalBin needs the suffix).
$triple = (& rustc -vV | Select-String "^host:").ToString().Split(":")[1].Trim()
Write-Host "== target triple: $triple =="

# 1) Backend sidecar (PyInstaller) -> bin/frontier-backend-<triple>.exe
Write-Host "== building backend sidecar (PyInstaller) =="
& $Py -m PyInstaller --noconfirm (Join-Path $root "packaging/frontier-backend.spec")
CheckExit "PyInstaller backend build"
Copy-Item (Join-Path $root "dist/frontier-backend.exe") (Join-Path $bin "frontier-backend-$triple.exe") -Force

# 2) Frontend standalone + vendored Node
Write-Host "== building frontend (Next.js standalone) =="
Push-Location (Join-Path $root "apps/frontend")
& npm ci; CheckExit "npm ci"
& npm run build; CheckExit "next build"
$dest = Join-Path $tauri "resources/frontend"
# Wipe the staged frontend first — Next content-hashes chunk filenames, so a plain
# copy-over leaves STALE old chunks behind (bundle bloat / mixed assets). Clean each build.
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item ".next/standalone/*" $dest -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $dest ".next/static") | Out-Null
Copy-Item ".next/static/*" (Join-Path $dest ".next/static") -Recurse -Force
if (Test-Path "public") { Copy-Item "public" (Join-Path $dest "public") -Recurse -Force }
Pop-Location
Copy-Item (Get-Command node).Source (Join-Path $bin "node.exe") -Force

# 3) Icons — ALWAYS regenerate from icon-source.png so logo updates take effect
#    (the generated icons/ dir is not committed; a stale set would otherwise stick).
Write-Host "== generating icons from icon-source.png =="
& cargo tauri icon (Join-Path $root "apps/desktop-tauri/icon-source.png")
CheckExit "cargo tauri icon"

# 4) Build the installer. The updater pubkey + GitHub Releases endpoint are baked
#    into tauri.conf.json (committed; public keys are safe), so the in-app
#    one-click updater is always configured. Signed update artifacts (latest.json
#    + .sig) are only emitted when TAURI_SIGNING_PRIVATE_KEY is set — that's done
#    in CI (desktop-release.yml). A keyless local build still produces a working
#    installer; it just doesn't sign an update manifest (you don't need one locally).
if ($env:TAURI_SIGNING_PRIVATE_KEY) {
  Write-Host "== cargo tauri build (signing update artifacts) =="
} else {
  Write-Host "== cargo tauri build (local build; update signing skipped — set `$env:TAURI_SIGNING_PRIVATE_KEY to sign) =="
}
Push-Location $tauri
try { & cargo tauri build; CheckExit "cargo tauri build" } finally { Pop-Location }

# cargo tauri build (no --target) writes to target/release/bundle; with a
# --target it's target/<triple>/release/bundle. Search both.
$installers = @(
  (Join-Path $tauri "target/release/bundle"),
  (Join-Path $tauri "target/$triple/release/bundle")
) | Where-Object { Test-Path $_ } |
  ForEach-Object { Get-ChildItem -Recurse $_ -Include *.msi, *.exe -ErrorAction SilentlyContinue }

if ($installers) {
  Write-Host "`n== DONE. Installers built: =="
  $installers | ForEach-Object { Write-Host "  $($_.FullName)" }
  Write-Host "`nSmartScreen will warn on the unsigned installer: click 'More info' -> 'Run anyway'."
} else {
  throw "Build finished but no .msi/.exe found under target/**/release/bundle."
}
