# LOCUS-477: read-only Windows UAT diagnosis for a failed local workflow.
# No user input required. Chooses the latest Failed run unless -RunId is set.
# Only GET requests. No run content, cookies, headers, environment secrets,
# credential material, model prompts, or whole responses are displayed.
# Inspect reported error summaries before sharing them externally.
[CmdletBinding()]
param(
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$apiBase = "http://127.0.0.1:3000/api"

function Get-LocusApi {
    param([Parameter(Mandatory=$true)][string]$Route)
    return Invoke-RestMethod -Uri ("{0}{1}" -f $apiBase, $Route) -Method Get -TimeoutSec 15 -ErrorAction Stop
}

try {
    $runs = @(Get-LocusApi -Route "/workflow-runs")
    if ($runs.Count -eq 0) {
        Write-Output "No workflow runs returned from the local API."
        return
    }

    if ([string]::IsNullOrWhiteSpace($RunId)) {
        $failed = @($runs | Where-Object {
            [string]$_.status -in @("Failed", "Blocked")
        } | Sort-Object -Property updatedAt -Descending)
        if ($failed.Count -eq 0) {
            Write-Output "No Failed or Blocked run found."
            $runs | Sort-Object -Property updatedAt -Descending |
                Select-Object -First 5 id, status, updatedAt |
                Format-Table -AutoSize
            return
        }
        $RunId = [string]$failed[0].id
    }
    if ($RunId -notmatch '^[a-zA-Z0-9_-]{8,100}$') {
        throw "Invalid run identifier. Refusing to request an untrusted URL."
    }

    $selectedRun = Get-LocusApi -Route ("/workflow-runs/{0}" -f $RunId)
    if ($null -eq $selectedRun -or [string]$selectedRun.id -ne $RunId) {
        throw "Run identity mismatch; refusing to use an unrelated response."
    }
    $eventList = @(Get-LocusApi -Route ("/workflow-runs/{0}/events" -f $RunId))
    Write-Output ("Run ID: {0}" -f $RunId)
    Write-Output ("Run status: {0}" -f $selectedRun.status)
    Write-Output ("Events received: {0}" -f $eventList.Count)

    $errors = @($eventList | Where-Object {
        $_.type -eq "error" -or
        $_.title -match "error|fail|block|denied|refused|unavailable" -or
        $_.summary -match "admission denied|model unavailable|execution error"
    })
    if ($errors.Count -eq 0) {
        Write-Output "No explicit error event; last five event types/titles:"
        $eventList | Select-Object -Last 5 type, title | Format-Table -AutoSize
        return
    }

    Write-Output "Failure events (review locally for sensitive details before sharing):"
    foreach ($record in ($errors | Select-Object -Last 8)) {
        # Do not expose metadata/payload/tool arguments. Bound event text.
        $label = [string]$record.title
        $summary = [string]$record.summary
        if ($label.Length -gt 150) { $label = $label.Substring(0, 150) }
        if ($summary.Length -gt 500) { $summary = $summary.Substring(0, 500) }
        [PSCustomObject]@{
            Type = [string]$record.type
            Title = $label
            Summary = $summary
        } | Format-List
    }
} catch {
    Write-Error ("Local workflow diagnostic stopped: {0}" -f $_.Exception.Message)
}
