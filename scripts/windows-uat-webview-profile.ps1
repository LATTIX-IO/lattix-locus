# LOCUS-475: safe, isolated desktop WebView2 profile experiment.
# Launches only if the prior Tauri instance has fully exited AND its backend/
# frontend ports have been released. Does not terminate processes, delete
# profiles, modify installed binaries or reset user data.
#
# First: use the Locus system tray -> Quit, then run:
#   pwsh -NoProfile -File .\scripts\windows-uat-webview-profile.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$exePath = Join-Path $env:LOCALAPPDATA "Lattix Locus\lattix-locus-desktop.exe"

try {
    if (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
        Write-Error "Desktop executable not found at the installed location."
        return
    }

    $appProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.Name -eq "lattix-locus-desktop.exe" -or
            ($_.ExecutablePath -and $_.ExecutablePath -eq $exePath)
        })
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @(3000, 8000) })

    if ($appProcesses.Count -gt 0 -or $listeners.Count -gt 0) {
        Write-Warning "NOT LAUNCHED: existing Locus process or port listener still active."
        if ($appProcesses.Count -gt 0) {
            $appProcesses |
                Select-Object ProcessId, Name, ExecutablePath |
                Format-Table -AutoSize
        }
        if ($listeners.Count -gt 0) {
            $listeners |
                Select-Object LocalPort, OwningProcess |
                Format-Table -AutoSize
        }
        Write-Output "Quit the desktop through its system-tray Quit command, save any active tasks, then retry. No processes were stopped."
        return
    }

    $profileFolder = Join-Path $env:TEMP (
        "Locus-WebView2-Test-" + [guid]::NewGuid().ToString("N")
    )
    $oldProfile = [Environment]::GetEnvironmentVariable(
        "WEBVIEW2_USER_DATA_FOLDER", "Process"
    )
    try {
        $env:WEBVIEW2_USER_DATA_FOLDER = $profileFolder
        $process = Start-Process -FilePath $exePath -PassThru -ErrorAction Stop
        Write-Output "Launched installed desktop PID: $($process.Id)"
        Write-Output "Requested temporary WebView2 profile: $profileFolder"
    }
    finally {
        if ($null -eq $oldProfile) {
            Remove-Item Env:\WEBVIEW2_USER_DATA_FOLDER -ErrorAction SilentlyContinue
        } else {
            $env:WEBVIEW2_USER_DATA_FOLDER = $oldProfile
        }
    }
    # No assertion that the runtime used the override until the device or
    # WebView2 API confirms it. Report the requested value, not fake success.
    Write-Output "Check whether Home renders in the new window. The original profile is unchanged."
} catch {
    Write-Error ("Isolated WebView2 experiment failed: {0}" -f $_.Exception.Message)
}
