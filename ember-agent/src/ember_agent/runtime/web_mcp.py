"""Public retrieval over pinned MCP tools. Only approved literal input enters arguments."""

import os
import re

import httpx
from mcp.shared.exceptions import MCPError
from pydantic import JsonValue

from ..web_evidence import (
    PublicWebOperation,
    WebEvidence,
    WebFailure,
    allowed_public_url,
    permitted_public_query,
    web_failure,
)
from .mcp_client import MCPClient, MCPWireError
from .web_mcp_decoders import resolve_decoder
from .web_mcp_settings import MCPWebBinding, binding_revision

_EXA = {
    "provider": "exa_mcp",
    "label": "Exa MCP",
    "endpoint": "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa",
    "ipv4": True,
    "result": "exa_text",
}
EXA_SEARCH = MCPWebBinding.model_validate(
    {
        **_EXA,
        "tool": "web_search_exa",
        "input_field": "query",
        "fixed": {"numResults": 1},
    }
)
EXA_READ = MCPWebBinding.model_validate(
    {
        **_EXA,
        "tool": "web_fetch_exa",
        "input_field": "urls",
        "input_as_list": True,
        "fixed": {"maxCharacters": 2600},
    }
)


class MCPWeb:
    """One immutable operation binding, independent of Conversation and task consent."""

    def __init__(
        self,
        binding: MCPWebBinding,
        operation: PublicWebOperation,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 8.0,
    ) -> None:
        self._binding = binding.model_copy(deep=True)
        self._decoder = resolve_decoder(self._binding.result)
        self.operation: PublicWebOperation = operation
        self.revision = binding_revision("mcp-web-v1", operation, binding.model_dump(mode="json"))
        self._transport = transport
        self._timeout = timeout

    def _token(self) -> str:
        token = os.environ.get(self._binding.bearer_token_env or "", "")
        return token if re.fullmatch(r"[!-~]{1,1024}", token) else ""

    @property
    def configured(self) -> bool:
        return not self._binding.bearer_token_env or bool(self._token())

    async def __call__(self, value: str) -> WebEvidence:
        binding, operation = self._binding, self.operation
        token = self._token()

        def failed(code: WebFailure) -> WebEvidence:
            return web_failure(binding.provider, operation, code)

        if not (
            permitted_public_query(value) if operation == "search" else allowed_public_url(value)
        ):
            return failed("input_rejected")
        if (allows_input := self._decoder.allows_input) is not None and not allows_input(
            operation, value
        ):
            return failed("input_rejected")
        if binding.bearer_token_env and not token:
            return failed("unavailable")
        # No credential in arguments, endpoint, or evidence. Do not pass it to the decoder.
        arguments: dict[str, JsonValue] = {key: arg for key, arg in binding.fixed.items()}
        arguments[binding.input_field] = [value] if binding.input_as_list else value
        try:
            result = await MCPClient().public_call(
                binding.endpoint,
                binding.tool,
                arguments,
                bearer=token,
                ipv4=binding.ipv4,
                transport=self._transport,
                timeout=self._timeout,
            )
            if result.get("isError") is True:
                return failed("tool_error")
            content = {
                key: result[key] for key in ("content", "structuredContent") if key in result
            }
            evidence = self._decoder.decode(binding.provider, operation, value, content)
            if operation == "read" and any(
                httpx.URL(source.url) != httpx.URL(value) for source in evidence.sources
            ):
                return failed("invalid_response")
            return evidence
        except MCPWireError as error:
            return failed(error.code)
        except (TimeoutError, httpx.TimeoutException):
            return failed("timeout")
        except httpx.HTTPError:
            return failed("unavailable")
        except (ValueError, MCPError, RecursionError):
            return failed("invalid_response")
