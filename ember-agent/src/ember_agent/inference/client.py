"""Strict gateway transport; no alternate upstream or automatic generation retry."""

from collections.abc import AsyncGenerator
from uuid import UUID

import httpx
from pydantic import ValidationError

from .contracts import GatewayError, GatewayEvent, GatewayHealth, GatewayRequest
from .frames import bounded_lines


class HttpGatewayClient:
    """Owned local authenticated transport; closing a stream only detaches its subscriber."""

    def __init__(
        self, endpoint: str, token: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.client = client or httpx.AsyncClient(base_url=endpoint, timeout=190, trust_env=False)
        self.headers = {"Authorization": "Bearer " + token}

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
                    raise GatewayError("gateway_rejected")
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
                        raise GatewayError(event.code or "generation_failed")
                    if event.sequence != sequence or (not started and event.kind != "start"):
                        raise GatewayError("gateway_stream_order")
                    if started and event.kind == "start":
                        raise GatewayError("gateway_stream_order")
                    started = True
                    sequence += 1
                    yield event
                    if event.kind == "end":
                        return
        except (httpx.HTTPError, ValidationError) as error:
            raise GatewayError("gateway_transport_failed") from error
        raise GatewayError("gateway_stream_incomplete")

    async def aclose(self) -> None:
        """Close only this client's HTTP transport."""
        await self.client.aclose()
