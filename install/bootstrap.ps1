$ErrorActionPreference = 'Stop'

$TempRoot = if ([string]::IsNullOrWhiteSpace($env:TEMP)) { '.\tmp' } else { $env:TEMP }
$BootstrapDir = Join-Path $TempRoot 'frontier-install'
$InstallerUrl = 'https://raw.githubusercontent.com/LATTIX-IO/lattix-xfrontier/main/install/frontier-installer.py'
$LocalInstallerPath = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'frontier-installer.py' } else { '' }
$MinimumPythonMinor = 12

function Add-PathEntry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathEntry
    )

    if ([string]::IsNullOrWhiteSpace($PathEntry) -or -not (Test-Path $PathEntry)) {
        return
    }

    $entries = @($env:PATH -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($entries -contains $PathEntry) {
        return
    }

    $env:PATH = "$PathEntry;$env:PATH"
}

function Refresh-CommonPaths {
    Add-PathEntry -PathEntry (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Scripts')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\Scripts')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314')
    Add-PathEntry -PathEntry (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\Scripts')
}

function Test-PythonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [System.Management.Automation.CommandInfo]$Command,

        [switch]$UseLauncher
    )

    try {
        if ($UseLauncher) {
            & $Command.Source -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MinimumPythonMinor) else 1)" *> $null
        }
        else {
            & $Command.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, $MinimumPythonMinor) else 1)" *> $null
        }
        $commandExitCode = $LASTEXITCODE
        return $commandExitCode -eq 0
    }
    catch {
        return $false
    }
}

function Install-WithWinget {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageId
    )

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $Winget) {
        return $false
    }

    & $Winget.Source install --exact --id $PackageId --accept-package-agreements --accept-source-agreements
    return $LASTEXITCODE -eq 0
}

function Install-WithChocolatey {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageName
    )

    $Choco = Get-Command choco -ErrorAction SilentlyContinue
    if ($null -eq $Choco) {
        return $false
    }

    & $Choco.Source install $PackageName -y
    return $LASTEXITCODE -eq 0
}

function Resolve-SupportedPython {
    Refresh-CommonPaths

    $Python = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $Python -and (Test-PythonCommand -Command $Python -UseLauncher)) {
        return $Python
    }

    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $Python -and (Test-PythonCommand -Command $Python)) {
        return $Python
    }

    $Python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($null -ne $Python3 -and (Test-PythonCommand -Command $Python3)) {
        return $Python3
    }

    return $null
}

function Ensure-Python {
    $Resolved = Resolve-SupportedPython
    if ($null -ne $Resolved) {
        return $Resolved
    }

    Write-Host '==> Installing Python 3.12+ for bootstrap'
    if (-not (Install-WithWinget -PackageId 'Python.Python.3.12')) {
        if (-not (Install-WithWinget -PackageId 'Python.Python.3.13')) {
            if (-not (Install-WithChocolatey -PackageName 'python')) {
                throw "Python 3.12+ is required but could not be installed automatically. Install Python 3.12 or newer, reopen PowerShell, and retry."
            }
        }
    }

    Refresh-CommonPaths
    $Resolved = Resolve-SupportedPython
    if ($null -eq $Resolved) {
        throw "Python 3.12+ is required but a supported interpreter was not found after automatic installation. Reopen PowerShell and retry."
    }
    return $Resolved
}

function Wait-ForDocker {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $Docker = Get-Command docker -ErrorAction SilentlyContinue
        if ($null -ne $Docker) {
            & $Docker.Source info *> $null
            if ($LASTEXITCODE -eq 0) {
                return
            }
        }
        Start-Sleep -Seconds 2
    }

    throw 'Docker is installed but the daemon is not ready. Start Docker Desktop and rerun the bootstrap.'
}

function Ensure-Docker {
    Refresh-CommonPaths
    $Docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $Docker) {
        Write-Host '==> Installing Docker Desktop for bootstrap'
        if (-not (Install-WithWinget -PackageId 'Docker.DockerDesktop')) {
            if (-not (Install-WithChocolatey -PackageName 'docker-desktop')) {
                throw 'Docker Desktop is required but could not be installed automatically. Install Docker Desktop, start it, and retry.'
            }
        }
        Refresh-CommonPaths
        $Docker = Get-Command docker -ErrorAction SilentlyContinue
        if ($null -eq $Docker) {
            throw 'Docker Desktop installation completed, but the docker CLI was not found on PATH. Reopen PowerShell and retry.'
        }
    }

    & $Docker.Source info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host '==> Starting Docker Desktop'
        $DockerDesktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (Test-Path $DockerDesktopExe) {
            Start-Process -FilePath $DockerDesktopExe | Out-Null
        }
        Wait-ForDocker
    }
}

function Stop-Bootstrap {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [int]$ExitCode = 1
    )

    $global:LASTEXITCODE = $ExitCode
    throw $Message
}

Write-Host '==> Lattix xFrontier bootstrap'
Write-Host '==> Preparing installer workspace'
New-Item -ItemType Directory -Force -Path $BootstrapDir | Out-Null

Refresh-CommonPaths
$Python = Ensure-Python
Ensure-Docker

$InstallerPath = Join-Path $BootstrapDir 'frontier-installer.py'
if ($LocalInstallerPath -and (Test-Path $LocalInstallerPath)) {
    Write-Host '==> Using local checkout installer'
    $InstallerPath = $LocalInstallerPath
}
else {
    Write-Host '==> Downloading installer'
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath
}

Write-Host '==> Launching interactive installer'
if ([string]::IsNullOrWhiteSpace($env:FRONTIER_INSTALLER_OUTPUT)) {
    try {
        if (-not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected) {
            $env:FRONTIER_INSTALLER_OUTPUT = 'tui'
        }
    }
    catch {
    }
}
if ($Python.Name -eq 'py.exe' -or $Python.Name -eq 'py') {
    & $Python.Source -3 $InstallerPath
} else {
    & $Python.Source $InstallerPath
}
$installerExitCode = $LASTEXITCODE
if ($installerExitCode -ne 0) {
    Stop-Bootstrap -Message "Installer failed with exit code $installerExitCode. The current terminal session was left open so you can inspect the error and retry." -ExitCode $installerExitCode
}
