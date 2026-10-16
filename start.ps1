# Convenience only: Ember's Python runtime owns services, readiness and shutdown.
[CmdletBinding()]
param(
    [string]$Config = ".local/config/deployment.non-mtp.toml",
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$emberExecutable = Join-Path $PSScriptRoot ".venv/Scripts/ember.exe"
if (-not (Test-Path -LiteralPath $emberExecutable -PathType Leaf)) {
    throw "Ember is not installed. Run uv sync --locked from the repository first."
}
$configFile = if ([IO.Path]::IsPathRooted($Config)) {
    $Config
} else {
    Join-Path $PSScriptRoot $Config
}
if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
    throw "Deployment configuration is missing. Configure Ember before launching."
}
# The Python descriptor owns listener selection; Windows setup only applies its scoped rules.
& (Join-Path $PSScriptRoot "scripts/setup_lan.ps1") -Config $configFile
if (-not $?) { throw "Ember LAN firewall setup failed." }
$emberArguments = @("start", "--config", $configFile)
if (-not $NoBrowser) { $emberArguments += "--open-browser" }
& $emberExecutable @emberArguments
exit $LASTEXITCODE
