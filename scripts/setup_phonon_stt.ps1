# Explicit setup only; startup never downloads speech dependencies or weights.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv/Scripts/python.exe"
$serviceRoot = Join-Path $root ".local/tools/phonon-stt"
$servicePython = Join-Path $serviceRoot ".venv/Scripts/python.exe"
$fermion = Join-Path $serviceRoot ".venv/Scripts/fermion.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Install Ember's Python environment first."
}
if (-not (Test-Path -LiteralPath $servicePython -PathType Leaf)) {
    & $python -m venv (Join-Path $serviceRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create Phonon environment." }
}
& $servicePython -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.9.1'
if ($LASTEXITCODE -ne 0) { throw "CPU PyTorch installation failed." }
& $servicePython -m pip install 'fermion-research==0.2.15' 'safetensors==0.8.0' 'soundfile==0.14.0' 'scipy==1.18.1' 'zstandard==0.25.0'
if ($LASTEXITCODE -ne 0) { throw "Phonon CPU dependencies failed to install." }
$verify = 'import torch; from fermion._catalog import SPEECH_PROFILES; assert torch.version.cuda is None; assert SPEECH_PROFILES["five-value"]["fallback"]["sha256"] == "98125795b6dda72f5c6eee9ba33d19815df65dcb18b50a357bf9f73c9935309e"'
& $servicePython -c $verify
if ($LASTEXITCODE -ne 0) { throw "CPU runtime or expected model pin did not validate." }
$oldCache = $env:FERMION_CACHE_DIR
$oldCuda = $env:CUDA_VISIBLE_DEVICES
try {
    $env:FERMION_CACHE_DIR = Join-Path $serviceRoot "cache"
    $env:CUDA_VISIBLE_DEVICES = "-1"
    & $fermion transcribe phonon-2 --download-only
    if ($LASTEXITCODE -ne 0) { throw "Verified Phonon model setup failed." }
} finally {
    $env:FERMION_CACHE_DIR = $oldCache
    $env:CUDA_VISIBLE_DEVICES = $oldCuda
}
Write-Output "CPU Phonon-2 installed. Configure the managed endpoint before starting Ember."
