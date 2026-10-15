param(
    [string]$PythonExecutable = 'python',
    [string]$DataDir = 'D:\0\Noir Fiction Engine\Scatter_Engine\data',
    [string]$MirrorDb = 'D:\0\Noir Fiction Engine\Red Note Gemini\data_v2\rednote-v2.sqlite',
    [string]$StudyDb = 'D:\0\Noir Fiction Engine\Red_Engine\data\studies.sqlite',
    [string]$IndexDb = 'D:\0\Noir Fiction Engine\Red_Engine\data\source-index.sqlite'
)

$ErrorActionPreference = 'Stop'
$engineDir = Join-Path $PSScriptRoot 'engines\scatter_graph'
if (-not (Test-Path -LiteralPath $engineDir -PathType Container)) {
    throw "Scatter engine source is missing: $engineDir"
}
foreach ($file in @($MirrorDb, $StudyDb, $IndexDb)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "Source database is missing: $file"
    }
}
if (-not (Test-Path -LiteralPath $DataDir -PathType Container)) {
    throw "Scatter data directory is missing: $DataDir"
}
$pythonCommand = Get-Command $PythonExecutable -ErrorAction Stop
$pythonPath = $pythonCommand.Source
$env:SCATTER_DATA_DIR = (Resolve-Path -LiteralPath $DataDir).Path
$env:REDNOTE_MIRROR_DB = (Resolve-Path -LiteralPath $MirrorDb).Path
$env:REDNOTE_STUDY_DB = (Resolve-Path -LiteralPath $StudyDb).Path
$env:REDNOTE_INDEX_DB = (Resolve-Path -LiteralPath $IndexDb).Path
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($gitCommand) {
    $buildId = & $gitCommand.Source -C $PSScriptRoot rev-parse --short HEAD 2>$null
    if ($LASTEXITCODE -eq 0 -and $buildId) {
        $env:SCATTER_BUILD_ID = [string]$buildId.Trim()
    }
}

try {
    $existing = Invoke-RestMethod -Uri 'http://127.0.0.1:5189/api/scatter/v1/health' -TimeoutSec 2
} catch {
    $existing = $null
}
if ($existing) {
    if ($existing.service -ne 'scatter-graph') { throw 'Port 5189 belongs to another service' }
    return $existing
}
if (Get-NetTCPConnection -LocalPort 5189 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 5189 is occupied by a service with no Scatter health response'
}

$telemetryDir = Join-Path $env:SCATTER_DATA_DIR 'telemetry'
New-Item -ItemType Directory -Path $telemetryDir -Force | Out-Null
$process = Start-Process -FilePath $pythonPath -ArgumentList 'api.py' `
    -WorkingDirectory $engineDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $telemetryDir 'api.stdout.log') `
    -RedirectStandardError (Join-Path $telemetryDir 'api.stderr.log')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:5189/api/scatter/v1/health' -TimeoutSec 2
        if ($health.service -eq 'scatter-graph') {
            return [pscustomobject]@{ Pid = $process.Id; Health = $health; Telemetry = $telemetryDir }
        }
    } catch {}
    if ($process.HasExited) { break }
}
throw "Scatter API did not become ready; see $telemetryDir\api.stderr.log"
