# Run on the Kokoro host while its loopback service is ready.
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[a-z][fm]_[a-z0-9_]{1,48}$')] [string]$Name,
    [Parameter(Mandatory)] [ValidateLength(1, 128)] [string]$Blend,
    [ValidateRange(1, 65535)] [int]$Port = 8880
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$voices = Join-Path $root ".local/tools/kokoro-fastapi/api/src/voices/v1_0"
if (-not (Test-Path -LiteralPath $voices -PathType Container)) {
    throw "Kokoro voices are missing. Install them with scripts/setup_speech.ps1."
}
$destination = Join-Path $voices "$Name.pt"
if (Test-Path -LiteralPath $destination) { throw "Voice '$Name' already exists. Choose another name." }
$temporary = Join-Path $voices ([IO.Path]::GetRandomFileName())
try {
    Invoke-WebRequest -UseBasicParsing -Method Post -ContentType 'application/json' `
        -Uri "http://127.0.0.1:$Port/v1/audio/voices/combine" `
        -Body (ConvertTo-Json -InputObject $Blend -Compress) -OutFile $temporary -TimeoutSec 30
    if ((Get-Item -LiteralPath $temporary).Length -eq 0) { throw "Kokoro returned an empty voice pack." }
    [IO.File]::Move($temporary, $destination)
}
finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
Write-Output "Saved voice '$Name'. Restart Ember to refresh its voice picker."
