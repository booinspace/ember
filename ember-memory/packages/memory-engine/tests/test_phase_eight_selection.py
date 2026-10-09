"""Persistent origin controls narrow retrieval and invalidate delivery certificates."""

from pathlib import Path

import pytest

from ember_memory_abi.errors import AccessDeniedError, RevisionConflictError
from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_abi.related import RelatedRequest
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.bundles import BundleService, restore_bundles

from .test_phase_seven import AUTHORITY, NativeModel, accept, append_native, context, extract
from .test_phase_seven_portability import approved_profile, seed_profile


def test_source_selection_separates_native_and_imported_memory(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        library, selections = approved_profile(engine, tmp_path)
        character = engine.create_character().store_id
        imported = seed_profile(engine, character, library, selections[1:])
        append_native(engine, character, "Please call me Morgan.")
        native = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Morgan"))[0],
        ).memory
        assert native is not None
        store = engine.character(character, authority=AUTHORITY)
        original = context(engine, character).fast_dependencies
        assert original is not None
        policy = store.configure_recall(RecallSelection(import_mode="none"))
        assert policy.revision == 1
        assert {r.memory.memory_id for r in store.search(MemoryQuery()).results} == {
            native.memory_id
        }
        assert not store.lookup_related(
            RelatedRequest(intent="validate", dependencies=original)
        ).valid
        policy = store.configure_recall(
            RecallSelection(
                local_enabled=False,
                import_mode="selected",
                included_libraries=(library,),
                revision=policy.revision,
            )
        )
        assert {r.memory.memory_id for r in store.search(MemoryQuery()).results} == set(imported)
        assert store.configure_recall(policy) == policy
        with pytest.raises(RevisionConflictError):
            store.configure_recall(RecallSelection(revision=0))
        with pytest.raises(AccessDeniedError):
            engine.character(character).configure_recall(RecallSelection())
    with CanonicalEngine(root) as engine:
        assert engine.character(character).recall_selection() == policy
        assert {
            r.memory.memory_id for r in engine.character(character).search(MemoryQuery()).results
        } == set(imported)


@pytest.mark.parametrize("coverage", ("full", "selected"))
def test_source_selection_survives_bundle(tmp_path: Path, coverage: str) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        library, selections = approved_profile(engine, tmp_path)
        character = engine.create_character().store_id
        imported = seed_profile(engine, character, library, selections)
        policy = engine.character(character, authority=AUTHORITY).configure_recall(
            RecallSelection(local_enabled=False, import_mode="none", excluded_libraries=(library,))
        )
    bundle = tmp_path / "bundle"
    BundleService(root).export_character(
        character,
        bundle,
        mode="self_contained",
        memory_ids=imported if coverage == "selected" else None,
    )
    restored = tmp_path / "restored"
    restore_bundles((bundle,), restored)
    with CanonicalEngine(restored) as engine:
        store = engine.character(character)
        assert store.recall_selection() == policy
        assert not store.search(MemoryQuery()).results
