"""Public page reads use an installed harness adapter while Ember owns grants and receipts."""

import json
from typing import cast

from pydantic import Field, JsonValue

from .config import RuntimeModel
from .searx_tools import public_url
from .skills import Skills
from .task_router import ToolContext, ToolReceipt
from .task_store import TaskConflict


class PageRequest(RuntimeModel):
    """A page read cannot carry cookies, headers, credentials or extraction instructions."""

    url: str = Field(min_length=1, max_length=512)


class PublicPageProvider:
    """The same public-only reader serves foreground preflight and explicitly granted tasks."""

    capabilities = frozenset({"read_page"})

    def __init__(self, skills: Skills) -> None:
        self.skills = skills

    async def read(self, url: str) -> str:
        value = PageRequest(url=url)
        if not public_url(value.url):
            raise TaskConflict("page_url_denied")
        result = await self.skills.client.call("read_page", value.url)
        if not isinstance(result, dict):
            raise TaskConflict("page_result_invalid")
        if (
            not isinstance(final := result.get("url"), str)
            or not public_url(final)
            or result.get("requested_url") != value.url
            or not isinstance(result.get("text"), str)
            or type(result.get("partial")) is not bool
        ):
            raise TaskConflict("page_result_invalid")
        raw = json.dumps(result, ensure_ascii=False, allow_nan=False)
        if len(raw.encode()) > 2560:
            raise TaskConflict("page_result_too_large")
        return raw

    async def invoke(
        self, name: str, arguments: dict[str, JsonValue], context: ToolContext
    ) -> ToolReceipt:
        if name != "read_page":
            raise TaskConflict("tool_unavailable")
        request = PageRequest.model_validate(arguments)
        data = json.loads(await self.read(request.url))
        return ToolReceipt(context=context, tool=name, data=cast(dict[str, JsonValue], data))
