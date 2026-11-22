"""Pinned managed Strata runner behind Ember's inference gateway."""

import asyncio
import os
import re
from uuid import UUID

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from ..runtime.config import DeploymentConfig
from .contracts import GatewayError, GatewayRequest, UpstreamPromptOverflow
from .vllm import OpenAIStreamUpstream

_REJECTION = TypeAdapter(dict[str, dict[str, str]])
_RECEIPT = TypeAdapter(dict[str, str])
_PREEMPTION = "/v1/ember-preemption"


class StrataUpstream(OpenAIStreamUpstream):
    """Check managed server identity and translate Strata's terminal usage frame."""

    api_key_environment = "EMBER_STRATA_API_KEY"
    usage_on_finish = True

    def __init__(self, config: DeploymentConfig, client: httpx.AsyncClient | None = None) -> None:
        settings = config.inference.strata
        if settings is None or config.inference.backend != "strata":
            raise GatewayError("upstream_configuration_mismatch")
        super().__init__(config, settings.endpoint, client)
        self.settings = settings
        self._preemption_attested = False

    def _request_options(self, request: GatewayRequest) -> dict[str, JsonValue]:
        options: dict[str, JsonValue] = {}
        if request.thinking:
            # Strata treats the template boolean only as an explicit off switch.
            options["reasoning_effort"] = request.reasoning_effort or "high"
        if request.workload == "P5":
            options["ember_coding_input_limit"] = self.config.max_input_tokens
            if self.config.preemption_enabled:
                options["ember_preemption_id"] = request.request_id.hex
        return options

    async def verify(self, *, loaded: bool = False) -> None:
        """Require an authenticated stop capability on the owned native runner."""
        await super().verify(loaded=loaded)
        if self.config.preemption_enabled and not self._preemption_attested:
            try:
                ready = await self._metadata(_PREEMPTION)
            except (httpx.HTTPError, ValidationError) as error:
                raise GatewayError("upstream_preemption_unavailable") from error
            if ready != {
                "instance": os.environ.get("EMBER_STRATA_INSTANCE_ID"),
                "outcome": "ready",
            }:
                raise GatewayError("upstream_preemption_unavailable")
            self._preemption_attested = True

    async def preempt(self, identity: UUID) -> None:
        """Wait for the exact coding request's native DONE and released admission."""
        instance = os.environ.get("EMBER_STRATA_INSTANCE_ID")
        if instance is None or re.fullmatch(r"[0-9a-f]{32}", instance) is None:
            raise GatewayError("upstream_preemption_unknown")
        try:
            async with asyncio.timeout(6):
                while True:
                    async with self.client.stream(
                        "POST", f"{_PREEMPTION}/{identity.hex}", headers=self._headers()
                    ) as response:
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(body) + len(chunk) > 1024:
                                raise GatewayError("upstream_preemption_unknown")
                            body.extend(chunk)
                        receipt = _RECEIPT.validate_json(body)
                        status = response.status_code
                    if receipt.get("instance") != instance or receipt.get("id") != identity.hex:
                        break
                    outcome = receipt.get("outcome")
                    if status == 200 and outcome in {"aborted", "completed"}:
                        return
                    if status != 404 or outcome != "not_registered":
                        break
                    await asyncio.sleep(0.01)
        except (TimeoutError, httpx.HTTPError, ValidationError) as error:
            raise GatewayError("upstream_preemption_unknown") from error
        raise GatewayError("upstream_preemption_unknown")

    async def _check_generation_status(self, response: httpx.Response) -> None:
        if (
            response.status_code == 400
            and response.headers.get("content-encoding", "identity") == "identity"
        ):
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > 1024:
                    break
                body.extend(chunk)
            else:
                try:
                    failure = _REJECTION.validate_json(body)
                except ValidationError:
                    pass
                else:
                    if failure == {
                        "error": {
                            "type": "invalid_request_error",
                            "message": "ember_coding_context_length_exceeded",
                        }
                    }:
                        raise UpstreamPromptOverflow()
        response.raise_for_status()

    async def _attest(self) -> tuple[str, str]:
        try:
            status = await self._metadata("/v1/status")
            if status.get("service") != "strata" or status.get("loaded") is not True:
                raise GatewayError("upstream_unavailable")
            if status.get("model") != self.config.model_tag:
                raise GatewayError("model_pin_mismatch")
            context = status.get("context")
            if not isinstance(context, dict) or context.get("native") != self.config.context_tokens:
                raise GatewayError("runner_context_mismatch")
            telemetry = await self._metadata("/v1/telemetry")
            instance = telemetry.get("instance")
            identity = instance.get("id") if isinstance(instance, dict) else None
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
                raise GatewayError("invalid_upstream_metadata")
            if identity != os.environ.get("EMBER_STRATA_INSTANCE_ID"):
                raise GatewayError("upstream_instance_changed")
            return self.settings.runner_config_sha256, identity
        except (httpx.HTTPError, ValidationError, ValueError) as error:
            raise GatewayError("upstream_unavailable") from error
