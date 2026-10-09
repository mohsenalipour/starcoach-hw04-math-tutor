$ErrorActionPreference = 'Stop'
$tutorRoot = $PSScriptRoot
$tutorPython = Join-Path $tutorRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $tutorPython)) {
    Write-Error 'Run setup.ps1 first to install this project.'
}
& $tutorPython (Join-Path $tutorRoot 'cli.py') @args
exit $LASTEXITCODE
