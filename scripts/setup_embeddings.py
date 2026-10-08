"""Explicit pinned BGE-small setup; startup/query code never downloads assets."""

import argparse
import urllib.request
from hashlib import sha1
from pathlib import Path

from pydantic import BaseModel

from ember_memory_engine.embedding import file_sha256
from ember_memory_engine.search_contracts import EmbeddingSpec

REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_SHA = "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35"
TOKENIZER_GIT_SHA = "688882a79f44442ddc1f60d70334a7ff5df0fb47"


class Arguments(BaseModel):
    """Explicit setup destination; no runtime acquisition policy is implied."""

    output: Path


def main() -> int:
    """Download only named official assets and verify pinned integrity before publication."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/models/bge-small-en-v1.5"))
    args = Arguments.model_validate(vars(parser.parse_args()))
    directory = args.output
    directory.mkdir(parents=True, exist_ok=True)
    for remote, name in (("onnx/model.onnx", "model.onnx"), ("tokenizer.json", "tokenizer.json")):
        target = directory / name
        if not target.exists():
            staging = directory / (name + ".part")
            urllib.request.urlretrieve(
                f"https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/{REVISION}/{remote}",
                staging,
            )
            staging.replace(target)
        print(f"Installed {name}.", flush=True)
    if file_sha256(directory / "model.onnx") != MODEL_SHA:
        raise ValueError("Pinned model checksum failed.")
    tokenizer = (directory / "tokenizer.json").read_bytes()
    if (
        sha1(b"blob " + str(len(tokenizer)).encode() + b"\0" + tokenizer).hexdigest()
        != TOKENIZER_GIT_SHA
    ):
        raise ValueError("Pinned tokenizer checksum failed.")
    spec = EmbeddingSpec(
        model_revision=REVISION,
        tokenizer_revision=REVISION,
        model_sha256=MODEL_SHA,
        tokenizer_sha256=file_sha256(directory / "tokenizer.json"),
    )
    (directory / "embedding.json").write_text(
        spec.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print("Pinned CPU embedding setup verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
