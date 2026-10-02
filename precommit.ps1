$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$FrontendRoot = Join-Path $RepoRoot "apps\frontend"
$StepResults = [System.Collections.Generic.List[object]]::new()

Write-Host "Lattix xFrontier pre-commit checks"

function Get-PythonCommand {
  $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    return [pscustomobject]@{
      Executable = (Resolve-Path $venvPython).Path
      PrefixArgs = @()
    }
  }

  $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    return [pscustomobject]@{
      Executable = $pyLauncher.Source
      PrefixArgs = @("-3")
    }
  }

  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    return [pscustomobject]@{
      Executable = $python.Source
      PrefixArgs = @()
    }
  }

  throw "Python 3 is required but was not found in PATH."
}

function Get-OpaCommand {
  $localOpa = Join-Path $RepoRoot ".tools\opa\opa.exe"
  if (Test-Path $localOpa) {
    return (Resolve-Path $localOpa).Path
  }

  $opa = Get-Command opa -ErrorAction SilentlyContinue
  if ($opa) {
    return $opa.Source
  }

  return $null
}

function Get-HelmCommand {
  $repoHelmCandidates = @(
    (Join-Path $RepoRoot ".tools\helm\windows-amd64\helm.exe"),
    (Join-Path $RepoRoot ".tools\helm\helm.exe")
  )

  foreach ($candidate in $repoHelmCandidates) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  $helmExe = Get-Command helm.exe -ErrorAction SilentlyContinue
  if ($helmExe -and -not [string]::IsNullOrWhiteSpace($helmExe.Source)) {
    return $helmExe.Source
  }

  $helmFallback = Get-Command helm -ErrorAction SilentlyContinue
  if ($helmFallback -and -not [string]::IsNullOrWhiteSpace($helmFallback.Source)) {
    if ($helmFallback.Source -match '\.(exe|cmd|bat)$') {
      return $helmFallback.Source
    }
  }

  return $null
}

function Get-SyftCommand {
  $repoSyftCandidates = @(
    (Join-Path $RepoRoot ".tools\syft\syft.exe"),
    (Join-Path $RepoRoot ".tools\syft\windows-amd64\syft.exe")
  )

  foreach ($candidate in $repoSyftCandidates) {
    if (Test-Path $candidate) {
      return (Resolve-Path $candidate).Path
    }
  }

  $syftExe = Get-Command syft.exe -ErrorAction SilentlyContinue
  if ($syftExe -and -not [string]::IsNullOrWhiteSpace($syftExe.Source)) {
    return $syftExe.Source
  }

  $syftFallback = Get-Command syft -ErrorAction SilentlyContinue
  if ($syftFallback -and -not [string]::IsNullOrWhiteSpace($syftFallback.Source)) {
    if ($syftFallback.Source -match '\.(exe|cmd|bat)$') {
      return $syftFallback.Source
    }
  }

  return $null
}

function Add-StepResult {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [ValidateSet("PASS", "FAIL", "SKIP")]
    [string]$Status,
    [int]$ExitCode = 0,
    [string]$Detail = ""
  )

  $StepResults.Add([pscustomobject]@{
      Name = $Name
      Status = $Status
      ExitCode = $ExitCode
      Detail = $Detail
    }) | Out-Null
}

function Write-StepSummary {
  param(
    [switch]$Final
  )

  Write-Host ""
  Write-Host "Pre-commit status summary"
  foreach ($result in $StepResults) {
    $suffix = if ([string]::IsNullOrWhiteSpace($result.Detail)) { "" } else { " - $($result.Detail)" }
    Write-Host ("[{0}] {1}{2}" -f $result.Status, $result.Name, $suffix)
  }
  if ($Final) {
    Write-Host ""
  }
}

function Invoke-Python {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  & $Python.Executable @($Python.PrefixArgs + $Arguments)
}

function Invoke-Step {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [scriptblock]$Action,
    [string]$WorkingDirectory = $RepoRoot
  )

  Write-Host ("==> {0}" -f $Name)
  Push-Location $WorkingDirectory
  try {
    $global:LASTEXITCODE = 0
    & $Action
    $exitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    if ($exitCode -ne 0) {
      Add-StepResult -Name $Name -Status "FAIL" -ExitCode $exitCode -Detail ("exit code {0}" -f $exitCode)
      Write-StepSummary
      exit $exitCode
    }
    Add-StepResult -Name $Name -Status "PASS"
    Write-Host ("PASS: {0}" -f $Name)
  }
  catch {
    Add-StepResult -Name $Name -Status "FAIL" -ExitCode 1 -Detail $_.Exception.Message
    Write-StepSummary
    throw
  }
  finally {
    Pop-Location
  }
}

function Invoke-IfAvailable {
  param(
    [Parameter(Mandatory = $true)]
    [string]$CommandName,
    [Parameter(Mandatory = $true)]
    [string]$Description,
    [Parameter(Mandatory = $true)]
    [scriptblock]$Action,
    [string]$WorkingDirectory = $RepoRoot
  )

  if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
    Invoke-Step -Name $Description -Action $Action -WorkingDirectory $WorkingDirectory
  }
  else {
    $detail = "missing $CommandName"
    Write-Host ("SKIP: {0} ({1})" -f $Description, $detail)
    Add-StepResult -Name $Description -Status "SKIP" -Detail $detail
  }
}

$Python = Get-PythonCommand
$Opa = Get-OpaCommand
$Helm = Get-HelmCommand
$Syft = Get-SyftCommand
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Invoke-Step -Name "Install Python dependencies" -Action { Invoke-Python -Arguments @("-m", "pip", "install", "-e", ".[dev]") }
if (Get-Command npm -ErrorAction SilentlyContinue) {
  Invoke-Step -Name "Install frontend dependencies" -Action { npm ci } -WorkingDirectory $FrontendRoot
}
else {
  $npmDetail = "missing npm"
  Write-Host ("SKIP: Install frontend dependencies ({0})" -f $npmDetail)
  Add-StepResult -Name "Install frontend dependencies" -Status "SKIP" -Detail $npmDetail
}
Invoke-Step -Name "Python lint" -Action { Invoke-Python -Arguments @("-m", "ruff", "check", ".") }
Invoke-Step -Name "Python format check" -Action { Invoke-Python -Arguments @("-m", "ruff", "format", ".", "--check") }
Invoke-Step -Name "Python typecheck" -Action { Invoke-Python -Arguments @("-m", "mypy", "frontier_tooling/", "frontier_runtime/") }
Invoke-Step -Name "Python tests" -Action { Invoke-Python -Arguments @("-m", "pytest", "apps/backend/tests", "tests", "-v", "--cov=app", "--cov=frontier_runtime", "--cov-report=term-missing") }

if ($Opa) {
  Invoke-Step -Name "Policy tests" -Action { Invoke-Python -Arguments @("scripts/run_opa.py", "test", "policies/", "-v") }
}
else {
  $policyDetail = "OPA missing; install to .tools\\opa\\opa.exe or add 'opa' to PATH"
  Write-Host ("SKIP: Policy tests ({0})" -f $policyDetail)
  Add-StepResult -Name "Policy tests" -Status "SKIP" -Detail $policyDetail
}

if (Get-Command npm -ErrorAction SilentlyContinue) {
  Invoke-Step -Name "Frontend lint" -Action { npm run lint } -WorkingDirectory $FrontendRoot
  Invoke-Step -Name "Frontend tests" -Action { npm test } -WorkingDirectory $FrontendRoot
  Invoke-Step -Name "Frontend build" -Action { npm run build } -WorkingDirectory $FrontendRoot
}
else {
  $npmDetail = "missing npm"
  Write-Host ("SKIP: Frontend lint ({0})" -f $npmDetail)
  Add-StepResult -Name "Frontend lint" -Status "SKIP" -Detail $npmDetail
  Write-Host ("SKIP: Frontend tests ({0})" -f $npmDetail)
  Add-StepResult -Name "Frontend tests" -Status "SKIP" -Detail $npmDetail
  Write-Host ("SKIP: Frontend build ({0})" -f $npmDetail)
  Add-StepResult -Name "Frontend build" -Status "SKIP" -Detail $npmDetail
}

Invoke-IfAvailable -CommandName "docker" -Description "Compose config validation" -Action {
  docker compose config --quiet
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
  docker compose -f docker-compose.local.yml config --quiet
} -WorkingDirectory $RepoRoot

Invoke-IfAvailable -CommandName "semgrep" -Description "SAST via Semgrep" -Action { semgrep --config=auto --exclude .venv --exclude .next --exclude node_modules --exclude dist . }
Invoke-IfAvailable -CommandName "gitleaks" -Description "Secret scanning via Gitleaks" -Action { gitleaks detect --source . --no-git --redact --config .gitleaks.toml }
Invoke-IfAvailable -CommandName "trivy" -Description "SCA/config via Trivy" -Action { trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 --skip-dirs .venv,.next,node_modules,dist . }
if ($Syft) {
  Invoke-Step -Name "SBOM generation via Syft" -Action {
    $sbomDir = Join-Path $RepoRoot ".artifacts\sbom"
    if (-not (Test-Path $sbomDir)) {
      New-Item -ItemType Directory -Path $sbomDir -Force | Out-Null
    }
    & $Syft "dir:." "-o" "cyclonedx-json=$sbomDir\repository.cdx.json"
    if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
    }
  } -WorkingDirectory $RepoRoot
}
else {
  $sbomDetail = "missing syft.exe (install Syft to .tools\\syft\\syft.exe or add syft.exe to PATH)"
  Write-Host ("SKIP: SBOM generation via Syft ({0})" -f $sbomDetail)
  Add-StepResult -Name "SBOM generation via Syft" -Status "SKIP" -Detail $sbomDetail
}
if ($Helm) {
  Invoke-Step -Name "Helm chart validation" -Action {
    & $Helm lint ./helm/lattix-frontier
    if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
    }
    & $Helm template lattix ./helm/lattix-frontier -f helm/lattix-frontier/values-prod.yaml | Out-Null
  }
}
else {
  $helmDetail = "missing helm.exe (run .\\scripts\\frontier.ps1 install-helm or add helm.exe to PATH)"
  Write-Host ("SKIP: Helm chart validation ({0})" -f $helmDetail)
  Add-StepResult -Name "Helm chart validation" -Status "SKIP" -Detail $helmDetail
}

Write-StepSummary -Final
Write-Host "All pre-commit checks passed."
