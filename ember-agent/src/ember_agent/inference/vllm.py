"""Pinned vLLM OpenAI transport behind the sole owned inference gateway."""

import hashlib
import json
import math
import os
import re
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Annotated, Literal

import httpx
from pydantic import Field, JsonValue, TypeAdapter, ValidationError

from ..runtime.config import DeploymentConfig
from .contracts import GatewayError, GatewayRequest
from .upstream import BackendModel, NativeFrame, NativeFunction, NativeMessage, NativeTool

_LIMIT = 1024 * 1024
_JSON = TypeAdapter(dict[str, JsonValue])


def vllm_identity_digest(
    version: str,
    vllm_config: dict[str, JsonValue],
    artifact_sha256: str,
    profile_sha256: str,
) -> str:
    """Bind supported effective settings to the independently verified static setup manifest."""
    if not all(re.fullmatch(r"[0-9a-f]{64}", value) for value in (artifact_sha256, profile_sha256)):
        raise ValueError("Invalid vLLM profile digest.")
    payload: dict[str, JsonValue] = {
        "schema": "ember-vllm-profile-v1",
        "backend": "vllm",
        "version": version,
        "config": _stable_config(vllm_config),
        "artifact_sha256": artifact_sha256,
        "profile_sha256": profile_sha256,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _object(config: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise ValueError("Invalid vLLM configuration group.")
    return value


def _stable_config(config: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Project v0.31 settings; runtime counters and injected compilation state are not identity."""
    for key in (
        "speculative_config",
        "lora_config",
        "watermark_config",
        "diffusion_config",
        "weight_transfer_config",
        "kv_transfer_config",
        "ec_transfer_config",
    ):
        if config.get(key) is not None:
            raise ValueError("Unsupported vLLM execution profile.")
    model = _object(config, "model_config")
    cache = _object(config, "cache_config")
    parallel = _object(config, "parallel_config")
    for key in ("tensor_parallel_size", "pipeline_parallel_size", "data_parallel_size"):
        if type(parallel.get(key)) is not int or parallel[key] != 1:
            raise ValueError("Unsupported vLLM parallel execution profile.")
    parallel_keys = (
        "tensor_parallel_size",
        "pipeline_parallel_size",
        "data_parallel_size",
        "prefill_context_parallel_size",
        "decode_context_parallel_size",
        "nnodes",
    )
    for key in parallel_keys:
        value = parallel.get(key)
        if key in parallel and (not isinstance(value, int) or isinstance(value, bool) or value < 1):
            raise ValueError("Invalid vLLM parallel configuration.")
    result: dict[str, JsonValue] = {
        "model_config": {key: value for key, value in model.items() if key != "hf_token"},
        "cache_config": {
            key: value
            for key, value in cache.items()
            if key
            not in {
                "num_gpu_blocks",
                "num_cpu_blocks",
                "effective_attention_block_size",
                "kv_cache_size_tokens",
                "kv_cache_max_concurrency",
            }
        },
        "parallel_config": {key: parallel[key] for key in parallel_keys if key in parallel},
    }
    for key in (
        "structured_outputs_config",
        "attention_config",
        "kernel_config",
        "mamba_config",
        "offload_config",
        "additional_config",
        "reasoning_config",
    ):
        if key in config:
            value = config[key]
            if value is not None and not isinstance(value, dict):
                raise ValueError("Invalid vLLM configuration group.")
            result[key] = value
    for group, keys in (
        (
            "scheduler_config",
            (
                "max_num_seqs",
                "max_num_batched_tokens",
                "enable_chunked_prefill",
                "async_scheduling",
                "policy",
                "stream_interval",
            ),
        ),
        (
            "compilation_config",
            (
                "mode",
                "cudagraph_mode",
                "cudagraph_capture_sizes",
                "backend",
                "custom_ops",
                "pass_config",
            ),
        ),
    ):
        if group in config:
            value = _object(config, group)
            result[group] = {key: value[key] for key in keys if key in value}
    return result


def _grammar_schema(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Drop unsupported key-name guidance only; the gateway validates the original full schema."""
    result: dict[str, JsonValue] = {}
    for key, value in schema.items():
        if key == "propertyNames":
            continue
        if key in {
            "properties",
            "patternProperties",
            "$defs",
            "definitions",
            "dependentSchemas",
            "dependencies",
        } and isinstance(value, dict):
            result[key] = {
                name: _grammar_schema(child) if isinstance(child, dict) else child
                for name, child in value.items()
            }
        elif key in {
            "additionalProperties",
            "unevaluatedProperties",
            "contains",
            "items",
            "not",
            "if",
            "then",
            "else",
            "contentSchema",
            "additionalItems",
            "unevaluatedItems",
        } and isinstance(value, dict):
            result[key] = _grammar_schema(value)
        elif key in {"allOf", "anyOf", "oneOf", "prefixItems"} and isinstance(value, list):
            result[key] = [
                _grammar_schema(child) if isinstance(child, dict) else child for child in value
            ]
        else:
            result[key] = value
    return result


class _Function(BackendModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")] | None = None
    arguments: str | None = None


class _Tool(BackendModel):
    index: Annotated[int, Field(ge=0, lt=16)]
    type: Literal["function"] | None = None
    function: _Function | None = None


class _Delta(BackendModel):
    role: Literal["assistant"] | None = None
    content: str | None = None
    reasoning: str | None = Field(default=None, repr=False)
    reasoning_content: str | None = Field(default=None, repr=False)
    tool_calls: Annotated[list[_Tool], Field(max_length=16)] = Field(default_factory=list[_Tool])


class _Choice(BackendModel):
    index: Annotated[int, Field(ge=0, le=0)]
    delta: _Delta
    finish_reason: Literal["stop", "length", "tool_calls"] | None = None


class _Usage(BackendModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]


class _Metrics(BackendModel):
    generation_time_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    time_to_first_token_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class _StrataTimings(BackendModel):
    prompt_ms: Annotated[float, Field(ge=0, le=1e9, allow_inf_nan=False)] | None = None
    predicted_per_second: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class _Chunk(BackendModel):
    id: Annotated[str, Field(min_length=1, max_length=256)]
    object: Literal["chat.completion.chunk"]
    model: str
    choices: Annotated[list[_Choice], Field(max_length=1)]
    usage: _Usage | None = None
    metrics: _Metrics | None = None
    timings: _StrataTimings | None = None


def _identity_encoding(response: httpx.Response) -> None:
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise GatewayError("upstream_encoded_response")


async def _events(response: httpx.Response) -> AsyncIterator[str]:
    """Keep SSE delimiters and bound both individual lines and multiline data events."""
    pending = bytearray()
    data: list[str] = []
    size = 0
    async for chunk in response.aiter_raw():
        pending.extend(chunk)
        while (position := pending.find(b"\n")) >= 0:
            if position > _LIMIT:
                raise GatewayError("stream_frame_too_large")
            raw = bytes(pending[:position]).removesuffix(b"\r")
            del pending[: position + 1]
            try:
                line = raw.decode("utf-8")
            except UnicodeError as error:
                raise GatewayError("stream_invalid_encoding") from error
            if not line:
                if data:
                    yield "\n".join(data)
                data = []
                size = 0
            elif line.startswith("data:"):
                value = line[5:].removeprefix(" ")
                size += len(raw) + 1
                if size > _LIMIT:
                    raise GatewayError("stream_frame_too_large")
                data.append(value)
        if len(pending) > _LIMIT:
            raise GatewayError("stream_frame_too_large")
    if pending or data:
        raise GatewayError("stream_frame_incomplete")


class OpenAIStreamUpstream:
    """Shared bounded OpenAI streaming transport for local runners."""

    def __init__(
        self, config: DeploymentConfig, endpoint: str, client: httpx.AsyncClient | None
    ) -> None:
        self.config = config
        self._instance_id: str | None = None
        self._generation_instance_id: str | None = None
        self.client = client or httpx.AsyncClient(
            base_url=endpoint,
            timeout=httpx.Timeout(180, connect=3),
            trust_env=False,
        )

    api_key_environment: str | None = None
    usage_on_finish = False

    def _headers(self) -> dict[str, str]:
        headers = {"Accept-Encoding": "identity"}
        name = self.api_key_environment
        if name:
            value = os.environ.get(name, "")
            if (
                not value
                or len(value) > 4096
                or not value.isascii()
                or any(character in value for character in "\r\n")
            ):
                raise GatewayError("upstream_credentials_unavailable")
            headers["Authorization"] = "Bearer " + value
        return headers

    async def _metadata(self, route: str) -> dict[str, JsonValue]:
        async with self.client.stream("GET", route, headers=self._headers(), timeout=3) as response:
            response.raise_for_status()
            _identity_encoding(response)
            content = bytearray()
            async for chunk in response.aiter_raw():
                if len(content) + len(chunk) > _LIMIT:
                    raise GatewayError("upstream_metadata_too_large")
                content.extend(chunk)
            return _JSON.validate_json(content)

    async def verify(self, *, loaded: bool = False) -> None:
        """Re-attest effective configuration on every admission and completed generation."""
        digest, instance = await self._attest()
        if loaded and instance != (self._generation_instance_id or self._instance_id):
            raise GatewayError("upstream_instance_changed")
        if digest != self.config.model_digest.removeprefix("sha256:"):
            raise GatewayError("model_pin_mismatch")
        if loaded:
            self._generation_instance_id = None
        else:
            self._instance_id = instance

    async def inspect_identity(self) -> str:
        """Read the validated current pin without changing configured or admitted identity."""
        digest, _ = await self._attest()
        return digest

    async def _attest(self) -> tuple[str, str]:
        raise NotImplementedError

    async def generate(self, request: GatewayRequest) -> AsyncIterator[NativeFrame]:
        """Translate OpenAI deltas without completing before terminal usage and DONE."""
        if self._instance_id is None:
            await self.verify()
        self._generation_instance_id = self._instance_id
        messages: list[JsonValue] = []
        for item in request.messages:
            message: dict[str, JsonValue] = {"role": item.role, "content": item.content}
            if item.tool_calls:
                message["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, allow_nan=False),
                        },
                    }
                    for call in item.tool_calls
                ]
            if item.tool_call_id is not None:
                message["tool_call_id"] = item.tool_call_id
            messages.append(message)
        body: dict[str, JsonValue] = {
            "model": self.config.model_tag,
            "messages": messages,
            "temperature": 0.2,
            "max_completion_tokens": request.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": request.thinking},
            "stop": ["<|im_start|>", "<|im_end|>"],
        }
        if request.output == "json":
            body["response_format"] = (
                {"type": "json_object"}
                if request.output_schema is None
                else {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ember_output",
                        "schema": _grammar_schema(request.output_schema),
                    },
                }
            )
        if request.tools:
            body["tools"] = [
                _JSON.validate_python(tool.model_dump(mode="json")) for tool in request.tools
            ]
            body["tool_choice"] = "auto"
        names: dict[int, str] = {}
        tool_size = 0
        identifier: str | None = None
        finish: str | None = None
        usage: _Usage | None = None
        decode_rate: float | None = None
        prefill_duration: int | None = None
        done = False
        try:
            async with self.client.stream(
                "POST", "/v1/chat/completions", json=body, headers=self._headers()
            ) as response:
                response.raise_for_status()
                _identity_encoding(response)
                if response.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream":
                    raise GatewayError("invalid_upstream_stream")
                async for event in _events(response):
                    if event == "[DONE]":
                        if finish is None or usage is None:
                            raise GatewayError("upstream_incomplete")
                        done = True
                        break
                    try:
                        chunk = _Chunk.model_validate_json(event)
                    except ValidationError as error:
                        raise GatewayError("invalid_upstream_stream") from error
                    if chunk.model != self.config.model_tag:
                        raise GatewayError("upstream_model_mismatch")
                    if identifier is not None and chunk.id != identifier:
                        raise GatewayError("invalid_upstream_stream")
                    identifier = chunk.id
                    if self.usage_on_finish and chunk.choices and chunk.usage is not None:
                        if finish is not None or chunk.choices[0].finish_reason is None:
                            raise GatewayError("invalid_upstream_stream")
                        finish = chunk.choices[0].finish_reason
                        if chunk.choices[0].delta.model_fields_set:
                            raise GatewayError("invalid_upstream_stream")
                        chunk = chunk.model_copy(update={"choices": []})
                    if not chunk.choices:
                        if finish is None or usage is not None or chunk.usage is None:
                            raise GatewayError("invalid_upstream_stream")
                        usage = chunk.usage
                        if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
                            raise GatewayError("invalid_upstream_stream")
                        duration = (
                            chunk.metrics.generation_time_ms if chunk.metrics is not None else None
                        )
                        if duration is not None and duration > 0 and usage.completion_tokens > 1:
                            decode_rate = 1000.0 * (usage.completion_tokens - 1) / duration
                            if not math.isfinite(decode_rate):
                                raise GatewayError("invalid_upstream_stream")
                        first_step_ms = (
                            chunk.metrics.time_to_first_token_ms
                            if chunk.metrics is not None
                            else None
                        )
                        if first_step_ms is not None:
                            nanoseconds = first_step_ms * 1_000_000
                            if not math.isfinite(nanoseconds):
                                raise GatewayError("invalid_upstream_stream")
                            prefill_duration = int(nanoseconds)
                        if self.usage_on_finish and chunk.timings is not None:
                            decode_rate = chunk.timings.predicted_per_second
                            if chunk.timings.prompt_ms is not None:
                                prefill_duration = int(chunk.timings.prompt_ms * 1_000_000)
                        continue
                    if finish is not None or chunk.usage is not None:
                        raise GatewayError("invalid_upstream_stream")
                    choice = chunk.choices[0]
                    finish = choice.finish_reason
                    delta = choice.delta
                    tools: list[NativeTool] = []
                    for tool in delta.tool_calls:
                        function = tool.function
                        if function is None or function.name is None and function.arguments is None:
                            continue
                        name = names.get(tool.index)
                        if name is None:
                            if tool.index != len(names) or function.name is None:
                                raise GatewayError("tool_stream_conflict")
                            name = names[tool.index] = function.name
                        elif function.name not in {None, name}:
                            raise GatewayError("tool_stream_conflict")
                        arguments = function.arguments or ""
                        tool_size += len(arguments.encode("utf-8"))
                        if tool_size > _LIMIT:
                            raise GatewayError("upstream_frame_too_large")
                        tools.append(
                            NativeTool(
                                index=tool.index,
                                function=NativeFunction(name=name, arguments=arguments),
                            )
                        )
                    reasoning = delta.reasoning or delta.reasoning_content or ""
                    yield NativeFrame(
                        model=chunk.model,
                        message=NativeMessage(
                            content=delta.content or "",
                            thinking=reasoning if request.thinking else "",
                            tool_calls=tools,
                        ),
                    )
        except httpx.HTTPError as error:
            raise GatewayError("upstream_transport_failed") from error
        if not done or usage is None:
            raise GatewayError("upstream_incomplete")
        # v0.31 generation_time_ms covers first-to-last token, therefore N-1 decode intervals.
        yield NativeFrame(
            model=self.config.model_tag,
            done=True,
            done_reason=finish,
            prompt_eval_count=usage.prompt_tokens,
            # v0.31 native prefill: scheduled→first token, including sampling/preemptions,
            # excluding initial queue. This wall-clock interval differs from Ollama prompt_eval.
            prompt_eval_duration=prefill_duration,
            eval_count=usage.completion_tokens,
            decode_tokens_per_second=decode_rate,
        )

    async def aclose(self) -> None:
        """Close owned HTTP connections without stopping the independently managed server."""
        await self.client.aclose()


class VLLMUpstream(OpenAIStreamUpstream):
    """Pinned vLLM server with independent lifecycle."""

    def __init__(self, config: DeploymentConfig, client: httpx.AsyncClient | None = None) -> None:
        settings = config.inference.vllm
        if settings is None or config.inference.backend != "vllm":
            raise GatewayError("upstream_configuration_mismatch")
        super().__init__(config, settings.endpoint, client)
        self.settings = settings
        self.api_key_environment = settings.api_key_environment

    async def _attest(self) -> tuple[str, str]:
        try:
            profile = (await self._metadata("/ember/profile")).get("profile_sha256")
            if profile != self.settings.profile_sha256:
                raise GatewayError("upstream_profile_mismatch")
            version = (await self._metadata("/version")).get("version")
            settings = self.settings
            if not isinstance(version, str) or version != settings.version:
                raise GatewayError("upstream_version_mismatch")
            value = (await self._metadata("/server_info?config_format=json")).get("vllm_config")
            if not isinstance(value, dict):
                raise GatewayError("invalid_upstream_metadata")
            instance = value.get("instance_id")
            if not isinstance(instance, str) or not instance.strip() or len(instance) > 256:
                raise GatewayError("invalid_upstream_metadata")
            model_config = value.get("model_config")
            if not isinstance(model_config, dict):
                raise GatewayError("invalid_upstream_metadata")
            if model_config.get("max_model_len") != self.config.context_tokens:
                raise GatewayError("runner_context_mismatch")
            # The pinned GGUF plugin uses model for HF metadata and model_weights for GGUF bytes.
            model = model_config.get("model_weights")
            expected_file = "sha256-" + settings.artifact_sha256 + ".gguf"
            if not isinstance(model, str) or PurePosixPath(model).name != expected_file:
                raise GatewayError("model_artifact_mismatch")
            digest = vllm_identity_digest(
                version, value, settings.artifact_sha256, settings.profile_sha256
            )
            models = (await self._metadata("/v1/models")).get("data")
            if (
                not isinstance(models, list)
                or len(models) != 1
                or not isinstance(models[0], dict)
                or models[0].get("id") != self.config.model_tag
            ):
                raise GatewayError("model_pin_mismatch")
            return digest, instance
        except (httpx.HTTPError, ValidationError, ValueError) as error:
            raise GatewayError("upstream_unavailable") from error
