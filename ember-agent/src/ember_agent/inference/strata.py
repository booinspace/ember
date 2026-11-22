"""Pinned managed Strata runner behind Ember's inference gateway."""

import os
import re

import httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from ..runtime.config import DeploymentConfig
from .contracts import GatewayError, GatewayRequest, UpstreamPromptOverflow
from .vllm import OpenAIStreamUpstream

_REJECTION = TypeAdapter(dict[str, dict[str, str]])


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

    def _request_options(self, request: GatewayRequest) -> dict[str, JsonValue]:
        if request.workload != "P5":
            return {}
        return {"ember_coding_input_limit": self.config.max_input_tokens}

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
