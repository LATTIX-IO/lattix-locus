param(
    [Parameter(Position = 0)]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    if ($Args.Length -eq 1) {
        & $Args[0]
    }
    else {
        & $Args[0] $Args[1..($Args.Length - 1)]
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $($Args -join ' ')"
    }
}

function Get-PythonCommand {
    $venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return (Resolve-Path $venvPython).Path
    }
    return "python"
}

function Get-OpaCommand {
    $repoRoot = Get-RepoRoot
    $localOpa = Join-Path $repoRoot ".tools\opa\opa.exe"
    if (Test-Path $localOpa) {
        return (Resolve-Path $localOpa).Path
    }
    $pathOpa = Get-Command opa -ErrorAction SilentlyContinue
    if ($pathOpa) {
        return $pathOpa.Source
    }
    throw "OPA was not found. Install it to .tools\opa\opa.exe or add 'opa' to PATH."
}

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-InstallerEnvPath {
    param(
        [switch]$LocalProfile
    )

    $repoRoot = Get-RepoRoot
    $installerDir = Join-Path $repoRoot ".installer"
    if (-not (Test-Path $installerDir)) {
        New-Item -ItemType Directory -Path $installerDir | Out-Null
    }
    if ($LocalProfile) {
        return (Join-Path $installerDir "local-lightweight.env")
    }
    return (Join-Path $installerDir "local-secure.env")
}

function Get-EnvMapFromFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $map = [ordered]@{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($line in Get-Content -Path $Path) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) {
            continue
        }

        $parts = $line -split '=', 2
        if ($parts.Length -eq 2) {
            $map[$parts[0]] = $parts[1]
        }
    }

    return $map
}

function New-RandomSecret {
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48)
    return [Convert]::ToBase64String($bytes).TrimEnd('=')
}

function Normalize-A2AAudience {
    param(
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -eq "agents") {
        return "locus-runtime"
    }

    return $Value
}

function Get-HelmCommand {
    $repoRoot = Get-RepoRoot
    foreach ($candidate in @(
        (Join-Path $repoRoot ".tools\helm\windows-amd64\helm.exe"),
        (Join-Path $repoRoot ".tools\helm\helm.exe")
    )) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    $pathHelm = Get-Command helm.exe -ErrorAction SilentlyContinue
    if ($pathHelm -and -not [string]::IsNullOrWhiteSpace($pathHelm.Source)) {
        return $pathHelm.Source
    }

    $fallback = Get-Command helm -ErrorAction SilentlyContinue
    if ($fallback -and -not [string]::IsNullOrWhiteSpace($fallback.Source)) {
        $resolved = $fallback.Source
        if ($resolved -match '\.(exe|cmd|bat)$') {
            return $resolved
        }
    }

    throw "A real Helm executable was not found. Install Helm with .\\scripts\\locus.ps1 install-helm or add helm.exe to PATH; Windows may be resolving a non-executable shim named 'helm'."
}

function Ensure-ComposeEnvFile {
    param(
        [switch]$LocalProfile
    )

    $repoRoot = Get-RepoRoot
    $envExamplePath = Join-Path $repoRoot ".env.example"
    $envPath = Join-Path $repoRoot ".env"
    $installerEnvPath = Get-InstallerEnvPath -LocalProfile:$LocalProfile

    $envMap = [ordered]@{}
    foreach ($sourcePath in @($envExamplePath, $envPath, $installerEnvPath)) {
        $sourceMap = Get-EnvMapFromFile -Path $sourcePath
        foreach ($key in $sourceMap.Keys) {
            $envMap[$key] = $sourceMap[$key]
        }
    }

    if (-not $envMap.Contains("A2A_JWT_SECRET") -or [string]::IsNullOrWhiteSpace($envMap["A2A_JWT_SECRET"])) {
        $envMap["A2A_JWT_SECRET"] = New-RandomSecret
        if ($LocalProfile) {
            Write-Host "Generated local A2A_JWT_SECRET and stored it in .installer\local-lightweight.env"
        }
        else {
            Write-Host "Generated local A2A_JWT_SECRET and stored it in .installer\local-secure.env"
        }
    }

    if (-not $envMap.Contains("A2A_JWT_ALG") -or [string]::IsNullOrWhiteSpace($envMap["A2A_JWT_ALG"])) {
        $envMap["A2A_JWT_ALG"] = "HS256"
    }
    if (-not $envMap.Contains("A2A_JWT_ISS") -or [string]::IsNullOrWhiteSpace($envMap["A2A_JWT_ISS"])) {
        $envMap["A2A_JWT_ISS"] = "lattix-locus"
    }
    if (-not $envMap.Contains("A2A_JWT_AUD") -or [string]::IsNullOrWhiteSpace($envMap["A2A_JWT_AUD"])) {
        $envMap["A2A_JWT_AUD"] = "locus-runtime"
    }
    else {
        $envMap["A2A_JWT_AUD"] = Normalize-A2AAudience -Value $envMap["A2A_JWT_AUD"]
    }
    if (-not $envMap.Contains("A2A_TRUSTED_SUBJECTS") -or [string]::IsNullOrWhiteSpace($envMap["A2A_TRUSTED_SUBJECTS"])) {
        $envMap["A2A_TRUSTED_SUBJECTS"] = "backend,research,code,review,coordinator"
    }
    if (-not $envMap.Contains("LOCAL_STACK_HOST") -or [string]::IsNullOrWhiteSpace($envMap["LOCAL_STACK_HOST"])) {
        $envMap["LOCAL_STACK_HOST"] = "locus.local"
    }

    if ($LocalProfile) {
        $envMap["LOCUS_RUNTIME_PROFILE"] = "local-lightweight"
        $envMap["NEXT_PUBLIC_API_BASE_URL"] = "http://localhost:8000"
        $envMap["FRONTEND_ORIGIN"] = "http://localhost:3000"
        $envMap["LOCUS_LOCAL_API_BASE_URL"] = "http://localhost:8000"
    }
    else {
        $envMap["LOCUS_RUNTIME_PROFILE"] = "local-secure"
        $envMap["NEXT_PUBLIC_API_BASE_URL"] = "/api"
        $envMap["FRONTEND_ORIGIN"] = "http://$($envMap['LOCAL_STACK_HOST'])"
        $gatewayBindHost = if (-not $envMap.Contains("LOCAL_GATEWAY_BIND_HOST") -or [string]::IsNullOrWhiteSpace($envMap["LOCAL_GATEWAY_BIND_HOST"]) -or $envMap["LOCAL_GATEWAY_BIND_HOST"] -eq "0.0.0.0") {
            "127.0.0.1"
        }
        else {
            $envMap["LOCAL_GATEWAY_BIND_HOST"]
        }
        $gatewayPort = if (-not $envMap.Contains("LOCAL_GATEWAY_HTTP_PORT") -or [string]::IsNullOrWhiteSpace($envMap["LOCAL_GATEWAY_HTTP_PORT"])) {
            "80"
        }
        else {
            $envMap["LOCAL_GATEWAY_HTTP_PORT"]
        }
        $defaultSecureApiBase = if ($gatewayPort -eq "80") {
            "http://$gatewayBindHost/api"
        }
        else {
            "http://$gatewayBindHost`:$gatewayPort/api"
        }
        if (-not $envMap.Contains("LOCUS_LOCAL_API_BASE_URL") -or [string]::IsNullOrWhiteSpace($envMap["LOCUS_LOCAL_API_BASE_URL"]) -or $envMap["LOCUS_LOCAL_API_BASE_URL"] -eq "http://localhost:8000") {
            $envMap["LOCUS_LOCAL_API_BASE_URL"] = $defaultSecureApiBase
        }
    }

    $lines = foreach ($key in $envMap.Keys) {
        "$key=$($envMap[$key])"
    }
    Set-Content -Path $installerEnvPath -Value ($lines -join "`n") -Encoding utf8
    return $installerEnvPath
}

function Get-FullComposeCommandPrefix {
    $installerEnvPath = Get-InstallerEnvPath
    if (Test-Path $installerEnvPath) {
        return @("docker", "compose", "--env-file", $installerEnvPath)
    }
    return @("docker", "compose")
}

function Get-ComposeCommandPrefix {
    $installerEnvPath = Get-InstallerEnvPath -LocalProfile
    if (Test-Path $installerEnvPath) {
        return @("docker", "compose", "--env-file", $installerEnvPath, "-f", "docker-compose.local.yml")
    }
    return @("docker", "compose", "-f", "docker-compose.local.yml")
}

function Assert-CommandAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        return $false
    }

    & docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Get-DockerDesktopExecutable {
    $candidates = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Start-DockerDesktop {
    $dockerDesktop = Get-DockerDesktopExecutable
    if (-not $dockerDesktop) {
        throw "Docker Desktop is not running, and the Docker Desktop executable could not be found automatically."
    }

    Write-Host "Docker Desktop is installed but not running. Launching it now..."
    Start-Process -FilePath $dockerDesktop | Out-Null
}

function Wait-ForDockerReady {
    param(
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-DockerReady) {
            return
        }

        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "Docker Desktop started but the Docker daemon did not become ready within $TimeoutSeconds seconds."
}

function Assert-DockerReady {
    Assert-CommandAvailable -Name "docker"
    if (Test-DockerReady) {
        return
    }

    if ($IsWindows) {
        Start-DockerDesktop
        Wait-ForDockerReady
        return
    }

    throw "Docker is not running or not reachable. Start Docker, wait for it to initialize, then retry."
}

function Show-Help {
    Write-Host "Lattix Locus Windows helper"
    Write-Host ""
    Write-Host "Usage: .\scripts\locus.ps1 <command>"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  bootstrap   Install editable package + dev dependencies"
    Write-Host "  up          Start docker compose stack"
    Write-Host "  down        Stop docker compose stack"
    Write-Host "  update      Refresh the local install in place without deleting workflows or settings"
    Write-Host "  remove      Tear down local install and delete installer-managed env files"
    Write-Host "  local-up    Start the lightweight local-first stack"
    Write-Host "  local-down  Stop the lightweight local-first stack"
    Write-Host "  stack-up    Start the full platform stack"
    Write-Host "  stack-down  Stop the full platform stack"
    Write-Host "  start-docker Launch Docker Desktop and wait for readiness"
    Write-Host "  test        Run pytest"
    Write-Host "  lint        Run ruff check/format"
    Write-Host "  typecheck   Run mypy"
    Write-Host "  helm-validate Run Helm lint + template validation for the chart"
    Write-Host "  install-helm Install Helm via direct download into .tools\helm"
    Write-Host "  install-opa Install repo-local OPA binary"
    Write-Host "  policy-test Run opa tests"
    Write-Host "  health      Query the configured API /healthz endpoint"
    Write-Host "  ps          Show docker compose status"
    Write-Host "  logs        Show docker compose logs"
    Write-Host "  smoke       Alias for health"
    Write-Host ""
    Write-Host "Tip: You can also use the installed CLI directly with 'lattix up'."
}

function Get-ConfiguredApiBaseUrl {
    $secureEnvPath = Get-InstallerEnvPath
    $lightweightEnvPath = Get-InstallerEnvPath -LocalProfile

    if ((Test-Path $secureEnvPath) -or -not (Test-Path $lightweightEnvPath)) {
        $envPath = Ensure-ComposeEnvFile
    }
    else {
        $envPath = Ensure-ComposeEnvFile -LocalProfile
    }

    $envMap = Get-EnvMapFromFile -Path $envPath
    $configured = if ($envMap.Contains("LOCUS_LOCAL_API_BASE_URL")) {
        [string]$envMap["LOCUS_LOCAL_API_BASE_URL"]
    }
    else {
        ""
    }
    return $configured.TrimEnd('/')
}

function Get-ConfiguredApiHostHeader {
    $secureEnvPath = Get-InstallerEnvPath
    if (-not (Test-Path $secureEnvPath)) {
        return ""
    }

    $envPath = Ensure-ComposeEnvFile
    $envMap = Get-EnvMapFromFile -Path $envPath
    if ($envMap.Contains("LOCAL_STACK_HOST")) {
        return ([string]$envMap["LOCAL_STACK_HOST"]).Trim()
    }
    return ""
}

$python = Get-PythonCommand

switch ($Command.ToLowerInvariant()) {
    "bootstrap" {
        Invoke-ExternalCommand @($python, "-m", "pip", "install", "-e", ".[dev]")
    }
    "up" {
        Assert-DockerReady
        $composeEnvFile = Ensure-ComposeEnvFile
        Invoke-ExternalCommand @("docker", "compose", "--env-file", $composeEnvFile, "up", "-d")
    }
    "local-up" {
        Assert-DockerReady
        $composeEnvFile = Ensure-ComposeEnvFile -LocalProfile
        Invoke-ExternalCommand @("docker", "compose", "--env-file", $composeEnvFile, "-f", "docker-compose.local.yml", "up", "-d")
        Write-Host "Lightweight local stack running. Frontend: http://localhost:3000 ; API health: http://localhost:8000/healthz"
    }
    "stack-up" {
        Assert-DockerReady
        $composeEnvFile = Ensure-ComposeEnvFile
        Invoke-ExternalCommand @("docker", "compose", "--env-file", $composeEnvFile, "up", "-d")
    }
    "down" {
        Assert-DockerReady
        $composePrefix = Get-FullComposeCommandPrefix
        Invoke-ExternalCommand ($composePrefix + @("down", "-v"))
    }
    "update" {
        Invoke-ExternalCommand @($python, "-m", "locus_tooling.cli", "update")
    }
    "remove" {
        $secureEnvPath = Get-InstallerEnvPath
        if (Test-Path $secureEnvPath) {
            try {
                Assert-DockerReady
                Invoke-ExternalCommand @("docker", "compose", "--env-file", $secureEnvPath, "down", "-v", "--remove-orphans")
            }
            catch {
                Write-Warning "Could not fully tear down the secure stack automatically: $($_.Exception.Message)"
            }
        }

        $lightweightEnvPath = Get-InstallerEnvPath -LocalProfile
        if (Test-Path $lightweightEnvPath) {
            try {
                Assert-DockerReady
                Invoke-ExternalCommand @("docker", "compose", "--env-file", $lightweightEnvPath, "-f", "docker-compose.local.yml", "down", "-v", "--remove-orphans")
            }
            catch {
                Write-Warning "Could not fully tear down the lightweight stack automatically: $($_.Exception.Message)"
            }
        }

        foreach ($installerEnvPath in @($secureEnvPath, $lightweightEnvPath)) {
            if (Test-Path $installerEnvPath) {
                Remove-Item -Force $installerEnvPath
            }
        }

        Write-Host "Removed installer-managed local stack state. Your repo checkout and .env file were left in place."
    }
    "stack-down" {
        Assert-DockerReady
        $composePrefix = Get-FullComposeCommandPrefix
        Invoke-ExternalCommand ($composePrefix + @("down", "-v"))
    }
    "local-down" {
        Assert-DockerReady
        $composePrefix = Get-ComposeCommandPrefix
        Invoke-ExternalCommand ($composePrefix + @("down", "-v"))
    }
    "start-docker" {
        Assert-DockerReady
        Write-Host "Docker Desktop is ready."
    }
    "test" {
        Push-Location (Join-Path (Get-RepoRoot) "apps\backend")
        try {
            Invoke-ExternalCommand @($python, "-m", "pytest", "tests/", "-v", "--cov=app", "--cov-report=term-missing")
        }
        finally {
            Pop-Location
        }
    }
    "lint" {
        Invoke-ExternalCommand @($python, "-m", "ruff", "check", ".", "--fix")
        Invoke-ExternalCommand @($python, "-m", "ruff", "format", ".")
    }
    "typecheck" {
        Invoke-ExternalCommand @($python, "-m", "mypy", "locus_tooling/")
    }
    "install-helm" {
        $repoRoot = Get-RepoRoot
        $helmDir = Join-Path $repoRoot ".tools\helm"
        if (-not (Test-Path $helmDir)) {
            New-Item -ItemType Directory -Force -Path $helmDir | Out-Null
        }
        $zipPath = Join-Path $helmDir "helm.zip"
        $extractDir = Join-Path $helmDir "windows-amd64"
        Invoke-WebRequest -Uri "https://get.helm.sh/helm-v3.17.3-windows-amd64.zip" -OutFile $zipPath
        Expand-Archive -LiteralPath $zipPath -DestinationPath $helmDir -Force
        Invoke-ExternalCommand @((Join-Path $extractDir "helm.exe"), "version")
    }
    "helm-validate" {
        $helm = Get-HelmCommand
        $repoRoot = Get-RepoRoot
        Invoke-ExternalCommand @($helm, "lint", (Join-Path $repoRoot "helm\lattix-locus"))
        Invoke-ExternalCommand @($helm, "template", "lattix", (Join-Path $repoRoot "helm\lattix-locus"), "-f", (Join-Path $repoRoot "helm\lattix-locus\values-prod.yaml"))
    }
    "install-opa" {
        $repoRoot = Get-RepoRoot
        $opaDir = Join-Path $repoRoot ".tools\opa"
        if (-not (Test-Path $opaDir)) {
            New-Item -ItemType Directory -Force -Path $opaDir | Out-Null
        }
        $opaPath = Join-Path $opaDir "opa.exe"
        Invoke-WebRequest -Uri "https://openpolicyagent.org/downloads/v0.68.0/opa_windows_amd64.exe" -OutFile $opaPath
        Invoke-ExternalCommand @($opaPath, "version")
    }
    "policy-test" {
        $opa = Get-OpaCommand
        Invoke-ExternalCommand @($opa, "test", "policies/", "-v")
    }
    "health" {
        $apiBaseUrl = Get-ConfiguredApiBaseUrl
        $apiHostHeader = Get-ConfiguredApiHostHeader
        Invoke-ExternalCommand @($python, "-c", "import urllib.request; req=urllib.request.Request('$apiBaseUrl/healthz', headers={'Host': '$apiHostHeader'} if '$apiHostHeader' else {}); print(urllib.request.urlopen(req, timeout=5).read().decode())")
    }
    "ps" {
        Assert-DockerReady
        $composePrefix = Get-FullComposeCommandPrefix
        Invoke-ExternalCommand ($composePrefix + @("ps"))
    }
    "logs" {
        Assert-DockerReady
        $composePrefix = Get-FullComposeCommandPrefix
        Invoke-ExternalCommand ($composePrefix + @("logs", "--tail=200"))
    }
    "smoke" {
        $apiBaseUrl = Get-ConfiguredApiBaseUrl
        $apiHostHeader = Get-ConfiguredApiHostHeader
        Invoke-ExternalCommand @($python, "-c", "import urllib.request; req=urllib.request.Request('$apiBaseUrl/healthz', headers={'Host': '$apiHostHeader'} if '$apiHostHeader' else {}); print(urllib.request.urlopen(req, timeout=5).read().decode())")
    }
    "help" {
        Show-Help
    }
    default {
        throw "Unknown command '$Command'. Run .\scripts\locus.ps1 help"
    }
}