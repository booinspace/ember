"""Only local composition constructs concrete memory; character and adapters receive contracts."""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from uuid import uuid4

from pydantic import UUID4, ValidationError

from ember_memory_abi.errors import MemoryError
from ember_memory_abi.interfaces import MutationReceipt
from ember_memory_abi.selection import RecallSelection
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.capture import CaptureService, CaptureSettings
from ember_memory_engine.deep_profile import DeepSources
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.errors import EmbeddingError
from ember_memory_engine.library import LibraryService
from ember_memory_engine.memory_graph import GraphQuery
from ember_memory_engine.processing_contracts import (
    ExtractionSpec,
    ProcessingError,
    native_temporal_specification,
)
from ember_memory_engine.processing_gateway import GatewayExtractionModel
from ember_memory_engine.read_budget import ReadBudget

from ..character.core import CharacterCore, Personality, PreparedTurn, TurnObservation
from ..character.interpretation import CalendarInterpreter
from ..character.prompts import PromptDocuments, PromptEdit, PromptSnapshot
from ..inference.client import HttpGatewayClient
from ..inference.contracts import GatewayError
from ..memory.backends.dialogue import ArchiveDialogueSink
from ..memory.deep_contracts import DeepRecallRequest, DeepRecallResult
from ..memory.deep_recall import DeepRecallService
from .config import DeploymentConfig, RuntimeModel
from .console import read_line
from .controls import CaptureControl, GraphInput, LibraryChoice, ProcessingStatus
from .dashboard import Dashboard
from .prompt_files import PromptFiles


class CharacterBinding(RuntimeModel):
    """Stable profile identity; missing canonical state fails rather than resetting."""

    character_id: UUID4


class LocalRuntime:
    """Construct off-loop; drain clients and close their engine off-loop."""

    def __init__(
        self,
        config: DeploymentConfig,
        *,
        gateway_available: bool = True,
        thinking_available: bool = False,
    ) -> None:
        self.config = config
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ember-runtime-owner")
        encoder = None
        self.semantic_status = "not_configured"
        if config.embedding_directory is not None:
            try:
                encoder = BgeEncoder(config.embedding_directory)
                self.semantic_status = "available"
            except EmbeddingError:
                self.semantic_status = "unavailable_lexical_fallback"
        self.engine = CanonicalEngine(config.data_root, encoder=encoder, auto_refresh=True)
        try:
            config.runtime_root.mkdir(parents=True, exist_ok=True)
            binding_path = config.runtime_root / (config.active_profile + "-character.json")
            if binding_path.exists():
                binding = CharacterBinding.model_validate_json(binding_path.read_bytes())
                store = self.engine.character(binding.character_id)
            else:
                created = self.engine.create_character()
                store = self.engine.character(created.store_id)
                binding = CharacterBinding(character_id=created.store_id)
                with binding_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(binding.model_dump_json())
            self.subject = ensure_subject(self.engine, binding.character_id)
            self.author = MemoryAuthoringService(
                self.engine, self.subject, authority=MutationAuthority(mode="confirmed_user")
            )
            personality_path = config.runtime_root / (config.active_profile + "-personality.json")
            legacy_exists = personality_path.is_file()
            self.personality = (
                self._personality(personality_path) if legacy_exists else Personality()
            )
            token = (
                (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
                if gateway_available
                else ""
            )
            self.memory = InProcessAsyncMemoryClient(store)

            def deep_client(deadline: float) -> InProcessAsyncMemoryClient:
                budget = ReadBudget(deadline=deadline)
                return InProcessAsyncMemoryClient(
                    store, deep_sources=DeepSources(self.engine, budget), read_budget=budget
                )

            self.deep = DeepRecallService(binding.character_id, deep_client, validation=self.memory)
            self.model = HttpGatewayClient(config.services.gateway.endpoint, token)
            self.dashboard = Dashboard(
                model=config.model_tag,
                backend=config.inference.backend,
                context_tokens=config.context_tokens,
                health=self.model.health,
            )
            self.model.observe_event = self.dashboard.event
            self.capture = CaptureService(self.engine, binding.character_id)
            self.sink = ArchiveDialogueSink(self.engine.archive(binding.character_id), self.subject)
            ceiling = min(
                config.max_input_tokens,
                config.context_tokens - max(1192, config.output_reserve_tokens),
            )
            self.prompt_files = PromptFiles(
                config.system_prompt_file or config.runtime_root / "prompts/system.md",
                config.data_root / "characters" / str(binding.character_id) / "config",
                binding.character_id,
                config.active_profile,
                self.personality,
                legacy_exists,
                ceiling,
            )
            self.personality = self.personality.model_copy(update={"name": self.prompt_files.name})
            self.core = CharacterCore(
                self.memory,
                self.model,
                model_tag=config.model_tag,
                input_ceiling=ceiling,
                thinking_available=thinking_available,
                output_ceiling=config.output_reserve_tokens,
                personality=self.personality,
                sink=self.sink,
                timezone=config.recall_timezone,
                natural_deep_recall=config.natural_deep_recall,
                interpreter=CalendarInterpreter(
                    self.model,
                    config.model_tag,
                    timeout_seconds=config.interpretation.timeout_seconds,
                )
                if config.interpretation.enabled
                else None,
            )
            self.core.load_prompts = self.load_prompt_snapshot
            self._observe_core(self.core)
        except Exception:
            self.engine.close()
            self.pool.shutdown(wait=True)
            raise

    async def load_prompt_snapshot(self) -> PromptSnapshot:
        """Pin one bounded file pair using the existing off-loop runtime worker."""
        return await asyncio.wrap_future(self.pool.submit(self.prompt_files.load))

    def _observe_core(self, core: CharacterCore) -> None:
        def observe(turn: PreparedTurn) -> None:
            self.dashboard.capture(
                turn.request,
                budget=core.input_ceiling,
                input_units=turn.accounting.serialized_total if turn.accounting else None,
                validate=partial(core.current, turn.dependencies, turn.certificate),
            )

        core.activity = self.dashboard.activity
        core.observe_request = observe
        core.observe_end = self.dashboard.finish
        core.observe_input_started = self.dashboard.retire_context

    def new_text_core(self, *, input_ceiling: int, recall_enabled: bool) -> CharacterCore:
        """Share the character and ABI owner while giving a client its own bounded dialogue."""
        core = CharacterCore(
            self.memory,
            self.model,
            model_tag=self.config.model_tag,
            input_ceiling=min(input_ceiling, self.core.input_ceiling),
            personality=self.personality,
            sink=self.sink,
            timezone=self.config.recall_timezone,
            recall_enabled=recall_enabled,
        )
        core.load_prompts = self.load_prompt_snapshot
        core.activity = self.dashboard.activity
        core.public_search = self.core.public_search
        core.public_page = self.core.public_page
        core.public_web = self.core.public_web
        core.search_provider = self.core.search_provider
        core.search_unavailable = self.core.search_unavailable
        core.plan_web = self.core.plan_web
        return core

    async def start_public_search(self) -> None:
        """Compose only the configured public transport, independent of tasks and harnesses."""
        from .exa_mcp import ExaMCP
        from .hosted_web import HostedWeb
        from .native_search import NativeSearch, SearXNGSearch

        settings = self.config.public_search
        if settings is None:
            return
        self.core.search_provider = settings.provider
        self.core.plan_web = True
        if settings.provider == "exa_mcp":
            self.core.public_web = ExaMCP()
        elif settings.provider in ("exa", "parallel"):
            assert settings.api_key_env is not None
            hosted = HostedWeb(settings.provider, settings.api_key_env)
            if not hosted.configured:
                self.core.search_unavailable = "public_web_credentials_unavailable"
                return
            self.core.public_web = hosted
        elif settings.searxng_url is not None:
            self.core.public_search = SearXNGSearch(settings.searxng_url)
        elif await asyncio.to_thread(Path(settings.command[0]).is_file):
            self.core.public_search = NativeSearch(
                settings.command, self.config.runtime_root / "public-search-homes"
            )
        else:
            self.core.search_unavailable = "public_search_executable_unavailable"

    async def read_prompts(self) -> PromptDocuments:
        value = await asyncio.wrap_future(self.pool.submit(self.prompt_files.read))
        return value.model_copy(
            update={
                "applied_revision": self.core.applied_prompts.revision
                if self.core.applied_prompts
                else None
            }
        )

    async def edit_prompt(self, value: PromptEdit, *, save: bool) -> PromptDocuments:
        future = asyncio.wrap_future(self.pool.submit(self.prompt_files.edit, value, save=save))
        try:
            result = await asyncio.shield(future)
        except asyncio.CancelledError:
            # Keep admission fenced until an accepted write has finished.
            await asyncio.shield(future)
            raise
        return result.model_copy(
            update={
                "applied_revision": self.core.applied_prompts.revision
                if self.core.applied_prompts
                else None
            }
        )

    @staticmethod
    def _personality(path: Path) -> Personality:
        if path.exists():
            return Personality.model_validate_json(path.read_bytes())
        value = Personality()
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(value.model_dump_json(indent=2))
        return value

    async def investigate(self, query: str, observation: TurnObservation) -> DeepRecallResult:
        """Bind explicit text/speech recall to original observation and trusted character scope."""
        if observation.context_id != self.core.context_id:
            raise GatewayError("turn_observation_mismatch")
        return await self.deep.investigate(
            DeepRecallRequest(
                request_id=observation.request_id,
                character_id=self.memory.character_id,
                context_id=observation.context_id,
                query=query,
                reference_at=observation.observed_at,
                timezone=self.config.recall_timezone,
                previous_scope=self.core.last_scope,
            )
        )

    async def processing_status(self) -> ProcessingStatus:
        """Aggregate only on operator request; ordinary turns do not inspect job machinery."""
        summary = await asyncio.wrap_future(self.pool.submit(self.capture.summary))
        counts = {
            name: getattr(summary, name)
            for name in (
                "queued",
                "paused",
                "processing",
                "failed",
                "complete",
                "accepted",
                "supported",
                "superseded",
                "deferred",
                "rejected",
            )
        }
        return ProcessingStatus(
            counts=counts,
            failure_codes=summary.failure_codes,
            index_status=(await self.memory.capabilities()).index_status,
        )

    async def libraries(self) -> tuple[LibraryChoice, ...]:
        """Use registered source metadata; no new catalog, grant or archive scan is needed."""

        def choices() -> tuple[LibraryChoice, ...]:
            return tuple(
                LibraryChoice(
                    library_id=info.store_id,
                    namespace=info.namespace,
                    available=info.availability == "available",
                )
                for info in self.engine.stores()
                if info.kind == "library"
            )[:128]

        return await asyncio.wrap_future(self.pool.submit(choices))

    async def capture_settings(self) -> CaptureControl:
        """Inspect persistent operator controls off the realtime loop."""
        settings = await asyncio.wrap_future(self.pool.submit(self.capture.settings))
        return CaptureControl(enabled=settings.enabled, revision=settings.revision)

    async def configure_capture(self, value: CaptureControl) -> CaptureControl:
        """Operator-confirmed capture CAS never accepts imported claims."""
        settings = await asyncio.wrap_future(
            self.pool.submit(
                self.capture.configure,
                CaptureSettings(**value.model_dump()),
                MutationAuthority(mode="confirmed_user"),
            )
        )
        return CaptureControl(enabled=settings.enabled, revision=settings.revision)

    async def memory_graph(self, request: GraphInput) -> str:
        """Project the current character's graph into the private browser response."""
        graph = await self.memory.memory_graph(
            GraphQuery(
                text=request.text, kind=request.kind, cursor=request.cursor, anchor=request.anchor
            )
        )
        return graph.model_dump_json()

    async def recall_settings(self) -> RecallSelection:
        """Inspect controls using existing ABI workers."""
        return await self.memory.recall_selection()

    async def configure_recall(self, value: RecallSelection) -> RecallSelection:
        """Confirmed-user mutation stays in composition rather than model tool execution."""
        store = self.engine.character(
            self.memory.character_id, authority=MutationAuthority(mode="confirmed_user")
        )
        return await asyncio.wrap_future(self.pool.submit(store.configure_recall, value))

    async def opencode_note_writer(self) -> Callable[[str], Awaitable[MutationReceipt]]:
        """Prepare one delegated writer, bound to the current character and runtime worker."""
        return await self._note_writer(coding=False)

    async def coding_note_writer(self) -> Callable[[str], Awaitable[MutationReceipt]]:
        """Prepare the coding-only writer for the active character."""
        return await self._note_writer(coding=True)

    async def _note_writer(self, *, coding: bool) -> Callable[[str], Awaitable[MutationReceipt]]:
        authority = MutationAuthority(mode="coding_notes" if coding else "opencode_notes")
        service = LibraryService(self.engine, authority=authority)
        ensure = service.ensure_coding_library if coding else service.ensure_opencode_library
        append = service.save_coding_note if coding else service.save_opencode_note
        await asyncio.wrap_future(self.pool.submit(ensure))
        save = partial(append, self.memory.character_id)

        async def write(note: str) -> MutationReceipt:
            future = asyncio.wrap_future(self.pool.submit(save, note))
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                # Keep caller admission held until the accepted write drains.
                await asyncio.shield(future)
                raise

        return write

    async def remember(self, key: str, text: str) -> str:
        """Explicit human entry shared by terminal and room composition; never model-invoked."""
        request = NewMemoryInput(idempotency_key=key, text=text)
        receipt = await asyncio.wrap_future(self.pool.submit(self.author.create, request))
        return receipt.outcome

    async def consolidate(self, should_continue: Callable[[], bool] | None = None) -> int:
        """Process a bounded idle batch; gateway P3 admission owns GPU isolation."""
        pending = await asyncio.wrap_future(
            self.pool.submit(self.capture.pending, runnable_only=True)
        )
        if not pending:
            return 0
        token_path = self.config.runtime_root / "gateway-background.token"
        try:
            token = await asyncio.wrap_future(
                self.pool.submit(token_path.read_text, encoding="utf-8")
            )
        except FileNotFoundError as error:
            # A non-progressing pending count would make idle continuation spin.
            raise GatewayError("native_capture_credentials_unavailable") from error
        model = GatewayExtractionModel(
            self.config.services.gateway.endpoint,
            token.strip(),
            native_temporal_specification(
                self.config.model_tag,
                self.config.model_digest,
                timezone=self.config.recall_timezone,
                workload="P3",
            ),
        )

        def model_for(spec: ExtractionSpec) -> GatewayExtractionModel:
            # The batch owner closes the shared transport once; each job retains immutable pins.
            return (
                model
                if model.specification == spec
                else GatewayExtractionModel(
                    self.config.services.gateway.endpoint, token.strip(), spec, client=model.client
                )
            )

        try:
            async with asyncio.timeout(90):
                remaining = await self.capture.drain(
                    model, self.pool, should_continue=should_continue, model_for=model_for
                )
            return len(remaining)
        except ProcessingError as error:
            raise GatewayError("native_capture_deferred") from error
        finally:
            await model.aclose()

    async def aclose(self) -> None:
        """Drain native writes before closing their engine owner; never stop external services."""
        self.core.turn_view.clear()

        async def close_engine() -> None:
            await asyncio.wrap_future(self.pool.submit(self.engine.close))

        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(close_engine)
            cleanup.push_async_callback(self.model.aclose)
            cleanup.push_async_callback(self.memory.aclose)
            cleanup.push_async_callback(self.sink.aclose)
            cleanup.push_async_callback(self.deep.aclose)
            cleanup.push_async_callback(self.dashboard.aclose)

    def close_workers(self) -> None:
        """Join the drained lifecycle worker outside the realtime loop."""
        self.pool.shutdown(wait=True)

    async def chat(self) -> None:
        """Hold conservative realtime ownership for the entire connected terminal session."""
        identity = uuid4()
        try:
            await self.model.session(identity, connected=True)
            await self.start_public_search()
            await self._chat()
        finally:
            try:
                await self.model.session(identity, connected=False)
                try:
                    remaining = await self.consolidate()
                    if remaining:
                        print(
                            "Native capture has pending work; resume during the next idle window."
                        )
                except TimeoutError:
                    print("Native consolidation paused at its durable checkpoint.")
                except GatewayError as error:
                    print("Native consolidation deferred: " + error.code)
            finally:
                await self.aclose()

    async def _chat(self) -> None:
        """Foreground text loop using the same character core as the LiveKit adapter."""
        print(
            "Character: "
            + str(self.memory.character_id)
            + ". Use /remember KEY TEXT to save; /quit to finish."
        )
        while True:
            try:
                text = await read_line("You: ")
            except EOFError:
                break
            if text.strip() == "/quit":
                break
            if text.startswith("/remember "):
                parts = text.split(" ", 2)
                if len(parts) != 3:
                    print("Usage: /remember KEY TEXT")
                    continue
                try:
                    request = NewMemoryInput(idempotency_key=parts[1], text=parts[2])
                    receipt = await asyncio.wrap_future(
                        self.pool.submit(self.author.create, request)
                    )
                    print("Memory " + receipt.outcome + ".")
                except MemoryError as error:
                    print(str(error))
                except ValidationError:
                    print("Memory input is invalid.")
                continue
            try:
                investigation = None
                observation = None
                if text.startswith("/recall "):
                    query = text.removeprefix("/recall ").strip()
                    if not query:
                        print("Usage: /recall QUESTION")
                        continue
                    observation = await self.core.observe_input(text)
                    investigation = await self.investigate(query, observation)
                    print(
                        "Deep recall: "
                        + investigation.outcome
                        + "; "
                        + str(len(investigation.memories))
                        + " memories; "
                        + str(len(investigation.observations))
                        + " source observations."
                    )
                    if investigation.reasons:
                        print("Coverage: " + ", ".join(investigation.reasons))
                turn = await self.core.prepare(
                    text, investigation=investigation, observation=observation
                )
                print(self.personality.name + ": ", end="", flush=True)
                async for event in self.core.stream(turn):
                    if event.kind == "text":
                        print(event.text, end="", flush=True)
                print()
            except (GatewayError, MemoryError) as error:
                print(str(error))
