"""Local-only CPU BGE ONNX adapter and bounded encoding ownership."""

import importlib
import math
import struct
from collections import OrderedDict
from collections.abc import Sequence
from concurrent.futures import Future
from hashlib import sha256
from pathlib import Path
from threading import Lock, RLock
from typing import Protocol, cast

from ember_memory_abi.common import canonical_object_sha256

from .errors import EmbeddingError
from .search_contracts import EmbeddingSpec, Encoder
from .workers import OwnedWorkers


class _Encoding(Protocol):
    ids: list[int]
    offsets: list[tuple[int, int]]


class _Tokenizer(Protocol):
    def encode(self, text: str, add_special_tokens: bool = True) -> _Encoding: ...


class _Options(Protocol):
    intra_op_num_threads: int
    inter_op_num_threads: int


class _OptionsFactory(Protocol):
    def __call__(self) -> _Options: ...


class _SessionFactory(Protocol):
    def __call__(
        self, path: str, *, sess_options: _Options, providers: list[str]
    ) -> "_Session": ...


class _Session(Protocol):
    def run(self, outputs: object, inputs: dict[str, object]) -> list[object]: ...


def file_sha256(path: Path) -> str:
    """Hash assets/snapshots in bounded blocks without logging their values."""
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def validate_vector(vector: bytes) -> None:
    """Require finite normalized little-endian 384-dimensional float32 vectors."""
    if len(vector) != 384 * 4:
        raise EmbeddingError()
    values = struct.unpack("<384f", vector)
    if (
        not all(math.isfinite(value) for value in values)
        or not 0.999 < sum(value * value for value in values) < 1.001
    ):
        raise EmbeddingError()


class BgeEncoder:
    """Load pinned local assets once; never download or consume CUDA/Qwen capacity."""

    def __init__(self, directory: Path, *, threads: int = 2) -> None:
        try:
            payload = (directory / "embedding.json").read_bytes()
            if len(payload) > 16384:
                raise EmbeddingError()
            self._spec = EmbeddingSpec.model_validate_json(payload)
            if (
                file_sha256(directory / "model.onnx") != self._spec.model_sha256
                or file_sha256(directory / "tokenizer.json") != self._spec.tokenizer_sha256
            ):
                raise EmbeddingError()
            onnxruntime = importlib.import_module("onnxruntime")
            from tokenizers import Tokenizer

            self._tokenizer = cast(
                _Tokenizer, Tokenizer.from_file(str(directory / "tokenizer.json"))
            )
            # ONNX's external Python boundary is narrowed immediately to the methods
            # used here. Providers/threads and pooling are fixed local CPU behavior.
            options = cast(_OptionsFactory, onnxruntime.SessionOptions)()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            self._session = cast(_SessionFactory, onnxruntime.InferenceSession)(
                str(directory / "model.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._lock = Lock()
        except Exception as error:
            # Tokenizer/ONNX initialization raises extension-defined exceptions.
            # Translate this setup boundary so invalid local assets degrade safely.
            raise EmbeddingError() from error

    @property
    def specification(self) -> EmbeddingSpec:
        """Return model/tokenizer hashes and exact mode/pooling/truncation identity."""
        return self._spec

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return original Unicode offsets without special tokens or text normalization."""
        return tuple(self._tokenizer.encode(text, add_special_tokens=False).offsets)

    def token_count(self, text: str) -> int:
        """Count actual embedding tokens including special tokens."""
        return len(self._tokenizer.encode(text).ids)

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        """Encode at most 32 bounded inputs with CLS pooling and L2 normalization.

        Prefix is applied only to queries. Oversized inputs fail before inference;
        build-time callers split explicitly with exact original span mappings.
        """
        import numpy as np

        if not texts or len(texts) > 32:
            raise EmbeddingError()
        encoded = [
            self._tokenizer.encode(
                (self._spec.query_prefix if query else self._spec.document_prefix) + text
            ).ids
            for text in texts
        ]
        if any(len(item) > 512 for item in encoded):
            raise EmbeddingError()
        length = max(map(len, encoded))
        ids = np.zeros((len(encoded), length), dtype=np.int64)
        mask = np.zeros_like(ids)
        for index, item in enumerate(encoded):
            ids[index, : len(item)] = item
            mask[index, : len(item)] = 1
        try:
            with self._lock:
                outputs = self._session.run(
                    None,
                    {
                        "input_ids": ids,
                        "attention_mask": mask,
                        "token_type_ids": np.zeros_like(ids),
                    },
                )
        except Exception as error:
            # Vendor exception classes are extension-defined. Translate only this
            # inference boundary; callers receive a safe typed degradation reason.
            raise EmbeddingError() from error
        array = np.asarray(outputs[0], dtype=np.float32)
        if array.shape != (len(encoded), length, 384):
            raise EmbeddingError()
        pooled = array[:, 0, :]
        pooled /= np.linalg.norm(pooled, axis=1, keepdims=True)
        result = tuple(row.astype("<f4").tobytes() for row in pooled)
        for vector in result:
            validate_vector(vector)
        return result


class EncodingPool:
    """One CPU owner; late encodes retain their bounded slot and are never detached."""

    def __init__(self, encoder: Encoder | None) -> None:
        self.encoder = encoder
        self.workers = OwnedWorkers(1, max_pending=2, name="ember-query-encoding")
        self._guard = RLock()
        self._cache: OrderedDict[str, Future[bytes]] = OrderedDict()
        self.error_count = 0

    def query(self, text: str) -> Future[bytes]:
        """Submit one query encoding; reject missing encoder or full admission immediately."""
        if self.encoder is None:
            raise EmbeddingError()
        encoder = self.encoder
        key = canonical_object_sha256(
            {"profile": encoder.specification.model_dump(mode="json"), "text": text}
        )
        with self._guard:
            existing = self._cache.get(key)
            if existing is not None:
                self._cache.move_to_end(key)
                return existing

            def encode_query() -> bytes:
                vectors = encoder.encode((text,), query=True)
                if len(vectors) != 1:
                    raise EmbeddingError()
                validate_vector(vectors[0])
                return vectors[0]

            future = self.workers.submit(encode_query)
            self._cache[key] = future
            while len(self._cache) > 128:
                self._cache.popitem(last=False)

            def completed(job: Future[bytes]) -> None:
                # Consume late failures even if every waiting caller timed out.
                if job.cancelled() or job.exception() is not None:
                    with self._guard:
                        self.error_count += 1
                        if self._cache.get(key) is job:
                            self._cache.pop(key, None)

            future.add_done_callback(completed)
            return future

    def invalidate(self) -> None:
        """Drop cached query vectors on policy changes; accepted work stays owned."""
        with self._guard:
            self._cache.clear()

    def close(self) -> None:
        """Drain accepted work before releasing the encoder; composition calls off-loop."""
        self.workers.shutdown()
