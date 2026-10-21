$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path '.venv/Scripts/python.exe')) {
        throw 'Install the independent Python environment first; see README.md.'
    }
    if (-not (Test-Path 'web/app.bundle.js')) {
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    & .venv/Scripts/python.exe lalo.py
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
