"""Native capture uses exact delivered sources, durable jobs and idempotent acceptance."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.capture import CaptureService, CaptureSettings
from ember_memory_engine.processing_contracts import native_specification

from .test_phase_seven import AUTHORITY, NativeModel, append_native

SPEC = native_specification("synthetic", "a" * 64, workload="P3")


class CaptureModel(NativeModel):
    specification = SPEC


def test_capture_resume_accepts_once_and_does_not_rescan_history(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        service = CaptureService(engine, character)
        assert len(service.pending()) == 1
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert len(engine.character(character).search(MemoryQuery()).results) == 1
        append_native(engine, character, "Please call me Rowan.")
        assert len(service.pending()) == 1
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        definitions = service.processing.definitions()
        assert len(definitions) == 2
        assert all(service.processing.status(job.job_id).total_windows == 1 for job in definitions)
        assert len(engine.character(character).search(MemoryQuery()).results) == 1


def test_capture_disabled_is_independent_of_recall_and_survives_restart(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        service = CaptureService(engine, character)
        service.configure(CaptureSettings(enabled=False), AUTHORITY)
        append_native(engine, character, "Please call me Rowan.")
        assert service.pending()[0].status == "paused"
        assert engine.character(character).recall_selection().local_enabled
    with CanonicalEngine(root) as engine, ThreadPoolExecutor(max_workers=1) as worker:
        service = CaptureService(engine, character)
        assert not service.settings().enabled
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        service.configure(CaptureSettings(enabled=True, revision=1), AUTHORITY)
        assert not asyncio.run(service.drain(CaptureModel("user.preferred_name", "Rowan"), worker))
        assert len(engine.character(character).search(MemoryQuery()).results) == 1
