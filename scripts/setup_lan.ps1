# Narrow persistent rules are inactive without Ember's listeners. Never disable the firewall.
[CmdletBinding()]
param(
    [string]$Config = ".local/config/deployment.non-mtp.toml",
    [switch]$Elevated
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$configPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $repoRoot $Config }
$emberExecutable = Join-Path $repoRoot ".venv/Scripts/ember.exe"
$networkJson = & $emberExecutable network-info --config $configPath
if ($LASTEXITCODE -ne 0) { throw "LAN configuration could not be read." }
$network = $networkJson | ConvertFrom-Json
if (-not $network.enabled) { return }
$lanInterface = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $network.address -ErrorAction SilentlyContinue
if (-not $lanInterface) { throw "The configured LAN address is not assigned here. Update browser.lan_address." }
$lanProfile = Get-NetConnectionProfile -InterfaceIndex $lanInterface.InterfaceIndex
if ($lanProfile.NetworkCategory -notin @("Private", "DomainAuthenticated")) {
    throw "Ember LAN access requires a Private or Domain network profile."
}

function Test-EmberLanRule {
    param([object]$Definition, [string]$Address)
    $rule = Get-NetFirewallRule -Name $Definition.name -PolicyStore PersistentStore -ErrorAction SilentlyContinue
    if (-not $rule -or $rule.Enabled -ne "True" -or $rule.Direction -ne "Inbound" -or $rule.Action -ne "Allow") { return $false }
    $profiles = "$($rule.Profile)" -replace '\s', ''
    if ($profiles -notin @("Domain,Private", "Private,Domain")) { return $false }
    $application = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule
    $ports = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $rule
    $addresses = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule
    return $application.Program -eq $Definition.program -and
        "$($ports.Protocol)" -eq $Definition.protocol -and "$($ports.LocalPort)" -eq "$($Definition.port)" -and
        "$($addresses.LocalAddress)" -eq $Address -and
        ("$($addresses.RemoteAddress)" -replace '/32$', '') -eq $Definition.remote_address
}

$missing = @($network.rules | Where-Object { -not (Test-EmberLanRule $_ $network.address) })
if ($missing.Count -eq 0) { Write-Host "Ember LAN firewall rules are ready."; return }
$administrator = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $administrator) {
    if ($Elevated) { throw "Windows administrator elevation was not granted." }
    Write-Host "Windows administrator approval is needed once for Ember's LAN firewall rules."
    $shellExecutable = (Get-Process -Id $PID).Path
    $scriptArguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"{0}"' -f $PSCommandPath), "-Config", ('"{0}"' -f $configPath), "-Elevated")
    $setup = Start-Process -FilePath $shellExecutable -ArgumentList $scriptArguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($setup.ExitCode -ne 0) { throw "LAN firewall setup failed. Run scripts/setup_lan.ps1 from Administrator PowerShell." }
    foreach ($definition in $network.rules) {
        if (-not (Test-EmberLanRule $definition $network.address)) { throw "A required Ember LAN firewall rule is unavailable." }
    }
    return
}
foreach ($definition in $missing) {
    $parameters = @{
        Name = $definition.name
        Enabled = "True"
        Direction = "Inbound"
        Action = "Allow"
        Profile = @("Private", "Domain")
        Program = $definition.program
        Protocol = $definition.protocol
        LocalPort = $definition.port
        LocalAddress = $network.address
        RemoteAddress = $definition.remote_address
        PolicyStore = "PersistentStore"
    }
    if (Get-NetFirewallRule -Name $definition.name -PolicyStore PersistentStore -ErrorAction SilentlyContinue) {
        Set-NetFirewallRule @parameters
    } else {
        New-NetFirewallRule @parameters -DisplayName $definition.name -Group "Ember LAN runtime" | Out-Null
    }
    if (-not (Test-EmberLanRule $definition $network.address)) { throw "An Ember LAN firewall rule failed verification." }
}
Write-Host "Ember LAN firewall rules installed for the configured listeners."
