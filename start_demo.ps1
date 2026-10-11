param([int]$Port = 8501, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ((Test-Path -LiteralPath (Join-Path $demoRoot '.runtime\packages\streamlit')) -and (Test-Path -LiteralPath $bundledPython)) {
    $demoPython = $bundledPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $demoPython = 'py'
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $demoPython = 'python'
} else {
    throw 'Python 3.11 or later is required. See README.md.'
}
Set-Location -LiteralPath $demoRoot
Write-Host "SupplySync: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
& $demoPython -X utf8 -B (Join-Path $demoRoot 'bootstrap.py') streamlit --server.port $Port
exit $LASTEXITCODE
