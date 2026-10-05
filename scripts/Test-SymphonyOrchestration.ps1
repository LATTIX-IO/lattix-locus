param(
    [ValidateSet("native", "omniroute")]
    [string]$Route = "native",
    [string]$SymphonyRoot,
    [string]$DotenvFile
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$workflowPath = Join-Path $repoRoot "WORKFLOW.md"
if ([string]::IsNullOrWhiteSpace($SymphonyRoot)) {
    $SymphonyRoot = Join-Path $repoRoot "..\symphony"
}
$symphonyRoot = [System.IO.Path]::GetFullPath($SymphonyRoot)
if ([string]::IsNullOrWhiteSpace($DotenvFile)) {
    $DotenvFile = Join-Path $repoRoot ".env.symphony.local"
}
$dotenvPath = [System.IO.Path]::GetFullPath($DotenvFile)

if (Test-Path -LiteralPath $dotenvPath) {
    foreach ($line in Get-Content -LiteralPath $dotenvPath) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed -split "=", 2
        if ($parts.Length -ne 2) { throw "Invalid .env.symphony.local entry; expected NAME=VALUE." }
        $name = $parts[0].Trim()
        if ($name -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") { throw "Invalid environment variable name in .env.symphony.local." }
        $value = $parts[1].Trim()
        if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        Set-Item -Path "Env:$name" -Value $value
    }
}

foreach ($command in @("git", "gh", "codex")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is not available: $command" }
}

& gh auth status --hostname github.com 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "GitHub CLI is not authenticated for github.com." }

$gitBash = Get-Command "bash" -ErrorAction SilentlyContinue
if (-not $gitBash -and -not (Test-Path -LiteralPath "C:\Program Files\Git\bin\bash.exe")) {
    throw "Required command is not available: bash"
}
if (-not (Test-Path -LiteralPath $workflowPath)) { throw "Missing Symphony workflow: $workflowPath" }
if (-not (Test-Path -LiteralPath (Join-Path $symphonyRoot "elixir\mix.exs"))) {
    throw "Symphony is not installed at $symphonyRoot. Clone OpenAI Symphony there first."
}

$workflow = Get-Content -Raw -LiteralPath $workflowPath
foreach ($requiredText in @(
    "kind: linear",
    "api_key: `$LINEAR_API_KEY",
    'project_slug: "3b160e533200"',
    'bash "$SYMPHONY_CODEX_WRAPPER"',
    "## Unattended GitHub contract"
)) {
    if (-not $workflow.Contains($requiredText)) { throw "WORKFLOW.md is missing required configuration: $requiredText" }
}

$wrapperPath = Join-Path $repoRoot "scripts\symphony-codex.sh"
if (-not (Test-Path -LiteralPath $wrapperPath)) { throw "Missing Symphony Codex wrapper: $wrapperPath" }
$wrapper = Get-Content -Raw -LiteralPath $wrapperPath
if (-not $wrapper.Contains("--disable apps")) { throw "Symphony Codex wrapper must disable app connectors." }

& codex --disable apps -c 'service_tier="fast"' -c 'model_reasoning_effort="high"' --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Codex configuration validation failed." }

if ([string]::IsNullOrWhiteSpace($env:LINEAR_API_KEY)) {
    throw "LINEAR_API_KEY is not set. Symphony cannot poll the Locus project."
}
$linearQuery = @'
query SymphonyProjectPreflight($slug: String!) {
  projects(filter: {slugId: {eq: $slug}}, first: 1) {
    nodes { id name slugId }
  }
}
'@
try {
    $linearResponse = Invoke-RestMethod -Method Post -Uri "https://api.linear.app/graphql" -Headers @{
        Authorization = $env:LINEAR_API_KEY
        "Content-Type" = "application/json"
    } -Body (@{
        query = $linearQuery
        variables = @{ slug = "3b160e533200" }
    } | ConvertTo-Json -Depth 5) -TimeoutSec 10
}
catch {
    throw "Linear rejected the unattended Symphony credential or could not be reached."
}
if ($linearResponse.errors -or $linearResponse.data.projects.nodes.Count -ne 1) {
    throw "The unattended Symphony credential cannot resolve the Locus Linear project (3b160e533200)."
}
$linearProject = $linearResponse.data.projects.nodes[0]
if ($linearProject.name -ne "Locus" -or $linearProject.slugId -ne "3b160e533200") {
    throw "Linear project preflight returned an unexpected project."
}

if ($Route -eq "omniroute") {
    if (-not (Get-Command "omniroute" -ErrorAction SilentlyContinue)) { throw "OmniRoute is not installed." }
    if ([string]::IsNullOrWhiteSpace($env:OMNIROUTE_API_KEY)) { throw "OMNIROUTE_API_KEY is not set." }
    try {
        Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:20128/v1/models" -Headers @{ Authorization = "Bearer $($env:OMNIROUTE_API_KEY)" } -TimeoutSec 5 | Out-Null
    }
    catch {
        throw "OmniRoute is not reachable or rejected the dashboard key at http://127.0.0.1:20128/v1/models."
    }
}

Write-Output "Symphony orchestration preflight passed for route: $Route"
