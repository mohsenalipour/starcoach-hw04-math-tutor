$ErrorActionPreference = 'Stop'
$tutorRoot = $PSScriptRoot
$tutorVenv = Join-Path $tutorRoot '.venv'
if (-not (Test-Path -LiteralPath (Join-Path $tutorVenv 'Scripts\python.exe'))) {
    python -m venv $tutorVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
}
$tutorPython = Join-Path $tutorVenv 'Scripts\python.exe'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python $tutorPython -r (Join-Path $tutorRoot 'requirements.txt')
} else {
    & $tutorPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'Could not install pip.' }
    & $tutorPython -m pip install -r (Join-Path $tutorRoot 'requirements.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Could not install the dependencies.' }
$tutorEnv = Join-Path $tutorRoot '.env'
if (-not (Test-Path -LiteralPath $tutorEnv)) {
    Copy-Item -LiteralPath (Join-Path $tutorRoot '.env.example') -Destination $tutorEnv
    Write-Host 'Set your API key in .env, then run .\run.ps1'
} else {
    Write-Host 'Ready. Run .\run.ps1'
}
