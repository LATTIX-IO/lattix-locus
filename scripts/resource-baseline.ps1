# Capture a resource baseline for the local Lattix xFrontier stack.
# Writes a timestamped snapshot to docs/perf/baselines/ so optimizations can
# prove their effect. Read-only: starts nothing, kills nothing.
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

$ErrorActionPreference = "SilentlyContinue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $repoRoot "docs\perf\baselines"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile = Join-Path $outDir "baseline-$stamp.txt"

$lines = @()
$lines += "# Lattix xFrontier resource baseline - $(Get-Date -Format o)"
$lines += ""

$lines += "## Host"
$os = Get-CimInstance Win32_OperatingSystem
$lines += ("total RAM GB: " + [math]::Round($os.TotalVisibleMemorySize / 1MB, 1))
$lines += ("free RAM GB:  " + [math]::Round($os.FreePhysicalMemory / 1MB, 1))
$lines += ""

$lines += "## Stack-related processes (node / python)"
$procs = @()
foreach ($cimProc in (Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(node|python|pythonw)\.exe$' })) {
    $proc = Get-Process -Id $cimProc.ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    $command = $cimProc.CommandLine
    if (-not $command) { $command = $cimProc.Name }
    if ($command.Length -gt 160) { $command = $command.Substring(0, 160) }
    $procs += [PSCustomObject]@{
        Pid     = $cimProc.ProcessId
        RamMB   = [math]::Round($proc.WorkingSet64 / 1MB)
        Command = $command
    }
}
$procs = $procs | Sort-Object RamMB -Descending
foreach ($p in $procs) { $lines += ("{0,8} {1,8} MB  {2}" -f $p.Pid, $p.RamMB, $p.Command) }
$lines += ("process total MB: " + (($procs | Measure-Object RamMB -Sum).Sum))
$lines += ""

$lines += "## Containers (docker stats, one sample)"
$dockerStats = docker stats --no-stream --format "{{.Name}}`t{{.MemUsage}}`t{{.CPUPerc}}" 2>$null
if ($dockerStats) { $lines += $dockerStats } else { $lines += "(docker not running or no containers)" }

$lines | Set-Content -Path $outFile -Encoding utf8
Write-Output "baseline written: $outFile"
$lines | Select-Object -First 40 | Write-Output
