"""Installed search origins cannot turn untrusted results into host or tool authority."""

import asyncio
import json
from collections.abc import AsyncIterator
from time import time
from uuid import uuid4

import httpx
import pytest

from ember_agent.runtime.searx_tools import SearXNGTools, plain, public_url
from ember_agent.runtime.task_router import ToolContext, ToolRouter
from ember_agent.runtime.task_store import TaskConflict


class Chunks(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.data), 4096):
            yield self.data[offset : offset + 4096]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/x",
        "http://127.0.0.1/x",
        "http://10.254.254.10/x",
        "https://user:password@public.example/x",
        "https://public.example/a\nsecret",
        "https://host.local/x",
    ],
)
def test_search_rejects_local_and_credential_references(url: str) -> None:
    assert not public_url(url)


def test_only_bounded_public_references_reach_the_native_receipt() -> None:
    async def run() -> None:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = {
                "results": [
                    {"url": "file:///must-not-read", "title": "Denied", "content": "secret"}
                ]
                + [
                    {
                        "url": "https://public.example/" + str(i),
                        "title": "<img src=x onerror=bad>Safe",
                        "content": "<" * 8192 + "Untrusted",
                    }
                    for i in range(10)
                ]
            }
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                stream=Chunks(json.dumps(payload).encode()),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = SearXNGTools("http://installed.invalid", client=http)
            character = uuid4()
            context = ToolContext(
                character_id=character,
                session_id=uuid4(),
                turn_id=uuid4(),
                task_id=uuid4(),
                deadline_at=time() + 5,
            )
            receipt = await ToolRouter(provider, character).invoke(
                "searx_search",
                {"query": "Public synthetic query"},
                context,
                allowed=frozenset({"searx_search"}),
            )
            assert receipt.context == context
            assert len(receipt.data["results"]) == 3
            assert len(receipt.model_dump_json().encode()) <= 4096
            assert len(requests) == 1 and requests[0].url.host == "installed.invalid"
            assert "<img" not in receipt.model_dump_json()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,encoding,payload,code",
    [
        (302, "identity", b"{}", "unavailable"),
        (200, "gzip", b"{}", "unavailable"),
        (200, "identity", b"x" * 131073, "wire_limit"),
    ],
    ids=("redirect", "compressed", "wire-too-large"),
)
def test_redirects_compression_and_oversized_wire_fail_closed(
    status: int, encoding: str, payload: bytes, code: str
) -> None:
    async def run() -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": encoding,
                    "Location": "http://denied.invalid/",
                },
                stream=Chunks(payload),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            provider = SearXNGTools("http://installed.invalid", client=http)
            context = ToolContext(
                character_id=uuid4(), session_id=uuid4(), turn_id=uuid4(), task_id=uuid4()
            )
            with pytest.raises(TaskConflict, match=code):
                await provider.invoke("searx_search", {"query": "Synthetic query"}, context)

    asyncio.run(run())


def test_markup_and_entity_expansion_stay_bounded() -> None:
    assert len(plain("&amp;" * 100000, 128)) <= 128
    assert len(plain("<" * 100000, 128)) <= 128
