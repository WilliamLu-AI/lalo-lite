param(
    [ValidateSet('codex', 'claude', 'both')][string]$HostName = 'both',
    [string]$HomeDirectory = $HOME
)
$ErrorActionPreference = 'Stop'
$application = Join-Path $PSScriptRoot 'app/windows-x64/lalo.exe'
$source = Join-Path $PSScriptRoot 'skills/present-plan'
if (-not (Test-Path -LiteralPath $application -PathType Leaf)) { throw 'Keep install-skill.ps1 next to the app and skills folders.' }
$application = (Resolve-Path -LiteralPath $application).Path
$targets = if ($HostName -eq 'both') { @('codex', 'claude') } else { @($HostName) }
$destinations = @()
foreach ($item in $targets) {
    $hostFolder = if ($item -eq 'codex') { '.agents' } else { '.claude' }
    $destination = Join-Path $HomeDirectory "$hostFolder/skills/present-plan"
    $marker = Join-Path $destination '.lalo-lite-install'
    if ((Test-Path -LiteralPath $destination) -and
        ((-not (Test-Path -LiteralPath $marker)) -or ([IO.File]::ReadAllText($marker) -ne 'lalo-lite-public-v1'))) {
        throw "An existing skill is installed at $destination. Back it up or choose a different HomeDirectory before installing."
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
    [IO.File]::WriteAllText((Join-Path $destination 'app-path.txt'), $application, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $destination '.lalo-lite-install'), 'lalo-lite-public-v1', [Text.UTF8Encoding]::new($false))
    Write-Output "Installed: $destination"
}
Write-Output 'Restart or reload your coding agent to discover present-plan. Run this installer again if you move Lalo Lite.'
