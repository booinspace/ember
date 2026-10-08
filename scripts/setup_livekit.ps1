param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.13.9"
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$installRoot = Join-Path $projectRoot ".local\tools\livekit\$Version"
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
$assetName = "livekit_" + $Version + "_windows_amd64.zip"
$releaseBase = "https://github.com/livekit/livekit/releases/download/v$Version/"
$zipPath = Join-Path $installRoot $assetName
$manifestPath = Join-Path $installRoot "checksums.txt"
Invoke-WebRequest -Uri ($releaseBase + "checksums.txt") -OutFile $manifestPath
$checksumLine = Get-Content -LiteralPath $manifestPath | Where-Object { $_ -match ([regex]::Escape($assetName) + '$') }
if (@($checksumLine).Count -ne 1) { throw "The release checksum entry is missing or ambiguous." }
$expectedHash = ($checksumLine -split '\s+')[0]
Invoke-WebRequest -Uri ($releaseBase + $assetName) -OutFile $zipPath
if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ine $expectedHash) {
    throw "LiveKit release checksum verification failed."
}
Expand-Archive -LiteralPath $zipPath -DestinationPath $installRoot -Force
$executablePath = Join-Path $installRoot "livekit-server.exe"
if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
    throw "The release did not contain livekit-server.exe."
}
Write-Output ("Installed verified LiveKit Server " + $Version + ": " + $executablePath)

