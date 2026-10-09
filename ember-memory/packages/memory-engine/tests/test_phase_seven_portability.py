"""Reviewed imports, independent profiles, grants and portable Phase 7 metadata."""

import asyncio
from pathlib import Path

import pytest

from ember_memory_abi.common import CanonicalId, timestamp_from_datetime
from ember_memory_abi.interfaces import ContextRequest, MemoryQuery, PinRequest
from ember_memory_abi.memory import FactPayload
from ember_memory_abi.related import RelatedRequest
from ember_memory_engine import CanonicalEngine, SourceGrant
from ember_memory_engine.bundles import BundleService, restore_bundles
from ember_memory_engine.library import KnowledgeSelection, LibraryService, SeedRequest
from ember_memory_engine.processing_contracts import ClaimBatch, JobRequest, ModelWindow
from ember_memory_engine.processing_review import ReviewService
from ember_memory_engine.profile import ProfileSettings

from .conftest import TestClock
from .test_phase_seven import AUTHORITY, SPEC, NativeModel, context
from .test_phase_six_processing import summary


class SummaryProfileModel:
    specification = SPEC

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        fields = (
            ("call me", "user.preferred_name", "Rowan", "FACTUAL_ASSERTION"),
            ("pronouns", "user.pronouns", "they/them", "FACTUAL_ASSERTION"),
            ("prefer", "user.communication_style", "concise replies", "PREFERENCE"),
        )
        marker, predicate, value, modality = next(f for f in fields if f[0] in window.text)
        return await NativeModel(predicate, value, modality=modality).extract(window)


def approved_profile(
    engine: CanonicalEngine, tmp_path: Path
) -> tuple[CanonicalId, tuple[KnowledgeSelection, ...]]:
    processing, _ = summary(
        engine,
        tmp_path,
        ("Please call me Rowan.", "My pronouns are they/them.", "I prefer concise replies."),
    )
    job = processing.create(
        JobRequest(
            idempotency_key="profile",
            owner_store_id=processing.owner,
            specification=SPEC,
            source_kind="summary",
        )
    )
    assert (
        asyncio.run(processing.run(job.definition.job_id, SummaryProfileModel())).status
        == "complete"
    )
    reviewer = ReviewService(processing, authority=AUTHORITY)
    review = reviewer.export(job.definition.job_id)
    # No raw/pending summary claim is profile knowledge. Authority comes from review.
    assert all(
        entry.policy_decision.verdict.verdict == "requires_confirmation" for entry in review.entries
    )
    receipt = reviewer.apply(
        review.model_copy(
            update={
                "entries": tuple(
                    e.model_copy(update={"decision": "approve"}) for e in review.entries
                )
            }
        )
    )
    return processing.owner, tuple(
        KnowledgeSelection(knowledge_id=x, revision=1) for x in receipt.approved
    )


def seed_profile(
    engine: CanonicalEngine,
    character: CanonicalId,
    library: CanonicalId,
    selections: tuple[KnowledgeSelection, ...],
    key: str = "seed",
) -> tuple[CanonicalId, ...]:
    receipt = LibraryService(engine, authority=AUTHORITY).seed(
        character, SeedRequest(idempotency_key=key, library_id=library, selections=selections)
    )
    store = engine.character(character, authority=AUTHORITY)
    memories = tuple(store.get(r.memory_id) for r in receipt.affected)
    payload = memories[0].content.payload
    assert isinstance(payload, FactPayload)
    store.configure_profile(ProfileSettings(subject_id=payload.subject.entity_id))
    return tuple(m.memory_id for m in memories)


def test_reviewed_import_profile_independent_characters_restart_and_support(tmp_path: Path) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        library, selections = approved_profile(engine, tmp_path)
        first, second = engine.create_character().store_id, engine.create_character().store_id
        ids = seed_profile(engine, first, library, selections)
        other = seed_profile(engine, second, library, selections)
        assert set(ids).isdisjoint(other)
        again = LibraryService(engine, authority=AUTHORITY).seed(
            first, SeedRequest(idempotency_key="repeat", library_id=library, selections=selections)
        )
        assert tuple(r.memory_id for r in again.affected) == ids
        assert len(engine.character(first).search(MemoryQuery()).results) == 3
        assert len(context(engine, first).profile.automatic) == 3
        for identity in ids:
            expanded = engine.character(first).lookup_related(
                RelatedRequest(
                    intent="expand",
                    anchors=({"memory_id": identity, "revision": 1},),
                )
            )
            assert all(
                e.origins[0].origin_source_store_id == library for e in expanded.relationships
            )
        one = engine.character(first, authority=AUTHORITY)
        one.configure_profile(
            one.profile_settings().model_copy(update={"excluded": ("user.pronouns",)})
        )
        assert len(context(engine, first).profile.automatic) == 2
        assert len(context(engine, second).profile.automatic) == 3
    with CanonicalEngine(root) as reopened:
        selected = reopened.character(first).retrieve_context(
            ContextRequest(
                memory_query=MemoryQuery(text="How does my NAS resolve DNS?", mode="structured"),
                memory_token_budget=4096,
                include_profile=True,
            )
        )
        assert len(selected.profile.automatic) == 2
        assert all(
            reopened.character(first).get(x).origin.origin_source_store_id == library for x in ids
        )
        assert len(context(reopened, second).profile.automatic) == 3


@pytest.mark.parametrize("mode", ("full", "selected"))
@pytest.mark.integration
def test_profile_choices_and_native_policy_survive_bundle(tmp_path: Path, mode: str) -> None:
    from .test_phase_seven import accept, append_native, extract

    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        append_native(engine, character, "Please call me Rowan.")
        accepted = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        memory = accepted.memory
        store = engine.character(character, authority=AUTHORITY)
        store.pin(
            PinRequest(
                idempotency_key="pin", memory_id=memory.memory_id, expected_revision=1, priority=8
            )
        )
        store.configure_profile(
            store.profile_settings().model_copy(
                update={
                    "enabled": False,
                    "excluded": ("user.pronouns",),
                }
            )
        )
    bundle = tmp_path / "bundle"
    BundleService(root).export_character(
        character,
        bundle,
        mode="self_contained",
        memory_ids=(memory.memory_id,) if mode == "selected" else None,
    )
    restored = tmp_path / "restored"
    restore_bundles((bundle,), restored)
    with CanonicalEngine(restored) as engine:
        store = engine.character(character)
        settings = store.profile_settings()
        assert not settings.enabled and settings.excluded == ("user.pronouns",)
        selected = context(engine, character)
        assert not selected.profile.automatic
        assert selected.pinned[0].purpose == "manual" and selected.pinned[0].priority == 8
        assert (
            store.database.read(
                lambda c: c.execute("SELECT COUNT(*) FROM native_acceptance").fetchone()[0]
            )
            == 1
        )
        assert (
            store.database.read(
                lambda c: c.execute(
                    "SELECT COUNT(*) FROM processing_specs "
                    "WHERE json_extract(payload_json,'$.name')='ember.native-acceptance'"
                ).fetchone()[0]
            )
            == 1
        )
        assert store.get_sources(memory.memory_id)[0].quote == "Please call me Rowan."


def test_import_grant_expiry_invalidates_profile_before_limits(
    engine: CanonicalEngine,
    tmp_path: Path,
    clock: TestClock,
) -> None:
    from datetime import timedelta

    library, selections = approved_profile(engine, tmp_path)
    character = engine.create_character().store_id
    ids = seed_profile(engine, character, library, selections)
    store = engine.character(character)
    before = context(engine, character)
    sources = tuple(store.get(x).content.evidence[0].evidence.source for x in ids)
    engine.grant_source(
        character,
        SourceGrant(
            references=sources,
            capabilities=frozenset({"evidence_read"}),
            expires_at=timestamp_from_datetime(clock.now() + timedelta(seconds=1)),
        ),
    )
    assert len(context(engine, character).profile.automatic) == 3
    clock.advance(1)
    assert not context(engine, character).profile.automatic
    assert not store.lookup_related(
        RelatedRequest(intent="validate", dependencies=before.fast_dependencies)
    ).valid
    assert not store.lookup_related(
        RelatedRequest(intent="expand", anchors=({"memory_id": ids[0], "revision": 1},))
    ).memories


@pytest.mark.parametrize(
    "grant_case",
    ("permanent_part", "permanent_record", "overlapping_temporary", "expired_temporary"),
)
def test_certificate_uses_effective_grant_window(
    engine: CanonicalEngine, tmp_path: Path, clock: TestClock, grant_case: str
) -> None:
    from datetime import timedelta

    library, selections = approved_profile(engine, tmp_path)
    character = engine.create_character().store_id
    ids = seed_profile(engine, character, library, selections)
    store = engine.character(character)
    start = clock.now()
    for index, identity in enumerate(ids):
        source = store.get(identity).content.evidence[0].evidence.source
        short_expiry = timestamp_from_datetime(start + timedelta(seconds=1))
        if grant_case != "permanent_part":
            engine.grant_source(
                character,
                SourceGrant(
                    references=(source,),
                    capabilities=frozenset({"evidence_read"}),
                    expires_at=short_expiry,
                ),
            )
        if grant_case == "permanent_record":
            record_expiry = None
        elif grant_case == "permanent_part":
            record_expiry = short_expiry
        else:
            record_expiry = timestamp_from_datetime(start + timedelta(seconds=3 + index))
        engine.grant_source(
            character,
            SourceGrant(
                references=(source.model_copy(update={"part_id": None}),),
                capabilities=frozenset({"evidence_read"}),
                expires_at=record_expiry,
            ),
        )
    if grant_case == "expired_temporary":
        clock.advance(1)
    dependencies = context(engine, character).fast_dependencies
    assert dependencies is not None
    permanent = grant_case.startswith("permanent_")
    assert dependencies.valid_until == (
        None if permanent else timestamp_from_datetime(start + timedelta(seconds=3))
    )
    clock.advance(1)
    assert store.lookup_related(RelatedRequest(intent="validate", dependencies=dependencies)).valid
    assert len(context(engine, character).profile.automatic) == 3
    if permanent:
        clock.advance(10)
    else:
        clock.advance(int((start + timedelta(seconds=3) - clock.now()).total_seconds()))
    valid = store.lookup_related(RelatedRequest(intent="validate", dependencies=dependencies)).valid
    assert valid == permanent
    assert len(context(engine, character).profile.automatic) == (3 if permanent else 2)


def test_related_foreign_scope_deadline_and_row_limit(tmp_path: Path) -> None:
    from ember_memory_abi.errors import DeadlineExceededError

    from .test_phase_seven import accept, append_native, extract

    with CanonicalEngine(tmp_path / "data") as engine:
        first, second = engine.create_character().store_id, engine.create_character().store_id
        append_native(engine, first, "Please call me Rowan.")
        memory = accept(
            engine, first, extract(engine, first, NativeModel("user.preferred_name", "Rowan"))[0]
        ).memory
        request = RelatedRequest(intent="expand", anchors=(memory,), row_budget=1)
        assert engine.character(first).lookup_related(request).rows_examined <= 1
        assert not engine.character(second).lookup_related(request).memories
        with pytest.raises(DeadlineExceededError):
            engine.character(first).lookup_related(
                request.model_copy(update={"deadline_at": "2000-01-01T00:00:00.000000Z"})
            )


@pytest.mark.parametrize("mode", ("semantic", "lexical", "hybrid"))
def test_projected_grant_expiry_and_revoke_are_checked_before_top_k(
    engine: CanonicalEngine,
    tmp_path: Path,
    clock: TestClock,
    mode: str,
) -> None:
    from datetime import timedelta

    from .test_phase_five_search import SyntheticEncoder

    library, selections = approved_profile(engine, tmp_path)
    character = engine.create_character().store_id
    ids = seed_profile(engine, character, library, selections)
    store = engine.character(character)
    sources = tuple(store.get(x).content.evidence[0].evidence.source for x in ids)
    expiry = timestamp_from_datetime(clock.now() + timedelta(seconds=2))
    engine.grant_source(
        character,
        SourceGrant(
            references=sources, capabilities=frozenset({"evidence_read"}), expires_at=expiry
        ),
    )
    encoder = SyntheticEncoder()
    engine.encoding.encoder = encoder
    engine.rebuild_index(character, encoder)
    query = MemoryQuery(text="preferred name pronouns style", mode=mode)
    assert store.search(query).results
    clock.advance(2)
    assert not store.search(query).results
    # Grant restoration/expiry is reprojected during explicit maintenance, without
    # rewriting world-valid time or the immutable accepted memory JSON.
    engine.grant_source(
        character, SourceGrant(references=sources, capabilities=frozenset({"evidence_read"}))
    )
    engine.update_index(character, encoder)
    assert store.search(query).results
    engine.revoke_source(character, library)
    assert all(r.memory.memory_id != ids[0] for r in store.search(query).results)
    engine.update_index(character, encoder)
    assert all(r.memory.memory_id != ids[0] for r in store.search(query).results)


def test_native_support_of_seeded_fact_retains_independent_origins(tmp_path: Path) -> None:
    from .test_phase_seven import accept, append_native, extract

    with CanonicalEngine(tmp_path / "data") as engine:
        library, selections = approved_profile(engine, tmp_path)
        character = engine.create_character().store_id
        seeded = seed_profile(engine, character, library, selections)
        append_native(engine, character, "Please call me Rowan.")
        receipt = accept(
            engine,
            character,
            extract(engine, character, NativeModel("user.preferred_name", "Rowan"))[0],
        )
        assert receipt.outcome == "accepted"
        store = engine.character(character)
        assert store.get(receipt.memory.memory_id).origin is None
        assert store.get(seeded[0]).origin.origin_source_store_id == library
        engine.revoke_source(character, library)
        selected = context(engine, character)
        assert selected.profile.automatic[0].memory_id == receipt.memory.memory_id


def test_manual_imported_supersession_retains_origin_and_forget_dependencies(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    from ember_memory_abi.evidence import Evidence, EvidenceAssertion, make_evidence_span
    from ember_memory_abi.interfaces import ForgetRequest, SupersedeRequest
    from ember_memory_abi.memory import MemoryDraft, StringValue

    from .test_phase_seven import append_native

    with CanonicalEngine(tmp_path / "data") as engine:
        library, selections = approved_profile(engine, tmp_path)
        character = engine.create_character().store_id
        ids = seed_profile(engine, character, library, selections)
        store = engine.character(character, authority=AUTHORITY)
        old = next(
            store.get(identity)
            for identity in ids
            if store.get(identity).content.payload.predicate == "user.preferred_name"
        )
        text = "Actually, please call me Sky."
        message = append_native(engine, character, text)
        evidence = Evidence(
            evidence_id=uuid4(),
            source=message.source.model_copy(update={"part_id": message.parts[0].part_id}),
            span=make_evidence_span(text, 0, len(text)),
        )
        assertion = EvidenceAssertion(
            evidence=evidence,
            relation="transition",
            modality="FACTUAL_ASSERTION",
            authority="confirmed_user",
            verdict="manual_confirmation",
        )
        draft = MemoryDraft(
            content=old.content.model_copy(
                update={
                    "payload": old.content.payload.model_copy(
                        update={"value": StringValue(value="Sky")}
                    ),
                    "evidence": (assertion.model_copy(update={"relation": "supports"}),),
                }
            )
        )
        receipt = store.supersede(
            SupersedeRequest(
                idempotency_key="correction",
                memory_id=old.memory_id,
                expected_revision=old.revision,
                replacement=draft,
                transition_evidence=(assertion,),
                reason="confirmed correction",
            )
        )
        new = store.get(receipt.affected[-1].memory_id)
        assert new.origin == old.origin
        assert new.content.payload.value.value == "Sky"
        assert (
            store.lookup_related(
                RelatedRequest(
                    intent="resolve", anchors=({"memory_id": old.memory_id, "revision": 1},)
                )
            )
            .relationships[0]
            .origins
        )
        store.forget(
            ForgetRequest(
                idempotency_key="forget-old-lineage",
                memory_id=old.memory_id,
                expected_revision=2,
                reason="forget imported lineage",
            )
        )
        from ember_memory_abi.errors import AccessDeniedError

        with pytest.raises(AccessDeniedError):
            store.get(new.memory_id)
