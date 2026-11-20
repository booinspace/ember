"""Opt-in real Qwen evidence using queued background admission, preserving ongoing ingest."""

import asyncio
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from ember_agent.character.core import CharacterCore, prompt_cost
from ember_agent.inference.client import HttpGatewayClient
from ember_agent.inference.contracts import GatewayError
from ember_memory_abi.archive import (
    ActorAttribution,
    ContentPart,
    Conversation,
    ImportCursor,
    Message,
    NormalizedArchiveBatch,
    ProcessingSpecRef,
)
from ember_memory_abi.common import timestamp_from_datetime
from ember_memory_abi.evidence import SourceRef, source_text_sha256
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import ensure_subject
from ember_memory_engine.consolidation import NativeAcceptanceRequest, NativeAcceptanceService
from ember_memory_engine.database import row_text
from ember_memory_engine.models import SourceKey, SourceKeyAssignment
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import JobRequest, native_specification
from ember_memory_engine.processing_gateway import GatewayExtractionModel


@pytest.mark.integration
def test_real_qwen_native_acceptance_and_query_independent_profile(tmp_path: Path) -> None:
    endpoint = os.getenv("EMBER_PHASE_SEVEN_GATEWAY")
    token_path = os.getenv("EMBER_PHASE_SEVEN_TOKEN_FILE")
    if not endpoint or not token_path:
        pytest.skip(
            "Set explicit background gateway/token paths; never start or reserve a gateway here."
        )
    foreground_path = os.getenv("EMBER_PHASE_SEVEN_FOREGROUND_TOKEN_FILE")
    if not foreground_path:
        pytest.skip("Supply an explicit foreground token for the idle P0 text probe.")
    token = Path(token_path).read_text(encoding="utf-8").strip()
    foreground_token = Path(foreground_path).read_text(encoding="utf-8").strip()
    with CanonicalEngine(tmp_path / "synthetic-memory") as engine:
        character = engine.create_character().store_id
        subject = ensure_subject(engine, character)
        text = (
            "Please call me Rowan. My pronouns are they/them. I prefer concise replies. "
            "My timezone is Europe/London."
        )
        stamp = timestamp_from_datetime(datetime.now(UTC))
        conversation = SourceRef(
            source_store_id=character,
            record_kind="conversation",
            record_id=uuid4(),
            record_revision=1,
        )
        message = Message(
            source=SourceRef(
                source_store_id=character,
                record_kind="message",
                record_id=uuid4(),
                record_revision=1,
            ),
            recorded_at=stamp,
            conversation=conversation,
            actor=ActorAttribution(role="user", actor_id=subject.entity_id),
            channel="final",
            parts=(
                ContentPart(
                    part_id=uuid4(),
                    kind="text",
                    status="available",
                    text=text,
                    content_sha256=source_text_sha256(text),
                ),
            ),
        )
        writer = engine.archive(character)
        batch = NormalizedArchiveBatch(
            source_store_id=character,
            source_namespace=writer.namespace,
            idempotency_key="real-native",
            processing_spec=ProcessingSpecRef(
                name="ember.native_delivery", version="0.2.0", specification_sha256="a" * 64
            ),
            records=(Conversation(source=conversation, recorded_at=stamp), message),
            resume_cursor=ImportCursor(
                adapter="native_delivery",
                adapter_version="0.2.0",
                logical_inventory_sha256="b" * 64,
                component_sha256="c" * 64,
                next_record_index=2,
            ),
        )
        writer.write_batch(
            batch,
            keys=(
                SourceKeyAssignment(
                    key=SourceKey(
                        adapter="native_delivery",
                        record_kind="message",
                        external_id="v2:user:probe",
                    ),
                    record_id=message.source.record_id,
                ),
            ),
        )

        async def run() -> dict[str, object]:
            model = HttpGatewayClient(endpoint, token)
            try:
                health = await model.health()
                assert "P4" in health.supported_workloads
                spec = native_specification(health.model, health.digest.removeprefix("sha256:"))
                service = ProcessingService(engine, character)
                job = service.create(
                    JobRequest(
                        idempotency_key="probe",
                        owner_store_id=character,
                        specification=spec,
                        source_kind="native",
                    )
                )
                extractor = GatewayExtractionModel(endpoint, token, spec)
                try:
                    status = await service.run(job.definition.job_id, extractor, window_attempts=3)
                finally:
                    await extractor.client.aclose()
                assert status.status == "complete", status.model_dump_json()
                ids = service.database.read(
                    lambda c: tuple(
                        UUID(row_text(row, "candidate_id"))
                        for row in c.execute(
                            "SELECT candidate_id FROM processing_candidates WHERE job_id=?",
                            (str(job.definition.job_id),),
                        )
                    )
                )
                receipts = tuple(
                    NativeAcceptanceService(engine, character).accept(
                        NativeAcceptanceRequest(candidate_id=c, idempotency_key=str(c))
                    )
                    for c in ids
                )
                assert any(r.outcome == "accepted" for r in receipts)
                memory = InProcessAsyncMemoryClient(engine.character(character))
                foreground = HttpGatewayClient(endpoint, foreground_token)
                try:
                    suite = (
                        "Explain how a NAS resolves DNS, briefly.",
                        "Describe what VLAN tagging does.",
                        "Explain how a headphone amplifier works.",
                    )
                    pairs = []
                    for query_number, query in enumerate(suite):
                        for enabled in (False, True):
                            operator = engine.character(
                                character, authority=MutationAuthority(mode="confirmed_user")
                            )
                            operator.configure_profile(
                                operator.profile_settings().model_copy(update={"enabled": enabled})
                            )
                            core = CharacterCore(memory, foreground, model_tag=health.model)
                            turn = await core.prepare(query)
                            assert turn.retained_pins == (4 if enabled else 0)
                            if enabled:
                                assert "Rowan" in turn.request.messages[1].content
                                assert "they/them" in turn.request.messages[1].content
                                assert "concise" in turn.request.messages[1].content
                            # No session lease: each request waits for an idle P0 slot.
                            # A known HTTP rejection never admitted generation; an
                            # uncertain transport failure is never retried.
                            output: list[str] = []
                            usage = None
                            end = datetime.now(UTC) + timedelta(seconds=180)
                            while datetime.now(UTC) < end:
                                observed = await foreground.health()
                                if observed.active_request_id is not None:
                                    await asyncio.sleep(0.1)
                                    continue
                                request = turn.request.model_copy(
                                    update={
                                        "request_id": uuid4(),
                                        "max_output_tokens": 160,
                                        "deadline_at": timestamp_from_datetime(end),
                                    }
                                )
                                try:
                                    async for event in core.stream(replace(turn, request=request)):
                                        if event.kind == "text":
                                            output.append(event.text)
                                        if event.kind == "end":
                                            usage = event.usage
                                except GatewayError as error:
                                    if error.code not in {"busy", "gateway_rejected"} or output:
                                        raise
                                    await asyncio.sleep(0.1)
                                    continue
                                break
                            assert output and usage is not None
                            assert usage.prefill_seconds is not None
                            visible = "".join(output)
                            pairs.append(
                                {
                                    "query": query_number,
                                    "profile_enabled": enabled,
                                    "profile_pins": turn.retained_pins,
                                    "serialized_prompt_guard": prompt_cost(turn.request.messages),
                                    "prompt_tokens": usage.prompt_tokens,
                                    "first_visible_seconds": usage.first_visible_seconds,
                                    "prefill_seconds": usage.prefill_seconds,
                                    "output_tokens": usage.output_tokens,
                                    "visible_word_count": len(visible.split()),
                                    "uses_preferred_name": "Rowan" in visible,
                                }
                            )
                    return {
                        "model": health.model,
                        "digest": health.digest,
                        "extractor": spec.extractor,
                        "prompt_sha256": spec.prompt_sha256,
                        "native_candidates": len(ids),
                        "outcomes": [r.outcome for r in receipts],
                        "fixed_suite": list(suite),
                        "paired_text": pairs,
                        "field_inclusion": (
                            "All four fields in every enabled prompt; "
                            "zero profile pins when disabled"
                        ),
                        "prefill_measurement": (
                            "Prompt tokens, upstream prefill duration and gateway TTFT measured "
                            "using opt-in request version 0.3.0"
                        ),
                        "admission": "P4 native extraction; idle P0 text; no realtime reservation",
                    }
                finally:
                    await memory.aclose()
                    await foreground.client.aclose()
            finally:
                await model.client.aclose()

        report = asyncio.run(run())
        output_path = os.getenv("EMBER_PHASE_SEVEN_REAL_REPORT")
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                json.dump(report, output, indent=2)
