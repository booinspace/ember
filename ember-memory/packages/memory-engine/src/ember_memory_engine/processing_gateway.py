"""Narrow optional HTTP adapter to Ember's gateway; the engine never owns inference admission."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import Field, JsonValue, ValidationError

from ember_memory_abi.common import CanonicalId, ContractModel

from .processing_contracts import ClaimBatch, ExtractionSpec, ModelWindow, ProcessingError
from .processing_prompt import INSTRUCTIONS


class _Health(ContractModel):
    """Narrow consumed server fields while allowing independently versioned diagnostics."""

    model_config = {"extra": "ignore", "frozen": True, "strict": True}
    version: Literal["0.1.0", "0.2.0"]
    status: str
    model: str
    digest: str
    supported_workloads: tuple[str, ...]
    journal_status: str


class _Event(ContractModel):
    """The gateway owns this wire protocol; the adapter consumes only required fields."""

    model_config = {"extra": "ignore", "frozen": True, "strict": True}
    request_id: CanonicalId
    context_id: CanonicalId
    sequence: Annotated[int, Field(ge=0)]
    kind: Literal["start", "progress", "text", "tools", "end", "error"]
    text: str = Field(default="", repr=False)
    code: str | None = None


class GatewayExtractionModel:
    """Use scoped P3/P4 credentials and validated completed JSON through one gateway."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        specification: ExtractionSpec,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        origin = urlsplit(endpoint)
        if (
            origin.scheme != "http"
            or origin.hostname not in {"localhost", "127.0.0.1", "::1"}
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or origin.path not in {"", "/"}
            or origin.port is None
        ):
            raise ProcessingError("gateway_origin_invalid")
        self._specification = specification
        self.client = client or httpx.AsyncClient(base_url=endpoint, timeout=190, trust_env=False)
        self.headers = {"Authorization": "Bearer " + token}

    @property
    def specification(self) -> ExtractionSpec:
        """Return the exact pinned profile required by the durable job."""
        return self._specification

    async def _lines(self, response: httpx.Response) -> AsyncIterator[bytes]:
        buffer = bytearray()
        async for block in response.aiter_bytes():
            buffer.extend(block)
            if len(buffer) > 1024 * 1024:
                raise ProcessingError("gateway_frame_too_large")
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if line:
                    yield bytes(line)
        if buffer:
            yield bytes(buffer)

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        """Verify model/workload health; commit nothing before an explicit validated end."""
        import json

        try:
            response = await self.client.get("/health", headers=self.headers, timeout=5)
            response.raise_for_status()
            health = _Health.model_validate_json(response.content)
            spec = self.specification
            from hashlib import sha256

            from ember_memory_abi.common import canonical_object_sha256

            if spec.extractor != "ember.atomic-claims@0.1.2":
                raise ProcessingError("processing_extractor_changed")
            if spec.prompt_sha256 != sha256(
                INSTRUCTIONS.encode()
            ).hexdigest() or spec.output_schema_sha256 != canonical_object_sha256(
                ClaimBatch.model_json_schema()
            ):
                raise ProcessingError("processing_prompt_or_schema_changed")
            if (
                health.model != spec.model
                or health.digest.removeprefix("sha256:") != spec.model_digest
            ):
                raise ProcessingError("gateway_model_pin_mismatch")
            if (
                spec.workload not in health.supported_workloads
                or health.journal_status != "available"
                or health.status not in {"ready", "busy"}
            ):
                raise ProcessingError("gateway_background_unavailable")
            request_id, context_id = uuid4(), uuid4()
            body: dict[str, JsonValue] = {
                "version": "0.2.0",
                "request_id": str(request_id),
                "context_id": str(context_id),
                "workload": spec.workload,
                "model": spec.model,
                "output": "json",
                "output_schema": ClaimBatch.model_json_schema(),
                "max_output_tokens": spec.output_tokens,
                "deadline_at": (datetime.now(UTC) + timedelta(seconds=180))
                .isoformat()
                .replace("+00:00", "Z"),
                "messages": [
                    {"role": "system", "content": INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "role": window.reference.role,
                                "authority": window.reference.authority,
                                "branch": window.reference.branch,
                                "text": window.text,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            }
            output: list[str] = []
            size = 0
            sequence = 0
            started = False
            async with self.client.stream(
                "POST", "/v1/generate", headers=self.headers, json=body
            ) as stream:
                if stream.status_code != 200:
                    raise ProcessingError("gateway_background_rejected")
                async for line in self._lines(stream):
                    event = _Event.model_validate_json(line)
                    if (
                        event.request_id != request_id
                        or event.context_id != context_id
                        or event.sequence != sequence
                    ):
                        raise ProcessingError("gateway_stream_identity_or_order")
                    if event.kind == "error":
                        if event.code in {
                            "invalid_structured_output",
                            "structured_schema_mismatch",
                            "structured_output_too_large",
                        }:
                            raise ProcessingError("gateway_structured_output_invalid")
                        raise ProcessingError("gateway_generation_failed")
                    if (not started and event.kind != "start") or (
                        started and event.kind == "start"
                    ):
                        raise ProcessingError("gateway_stream_order")
                    started = True
                    sequence += 1
                    if event.kind == "tools":
                        raise ProcessingError("unexpected_extraction_tool_output")
                    if event.kind == "text":
                        output.append(event.text)
                        size += len(event.text.encode("utf-8"))
                        if size > 128 * 1024:
                            raise ProcessingError("extraction_output_too_large")
                    if event.kind == "end":
                        try:
                            return ClaimBatch.model_validate_json("".join(output))
                        except ValidationError as error:
                            raise ProcessingError("gateway_structured_output_invalid") from error
            raise ProcessingError("gateway_stream_incomplete")
        except httpx.HTTPError as error:
            raise ProcessingError("gateway_transport_failed") from error
        except ValidationError as error:
            raise ProcessingError("gateway_protocol_invalid") from error

    async def aclose(self) -> None:
        """Close only this transport; it cannot stop/unload the model or gateway."""
        await self.client.aclose()
