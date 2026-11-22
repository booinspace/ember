$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$configDir = Join-Path $root '.local\config'
if (-not (Test-Path -LiteralPath $configDir -PathType Container)) {
    throw "No deployment directory at $configDir. See README.md for Windows setup."
}
$configs = @(Get-ChildItem -LiteralPath $configDir -Filter 'deployment*.toml' -File |
    Sort-Object Name)
if ($configs.Count -eq 0) { throw "No deployment TOML files in $configDir" }

Write-Host 'Select an Ember deployment:'
for ($i = 0; $i -lt $configs.Count; $i++) {
    Write-Host ('  {0}. {1}' -f ($i + 1), $configs[$i].Name)
}
Write-Host '  0. Quit'

while ($true) {
    $choice = Read-Host 'Profile number'
    if ([string]::IsNullOrWhiteSpace($choice) -or $choice -eq '0') { exit 0 }
    $number = 0
    if ([int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $configs.Count) {
        $config = $configs[$number - 1].FullName
        break
    }
    Write-Host 'Enter a number from the menu.'
}

Set-Location -LiteralPath $root
& (Join-Path $root 'start.ps1') -Config $config
exit $LASTEXITCODE
