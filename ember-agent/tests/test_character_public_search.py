import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

from ember_agent.character.conversation import CompletedTurn
from ember_agent.character.core import CharacterCore
from ember_agent.inference.contracts import GatewayError, GatewayEvent, GatewayRequest
from ember_agent.public_web import PublicWeb, WebBinding
from ember_agent.runtime.public_web_runtime import _snippet_retrieval
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


def test_explicit_snippet_search_does_not_enter_private_recall(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

            async def forbidden(_: ContextRequest) -> RetrievalContext:
                raise AssertionError("Public search entered recall")

            async def search(query: str) -> str:
                assert query == "public API documentation"
                return (
                    '{"results":[{"url":"https://example.com/","title":"Public",'
                    '"snippet":"Public documentation."}]}'
                )

            memory.retrieve_context = forbidden
            core = CharacterCore(memory, NoGeneration(), model_tag="synthetic", input_ceiling=4096)
            core.public_web = PublicWeb(
                WebBinding(
                    "searxng",
                    "SearXNG",
                    "fixture",
                    _snippet_retrieval("searxng", search),
                    snippets=True,
                )
            )
            turn = await core.prepare("SearXNG search: public API documentation")
            assert turn.web_evidence and turn.web_evidence.snippets
            assert turn.web_evidence.sources[0].url == "https://example.com/"
            assert "search snippets only" in turn.request.model_dump_json()
            assert turn.accounting and turn.accounting.serialized_total <= 4096

    asyncio.run(run())


def test_failed_search_clarifies_without_generation_or_private_recall(tmp_path: Path) -> None:
    async def run() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            character = engine.create_character()
            memory = InProcessAsyncMemoryClient(engine.character(character.store_id))

            async def search(_: str) -> str:
                raise GatewayError("public_search_unavailable")

            async def forbidden(_: ContextRequest) -> RetrievalContext:
                raise AssertionError("Recall ran after failed public search")

            memory.retrieve_context = forbidden
            model = NoGeneration()
            core = CharacterCore(memory, model, model_tag="synthetic")
            core.public_web = PublicWeb(
                WebBinding(
                    "searxng",
                    "SearXNG",
                    "fixture",
                    _snippet_retrieval("searxng", search),
                    snippets=True,
                )
            )
            turn = await core.prepare("SearXNG search: public documentation")
            assert turn.local_reply is not None and "unavailable" in turn.local_reply
            assert model.requests == []
            assert core.history[-1].user == "SearXNG search: public documentation"

    asyncio.run(run())
