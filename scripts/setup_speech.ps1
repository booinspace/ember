# Explicit setup only. Runtime startup never installs packages or downloads models.
[CmdletBinding()]
param([string]$Uv = "uv")

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$emberPython = Join-Path $workspaceRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $emberPython -PathType Leaf)) {
    throw "Install the root environment with uv sync --locked before speech setup."
}
$kokoroRoot = Join-Path $workspaceRoot ".local/tools/kokoro-fastapi"
$kokoroRevision = "61797914e5f8da0baf1d0b97d9d0b15c66625615"
if (-not (Test-Path -LiteralPath $kokoroRoot)) {
    New-Item -ItemType Directory -Force (Split-Path -Parent $kokoroRoot) | Out-Null
    & git clone https://github.com/remsky/Kokoro-FastAPI.git $kokoroRoot
    if ($LASTEXITCODE -ne 0) { throw "Kokoro checkout failed." }
    & git -C $kokoroRoot checkout --detach $kokoroRevision
    if ($LASTEXITCODE -ne 0) { throw "Pinned Kokoro checkout failed." }
}
$installedRevision = & git -C $kokoroRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $installedRevision -ne $kokoroRevision) {
    throw "Existing Kokoro checkout differs from the setup pin; inspect it before updating."
}
Push-Location $kokoroRoot
try {
    # Upstream's lock has stale project-version metadata; uv updates that metadata.
    # The installed provider retains its own lock/environment outside Ember packages.
    & $Uv sync --extra cpu --python $emberPython
    if ($LASTEXITCODE -ne 0) { throw "Kokoro CPU dependency installation failed." }
    $kokoroPython = Join-Path $kokoroRoot ".venv/Scripts/python.exe"
    & $kokoroPython docker/scripts/download_model.py --output api/src/models/v1_0
    if ($LASTEXITCODE -ne 0) { throw "Kokoro model installation failed." }
}
finally { Pop-Location }

$kokoroModels = (Join-Path $kokoroRoot "api/src/models").Replace("\", "/")
$kokoroVoices = (Join-Path $kokoroRoot "api/src/voices/v1_0").Replace("\", "/")
$providerSettings = @"
USE_GPU=false
DEVICE_TYPE=cpu
MODEL_DIR="$kokoroModels"
VOICES_DIR="$kokoroVoices"
DEFAULT_VOICE=af_alloy
ENABLE_WEB_PLAYER=false
CORS_ENABLED=false
ENABLE_DEBUG_ENDPOINTS=false
MAX_INPUT_LENGTH=1600
TARGET_MIN_TOKENS=25
TARGET_MAX_TOKENS=80
ABSOLUTE_MAX_TOKENS=256
"@
[IO.File]::WriteAllText((Join-Path $kokoroRoot ".env"), $providerSettings, [Text.UTF8Encoding]::new($false))

Push-Location $workspaceRoot
try {
    @'
from huggingface_hub import snapshot_download
snapshot_download(
    "Systran/faster-whisper-small.en",
    revision="d1d751a5f8271d482d14ca55d9e2deeebbae577f",
    local_dir=".local/models/faster-whisper-small.en",
    allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.*"],
)
'@ | & $emberPython -
    if ($LASTEXITCODE -ne 0) { throw "Whisper model installation failed." }
}
finally { Pop-Location }
Write-Output "CPU speech installed. Configure audio and managed Kokoro before launching Ember."
