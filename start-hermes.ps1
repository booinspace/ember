# Ember's optional native text client. Ember itself still starts through start.ps1.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$homePath = Join-Path $env:LOCALAPPDATA 'hermes\profiles\ember'
$receiptPath = Join-Path $homePath 'ember-setup.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
    throw 'Configure the named Ember profile with scripts/hermes/hermes_client_setup.py first.'
}
$receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
if ($receipt.schema_version -ne 'ember-hermes-client-setup-1' -or
    [IO.Path]::GetFullPath($receipt.home) -ne [IO.Path]::GetFullPath($homePath) -or
    $null -ne $receipt.desktop_data) {
    throw 'The named Ember profile receipt is invalid.'
}
$package = @(Get-AppxPackage -Name 'NousResearchInc.HermesAgent')
if ($package.Count -ne 1 -or [string]$package[0].Version -ne $receipt.package_version) {
    throw 'Install or requalify the pinned Hermes Desktop package before launching Ember.'
}
$stampPath = Join-Path $package[0].InstallLocation 'app\resources\install-stamp.json'
$stamp = Get-Content -LiteralPath $stampPath -Raw | ConvertFrom-Json
if ($stamp.commit -ne $receipt.sdk_revision) { throw 'The Hermes Desktop SDK revision changed.' }
$alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\hermes.exe'
if (-not (Test-Path -LiteralPath $alias -PathType Leaf)) {
    throw 'Enable the installed Hermes app execution alias in Windows Settings.'
}
# Select a regular named profile; never create another Electron data directory or inherit a remote.
$overrides = @{
    HERMES_HOME = $null
    HERMES_DESKTOP_USER_DATA_DIR = $null
    HERMES_DESKTOP_HERMES_ROOT = $null
    HERMES_DESKTOP_REMOTE_URL = $null
    HERMES_DESKTOP_REMOTE_TOKEN = $null
    HERMES_DESKTOP_DEV_SERVER = $null
    HERMES_BUNDLED_PLUGINS = $null
    HERMES_TUI_TOOLSETS = 'search'
    HERMES_ENABLE_PROJECT_PLUGINS = '0'
    PYTHONDONTWRITEBYTECODE = '1'
}
$previous = @{}
try {
    foreach ($name in $overrides.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $overrides[$name], 'Process')
    }
    Write-Host 'Opening Hermes Desktop. If it is already open, select Ember in its profile picker.'
    & $alias --profile ember desktop --cwd $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Hermes Desktop could not launch the Ember profile.' }
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
exit $LASTEXITCODE
