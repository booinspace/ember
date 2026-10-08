"""Extraction adapter checks pinning, completed structured output and isolated contexts."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_engine.processing_contracts import (
    ExtractionSpec,
    ModelWindow,
    ProcessingError,
    WindowRef,
)
from ember_memory_engine.processing_gateway import GatewayExtractionModel


@pytest.mark.parametrize("fault", [None, "incomplete", "identity", "schema", "pin", "journal"])
def test_extraction_transport_never_accepts_invalid_or_incomplete_output(fault: str | None) -> None:
    async def run() -> None:
        spec = ExtractionSpec(model="synthetic", model_digest="a" * 64)
        observed: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return httpx.Response(
                    200,
                    json={
                        "version": "0.2.0",
                        "status": "ready",
                        "model": "synthetic",
                        "digest": "b" * 64 if fault == "pin" else "a" * 64,
                        "supported_workloads": ["P0", "P3", "P4"],
                        "journal_status": "failed" if fault == "journal" else "available",
                    },
                )
            body = json.loads(request.content)
            observed.append(body)
            identity = {"request_id": body["request_id"], "context_id": body["context_id"]}
            if fault == "identity":
                identity["context_id"] = str(uuid4())
            events = [
                dict(identity, sequence=0, kind="start"),
                dict(
                    identity,
                    sequence=1,
                    kind="text",
                    text=('{"wrong":[]}' if fault == "schema" else '{"claims":[]}'),
                ),
            ]
            if fault != "incomplete":
                events.append(dict(identity, sequence=2, kind="end"))
            return httpx.Response(
                200, content="".join(json.dumps(event) + "\n" for event in events)
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://127.0.0.1:11435"
        )
        model = GatewayExtractionModel("http://127.0.0.1:11435", "b" * 40, spec, client=client)
        text = "A synthetic note."
        window = ModelWindow(
            reference=WindowRef(
                source=SourceRef(
                    source_store_id=uuid4(),
                    record_kind="document",
                    record_id=uuid4(),
                    record_revision=1,
                    part_id=uuid4(),
                ),
                source_sha256=source_text_sha256(text),
                start=0,
                end=len(text),
                text_sha256=source_text_sha256(text),
                role="user",
                authority="summary",
                branch="not_applicable",
            ),
            text=text,
        )
        try:
            if fault is not None:
                with pytest.raises(ProcessingError):
                    await model.extract(window)
            else:
                assert (await model.extract(window)).claims == ()
                assert (await model.extract(window)).claims == ()
                assert len(observed) == 2
                assert observed[0]["context_id"] != observed[1]["context_id"]
                assert observed[0]["request_id"] != observed[1]["request_id"]
                assert observed[0]["workload"] == "P4"
                assert observed[0]["output_schema"]["required"] == ["claims"]
            if fault in {"pin", "journal"}:
                assert observed == []
        finally:
            await model.aclose()

    asyncio.run(run())
