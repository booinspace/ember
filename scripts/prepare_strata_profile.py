"""Create a local Ember deployment from an installed Strata runner. No downloads."""

import argparse
import json
import re
import tempfile
from hashlib import file_digest, sha256
from pathlib import Path

from ember_agent.runtime.config import load_config
from ember_agent.runtime.strata_runtime import (
    _engine_paths,  # pyright: ignore[reportPrivateUsage]
    _RunnerProfile,  # pyright: ignore[reportPrivateUsage]
)

_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Pin a generated Strata JSON and create a separate loopback Ember profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runner", type=Path, help="Strata-generated runner JSON")
    parser.add_argument("python", type=Path, help="Installed Strata Python executable")
    parser.add_argument("server", type=Path, help="Installed Strata serve/server.py")
    parser.add_argument("--context", type=int, default=131072)
    args = parser.parse_args()
    if args.context <= 4096:
        parser.error("Context must exceed the 4096-token output reserve.")
    root = _ROOT / ".local/config"
    runner, deployment = (
        root / name for name in ("runner.ember-strata.json", "deployment.strata.toml")
    )
    if runner.exists() or deployment.exists():
        parser.error("Move both existing Strata profile files aside before regenerating.")

    python, server = (path.resolve(strict=True) for path in (args.python, args.server))
    raw = json.loads(args.runner.read_text(encoding="utf-8-sig"))
    for key in ("log", "gpus_asked", "args_written"):
        raw.pop(key, None)
    if raw.get("mtp") is False:
        raw.setdefault("drafting", "off")
    raw["args"][raw["args"].index("--max-context") + 1] = str(args.context)
    raw.update(host="127.0.0.1", port=18110, open_browser=False, lazy_load=False, idle_unload_s=0.0)
    profile = _RunnerProfile.model_validate({**raw, "ember_artifacts": {str(server): "pending"}})
    files, directories = _engine_paths(profile, args.context)
    files.update((server, Path(profile.exe)))
    tokenizer = Path(profile.tokenizer)
    directories.add(tokenizer)
    files.update(tokenizer / name for name in ("vocab.json", "merges.txt", "token_type.json"))
    for directory in sorted(directories):
        if not directory.is_dir():
            parser.error(f"Missing model directory: {directory}")
        for folder, subdirs, names in directory.walk():
            if any((folder / name).is_junction() for name in subdirs):
                parser.error(f"Junction found under {directory}")
            files.update(folder / name for name in names)
    print("Hashing installed Strata artifacts. This may take several minutes.", flush=True)
    pins: dict[str, str] = {}
    for artifact in sorted({file.resolve(strict=True) for file in files}):
        with artifact.open("rb") as stream:
            pins[str(artifact)] = file_digest(stream, "sha256").hexdigest()
    raw["ember_artifacts"] = pins
    pinned = (json.dumps(raw, indent=2) + "\n").encode()
    digest = sha256(pinned).hexdigest()
    template = (_ROOT / "ember-agent/config/deployment.example.toml").read_text(encoding="utf-8")
    # ponytail: Start from Ember's checked-in template, not private deployment state.
    overrides = {
        "": {"active_profile": "strata"},
        "paths": {"data_root": "../data-strata", "runtime_root": "../runtime/strata"},
        "model": {
            "tag": profile.model_name,
            "expected_digest": f"sha256:{digest}",
            "context_tokens": args.context,
            "max_input_tokens": args.context - 4096,
            "output_reserve_tokens": 4096,
        },
        "services.ollama": {"mode": "disabled"},
        "services.livekit": {
            "mode": "managed",
            "endpoint": "ws://127.0.0.1:17880",
            "tcp_port": 17881,
            "udp_port": 17882,
            "executable": "../tools/livekit/1.13.9/livekit-server.exe",
            "credentials_file": "../runtime/strata/livekit-credentials.json",
        },
        "services.tts": {"mode": "disabled"},
        "gateway": {"endpoint": "http://127.0.0.1:18111"},
        "browser": {"assets": "../../ember-agent/frontend/dist"},
        "inference": {"backend": "strata"},
        "inference.strata": {
            "endpoint": "http://127.0.0.1:18110",
            "python_executable": str(python),
            "server_script": str(server),
            "runner_config": str(runner),
            "runner_config_sha256": digest,
            "startup_timeout_seconds": 300.0,
        },
    }
    chunks = re.split(r"(?m)^\[([^\[\]\n]+)\][ \t]*$", template)
    output: list[str] = []
    for section, body in zip(["", *chunks[1::2]], chunks[::2], strict=True):
        values = overrides.pop(section, {})
        if section:
            output.append(f"[{section}]")
        output.extend(
            line
            for line in body.strip().splitlines()
            if line.partition("=")[0].strip() not in values
        )
        output.extend(f"{key} = {json.dumps(value)}" for key, value in values.items())
        output.append("")
    for section, values in overrides.items():
        output.append(f"[{section}]")
        output.extend(f"{key} = {json.dumps(value)}" for key, value in values.items())
        output.append("")
    text = "\n".join(output)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=root, suffix=".toml", delete=False) as stream:
        check = Path(stream.name)
        stream.write(text.encode())
    try:
        load_config(check, require_model_pin=True)
    finally:
        check.unlink()
    with runner.open("xb") as stream:
        stream.write(pinned)
    try:
        with deployment.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    except OSError:
        runner.unlink()
        raise
    print(f"Created {deployment.name}: {profile.model_name}, {args.context} tokens.")


if __name__ == "__main__":
    main()
