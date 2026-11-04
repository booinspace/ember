"""Operator prompt files preserve scope, budgets, observations and pinned turn instructions."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from ember_agent.character.core import CharacterCore, Personality, prompt_cost
from ember_agent.character.prompts import PromptDocuments, PromptEdit, PromptKind, compile_prompts
from ember_agent.inference.contracts import GatewayError
from ember_agent.memory.deep_recall import DeepRecallService
from ember_agent.realtime.contracts import RoomBinding
from ember_agent.realtime.session import HostedRoom
from ember_agent.runtime.browser_control import BrowserControl
from ember_agent.runtime.prompt_files import PromptFiles
from ember_memory_engine import CanonicalEngine, MutationAuthority
from ember_memory_engine.async_client import InProcessAsyncMemoryClient
from ember_memory_engine.authoring import MemoryAuthoringService, NewMemoryInput, ensure_subject
from ember_memory_engine.bundles import BundleService, restore_bundles

from .test_character import FakeModel
from .test_phase_eight_browser import ORIGIN, Controls


def files(
    root: Path,
    identity: UUID | None = None,
    *,
    legacy: Personality | None = None,
    profile: str = "first",
    exists: bool = False,
) -> PromptFiles:
    identity = identity or uuid4()
    return PromptFiles(
        root / "prompts/system.md",
        root / "characters" / str(identity) / "config",
        identity,
        profile,
        legacy or Personality(),
        exists,
        24576,
    )


def edit(owner: PromptFiles, text: str, *, kind: PromptKind = "personality") -> PromptEdit:
    current = owner.read()
    return PromptEdit(
        kind=kind,
        markdown=text,
        system_revision=current.system.revision,
        personality_revision=current.personality.revision,
    )


def test_files_share_system_isolate_character_and_preserve_failed_writes(tmp_path: Path) -> None:
    one, two = files(tmp_path), files(tmp_path)
    initial = one.load()
    preview = one.edit(edit(one, "# Cobalt\nSpeak precisely."), save=False)
    assert preview.snapshot and "Cobalt" in preview.snapshot.instructions
    assert one.load() == initial
    stale = edit(one, "Stale draft")
    one.edit(edit(one, "# Cobalt\nSpeak precisely."), save=True)
    assert "Cobalt" not in two.load().instructions
    assert one.personality.with_name("personality.md.previous").read_text() == initial.personality
    with pytest.raises(GatewayError, match="prompt_revision_conflict"):
        one.edit(stale, save=True)
    one.edit(edit(one, "# Shared\nUse short sentences.", kind="system"), save=True)
    assert one.load().system_revision == two.load().system_revision
    before = one.personality.read_bytes()
    with patch("ember_agent.runtime.prompt_files.os.fsync", side_effect=OSError("synthetic")):
        with pytest.raises(GatewayError, match="prompt_write_failed"):
            one.edit(edit(one, "Replacement"), save=True)
    assert one.personality.read_bytes() == before
    with one._locks():
        with pytest.raises(GatewayError, match="prompt_files_unavailable"):
            two.read()
    assert not list(one.personality.parent.glob("*.tmp"))


def test_migration_custom_profiles_and_invalid_external_text(tmp_path: Path) -> None:
    legacy = Personality(name="Synthetic", instructions="Custom identity and style, verbatim.\n")
    one = files(tmp_path, legacy=legacy, exists=True)
    assert one.load().personality == legacy.instructions
    # Same profile's JSON becomes historical input once Markdown has been provisioned.
    restarted = files(
        tmp_path, one.character_id, legacy=Personality(), profile="first", exists=True
    )
    assert restarted.name == "Synthetic"
    assert restarted.load().personality == legacy.instructions
    with pytest.raises(GatewayError, match="prompt_legacy_profile_conflict"):
        files(tmp_path, one.character_id, legacy=Personality(), profile="other", exists=True)
    original = one.personality.read_bytes()
    one.personality.write_bytes(b"\xff broken")
    assert one.read().error == "prompt_invalid_utf8"
    with pytest.raises(GatewayError, match="prompt_invalid_utf8"):
        one.load()
    one.edit(edit(one, "# Repaired\nExact Markdown.\n"), save=True)
    assert one.load().personality.endswith("\n")
    assert one.personality.with_name("personality.md.previous").read_bytes() == b"\xff broken"
    assert original == legacy.instructions.encode()
    one.personality.unlink()
    restarted = files(tmp_path, one.character_id, legacy=legacy, exists=True)
    assert not restarted.personality.exists() and restarted.read().error == "prompt_file_missing"
    restarted.edit(edit(restarted, "# Recovered explicitly"), save=True)
    assert restarted.load().personality == "# Recovered explicitly"
    # UTF-8/framing guards account for escaping and never enlarge the 24K total.
    for text in ("", "🌊" * 1500, '"\\\n' * 2000):
        before = one.personality.read_bytes()
        with pytest.raises(GatewayError):
            one.edit(edit(one, text), save=True)
        assert one.personality.read_bytes() == before
    with pytest.raises(GatewayError, match="prompt_instruction_budget_exceeded"):
        compile_prompts("S" * 800, "P" * 800, 4000)
    link = tmp_path / "alias.md"
    try:
        link.symlink_to(one.personality)
    except OSError:
        return  # Windows synthetic accounts may lack symlink creation privileges.
    with pytest.raises(GatewayError, match="prompt_path_invalid"):
        PromptFiles(link, tmp_path / "separate", uuid4(), "alias", Personality(), False, 24576)


def test_core_pins_snapshot_before_recall_without_resetting_history(tmp_path: Path) -> None:
    with CanonicalEngine(tmp_path / "data") as engine:
        character = engine.create_character().store_id
        owner = files(tmp_path / "config", character)
        memory = InProcessAsyncMemoryClient(engine.character(character))

        async def run() -> None:
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic", input_ceiling=126976)
            core.load_prompts = lambda: asyncio.to_thread(owner.load)
            context = core.context_id
            observation = await core.observe_input("My synthetic aquarium is Moonbeam.")
            owner.edit(edit(owner, "# New style\nAnswer concisely."), save=True)
            first = await core.prepare(observation.text, observation=observation)
            assert first.prompts and first.request.messages[0].content == first.prompts.instructions
            assert "New style" not in first.request.messages[0].content
            _ = [event async for event in core.stream(first)]
            second = await core.prepare("What is my aquarium called?")
            assert "New style" in second.request.messages[0].content
            assert any("Moonbeam" in item.content for item in second.request.messages)
            assert core.context_id == context and prompt_cost(second.request.messages) <= 24576
            clarification = await core.prepare("memories from September")
            assert clarification.local_reply and clarification.prompts
            assert clarification.request.messages[0].content == clarification.prompts.instructions
            owner.personality.write_bytes(b"\xff")
            with pytest.raises(GatewayError, match="prompt_invalid_utf8"):
                await core.prepare("Observed even when configuration fails.")
            assert core.history[-1].user == "Observed even when configuration fails."
            assert core.turn_view.entry and core.turn_view.entry.value.stage == "failed"
            await memory.aclose()

        asyncio.run(run())


def test_authenticated_editor_api_binds_scope_and_rejects_speech_in_progress(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        with CanonicalEngine(tmp_path / "data") as engine:
            identity = engine.create_character().store_id
            owner = files(tmp_path / "config", identity)
            memory = InProcessAsyncMemoryClient(engine.character(identity))
            core = CharacterCore(memory, FakeModel(), model_tag="synthetic")
            deep = DeepRecallService(identity, lambda _: memory, validation=memory)
            binding = RoomBinding(
                instance_id=uuid4(),
                character_id=identity,
                room="ember-" + uuid4().hex,
                endpoint="ws://127.0.0.1:7880",
            )
            host = HostedRoom(core, binding, AsyncMock())

            class Port:
                async def read_prompts(self) -> PromptDocuments:
                    return await asyncio.to_thread(owner.read)

                async def edit_prompt(self, value: PromptEdit, *, save: bool) -> PromptDocuments:
                    return await asyncio.to_thread(owner.edit, value, save=save)

            browser = BrowserControl(
                origin=ORIGIN,
                assets=tmp_path,
                core=core,
                deep=deep,
                binding=binding,
                controls=Controls(),
                room_token=lambda: "unused",
                answer=AsyncMock(),
                prompt_controls=Port(),
                prompt_idle=host.prompt_idle,
            )
            with TestClient(browser.app, base_url=ORIGIN, headers={"origin": ORIGIN}) as client:
                assert client.get("/api/prompts").status_code == 401
                client.post("/api/bootstrap", json={"token": browser.activation})
                response = client.get("/api/prompts")
                assert response.headers["cache-control"] == "no-store"
                assert response.json()["character_id"] == str(identity)
                body = edit(owner, "# Updated\nLiteral <script>text</script>.").model_dump()
                assert client.post("/api/prompts/preview", json=body).status_code == 200
                assert "Updated" not in owner.load().personality
                host.busy = True
                assert (
                    client.put("/api/prompts/personality", json=body).json()["error"]
                    == "prompt_settings_busy"
                )
                host.busy = False
                assert (
                    client.put(
                        "/api/prompts/personality",
                        json=body,
                        headers={"origin": "https://untrusted.invalid"},
                    ).status_code
                    == 403
                )
                assert client.put("/api/prompts/system", json=body).status_code == 409
                assert client.put("/api/prompts/../../etc", json=body).status_code != 200
                assert (
                    client.put(
                        "/api/prompts/personality", json={**body, "path": "untrusted"}
                    ).status_code
                    == 400
                )
                assert client.put("/api/prompts/personality", json=body).status_code == 200
                assert (
                    client.put("/api/prompts/personality", json=body).json()["error"]
                    == "prompt_revision_conflict"
                )
                assert "<script>" in owner.load().instructions
            await browser.aclose()
            await host.aclose()
            await deep.aclose()
            await memory.aclose()

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["linked", "self_contained"])
def test_personality_markdown_travels_with_character_bundles(tmp_path: Path, mode: str) -> None:
    root = tmp_path / "data"
    with CanonicalEngine(root) as engine:
        identity = engine.create_character().store_id
        subject = ensure_subject(engine, identity)
        receipt = MemoryAuthoringService(
            engine, subject, authority=MutationAuthority(mode="confirmed_user")
        ).create(NewMemoryInput(idempotency_key="synthetic", text="Synthetic aquarium is cobalt."))
        owner = PromptFiles(
            tmp_path / "deployment/system.md",
            root / "characters" / str(identity) / "config",
            identity,
            "synthetic",
            Personality(),
            False,
            24576,
        )
        expected = owner.load().personality
    output = tmp_path / "bundle"
    BundleService(root).export_character(
        identity,
        output,
        mode=mode,
        memory_ids=(receipt.affected[0].memory_id,) if mode == "self_contained" else None,
    )
    restored = tmp_path / "restored"
    restore_bundles((output,), restored)
    config = restored / "characters" / str(identity) / "config"
    assert (config / "personality.md").read_text() == expected
    assert (config / "personality-migration.json").is_file()
    assert not (config / "system.md").exists()


def test_prompt_migration_retries_partial_publication_without_overwriting(tmp_path: Path) -> None:
    identity = uuid4()
    publish = PromptFiles._publish

    def interrupted(path: Path, raw: bytes, *, backup: bool = True) -> None:
        if path.name == "personality.md":
            raise OSError("synthetic interrupted migration")
        publish(path, raw, backup=backup)

    with patch.object(PromptFiles, "_publish", side_effect=interrupted):
        with pytest.raises(GatewayError, match="prompt_files_unavailable"):
            files(tmp_path, identity)
    system = (tmp_path / "prompts/system.md").read_bytes()
    restarted = files(tmp_path, identity)
    assert restarted.load().personality
    assert (tmp_path / "prompts/system.md").read_bytes() == system


def test_cancelled_prompt_save_drains_the_owned_write(tmp_path: Path) -> None:
    from threading import Event

    from ember_agent.runtime.composition import LocalRuntime
    from ember_agent.runtime.config import load_config

    config = load_config(Path(__file__).parents[1] / "config/deployment.example.toml").model_copy(
        update={
            "data_root": tmp_path / "data",
            "runtime_root": tmp_path / "runtime",
            "system_prompt_file": tmp_path / "prompts/system.md",
            "embedding_directory": None,
        }
    )
    runtime = LocalRuntime(config, gateway_available=False)
    entered, release = Event(), Event()
    original = runtime.prompt_files.edit

    def blocked(value: PromptEdit, *, save: bool) -> PromptDocuments:
        entered.set()
        assert release.wait(5)
        return original(value, save=save)

    async def exercise() -> None:
        try:
            value = edit(runtime.prompt_files, "# Saved despite lost subscriber")
            with patch.object(runtime.prompt_files, "edit", side_effect=blocked):
                task = asyncio.create_task(runtime.edit_prompt(value, save=True))
                assert await asyncio.to_thread(entered.wait, 2)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert runtime.prompt_files.load().personality == value.markdown
        finally:
            release.set()
            await runtime.aclose()

    try:
        asyncio.run(exercise())
    finally:
        runtime.close_workers()
