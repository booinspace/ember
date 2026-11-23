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
    ProcessingDeferred,
    ProcessingError,
    WindowRef,
)
from ember_memory_engine.processing_gateway import GatewayExtractionModel


def gateway_health(*, digest: str = "a" * 64, journal: str = "available") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "version": "0.2.0",
            "status": "ready",
            "model": "synthetic",
            "digest": digest,
            "supported_workloads": ["P0", "P3", "P4"],
            "journal_status": journal,
        },
    )


def synthetic_window() -> ModelWindow:
    text = "A synthetic note."
    return ModelWindow(
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


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "incomplete",
        "identity",
        "schema",
        "pin",
        "journal",
        "gateway_schema",
        "gateway_json",
        "gateway_limit",
        "stream_deferral",
        "generation",
        "transport",
        "event_schema",
    ],
)
def test_extraction_transport_never_accepts_invalid_or_incomplete_output(fault: str | None) -> None:
    async def run() -> None:
        spec = ExtractionSpec(model="synthetic", model_digest="a" * 64)
        observed: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return gateway_health(
                    digest="b" * 64 if fault == "pin" else "a" * 64,
                    journal="failed" if fault == "journal" else "available",
                )
            if fault == "transport":
                raise httpx.ConnectError("Synthetic connection failure", request=request)
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
            gateway_errors = {
                "gateway_schema": "structured_schema_mismatch",
                "gateway_json": "invalid_structured_output",
                "gateway_limit": "structured_output_too_large",
                "stream_deferral": "idle_capture_deferred",
                "generation": "upstream_generation_failed",
            }
            if fault in gateway_errors:
                events = [
                    dict(identity, sequence=0, kind="start"),
                    dict(identity, sequence=1, kind="error", code=gateway_errors[fault]),
                ]
            if fault == "event_schema":
                events[1]["kind"] = "synthetic_unknown_kind"
            if fault != "incomplete":
                events.append(dict(identity, sequence=2, kind="end"))
            return httpx.Response(
                200, content="".join(json.dumps(event) + "\n" for event in events)
            )

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://127.0.0.1:11435"
        )
        model = GatewayExtractionModel("http://127.0.0.1:11435", "b" * 40, spec, client=client)
        window = synthetic_window()
        try:
            if fault is not None:
                expected = {
                    "schema": "gateway_structured_output_invalid",
                    "gateway_schema": "gateway_structured_output_invalid",
                    "gateway_json": "gateway_structured_output_invalid",
                    "gateway_limit": "gateway_structured_output_invalid",
                    "stream_deferral": "idle_capture_deferred",
                    "generation": "gateway_generation_failed",
                    "transport": "gateway_transport_failed",
                    "event_schema": "gateway_protocol_invalid",
                }
                with pytest.raises(ProcessingError, match=expected.get(fault)) as caught:
                    await model.extract(window)
                assert isinstance(caught.value, ProcessingDeferred) == (fault == "stream_deferral")
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


def test_extraction_checks_idle_after_health_and_pins_context() -> None:
    async def run() -> None:
        context_id = uuid4()
        allowed = True
        defer_on_health = True
        request_ids: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal allowed
            if request.url.path == "/health":
                allowed = not defer_on_health
                return gateway_health()
            assert request.url.path == "/v1/generate" and allowed
            body = json.loads(request.content)
            assert body["context_id"] == str(context_id)
            request_ids.append(body["request_id"])
            identity = {"request_id": body["request_id"], "context_id": body["context_id"]}
            events = [
                dict(identity, sequence=0, kind="start"),
                dict(identity, sequence=1, kind="text", text='{"claims":[]}'),
                dict(identity, sequence=2, kind="end"),
            ]
            return httpx.Response(200, content="\n".join(map(json.dumps, events)))

        endpoint = "http://127.0.0.1:11435"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url=endpoint
        ) as client:
            model = GatewayExtractionModel(
                endpoint,
                "b" * 40,
                ExtractionSpec(model="synthetic", model_digest="a" * 64, workload="P3"),
                client=client,
                context_id=context_id,
                should_continue=lambda: allowed,
            )
            window = synthetic_window()
            with pytest.raises(ProcessingDeferred, match="idle_capture_deferred"):
                await model.extract(window)
            assert not request_ids
            defer_on_health = False
            assert not (await model.extract(window)).claims
            assert not (await model.extract(window)).claims
            assert len(set(request_ids)) == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "body", "deferred"),
    [
        (409, b'{"code": "idle_capture_deferred"}', True),
        (409, b'{"code": "idle_capture_deferred", "extra": 0}', False),
        (409, b'{"code": 0, "code": "idle_capture_deferred"}', False),
        (409, b'{"code": "idle_capture_deferred"' + b" " * 1024 + b"}", False),
        (409, b"idle_capture_deferred", False),
        (503, b'{"code": "idle_capture_deferred"}', False),
    ],
)
def test_extraction_conflict_defers_only_for_bounded_exact_code(
    status: int, body: bytes, deferred: bool
) -> None:
    async def run() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/health":
                return gateway_health()
            return httpx.Response(status, content=body)

        endpoint = "http://127.0.0.1:11435"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url=endpoint
        ) as client:
            model = GatewayExtractionModel(
                endpoint,
                "b" * 40,
                ExtractionSpec(model="synthetic", model_digest="a" * 64, workload="P3"),
                client=client,
            )
            expected = "idle_capture_deferred" if deferred else "gateway_background_rejected"
            with pytest.raises(ProcessingError, match=expected) as caught:
                await model.extract(synthetic_window())
            assert isinstance(caught.value, ProcessingDeferred) == deferred

    asyncio.run(run())
