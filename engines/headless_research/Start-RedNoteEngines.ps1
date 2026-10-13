$ErrorActionPreference = 'Stop'
$ArchivePort = 5182
$ResearchPort = 5188
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$harvestRoot = Join-Path $projectRoot '01_Mining_App\Reel_Harvest'
$archiveRoot = Join-Path $projectRoot 'Red Note Gemini'
$harvestPython = Join-Path $harvestRoot '.venv\Scripts\python.exe'
$researchPython = 'C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe'

foreach ($file in @($harvestPython, $researchPython,
    (Join-Path $archiveRoot 'data_v2\rednote-v2.sqlite'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
        throw "Required engine file missing: $file"
    }
}

function Get-ServiceHealth([int]$port, [string]$path) {
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$port$path" -TimeoutSec 2
    } catch {
        return $null
    }
}

$research = Get-ServiceHealth $ResearchPort '/api/health'
if ($research -and $research.service -ne 'red-engine-research') {
    throw "Port $ResearchPort is occupied by a different service"
}
if (-not $research) {
    if (Get-NetTCPConnection -LocalPort $ResearchPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $ResearchPort is already in use"
    }
    Start-Process -FilePath $researchPython -ArgumentList 'research_service.py' `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden | Out-Null
}

$archive = Get-ServiceHealth $ArchivePort '/api/engine/health'
if ($archive -and $archive.ui -ne $false) {
    throw "Port $ArchivePort is occupied by a different service"
}
if (-not $archive) {
    if (Get-NetTCPConnection -LocalPort $ArchivePort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $ArchivePort is already in use"
    }
    $engineArgs = '-m rednote_engine.server --base "' + (Join-Path $PSScriptRoot 'runtime') +
        '" --read-only-archive "' + $archiveRoot + '" --port ' + $ArchivePort
    Start-Process -FilePath $harvestPython -ArgumentList $engineArgs `
        -WorkingDirectory $harvestRoot -WindowStyle Hidden | Out-Null
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    $research = Get-ServiceHealth $ResearchPort '/api/health'
    $archive = Get-ServiceHealth $ArchivePort '/api/engine/system'
    if ($research -and $archive) { break }
    Start-Sleep -Milliseconds 250
}
if (-not $research -or -not $archive) {
    throw 'One or more headless services did not become ready'
}

[pscustomobject]@{
    ArchiveEngine = "http://127.0.0.1:$ArchivePort/api/engine/system"
    ArchiveMode = $archive.archive.state
    CapturedProfiles = $archive.archive.counts.profiles
    CapturedPosts = $archive.archive.counts.posts
    CapturedComments = $archive.archive.counts.comments
    CaptureAvailable = $archive.acquisition.captureAvailable
    ResearchService = "http://127.0.0.1:$ResearchPort/api/health"
    StudyDatabaseAvailable = $archive.research.studies
}
