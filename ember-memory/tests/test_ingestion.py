"""Interactive ingestion exercises real durable services using only synthetic sources."""

import asyncio
import json
import re
import struct
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from ember_memory_abi.interfaces import MemoryQuery
from ember_memory_cli import ingestion
from ember_memory_cli.ingestion_settings import IngestionArguments, settings_for
from ember_memory_cli.interactive_review import IngestionCancelled, confirm, review_candidates
from ember_memory_cli.maintenance import EngineImportTarget
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.library import LibraryService, SeedRequest
from ember_memory_engine.processing import ProcessingService
from ember_memory_engine.processing_contracts import (
    ClaimBatch,
    ExtractionSpec,
    JobRequest,
    ModelWindow,
    OtherClaim,
    ProcessingError,
)
from ember_memory_engine.processing_gateway import GatewayExtractionModel
from ember_memory_engine.processing_review import ReviewService
from ember_memory_engine.search_contracts import EmbeddingSpec
from ember_memory_importers.normalization.memory_summary import import_summary


class Console:
    def __init__(self, answers: Sequence[str]) -> None:
        self.answers = iter(answers)
        self.output: list[str] = []

    def read(self, prompt: str) -> str:
        self.output.append(prompt)
        try:
            return next(self.answers)
        except StopIteration as error:
            raise IngestionCancelled() from error

    def write(self, text: str) -> None:
        self.output.append(text)


class Model:
    def __init__(self, spec: ExtractionSpec) -> None:
        self.specification = spec

    async def extract(self, window: ModelWindow) -> ClaimBatch:
        return claims(window.text)


def claims(text: str) -> ClaimBatch:
    return ClaimBatch(
        claims=(
            OtherClaim(
                kind="episode",
                statement=text,
                quote=text,
                quote_start=0,
                modality="PREFERENCE",
                predicate=None,
                value=None,
                entity_name=None,
                confidence=0.95,
                importance=0.8,
                verdict="supported",
            ),
        )
    )


class Encoder:
    specification = EmbeddingSpec(
        model_revision="synthetic",
        tokenizer_revision="synthetic",
        model_sha256="a" * 64,
        tokenizer_sha256="b" * 64,
    )

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        return tuple((m.start(), m.end()) for m in re.finditer(r"\S+", text))

    def token_count(self, text: str) -> int:
        return len(self.offsets(text)) + 2

    def encode(self, texts: Sequence[str], *, query: bool = False) -> tuple[bytes, ...]:
        return tuple(struct.pack("<384f", 1.0, *([0.0] * 383)) for _ in texts)


def arguments(values: list[str]) -> IngestionArguments:
    return IngestionArguments.model_validate(vars(ingestion._parser().parse_args(values)))


def prepare(root: Path, count: int = 3) -> tuple[UUID, UUID, UUID]:
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        library = engine.create_library("synthetic-summary").store_id
        source = root.parent / "synthetic.json"
        source.write_text(
            json.dumps(
                {"memories": [f"I enjoy synthetic music number {n}." for n in range(count)]}
            ),
            encoding="utf-8",
        )
        import_summary(
            source,
            target=EngineImportTarget(engine.archive(library)),
            raw_vault=root.parent / "raw",
            manifests=root.parent / "manifests",
            format="json",
        )
        service = ProcessingService(engine, library)
        spec = ExtractionSpec(model="synthetic", model_digest="a" * 64)
        job = service.create(
            JobRequest(
                idempotency_key="synthetic",
                owner_store_id=library,
                specification=spec,
                source_kind="summary",
            )
        ).definition.job_id
        assert asyncio.run(service.run(job, Model(spec))).status == "complete"
        return character, library, job


def existing_args(root: Path, character: UUID, library: UUID, job: UUID) -> IngestionArguments:
    return arguments(
        [
            "--root",
            str(root),
            "--character",
            str(character),
            "--store",
            str(library),
            "--job",
            str(job),
            "--skip-index",
        ]
    )


def test_individual_decisions_and_seed_are_separate(tmp_path: Path) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root)
    args = existing_args(root, character, library, job)
    assert ingestion.run_workflow(args, settings_for(args), Console(["i", "a", "r", "s", "n"])) == 0
    with CanonicalEngine(root) as engine:
        service = ProcessingService(engine, library)
        entries = ReviewService(service).export(job, include_decisions=True).entries
        assert [e.decision for e in entries] == ["approve", "reject", "pending"]
        assert not engine.character(character).search(MemoryQuery()).results
        assert [e.decision for e in ReviewService(service).export(job).entries] == ["pending"] * 3


def test_quit_retains_decisions_bulk_preserves_reject_and_repeat_does_not_duplicate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root)
    args = existing_args(root, character, library, job)
    with pytest.raises(IngestionCancelled):
        ingestion.run_workflow(args, settings_for(args), Console(["i", "r", "a", "q"]))
    for _ in range(2):
        assert ingestion.run_workflow(args, settings_for(args), Console(["a", "y", "y"])) == 0
    with CanonicalEngine(root) as engine:
        entries = (
            ReviewService(ProcessingService(engine, library))
            .export(
                job,
                include_decisions=True,
            )
            .entries
        )
        assert [e.decision for e in entries] == ["reject", "approve", "approve"]
        memories = engine.character(character).search(MemoryQuery()).results
        assert len(memories) == 2
        assert {
            engine.character(character).get_sources(m.memory.memory_id)[0].quote for m in memories
        } == {e.quotes[0] for e in entries if e.decision == "approve"}


def test_pagination_and_prior_manual_seeds(tmp_path: Path) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root, 101)
    with CanonicalEngine(root) as engine:
        service = ReviewService(
            ProcessingService(engine, library), authority=MutationAuthority(mode="confirmed_user")
        )
        approved = review_candidates(service, job, Console(["a", "y"]))
        assert len(approved) == 101
        LibraryService(engine, authority=MutationAuthority(mode="confirmed_user")).seed(
            character,
            SeedRequest(idempotency_key="manual", library_id=library, selections=(approved[0],)),
        )
    args = existing_args(root, character, library, job)
    console = Console(["i", "y"])
    assert ingestion.run_workflow(args, settings_for(args), console) == 0
    assert "Seeded: 100; already seeded (left unchanged): 1." in console.output
    with CanonicalEngine(root) as engine:
        _, database = engine.maintenance_store(character)
        assert (
            database.read(lambda c: c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]) == 101
        )


@pytest.mark.parametrize("bad_window", [False, True])
def test_full_import_gateway_review_seed_index_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad_window: bool,
) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        character = engine.create_character().store_id
        other = engine.create_character().store_id
    source = tmp_path / "summary.json"
    source.write_text(
        json.dumps(
            {"memories": (["Invalid window."] if bad_window else []) + ["I enjoy synthetic jazz."]}
        ),
        encoding="utf-8",
    )
    token = tmp_path / "token"
    token.write_text("b" * 40, encoding="utf-8")
    observed: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "version": "0.2.0",
                    "status": "ready",
                    "model": "synthetic",
                    "digest": "a" * 64,
                    "supported_workloads": ["P4"],
                    "journal_status": "available",
                },
            )
        body = json.loads(request.content)
        observed.append(body)
        text = json.loads(body["messages"][-1]["content"])["text"]
        identity = {"request_id": body["request_id"], "context_id": body["context_id"]}
        events = [
            dict(identity, sequence=0, kind="start"),
            dict(
                identity,
                sequence=1,
                kind="text",
                text=claims(
                    "Missing quote." if text == "Invalid window." else text
                ).model_dump_json(),
            ),
            dict(identity, sequence=2, kind="end"),
        ]
        return httpx.Response(200, content="".join(json.dumps(e) + "\n" for e in events))

    def gateway(endpoint: str, credential: str, spec: ExtractionSpec) -> GatewayExtractionModel:
        return GatewayExtractionModel(
            endpoint,
            credential,
            spec,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(respond),
                base_url=endpoint,
            ),
        )

    monkeypatch.setattr(ingestion, "GatewayExtractionModel", gateway)
    monkeypatch.setattr(ingestion, "BgeEncoder", lambda path: Encoder())
    args = arguments(
        [
            str(source),
            "--root",
            str(root),
            "--character",
            str(character),
            "--source-namespace",
            "synthetic",
            "--model",
            "synthetic",
            "--digest",
            "a" * 64,
            "--token-file",
            str(token),
            "--encoder",
            str(tmp_path / "encoder"),
        ]
    )
    assert (
        ingestion.run_workflow(
            args,
            settings_for(args),
            Console(["y"] + (["y"] if bad_window else []) + ["a", "y", "y"]),
        )
        == 0
    )
    assert len(observed) == (4 if bad_window else 1)
    assert observed[0]["workload"] == "P4"
    with CanonicalEngine(root) as engine:
        engine.encoding.encoder = Encoder()
        response = engine.character(character).search(MemoryQuery(text="jazz", mode="hybrid"))
        assert response.coverage.index_status == "available"
        assert len(response.results) == 1
        memory = response.results[0].memory
        assert memory.content.confidence == 0.79
        assert engine.character(character).get_sources(memory.memory_id)[0].quote == (
            "I enjoy synthetic jazz."
        )
        assert not engine.character(other).search(MemoryQuery()).results


def test_bulk_rejection_and_default_no_do_not_seed(tmp_path: Path) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root)
    args = existing_args(root, character, library, job)
    assert ingestion.run_workflow(args, settings_for(args), Console(["r", "yes"])) == 0
    with CanonicalEngine(root) as engine:
        entries = (
            ReviewService(ProcessingService(engine, library))
            .export(
                job,
                include_decisions=True,
            )
            .entries
        )
        assert all(e.decision == "reject" for e in entries)
        assert not engine.character(character).search(MemoryQuery()).results
    assert not confirm(Console(["invalid", ""]), "Seed?")
    with pytest.raises(IngestionCancelled):
        confirm(Console([]), "Seed?")


def test_config_paths_binding_and_unsafe_overrides(tmp_path: Path) -> None:
    config = tmp_path / "config" / "deployment.toml"
    config.parent.mkdir()
    character = UUID("00000000-0000-4000-8000-000000000001")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "primary-character.json").write_text(
        json.dumps({"character_id": str(character)}),
        encoding="utf-8",
    )
    config.write_text(
        'config_version=1\nactive_profile="primary"\n[paths]\n'
        'data_root="../data"\nruntime_root="../runtime"\n[model]\n'
        'tag="synthetic"\nexpected_digest="sha256:' + "a" * 64 + '"\n'
        '[gateway]\nendpoint="http://127.0.0.1:11435"\n'
        '[search]\nembedding_directory="../encoder"\n',
        encoding="utf-8",
    )
    settings = settings_for(arguments(["--config", str(config)]))
    assert settings.root == tmp_path / "data"
    assert settings.character == character
    assert settings.encoder == tmp_path / "encoder"
    assert settings.token_file == runtime / "gateway-background.token"
    assert settings.digest == "a" * 64
    with pytest.raises(ProcessingError, match="root_mismatch"):
        settings_for(arguments(["--config", str(config), "--root", str(tmp_path / "wrong")]))
    with pytest.raises(ProcessingError, match="gateway_origin"):
        settings_for(arguments(["--gateway", "http://remote.invalid:11435"]))


def test_nonterminal_and_busy_root_fail_without_takeover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "data"
    monkeypatch.setattr(ingestion.sys.stdin, "isatty", lambda: False)
    assert ingestion.main(["--root", str(root)]) == 2
    assert not root.exists()
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(ingestion.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(ingestion.sys.stdout, "isatty", lambda: True)
    with CanonicalEngine(root):
        assert ingestion.main(["--root", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "writer owner" in captured.err


def test_seed_default_no_retains_approvals_without_character_writes(tmp_path: Path) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root)
    args = existing_args(root, character, library, job)
    ingestion.run_workflow(args, settings_for(args), Console(["a", "y", ""]))
    with CanonicalEngine(root) as engine:
        entries = (
            ReviewService(ProcessingService(engine, library))
            .export(
                job,
                include_decisions=True,
            )
            .entries
        )
        assert all(entry.decision == "approve" for entry in entries)
        assert not engine.character(character).search(MemoryQuery()).results


def test_index_failure_keeps_seeds_and_rerun_repairs_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root, 1)
    args = existing_args(root, character, library, job).model_copy(update={"skip_index": False})
    settings = settings_for(args).model_copy(update={"encoder": tmp_path / "encoder"})

    def broken(path: Path) -> Encoder:
        raise ValueError("Synthetic absent assets")

    monkeypatch.setattr(ingestion, "BgeEncoder", broken)
    with pytest.raises(ValueError):
        ingestion.run_workflow(args, settings, Console(["a", "y", "y"]))
    monkeypatch.setattr(ingestion, "BgeEncoder", lambda path: Encoder())
    console = Console(["i", "y"])
    assert ingestion.run_workflow(args, settings, console) == 0
    assert "Seeded: 0; already seeded (left unchanged): 1." in console.output
    with CanonicalEngine(root) as engine:
        engine.encoding.encoder = Encoder()
        response = engine.character(character).search(MemoryQuery(text="music", mode="hybrid"))
        assert len(response.results) == 1 and response.coverage.index_status == "available"


def test_existing_job_menu_uses_durable_snapshots_not_new_extraction(tmp_path: Path) -> None:
    root = tmp_path / "data"
    character, library, job = prepare(root, 1)
    args = existing_args(root, character, library, job).model_copy(update={"job": None})
    ingestion.run_workflow(args, settings_for(args), Console(["1", "a", "y", "n"]))
    with CanonicalEngine(root) as engine:
        service = ProcessingService(engine, library)
        assert len(service.definitions()) == 1
        assert service.definitions()[0].job_id == job
        assert service.definitions(offset=1) == ()
        with pytest.raises(ProcessingError):
            service.definitions(limit=101)


def test_configuration_adapter_matches_runtime_paths_and_pins(tmp_path: Path) -> None:
    from ember_agent.runtime.config import load_config

    source = Path("ember-agent/config/deployment.example.toml").read_text(encoding="utf-8")
    config = tmp_path / "config" / "deployment.toml"
    config.parent.mkdir()
    source = (
        source.replace('tag = ""', 'tag = "synthetic"')
        .replace(
            'expected_digest = ""',
            'expected_digest = "sha256:' + "a" * 64 + '"',
        )
        .replace('embedding_directory = ""', 'embedding_directory = "../encoder"')
    )
    config.write_text(source, encoding="utf-8")
    runtime = load_config(config, require_model_pin=True)
    maintenance = settings_for(arguments(["--config", str(config)]))
    assert maintenance.root == runtime.data_root
    assert maintenance.model == runtime.model_tag
    assert maintenance.digest == runtime.model_digest.removeprefix("sha256:")
    assert maintenance.gateway == runtime.services.gateway.endpoint
    assert maintenance.encoder == runtime.embedding_directory
    assert maintenance.token_file == runtime.runtime_root / "gateway-background.token"


def test_accept_all_keeps_unsupported_semantics_pending(tmp_path: Path) -> None:
    from ember_memory_engine.processing_contracts import FactClaim

    root = tmp_path / "data"
    character, library, _ = prepare(root, 1)
    with CanonicalEngine(root) as engine:
        service = ProcessingService(engine, library)
        spec = ExtractionSpec(model="synthetic", model_digest="a" * 64)
        job = service.create(
            JobRequest(
                idempotency_key="unsupported",
                owner_store_id=library,
                specification=spec,
                source_kind="summary",
            )
        ).definition.job_id

        class Unsupported(Model):
            async def extract(self, window: ModelWindow) -> ClaimBatch:
                return ClaimBatch(
                    claims=(
                        FactClaim(
                            kind="fact",
                            statement=window.text,
                            quote=window.text,
                            quote_start=0,
                            modality="FACTUAL_ASSERTION",
                            predicate="unregistered.synthetic",
                            value="synthetic",
                            entity_name=None,
                            confidence=0.95,
                            importance=0.8,
                            verdict="supported",
                        ),
                    )
                )

        asyncio.run(service.run(job, Unsupported(spec)))
    args = existing_args(root, character, library, job)
    console = Console(["a", "y"])
    assert ingestion.run_workflow(args, settings_for(args), console) == 0
    with CanonicalEngine(root) as engine:
        entry = (
            ReviewService(ProcessingService(engine, library))
            .export(
                job,
                include_decisions=True,
            )
            .entries[0]
        )
        assert entry.decision == "pending"
        assert not engine.character(character).search(MemoryQuery()).results
    assert any("never force-written" in line for line in console.output)


def test_rerun_does_not_resurrect_forgotten_seed(tmp_path: Path) -> None:
    from ember_memory_abi.interfaces import ForgetRequest

    root = tmp_path / "data"
    character, library, job = prepare(root, 1)
    args = existing_args(root, character, library, job)
    ingestion.run_workflow(args, settings_for(args), Console(["a", "y", "y"]))
    with CanonicalEngine(root) as engine:
        store = engine.character(character, authority=MutationAuthority(mode="confirmed_user"))
        memory = store.search(MemoryQuery()).results[0].memory
        store.forget(
            ForgetRequest(
                idempotency_key="forget-seed",
                memory_id=memory.memory_id,
                expected_revision=memory.revision,
                reason="confirmed_forget",
            )
        )
    console = Console(["i", "y"])
    ingestion.run_workflow(args, settings_for(args), console)
    assert "Seeded: 0; already seeded (left unchanged): 1." in console.output
    with CanonicalEngine(root) as engine:
        assert not engine.character(character).search(MemoryQuery()).results


@pytest.mark.parametrize("version", ["0.1.0", "0.1.1"])
def test_historical_job_discovery_preserves_bytes_and_current_job_selection(
    tmp_path: Path,
    version: str,
) -> None:
    from uuid import uuid4

    from ember_memory_abi.common import canonical_object_sha256
    from ember_memory_engine.database import model_bytes, payload_hash

    root = tmp_path / "data"
    character, library, historical = prepare(root, 1)
    with CanonicalEngine(root) as engine:
        service = ProcessingService(engine, library)
        original = service.definition(historical)
        old_spec = original.request.specification.model_copy(
            update={"extractor": "ember.atomic-claims@" + version},
        )
        old_request = original.request.model_copy(
            update={"specification": old_spec, "idempotency_key": "historical"}
        )
        historical = uuid4()
        old = original.model_copy(update={"job_id": historical, "request": old_request})
        payload = model_bytes(old)
        # Synthetic fixture represents a legitimately persisted earlier producer.
        service.database.write(
            lambda connection: connection.execute(
                "INSERT INTO processing_jobs VALUES(?,?,?,?,?,'planning',0)",
                (
                    str(historical),
                    old_request.idempotency_key,
                    canonical_object_sha256(old_request),
                    payload,
                    payload_hash(payload),
                ),
            )
        )
        service._plan(old)
        current = service.create(
            JobRequest(
                idempotency_key="current",
                owner_store_id=library,
                specification=ExtractionSpec(model="synthetic", model_digest="a" * 64),
                source_kind="summary",
            )
        ).definition.job_id
        asyncio.run(service.run(current, Model(service.definition(current).request.specification)))
        definitions = service.definitions()
        assert [d.job_id for d in definitions[:2]] == [current, historical]
        assert service.definition(historical).request.specification.reference().version == version
        assert (
            service.database.read(
                lambda c: c.execute(
                    "SELECT payload_json FROM processing_jobs WHERE job_id=?",
                    (str(historical),),
                ).fetchone()[0]
            )
            == payload
        )
        with pytest.raises(ProcessingError, match="processing_extractor_changed"):
            service.create(old_request.model_copy(update={"idempotency_key": "old-new-job"}))
        service.database.write(
            lambda c: c.execute(
                "UPDATE processing_jobs SET status='ready' WHERE job_id=?",
                (str(historical),),
            )
        )
        with pytest.raises(ProcessingError, match="processing_extractor_changed"):
            asyncio.run(service.run(historical, Model(old_spec)))
        assert service.status(historical).completed_windows == 0
        assert service.status(historical).total_windows == 1
    args = existing_args(root, character, library, current).model_copy(update={"job": None})
    assert ingestion.run_workflow(args, settings_for(args), Console(["1", "a", "y", "n"])) == 0
