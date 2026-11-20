"""Strict gateway transport; no alternate upstream or automatic generation retry."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from uuid import UUID

import httpx
from pydantic import TypeAdapter, ValidationError

from ..observability import notify
from .contracts import GatewayError, GatewayEvent, GatewayHealth, GatewayRequest
from .frames import bounded_lines

_LOG = logging.getLogger(__name__)
_ERROR = TypeAdapter(dict[str, str])
_REJECTIONS = frozenset(
    {
        "unauthorized",
        "workload_not_authorized",
        "request_too_large",
        "invalid_request",
        "draining",
        "model_not_allowlisted",
        "journal_unavailable",
        "quiescence_required",
        "live_reservation_required",
        "busy",
        "background_queue_full",
        "background_requires_structured_output",
        "deadline_exceeded",
        "output_budget_exceeded",
        "prompt_overflow",
        "duplicate_request",
        "request_registry_full",
        "duplicate_tool_definition",
        "unsupported_tool_schema",
        "invalid_tool_schema",
        "invalid_output_schema",
        "external_schema_reference",
        "journal_busy",
        "journal_closed",
        "model_pin_mismatch",
        "runner_context_mismatch",
        "invalid_upstream_metadata",
        "upstream_unavailable",
        "upstream_configuration_mismatch",
        "upstream_credentials_unavailable",
        "upstream_metadata_too_large",
        "upstream_encoded_response",
        "upstream_instance_changed",
        "upstream_profile_mismatch",
        "upstream_version_mismatch",
        "model_artifact_mismatch",
    }
)


async def _rejection_code(response: httpx.Response) -> str:
    # Error bodies are untrusted too; retain only known codes within byte/time bounds.
    if response.headers.get("content-encoding", "identity") != "identity":
        return "gateway_rejected"
    try:
        async with asyncio.timeout(1):
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=1024):
                if len(body) + len(chunk) > 1024:
                    break
                body.extend(chunk)
            else:
                value = _ERROR.validate_json(body)
                if set(value) == {"code"} and value["code"] in _REJECTIONS:
                    return value["code"]
    except (httpx.HTTPError, ValidationError, TimeoutError):
        pass
    return "gateway_rejected"


class HttpGatewayClient:
    """Owned local authenticated transport; closing a stream only detaches its subscriber."""

    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        observe_event: Callable[[GatewayEvent], None] | None = None,
    ) -> None:
        self.client = client or httpx.AsyncClient(base_url=endpoint, timeout=190, trust_env=False)
        self.headers = {"Authorization": "Bearer " + token}
        self.observe_event = observe_event
        self.observation_failures = 0

    async def health(self) -> GatewayHealth:
        """Return validated content-free gateway state."""
        try:
            response = await self.client.get("/health", headers=self.headers, timeout=5)
            response.raise_for_status()
            return GatewayHealth.model_validate_json(response.content)
        except (httpx.HTTPError, ValidationError) as error:
            raise GatewayError("gateway_unavailable") from error

    async def session(self, identity: UUID, *, connected: bool) -> None:
        """Reserve/release a trusted realtime lifetime, waiting for safe native quiescence."""
        try:
            response = await self.client.put(
                "/v1/realtime/" + str(identity),
                headers=self.headers,
                json={"connected": connected},
                timeout=190,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise GatewayError("realtime_reservation_failed") from error

    async def stream(self, request: GatewayRequest) -> AsyncGenerator[GatewayEvent, None]:
        """Validate identities/order and require an explicit end; partial EOF is never success."""
        sequence = 0
        started = False
        try:
            async with self.client.stream(
                "POST", "/v1/generate", headers=self.headers, content=request.model_dump_json()
            ) as response:
                if response.status_code != 200:
                    code = await _rejection_code(response)
                    _LOG.warning(
                        "Inference request rejected: HTTP %d, %s", response.status_code, code
                    )
                    if self.observe_event is not None:
                        self.observation_failures += not notify(
                            self.observe_event,
                            GatewayEvent(
                                request_id=request.request_id,
                                context_id=request.context_id,
                                sequence=0,
                                kind="error",
                                code=code,
                            ),
                        )
                    raise GatewayError(code)
                async for line in bounded_lines(response):
                    if len(line.encode("utf-8")) > 1024 * 1024:
                        raise GatewayError("gateway_frame_too_large")
                    event = GatewayEvent.model_validate_json(line)
                    if (
                        event.request_id != request.request_id
                        or event.context_id != request.context_id
                    ):
                        raise GatewayError("gateway_stream_identity")
                    if event.kind == "error":
                        if self.observe_event is not None:
                            self.observation_failures += not notify(self.observe_event, event)
                        raise GatewayError(event.code or "generation_failed")
                    if event.sequence != sequence or (not started and event.kind != "start"):
                        raise GatewayError("gateway_stream_order")
                    if started and event.kind == "start":
                        raise GatewayError("gateway_stream_order")
                    started = True
                    sequence += 1
                    if event.kind == "end" and self.observe_event is not None:
                        self.observation_failures += not notify(self.observe_event, event)
                    yield event
                    if event.kind == "end":
                        return
        except (httpx.HTTPError, ValidationError) as error:
            raise GatewayError("gateway_transport_failed") from error
        raise GatewayError("gateway_stream_incomplete")

    async def aclose(self) -> None:
        """Close only this client's HTTP transport."""
        await self.client.aclose()
