# LOCUS-477: read-only failed-run diagnosis against local Locus.
# Does not print prompts, credentials, full artifact content, or raw metadata.
# The relevant provider/auth error may be stored in a blocked artifact.
# Run while the Locus backend is listening:
#   pwsh -NoProfile -File .\scripts\windows-uat-run-diagnostics.ps1
[CmdletBinding()]
param(
    [string]$RunId = ""
)

$ErrorActionPreference = "Stop"
$apiBase = "http://127.0.0.1:3000/api"

function Get-LocusApi {
    param([Parameter(Mandatory = $true)][string]$Route)
    Invoke-RestMethod -Uri ("{0}{1}" -f $apiBase, $Route) -Method Get -TimeoutSec 15 -ErrorAction Stop
}

try {
    # Capture before wrapping: @(Invoke-RestMethod ...) may contain a nested
    # array with every run status, preventing correct element-wise filtering.
    $response = Get-LocusApi -Route "/workflow-runs"
    if ($null -eq $response) {
        Write-Output "No workflow runs returned from the local API."
        return
    }
    if ($response -is [array]) {
        $runs = @($response)
    } elseif ($response.PSObject.Properties["items"]) {
        $runs = @($response.items)
    } elseif ($response.PSObject.Properties["runs"]) {
        $runs = @($response.runs)
    } else {
        $runs = @($response)
    }
    if ($runs.Count -eq 1 -and $runs[0] -is [array]) {
        $runs = @($runs[0])
    }

    Write-Output ("Run records: {0}" -f $runs.Count)
    $runs | Group-Object -Property status | Sort-Object Name |
        ForEach-Object {
            Write-Output ("Status {0}: {1}" -f $_.Name, $_.Count)
        }

    if ([string]::IsNullOrWhiteSpace($RunId)) {
        $failed = @($runs |
            Where-Object { [string]$_.status -match '^(failed|blocked|error)$' } |
            Sort-Object -Property updatedAt -Descending)
        if ($failed.Count -eq 0) {
            Write-Output "No Failed or Blocked run found."
            return
        }
        $RunId = [string]$failed[0].id
    }
    if ($RunId -notmatch '^[a-zA-Z0-9_-]{8,100}$') {
        throw "Invalid run identifier; refusing an untrusted URL."
    }

    $selected = Get-LocusApi -Route ("/workflow-runs/{0}" -f $RunId)
    if ($null -eq $selected -or [string]$selected.id -ne $RunId) {
        throw "Run identity mismatch; refusing to use unrelated data."
    }
    $rawEvents = Get-LocusApi -Route ("/workflow-runs/{0}/events" -f $RunId)
    $events = @($rawEvents)
    if ($events.Count -eq 1 -and $events[0] -is [array]) {
        $events = @($events[0])
    }
    Write-Output ("Run ID: {0}" -f $RunId)
    Write-Output ("Run status: {0}" -f $selected.status)
    Write-Output ("Events: {0}" -f $events.Count)

    $errors = @($events |
        Where-Object {
            $_.type -eq "error" -or
            $_.title -match "error|fail|block|denied|refused|unavailable" -or
            $_.summary -match "admission denied|model unavailable|execution error"
        })
    foreach ($event in ($errors | Select-Object -Last 8)) {
        $title = [string]$event.title
        $summary = [string]$event.summary
        if ($title.Length -gt 150) { $title = $title.Substring(0, 150) }
        if ($summary.Length -gt 350) { $summary = $summary.Substring(0, 350) }
        Write-Output ("Failure event: {0} | {1} | {2}" -f $event.type, $title, $summary)
    }
    if ($errors.Count -eq 0) {
        Write-Output "No explicit failure event; inspect blocked artifact metadata."
    }

    $artifacts = @($selected.artifacts)
    $artifacts = @($artifacts | Where-Object { $null -ne $_ })
    Write-Output ("Run artifact count: {0}" -f $artifacts.Count)
    foreach ($artifact in $artifacts) {
        # IDs and statuses only: content can contain secrets or user prompts.
        Write-Output (
            "Artifact ID={0}; name={1}; status={2}; version={3}" -f
            ([string]$artifact.id).Substring(0, [Math]::Min(100, ([string]$artifact.id).Length)),
            ([string]$artifact.name).Substring(0, [Math]::Min(120, ([string]$artifact.name).Length)),
            [string]$artifact.status,
            [string]$artifact.version
        )
    }
    Write-Output "Review failure artifact content locally via the authenticated GET /api/artifacts/{id} endpoint; do not share secrets."
} catch {
    Write-Error ("Local workflow diagnosis stopped: {0}" -f $_.Exception.Message)
}
