# Explicit optional setup; the running provider never downloads voices.
[CmdletBinding()]
param([string[]]$Voice = @("Juniper", "Zoey", "Easton", "Maeve", "Freya", "Alfie"))
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv/Scripts/python.exe"
$model = Join-Path $root ".local/models/phonon-31m-en"
if (-not (Test-Path (Join-Path $model "model.q8.gguf"))) {
    throw "Install the CPU Phonon 31M model before adding voices."
}
$setup = @'
import hashlib
import json
import shutil
import sys
from pathlib import Path
from huggingface_hub import hf_hub_download

revision = "0ee17205f254b5abc43b266c8a0f215565c352f7"
repo = "gradium/phonon-voices"
catalog = Path(hf_hub_download(repo, "voices.jsonl", revision=revision))
rows = {row["name"]: row for line in catalog.read_text(encoding="utf-8").splitlines()
        if line.strip() for row in [json.loads(line)] if row.get("language") == "en"}
names = sys.argv[2:]
if not names or any(name not in rows or Path(name).name != name for name in names):
    raise SystemExit("Choose named English voices from the pinned Gradium catalog.")
target = Path(sys.argv[1]) / "voices"
target.mkdir(exist_ok=True)
receipts = []
for name in names:
    filename = rows[name]["filename"]
    source = Path(hf_hub_download(repo, filename, revision=revision))
    destination = target / (name + ".safetensors")
    shutil.copyfile(source, destination)
    receipts.append({"name": name, "source": filename,
                     "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
(target / "setup-receipt.json").write_text(
    json.dumps({"repository": repo, "revision": revision, "license": "Apache-2.0",
                "voices": receipts}, indent=2) + "\n", encoding="utf-8")
print("Installed voices: " + ", ".join(names))
'@
$setup | & $python - $model @Voice
if ($LASTEXITCODE -ne 0) { throw "Pinned Phonon voice setup failed." }
Write-Output "Restart Ember to load the installed voices. No deployment settings were changed."
