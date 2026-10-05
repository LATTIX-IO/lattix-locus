[CmdletBinding()]
param(
    [ValidateSet("codex", "native")]
    [string]$Harness = "codex",
    [string]$Model = "gpt-oss:20b",
    [switch]$Once,
    [switch]$Serve
)

$ErrorActionPreference = "Stop"
if ($Once -and $Serve) {
    throw "Choose either -Once or -Serve."
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonCandidate = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $pythonCandidate -PathType Leaf) {
    $python = $pythonCandidate
} else {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    }
    if (-not $pythonCommand) {
        throw "Python 3.12+ is required. Create the repo .venv or add Python to PATH."
    }
    $python = $pythonCommand.Source
}
$pythonVersion = & $python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.12") {
    throw "Python 3.12+ is required. Create .venv with 'uv venv .venv --python 3.12' and install with 'uv pip install --python .venv\Scripts\python.exe -e `".[dev]`"'."
}

$configuredBase = ([string]$env:CODEX_OLLAMA_BASE_URL).Trim()
if (-not $configuredBase) { $configuredBase = ([string]$env:OLLAMA_BASE_URL).Trim() }
if (-not $configuredBase) { $configuredBase = "http://127.0.0.1:11434/v1" }
$ollamaUri = [Uri]$configuredBase
if (-not $ollamaUri.IsLoopback -or $ollamaUri.UserInfo) {
    throw "Codex RSI requires a credential-free loopback Ollama URL; configured host was not used."
}
$ollamaApi = $configuredBase.TrimEnd("/") -replace "/v1$", ""
$ollamaOpenAi = "$ollamaApi/v1"
$env:OLLAMA_BASE_URL = $ollamaOpenAi
$env:CODEX_OLLAMA_BASE_URL = $ollamaOpenAi
$env:LOCUS_LOOP_CODING_HARNESS = $Harness
$env:LOCUS_LOOP_CODEX_MODEL = $Model
$env:LOCUS_AGENT_MODEL_CHAIN = "ollama/$Model"
$env:LOCUS_LOOP_SCORECARD_MODEL = $Model
$env:LOCUS_RUNTIME_PROFILE = "local-native"
$env:LOCUS_LOOP_RESEARCH_MODE = "1"
$env:LOCUS_LOOP_AUTO_MERGE = "0"
$env:LOCUS_LOOP_QUALITY_GATES = "1"
$env:LOCUS_LOOP_EVAL_GATE = "advisory"
$env:LOCUS_LOOP_SCORECARD = "advisory"

Push-Location $repoRoot
try {
    $tagResponse = Invoke-RestMethod -Method Get -Uri "$ollamaApi/api/tags" -TimeoutSec 5
    $modelNames = @($tagResponse.models | ForEach-Object { [string]$_.name })
    if ($model -notin $modelNames) {
        throw "Ollama is running but model '$Model' is missing. Pull it with: ollama pull $Model"
    }

    if ($Harness -eq "codex") {
        $codex = Get-Command codex -ErrorAction SilentlyContinue
        if (-not $codex) { throw "Codex CLI is not on PATH." }
        & $codex.Source --version | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Codex CLI did not start successfully." }
    }

    $preflight = @'
import json
import sys
from pathlib import Path
from locus_runtime.loop_runner.state import LoopConfig
from locus_runtime.loop_runner.linear import LinearError, LinearNotConfigured
from locus_runtime.policy_engine import build_policy_engine
from locus_runtime.loop_runner.linear_mcp import LinearMcpTracker
from locus_runtime.win_toolchain import toolchain_for

config = LoopConfig.load(Path.cwd())
if not config.project_slug:
    raise SystemExit("WORKFLOW.md does not identify a Linear project")
engine = build_policy_engine()
try:
    engine.start()
    if not getattr(engine, "running", False):
        raise SystemExit("OPA policy engine did not become healthy")
finally:
    engine.close()
toolchain = "not-required"
if sys.platform == "win32":
    if not toolchain_for().is_installed():
        raise SystemExit("the Windows toolchain is missing; run `lattix native-fetch-toolchain`")
    toolchain = "ready"
try:
    tracker = LinearMcpTracker()
    issues = tracker.list_candidate_issues(
        config.project_slug, active_states=config.active_states, label=config.required_label
    )
except (LinearError, LinearNotConfigured) as exc:
    raise SystemExit(f"Linear MCP preflight failed: {exc}") from None
print(json.dumps({"policy": "ready", "toolchain": toolchain, "linear": "connected", "eligible_count": len(issues)}))
'@
    $preflightOutput = & $python -c $preflight
    if ($LASTEXITCODE -ne 0) {
        throw "Locus preflight failed. Confirm the local backend and Linear OAuth connection are ready."
    }
    $preflightResult = ($preflightOutput -join "`n") | ConvertFrom-Json

    Write-Host "Locus product RSI preflight passed"
    Write-Host "  Harness: $Harness"
    Write-Host "  Model: Ollama / $Model (loopback)"
    Write-Host "  OPA policy engine: $($preflightResult.policy)"
    Write-Host "  Locus process toolchain: $($preflightResult.toolchain)"
    Write-Host "  Linear MCP: $($preflightResult.linear); eligible issues: $($preflightResult.eligible_count)"
    Write-Host "  Auto-merge: disabled; research mode: enabled"

    if ($Serve) {
        & $python -m locus_tooling.cli loop serve
    } elseif ($Once) {
        & $python -m locus_tooling.cli loop run --once
    } else {
        Write-Host "Preflight only. Use -Once to run one tick, or -Serve to start the polling loop."
    }
    if (($Once -or $Serve) -and $LASTEXITCODE -ne 0) {
        throw "Locus loop exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
