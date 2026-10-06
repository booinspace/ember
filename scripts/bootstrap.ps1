param(
    [string]$Uv = "uv",
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$workspaceRoot = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $workspaceRoot ".cache\uv"
$env:UV_PYTHON_DOWNLOADS = "never"

# Setup can install packaging dependencies. Runtime never installs or downloads.
foreach ($project in @(".", "character-memory", "character-agent")) {
    Push-Location (Join-Path $workspaceRoot $project)
    try {
        $arguments = @("sync", "--locked", "--python", $Python)
        if ($project -eq "character-memory") { $arguments += "--all-packages" }
        elseif ($project -eq "character-agent") { $arguments += @("--extra", "local-deployment") }
        & $Uv @arguments
        if ($LASTEXITCODE -ne 0) { throw "uv sync failed for $project" }
    }
    finally { Pop-Location }
}

$checkPython = Join-Path $workspaceRoot ".venv\Scripts\python.exe"
& $checkPython (Join-Path $PSScriptRoot "check.py")
if ($LASTEXITCODE -ne 0) { throw "Quality gate failed" }

Write-Output "Phase -1 setup complete. No models or runtime services were started."
