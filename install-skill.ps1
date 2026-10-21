param(
    [ValidateSet('codex', 'claude', 'both')][string]$HostName = 'both',
    [string]$HomeDirectory = $HOME
)
$ErrorActionPreference = 'Stop'
$sourceRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$source = Join-Path $sourceRoot 'skills/present-plan'
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'lalo.py') -PathType Leaf)) { throw 'Keep this installer in the Lalo source checkout.' }
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot '.venv/Scripts/python.exe') -PathType Leaf)) { throw 'Install the checkout Python environment first; see README.md.' }
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
    [IO.File]::WriteAllText((Join-Path $destination '.lalo-lite-install'), 'lalo-lite-source-v1', [Text.UTF8Encoding]::new($false))
    Write-Output "Installed: $destination"
}
Write-Output 'Reload your coding agent to discover present-plan. Run this installer again after moving the checkout.'
