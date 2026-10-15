param(
    [string]$PythonExecutable = 'python',
    [string]$PrivateAssetRoot = 'D:\0\Noir Fiction Engine\01_Mining_App\Reel_Harvest\prototype_graph\assets',
    [string]$VideoInputsJson = 'D:\0\Noir Fiction Engine\00_ADMIN\RedNote_Graph_UI_Pass1_20261008\video-inputs.json',
    [string]$FourthVideoPath = 'C:\Users\User\OneDrive\Videos\4K Video Downloader+\Vanity 6 - Nasty Girl (1982) • TopPop.mp4'
)

$ErrorActionPreference = 'Stop'
$boardDir = Join-Path $PSScriptRoot 'ui_prototypes\cinematic_graph'
if (-not (Test-Path -LiteralPath $boardDir -PathType Container)) {
    throw "Scatter board source is missing: $boardDir"
}
if (-not (Test-Path -LiteralPath $PrivateAssetRoot -PathType Container)) {
    throw "Private board asset directory is missing: $PrivateAssetRoot"
}
if (Get-NetTCPConnection -LocalPort 5186 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 5186 is occupied; inspect the existing board before starting another one'
}

$runtimeDir = Join-Path $env:LOCALAPPDATA 'RedNoteScatter'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$videos = @()
if (Test-Path -LiteralPath $VideoInputsJson -PathType Leaf) {
    $videos = @(Get-Content -LiteralPath $VideoInputsJson -Raw | ConvertFrom-Json)
}
if (Test-Path -LiteralPath $FourthVideoPath -PathType Leaf) {
    $videos = @($videos | Where-Object { [string]$_.slot -ne '4' })
    $videos += [pscustomobject]@{ slot = 4; path = (Resolve-Path -LiteralPath $FourthVideoPath).Path }
}
$manifest = Join-Path $runtimeDir 'video-inputs.json'
ConvertTo-Json -InputObject @($videos) -Depth 15 | Set-Content -LiteralPath $manifest -Encoding utf8
$env:SCATTER_VIDEO_INPUTS_JSON = $manifest
$env:SCATTER_PRIVATE_ASSET_ROOT = (Resolve-Path -LiteralPath $PrivateAssetRoot).Path
$pythonPath = (Get-Command $PythonExecutable -ErrorAction Stop).Source
$process = Start-Process -FilePath $pythonPath -ArgumentList 'server.py' `
    -WorkingDirectory $boardDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $runtimeDir 'board.stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'board.stderr.log')
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $response = Invoke-WebRequest 'http://127.0.0.1:5186/' -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            return [pscustomobject]@{ Pid = $process.Id; Url = 'http://127.0.0.1:5186/';
                PrivateAssets = $env:SCATTER_PRIVATE_ASSET_ROOT; VideoSlots = @($videos).Count;
                Logs = $runtimeDir }
        }
    } catch {}
    if ($process.HasExited) { break }
}
throw "Scatter board did not become ready; inspect $runtimeDir\board.stderr.log"
