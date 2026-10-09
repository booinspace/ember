"""Only local composition constructs concrete memory; character and adapters receive contracts."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

from pydantic import UUID4, ValidationError

from ember_memory_abi.errors import MemoryError
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.embedding import BgeEncoder
from ember_memory_engine.errors import EmbeddingError

from ..character.core import CharacterCore, Personality
from ..inference.client import HttpGatewayClient
from ..inference.contracts import GatewayError
from ..memory.backends.dialogue import ArchiveDialogueSink
from .config import DeploymentConfig, RuntimeModel
from .console import read_line


class CharacterBinding(RuntimeModel):
    """Stable profile identity; missing canonical state fails rather than resetting."""

    character_id: UUID4


class LocalRuntime:
    """Construct off-loop; drain clients and close their engine off-loop."""

    def __init__(self, config: DeploymentConfig) -> None:
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
            self.personality = self._personality(personality_path)
            token = (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
            self.memory = InProcessAsyncMemoryClient(store)
            self.model = HttpGatewayClient(config.services.gateway.endpoint, token)
            self.sink = ArchiveDialogueSink(self.engine.archive(binding.character_id), self.subject)
            ceiling = min(
                config.max_input_tokens,
                config.context_tokens - max(1192, config.output_reserve_tokens),
            )
            self.core = CharacterCore(
                self.memory,
                self.model,
                model_tag=config.model_tag,
                input_ceiling=ceiling,
                personality=self.personality,
                sink=self.sink,
            )
        except Exception:
            self.engine.close()
            self.pool.shutdown(wait=True)
            raise

    @staticmethod
    def _personality(path: Path) -> Personality:
        if path.exists():
            return Personality.model_validate_json(path.read_bytes())
        value = Personality()
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(value.model_dump_json(indent=2))
        return value

    async def remember(self, key: str, text: str) -> str:
        """Explicit human entry shared by terminal and room composition; never model-invoked."""
        request = NewMemoryInput(idempotency_key=key, text=text)
        receipt = await asyncio.wrap_future(self.pool.submit(self.author.create, request))
        return receipt.outcome

    async def aclose(self) -> None:
        """Drain native writes before closing their engine owner; never stop external services."""

        async def close_engine() -> None:
            await asyncio.wrap_future(self.pool.submit(self.engine.close))

        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(close_engine)
            cleanup.push_async_callback(self.model.aclose)
            cleanup.push_async_callback(self.memory.aclose)
            cleanup.push_async_callback(self.sink.aclose)

    def close_workers(self) -> None:
        """Join the drained lifecycle worker outside the realtime loop."""
        self.pool.shutdown(wait=True)

    async def chat(self) -> None:
        """Hold conservative realtime ownership for the entire connected terminal session."""
        identity = uuid4()
        try:
            await self.model.session(identity, connected=True)
            await self._chat()
        finally:
            try:
                await self.model.session(identity, connected=False)
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
                turn = await self.core.prepare(text)
                print(self.personality.name + ": ", end="", flush=True)
                async for event in self.core.stream(turn):
                    if event.kind == "text":
                        print(event.text, end="", flush=True)
                print()
            except (GatewayError, MemoryError) as error:
                print(str(error))
