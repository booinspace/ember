import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError, GatewayEvent, GatewayRequest
from ember_memory_abi.interfaces import ContextRequest, RetrievalContext
from ember_memory_abi.related import ContextDependencies, MemoryReference
from ember_memory_engine import CanonicalEngine
from ember_memory_engine.async_client import InProcessAsyncMemoryClient


class NoGeneration:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        self.requests.append(request)
        yield GatewayEvent(
            request_id=request.request_id,
            context_id=request.context_id,
            sequence=0,
            kind="error",
            code="unexpected_generation",
        )


def test_no_recall_excludes_memory_bound_history_and_does_not_probe_canonical_memory(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

            async def forbidden(*args: object, **kwargs: object) -> object:
                raise AssertionError("NoRecall attempted a canonical memory read")

            memory.capabilities = forbidden
            memory.retrieve_context = forbidden
            memory.get = forbidden
            core = CharacterCore(
                memory, NoGeneration(), model_tag="synthetic", recall_enabled=False
            )
            dependency = (uuid4(), 1)
            core.history.append(
                CompletedTurn("Earlier input", "PRIVATE_MEMORY_CANARY", (dependency,))
            )
            core.history.append(
                CompletedTurn(
                    "Another input",
                    "PRIVATE_CERTIFICATE_CANARY",
                    (),
                    certificate=ContextDependencies(
                        memories=(MemoryReference(memory_id=uuid4(), revision=1),),
                        policy_epoch=0,
                    ),
                )
            )
            assert not await core.current((dependency,))
            turn = await core.prepare("What happened last summer?")
            assert turn.retained_memories == 0
            assert not turn.dependencies and turn.certificate is None
            assert "PRIVATE_MEMORY_CANARY" not in turn.request.model_dump_json()
            assert "PRIVATE_CERTIFICATE_CANARY" not in turn.request.model_dump_json()

    asyncio.run(run())


def test_public_search_precedes_recall_and_stays_in_the_existing_prompt_budget(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))
            events: list[str] = []
            retrieve = memory.retrieve_context

            async def checked(request: ContextRequest) -> RetrievalContext:
                events.append("recall")
                assert events[0] == "search"
                return await retrieve(request)

            async def search(query: str) -> str:
                events.append("search")
                assert query == "public API documentation"
                return '{"results":[{"url":"https://public.example/","title":"Public"}]}'

            memory.retrieve_context = checked
            core = CharacterCore(memory, NoGeneration(), model_tag="synthetic", input_ceiling=4096)
            core.public_search = search
            turn = await core.prepare("Search for public API documentation")
            assert events[:2] == ["search", "recall"]
            assert "https://public.example/" in turn.request.model_dump_json()
            assert turn.accounting is not None and turn.accounting.serialized_total <= 4096

    asyncio.run(run())


def test_failed_search_clarifies_without_generation_or_private_recall(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

            async def search(query: str) -> str:
                raise GatewayError("public_search_unavailable")

            async def forbidden(request: ContextRequest) -> RetrievalContext:
                raise AssertionError("Recall ran after failed public search")

            memory.retrieve_context = forbidden
            model = NoGeneration()
            core = CharacterCore(memory, model, model_tag="synthetic")
            core.public_search = search
            turn = await core.prepare("Search for public documentation")
            assert turn.local_reply is not None and "unavailable" in turn.local_reply
            assert model.requests == []
            assert core.history[-1].user == "Search for public documentation"

    asyncio.run(run())
