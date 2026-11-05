param([int]$Port = 8765)
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.conda\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    Write-Error 'Conda environment missing. Follow README.md to create it first.'
    exit 1
}
Set-Location -LiteralPath $projectRoot
$env:PYTHONNOUSERSITE = '1'
& $projectPython -s -m uvicorn ai_pulse.app:app --host 127.0.0.1 --port $Port --workers 1
exit $LASTEXITCODE
