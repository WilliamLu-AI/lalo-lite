param(
    [ValidateSet('codex', 'claude', 'both')][string]$HostName = 'both',
    [string]$HomeDirectory = $HOME
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$source = Join-Path $sourceRoot 'skills/present-plan'
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'lalo.py') -PathType Leaf)) { throw 'Keep this installer in the Lalo source checkout.' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot '.venv/Scripts/python.exe') -PathType Leaf)) { throw 'Install the checkout Python environment first; see README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'web/app.bundle.js') -PathType Leaf)) { throw 'Frontend missing. In the checkout run npm ci and npm run build before installing the Skill.' }
$python = Join-Path $sourceRoot '.venv/Scripts/python.exe'
$diagnosticOutput = & $python (Join-Path $sourceRoot 'lalo.py') doctor
if ($LASTEXITCODE -ne 0) { throw 'Application dependency check failed. Install requirements-lock.txt and run lalo.py doctor.' }
try { $diagnostic = ($diagnosticOutput -join "`n") | ConvertFrom-Json } catch { throw 'Application diagnostic check did not return valid JSON.' }
if (-not $diagnostic.frontendReady -or -not $diagnostic.readiness.runtime.ready) { throw 'Application resources or runtime dependencies are unavailable. Run lalo.py doctor.' }
$targets = if ($HostName -eq 'both') { @('codex', 'claude') } else { @($HostName) }
$destinations = @()
foreach ($item in $targets) {
    $hostFolder = if ($item -eq 'codex') { '.agents' } else { '.claude' }
    $destination = Join-Path $HomeDirectory "$hostFolder/skills/present-plan"
    $marker = Join-Path $destination '.lalo-lite-install'
    if (Test-Path -LiteralPath $destination) {
        if (-not (Test-Path -LiteralPath $marker)) { throw "An unmanaged Skill exists at $destination. Choose a different HomeDirectory or back it up first." }
        $kind = [IO.File]::ReadAllText($marker)
        if ($kind -notin @('lalo-lite-public-v1', 'lalo-lite-source-v1')) { throw "An unmanaged Skill exists at $destination." }
    }
    $destinations += $destination
}
foreach ($destination in $destinations) {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($relative in @('SKILL.md', 'README.md', 'LICENSE', 'scripts/launch.py')) {
        $output = Join-Path $destination $relative
        New-Item -ItemType Directory -Path (Split-Path $output) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $source $relative) -Destination $output -Force
    }
    [IO.File]::WriteAllText((Join-Path $destination 'source-root.txt'), $sourceRoot, [Text.UTF8Encoding]::new($false))
    $oldPointer = Join-Path $destination 'app-path.txt'
    if (Test-Path -LiteralPath $oldPointer -PathType Leaf) { Remove-Item -LiteralPath $oldPointer }
    [IO.File]::WriteAllText((Join-Path $destination '.lalo-lite-install'), 'lalo-lite-source-v1', [Text.UTF8Encoding]::new($false))
    Write-Output "Skill installed: $destination"
}
Write-Output 'Reload your coding agent to discover present-plan. Run this installer again after moving the checkout.'
Write-Output 'Model credentials and login are configured separately in Lalo settings; optional Smart Turn weights are not required for cloud ASR.'
