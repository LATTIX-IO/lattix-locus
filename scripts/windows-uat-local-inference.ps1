# LOCUS-480 installed Windows Ollama/model-inventory acceptance probe.
# Read-only Locus API checks; one explicitly requested synthetic Ollama chat.
# No provider keys, prompt transcripts, or user work are read or printed.
# Example: pwsh -NoProfile -File .\scripts\windows-uat-local-inference.ps1 -ModelId 'gpt-oss:20b'
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$ModelId,
    [string]$LocusApi = 'http://127.0.0.1:3000/api',
    [string]$OllamaApi = 'http://127.0.0.1:11434'
)

$ErrorActionPreference = 'Stop'
$allowed = @('127.0.0.1', 'localhost', '[::1]')
foreach ($url in @($LocusApi, $OllamaApi)) {
    $uri = [uri]$url
    if ($uri.Scheme -ne 'http' -or $uri.Host -notin $allowed) {
        throw 'This local acceptance probe is restricted to HTTP loopback endpoints.'
    }
}

$tags = Invoke-RestMethod -Method Get -Uri "$($OllamaApi.TrimEnd('/'))/api/tags" -TimeoutSec 20 -MaximumRedirection 0
$overview = Invoke-RestMethod -Method Get -Uri "$($LocusApi.TrimEnd('/'))/models/overview" -TimeoutSec 25 -MaximumRedirection 0

$ollamaModels = @($tags.models | ForEach-Object { [string]$_.name } | Where-Object { $_ })
$locus = $overview.providers.ollama
$locusModels = @($locus.installed_models | ForEach-Object { [string]$_.id } | Where-Object { $_ })

if (-not $locus.available) { throw 'Locus reports Ollama unavailable.' }
if (-not ($ollamaModels -contains $ModelId)) { throw "The selected model is absent from Ollama: $ModelId" }
if (-not ($locusModels -contains $ModelId)) { throw "The selected model is absent from Locus: $ModelId" }

$unseen = @($ollamaModels | Where-Object { $locusModels -notcontains $_ })
if ($unseen.Count -gt 0) {
    throw "Locus inventory omits $($unseen.Count) installed Ollama model(s): $($unseen -join ', ')"
}
$stale = @($locusModels | Where-Object { $ollamaModels -notcontains $_ })
if ($stale.Count -gt 0) {
    throw "Locus inventory includes $($stale.Count) model(s) absent from Ollama: $($stale -join ', ')"
}

$body = @{
    model = $ModelId
    stream = $false
    messages = @(@{ role = 'user'; content = 'Reply with a short confirmation that local inference is available.' })
    options = @{ num_predict = 48; temperature = 0 }
} | ConvertTo-Json -Depth 8
$answer = Invoke-RestMethod -Method Post -Uri "$($OllamaApi.TrimEnd('/'))/api/chat" -ContentType 'application/json' -Body $body -TimeoutSec 180 -MaximumRedirection 0
if (-not ([string]$answer.message.content).Trim()) {
    throw 'Ollama completed without a usable response.'
}

Write-Host "PASS: Ollama reports $($ollamaModels.Count) installed model(s)."
Write-Host 'PASS: Locus reports the same installed model inventory.'
Write-Host "PASS: Explicit model '$ModelId' completed a real local Ollama inference."
Write-Host 'NEXT: Verify a native Locus task and a follow-up with this exact model.'
Write-Host 'NEXT: Confirm Activity shows provider=ollama and the selected model with no hosted model calls.'
