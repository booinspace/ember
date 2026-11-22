"""Pinned vLLM protocol regressions through the existing owned inference gateway."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import JsonValue

from ember_agent.inference.contracts import (
    GatewayError,
    GatewayRequest,
    PromptMessage,
    PromptToolCall,
    ToolDefinition,
    ToolFunction,
)
from ember_agent.inference.gateway import P0Gateway
from ember_agent.inference.vllm import OpenAIStreamUpstream, VLLMUpstream, vllm_identity_digest
from ember_agent.runtime.config import (
    DeploymentConfig,
    InferenceSettings,
    VLLMSettings,
    load_config,
)
from ember_memory_engine.processing_contracts import ClaimBatch, DatedClaimBatch

_ARTIFACT = "b" * 64
_PROFILE = "c" * 64
_MODEL = "synthetic:27b"
_EXAMPLE = Path(__file__).parents[1] / "config/deployment.example.toml"


def _server_config() -> dict[str, JsonValue]:
    return {
        "instance_id": "1793764800000000000",
        "model_config": {
            "model": "/models/qwen-metadata",
            "model_weights": "/models/sha256-" + _ARTIFACT + ".gguf",
            "max_model_len": 8192,
            "dtype": "torch.float16",
        },
        "cache_config": {"cache_dtype": "auto"},
        "parallel_config": {
            "tensor_parallel_size": 1,
            "pipeline_parallel_size": 1,
            "data_parallel_size": 1,
            "prefill_context_parallel_size": 1,
            "decode_context_parallel_size": 1,
            "nnodes": 1,
            "data_parallel_master_port": 49152,
        },
        "scheduler_config": {"max_num_seqs": 1, "policy": "fcfs"},
        "compilation_config": {
            "mode": 0,
            "cudagraph_mode": "NONE",
            "custom_ops": [],
            "pass_config": {},
        },
    }


def _configuration(tmp_path: Path, *, api_key_environment: str = "") -> DeploymentConfig:
    path = tmp_path / "deployment.toml"
    path.write_text(
        _EXAMPLE.read_text(encoding="utf-8")
        .replace('tag = ""', 'tag = "' + _MODEL + '"')
        .replace('expected_digest = ""', 'expected_digest = "' + "a" * 64 + '"'),
        encoding="utf-8",
    )
    config = load_config(path, require_model_pin=True)
    return config.model_copy(
        update={
            "context_tokens": 8192,
            "model_digest": vllm_identity_digest("0.31.0", _server_config(), _ARTIFACT, _PROFILE),
            "inference": InferenceSettings(
                backend="vllm",
                vllm=VLLMSettings(
                    endpoint="http://127.0.0.1:8000",
                    artifact_sha256=_ARTIFACT,
                    profile_sha256=_PROFILE,
                    api_key_environment=api_key_environment,
                ),
            ),
        }
    )


def _request(config: DeploymentConfig, **updates: object) -> GatewayRequest:
    original = GatewayRequest(
        request_id=uuid4(),
        context_id=uuid4(),
        model=config.model_tag,
        messages=(PromptMessage(role="user", content="Synthetic greeting"),),
        deadline_at=(datetime.now(UTC) + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
    )
    return GatewayRequest.model_validate_json(original.model_copy(update=updates).model_dump_json())


def _chunk(
    delta: dict[str, object] | None = None,
    *,
    finish: str | None = None,
    usage: bool = False,
    **updates: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "synthetic-generation",
        "object": "chat.completion.chunk",
        "model": _MODEL,
        "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
    }
    if usage:
        value["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    return value | updates


def _sse(*values: dict[str, object] | str) -> bytes:
    return b"".join(
        ("data: " + (item if isinstance(item, str) else json.dumps(item)) + "\n\n").encode()
        for item in values
    )


def _complete(*values: dict[str, object]) -> bytes:
    return _sse(*values, _chunk(finish="stop"), _chunk(usage=True), "[DONE]")


class _Bytes(httpx.AsyncByteStream):
    def __init__(self, content: bytes, *, split: int = 31) -> None:
        self.content = content
        self.split = split
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for position in range(0, len(self.content), self.split):
            yield self.content[position : position + self.split]

    async def aclose(self) -> None:
        self.closed = True


class _Server:
    def __init__(self, stream: httpx.AsyncByteStream | bytes) -> None:
        self.stream = _Bytes(stream) if isinstance(stream, bytes) else stream
        self.config = _server_config()
        self.version: object = "0.31.0"
        self.profile: object = _PROFILE
        self.models: object = [{"id": _MODEL}]
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.encoded = False

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                self.status,
                headers={"content-type": "text/event-stream"},
                stream=self.stream,
            )
        if request.url.path == "/ember/profile":
            value = {"profile_sha256": self.profile}
        elif request.url.path == "/version":
            value = {"version": self.version}
        elif request.url.path == "/server_info":
            assert request.url.query == b"config_format=json"
            value = {"vllm_config": self.config}
        elif request.url.path == "/v1/models":
            value = {"data": self.models}
        else:
            raise AssertionError("Unexpected route")
        return httpx.Response(
            self.status,
            headers={"content-encoding": "gzip"} if self.encoded else {},
            stream=_Bytes(json.dumps(value).encode()),
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(self.respond)
        )


def test_effective_identity_is_fresh_and_hash_order_is_stable(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        await upstream.verify()
        await upstream.verify(loaded=True)
        assert [request.url.path for request in server.requests] == [
            "/ember/profile",
            "/version",
            "/server_info",
            "/v1/models",
        ] * 2
        assert (
            vllm_identity_digest(
                "0.31.0", dict(reversed(list(server.config.items()))), _ARTIFACT, _PROFILE
            )
            == config.model_digest
        )
        server.config["cache_config"] = {"cache_dtype": "fp8"}
        with pytest.raises(GatewayError, match="model_pin_mismatch"):
            await upstream.verify()
        await upstream.aclose()

    asyncio.run(run())


def test_identity_inspection_returns_current_pin_without_relabelling_configuration(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        await upstream.verify()
        server.config["cache_config"] = {"cache_dtype": "fp8"}
        expected = vllm_identity_digest("0.31.0", server.config, _ARTIFACT, _PROFILE)
        assert await upstream.inspect_identity() == expected
        assert expected != config.model_digest and upstream.config is config
        assert config.model_digest == _configuration(tmp_path).model_digest
        gateway = P0Gateway(config, upstream)
        with pytest.raises(GatewayError, match="model_pin_mismatch"):
            await gateway.admit(_request(config))
        assert all(item.method == "GET" for item in server.requests)
        await gateway.aclose()

    asyncio.run(run())


def test_identity_inspection_enforces_configured_profile_and_artifact(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        server.profile = "d" * 64
        with pytest.raises(GatewayError, match="upstream_profile_mismatch"):
            await upstream.inspect_identity()
        server.profile = _PROFILE
        model = server.config["model_config"]
        assert isinstance(model, dict)
        model["model_weights"] = "/models/unverified.gguf"
        with pytest.raises(GatewayError, match="model_artifact_mismatch"):
            await upstream.inspect_identity()
        assert all(item.method == "GET" for item in server.requests)
        await upstream.aclose()

    asyncio.run(run())


def test_idle_restart_preserves_profile_but_only_top_level_instance_is_excluded(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        await upstream.verify()
        server.config["instance_id"] = "1793764801000000000"
        assert (
            vllm_identity_digest("0.31.0", server.config, _ARTIFACT, _PROFILE)
            == config.model_digest
        )
        await upstream.verify()
        assert [frame async for frame in upstream.generate(_request(config))][-1].done
        await upstream.verify(loaded=True)
        server.config["additional_config"] = {"instance_id": "operator-owned-profile-value"}
        with pytest.raises(GatewayError, match="model_pin_mismatch"):
            await upstream.verify()
        await upstream.aclose()

    asyncio.run(run())


def test_runtime_counters_ports_secrets_and_compilation_stats_do_not_repin_profile(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        await upstream.verify()
        server.config["instance_id"] = "1793764801000000000"
        model = server.config["model_config"]
        cache = server.config["cache_config"]
        parallel = server.config["parallel_config"]
        compilation = server.config["compilation_config"]
        assert isinstance(model, dict) and isinstance(cache, dict)
        assert isinstance(parallel, dict) and isinstance(compilation, dict)
        model["hf_token"] = "private-runtime-credential"
        cache.update(
            {
                "num_gpu_blocks": 1024,
                "num_cpu_blocks": 0,
                "effective_attention_block_size": 16,
                "kv_cache_size_tokens": 16384,
                "kv_cache_max_concurrency": 2.0,
            }
        )
        parallel.update({"data_parallel_master_port": 50001, "master_port": 50002, "rank": 0})
        compilation.update(
            {
                "_compile_time": 42.0,
                "static_forward_context": {"layer": "injected-context"},
                "traced_files": ["runtime-generated.py"],
                "compilation_stats": {"graphs": 9},
            }
        )
        assert (
            vllm_identity_digest("0.31.0", server.config, _ARTIFACT, _PROFILE)
            == config.model_digest
        )
        await upstream.verify()
        assert model["hf_token"] == "private-runtime-credential"
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change", ["dtype", "context", "precision", "policy", "attention", "tensor_parallel", "profile"]
)
def test_effective_settings_and_manifest_remain_pinned(tmp_path: Path, change: str) -> None:
    config = _server_config()
    group = {
        "dtype": "model_config",
        "context": "model_config",
        "precision": "cache_config",
        "policy": "scheduler_config",
        "attention": "attention_config",
        "tensor_parallel": "parallel_config",
    }.get(change)
    if group is not None:
        config.setdefault(group, {})
        value = config[group]
        assert isinstance(value, dict)
        key, replacement = {
            "dtype": ("dtype", "torch.bfloat16"),
            "context": ("max_model_len", 4096),
            "precision": ("cache_dtype", "fp8"),
            "policy": ("policy", "priority"),
            "attention": ("backend", "FLASH_ATTN"),
            "tensor_parallel": ("tensor_parallel_size", 2),
        }[change]
        value[key] = replacement
    if change == "tensor_parallel":
        with pytest.raises(ValueError, match="parallel execution profile"):
            vllm_identity_digest("0.31.0", config, _ARTIFACT, _PROFILE)
    else:
        digest = vllm_identity_digest(
            "0.31.0", config, _ARTIFACT, "d" * 64 if change == "profile" else _PROFILE
        )
        assert digest != _configuration(tmp_path).model_digest


@pytest.mark.parametrize(
    "group",
    [
        "speculative_config",
        "lora_config",
        "watermark_config",
        "diffusion_config",
        "weight_transfer_config",
        "kv_transfer_config",
        "ec_transfer_config",
    ],
)
def test_unsupported_profile_features_fail_closed(tmp_path: Path, group: str) -> None:
    async def run() -> None:
        server = _Server(_complete())
        server.config[group] = {}
        with pytest.raises(ValueError, match="Unsupported vLLM execution profile"):
            vllm_identity_digest("0.31.0", server.config, _ARTIFACT, _PROFILE)
        upstream = VLLMUpstream(_configuration(tmp_path), server.client())
        with pytest.raises(GatewayError, match="upstream_unavailable"):
            await upstream.verify()
        await upstream.aclose()

    asyncio.run(run())


def test_malformed_projection_and_manifest_mismatch_never_generate(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        server.config["parallel_config"] = {"tensor_parallel_size": True}
        upstream = VLLMUpstream(config, server.client())
        with pytest.raises(GatewayError, match="upstream_unavailable"):
            await upstream.verify()
        server.config = _server_config()
        server.profile = "d" * 64
        gateway = P0Gateway(config, upstream)
        with pytest.raises(GatewayError, match="upstream_profile_mismatch"):
            await gateway.admit(_request(config))
        assert all(item.method == "GET" for item in server.requests)
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("instance", [None, "", " ", 123, "x" * 257])
def test_missing_or_invalid_instance_fails_attestation(tmp_path: Path, instance: object) -> None:
    async def run() -> None:
        server = _Server(_complete())
        server.config["instance_id"] = instance
        upstream = VLLMUpstream(_configuration(tmp_path), server.client())
        with pytest.raises(GatewayError, match="invalid_upstream_metadata"):
            await upstream.verify()
        await upstream.aclose()

    asyncio.run(run())


def test_inflight_restart_is_rejected_even_after_concurrent_idle_verification(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete(_chunk({"content": "Partial"})))
        upstream = VLLMUpstream(config, server.client())
        await upstream.verify()
        frames = upstream.generate(_request(config))
        assert (await anext(frames)).message.content == "Partial"
        server.config["instance_id"] = "1793764801000000000"
        await upstream.verify()
        assert [frame async for frame in frames][-1].done
        with pytest.raises(GatewayError, match="upstream_instance_changed"):
            await upstream.verify(loaded=True)
        await upstream.aclose()

    asyncio.run(run())


def test_gateway_requires_quiescence_when_server_restarts_during_generation(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete(_chunk({"content": "Partial"})))

        def restart_on_dispatch(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                server.config["instance_id"] = "1793764801000000000"
            return server.respond(request)

        client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(restart_on_dispatch)
        )
        gateway = P0Gateway(config, VLLMUpstream(config, client))
        events = [event async for event in (await gateway.admit(_request(config))).events()]
        assert events[-1].code == "upstream_instance_changed"
        assert gateway.uncertain and not any(item.kind == "end" for item in events)
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["version", "context", "artifact", "metadata_only", "alias", "duplicate", "malformed"]
)
def test_identity_mismatch_fails_before_generation(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        server = _Server(_complete())
        if fault == "version":
            server.version = "0.30.0"
        elif fault in {"context", "artifact", "metadata_only"}:
            model = server.config["model_config"]
            assert isinstance(model, dict)
            if fault == "metadata_only":
                model["model"] = model.pop("model_weights")
            else:
                model["max_model_len" if fault == "context" else "model_weights"] = (
                    4096 if fault == "context" else "/models/unverified.gguf"
                )
        elif fault == "alias":
            server.models = [{"id": "different-model"}]
        elif fault == "duplicate":
            server.models = [{"id": _MODEL}, {"id": _MODEL}]
        else:
            server.config = {"model_config": "unstructured"}
        gateway = P0Gateway(
            _configuration(tmp_path), VLLMUpstream(_configuration(tmp_path), server.client())
        )
        with pytest.raises(GatewayError):
            await gateway.admit(_request(gateway.config))
        assert not any(request.method == "POST" for request in server.requests)
        await gateway.aclose()

    asyncio.run(run())


def test_usage_after_finish_is_consumed_before_gateway_completion(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete(_chunk({"content": "Hello"})))
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [event async for event in (await gateway.admit(_request(config))).events()]
        assert "".join(event.text for event in events) == "Hello"
        usage = events[-1].usage
        assert events[-1].kind == "end" and usage is not None
        assert (usage.prompt_tokens, usage.output_tokens) == (12, 3)
        assert usage.tokens_per_second is None and usage.prefill_seconds is None
        assert server.stream.closed and not gateway.uncertain
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        assert body["max_completion_tokens"] == 512 and body["temperature"] == 0.2
        assert body["stream_options"] == {"include_usage": True}
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["stop"] == ["<|im_start|>", "<|im_end|>"]
        assert "extra_body" not in body
        await gateway.aclose()

    asyncio.run(run())


def test_vllm_preserves_complete_assistant_calls_and_tool_result_ids(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        request = _request(
            config,
            messages=(
                PromptMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        PromptToolCall(
                            call_id="call_synthetic", name="lookup", arguments={"query": "example"}
                        ),
                    ),
                ),
                PromptMessage(
                    role="tool", content="Synthetic source", tool_call_id="call_synthetic"
                ),
            ),
        )
        assert [frame async for frame in upstream.generate(request)][-1].done
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        call = body["messages"][0]["tool_calls"][0]
        assert call["id"] == "call_synthetic" and call["function"]["name"] == "lookup"
        assert json.loads(call["function"]["arguments"]) == {"query": "example"}
        assert body["messages"][1]["tool_call_id"] == "call_synthetic"
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("duration, expected", [(200.0, 10.0), (None, None), (0.0, None)])
def test_decode_rate_uses_server_first_to_last_interval(
    tmp_path: Path, duration: float | None, expected: float | None
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk({"content": "Visible"}, finish="stop"),
                _chunk(
                    usage=True, metrics={"generation_time_ms": duration, "tokens_per_second": 999.0}
                ),
                "[DONE]",
            )
        )
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [event async for event in (await gateway.admit(_request(config))).events()]
        assert events[-1].kind == "end"
        assert events[-1].usage is not None
        assert events[-1].usage.tokens_per_second == expected
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("milliseconds, expected", [(12.5, 12_500_000), (0.0, 0), (None, None)])
def test_native_first_step_duration_converts_milliseconds_to_nanoseconds(
    tmp_path: Path, milliseconds: float | None, expected: int | None
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk(finish="stop"),
                _chunk(usage=True, metrics={"time_to_first_token_ms": milliseconds}),
                "[DONE]",
            )
        )
        upstream = VLLMUpstream(config, server.client())
        frames = [frame async for frame in upstream.generate(_request(config))]
        assert frames[-1].prompt_eval_duration == expected
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("milliseconds", [-1.0, float("nan"), float("inf"), 1e308])
def test_invalid_native_first_step_duration_fails_closed(
    tmp_path: Path, milliseconds: float
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk(finish="stop"),
                _chunk(usage=True, metrics={"time_to_first_token_ms": milliseconds}),
                "[DONE]",
            )
        )
        upstream = VLLMUpstream(config, server.client())
        with pytest.raises(GatewayError, match="invalid_upstream_stream"):
            _ = [frame async for frame in upstream.generate(_request(config))]
        assert server.stream.closed
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("version", ["0.2.0", "0.3.0"])
@pytest.mark.parametrize("milliseconds, seconds", [(125.0, 0.125), (0.0, 0.0), (None, None)])
def test_native_first_step_duration_respects_gateway_wire_version(
    tmp_path: Path, version: str, milliseconds: float | None, seconds: float | None
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk({"content": "Visible"}, finish="stop"),
                _chunk(usage=True, metrics={"time_to_first_token_ms": milliseconds}),
                "[DONE]",
            )
        )
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [
            event
            async for event in (await gateway.admit(_request(config, version=version))).events()
        ]
        assert events[-1].kind == "end" and events[-1].usage is not None
        expected = seconds if version == "0.3.0" else None
        assert events[-1].usage.prefill_seconds == expected
        serialized = json.loads(events[-1].model_dump_json())["usage"]
        assert ("prefill_seconds" in serialized) == (expected is not None)
        await gateway.aclose()

    asyncio.run(run())


def test_invalid_structured_result_is_not_published(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete(_chunk({"content": '{"ready":"wrong"}'})))
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [
            event
            async for event in (
                await gateway.admit(
                    _request(
                        config,
                        output="json",
                        output_schema={
                            "type": "object",
                            "properties": {"ready": {"type": "boolean"}},
                        },
                    )
                )
            ).events()
        ]
        assert events[-1].code == "structured_schema_mismatch"
        assert not any(event.kind == "text" for event in events)
        await gateway.aclose()

    asyncio.run(run())


def test_grammar_projection_preserves_literal_property_names_and_data(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        schema = {
            "type": "object",
            "properties": {
                "propertyNames": {
                    "type": "object",
                    "const": {"propertyNames": "literal data"},
                    "default": {"propertyNames": {"pattern": "literal default"}},
                },
                "extensions": {
                    "type": "object",
                    "propertyNames": {"pattern": "^allowed\\."},
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["propertyNames", "extensions"],
        }
        before = json.dumps(schema, sort_keys=True)
        server = _Server(
            _complete(
                _chunk(
                    {
                        "content": json.dumps(
                            {
                                "propertyNames": {"propertyNames": "literal data"},
                                "extensions": {"allowed.key": "value"},
                            }
                        )
                    }
                )
            )
        )
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        request = _request(config, output="json", output_schema=schema)
        events = [event async for event in (await gateway.admit(request)).events()]
        assert events[-1].kind == "end"
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        projected = body["response_format"]["json_schema"]["schema"]
        assert projected["properties"]["propertyNames"] == schema["properties"]["propertyNames"]
        assert "propertyNames" not in projected["properties"]["extensions"]
        assert json.dumps(schema, sort_keys=True) == before
        assert request.output_schema == schema
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("batch", [ClaimBatch, DatedClaimBatch])
@pytest.mark.parametrize("valid", [False, True])
def test_full_memory_schema_is_guided_without_weakening_final_validation(
    tmp_path: Path, batch: type[ClaimBatch] | type[DatedClaimBatch], valid: bool
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        schema = batch.model_json_schema()
        before = json.dumps(schema, sort_keys=True)
        content = {"claims": [], "extensions": {} if valid else {"invalid": "rejected"}}
        server = _Server(_complete(_chunk({"content": json.dumps(content)})))
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        request = _request(config, workload="P3", output="json", output_schema=schema)
        events = [event async for event in (await gateway.admit(request)).events()]
        if valid:
            assert events[-1].kind == "end"
        else:
            assert events[-1].code == "structured_schema_mismatch"
            assert not any(event.kind in {"text", "end"} for event in events)
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        projected = body["response_format"]["json_schema"]["schema"]
        assert "propertyNames" not in projected["properties"]["extensions"]
        for definition in projected["$defs"].values():
            if "extensions" in definition.get("properties", {}):
                assert "propertyNames" not in definition["properties"]["extensions"]
        assert json.dumps(schema, sort_keys=True) == before and request.output_schema == schema
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("workload", ["P0", "P2", "P3"])
def test_truncated_json_never_becomes_a_complete_gateway_result(
    tmp_path: Path, workload: str
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk({"content": '{"ready":true}'}, finish="length"),
                _chunk(usage=True),
                "[DONE]",
            )
        )
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [
            event
            async for event in (
                await gateway.admit(_request(config, workload=workload, output="json"))
            ).events()
        ]
        assert events[-1].kind == "error"
        assert not any(item.kind in {"text", "end"} for item in events)
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf"), 1e-320])
def test_invalid_server_timings_fail_closed(tmp_path: Path, duration: float) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk(finish="stop"),
                _chunk(usage=True, metrics={"generation_time_ms": duration}),
                "[DONE]",
            )
        )
        upstream = VLLMUpstream(config, server.client())
        with pytest.raises(GatewayError, match="invalid_upstream_stream"):
            _ = [frame async for frame in upstream.generate(_request(config))]
        assert server.stream.closed
        await upstream.aclose()

    asyncio.run(run())


def test_multiline_sse_and_crlf_preserve_text(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        chunk = json.dumps(_chunk({"content": "Visible"}), indent=2)
        wire = (
            ": heartbeat\r\n"
            + "\r\n".join("data: " + line for line in chunk.splitlines())
            + "\r\n\r\n"
        )
        server = _Server(wire.encode() + _complete())
        upstream = VLLMUpstream(config, server.client())
        frames = [frame async for frame in upstream.generate(_request(config))]
        assert "".join(frame.message.content for frame in frames) == "Visible"
        assert frames[-1].done
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("enabled", [False, True])
def test_reasoning_is_ephemeral_and_explicit(tmp_path: Path, field: str, enabled: bool) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _complete(_chunk({field: "PRIVATE_REASONING"}), _chunk({"content": "Visible"}))
        )
        upstream = VLLMUpstream(config, server.client())
        frames = [
            frame
            async for frame in upstream.generate(
                _request(config, version="0.4.0", thinking=enabled)
            )
        ]
        assert "".join(frame.message.content for frame in frames) == "Visible"
        assert "".join(frame.message.thinking for frame in frames) == (
            "PRIVATE_REASONING" if enabled else ""
        )
        assert all("PRIVATE_REASONING" not in frame.model_dump_json() for frame in frames)
        await upstream.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "schema",
    [None, {"type": "object", "required": ["ready"], "properties": {"ready": {"type": "boolean"}}}],
)
def test_structured_output_preserves_gateway_validation(tmp_path: Path, schema: object) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(_complete(_chunk({"content": '{"ready":true}'})))
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [
            event
            async for event in (
                await gateway.admit(_request(config, output="json", output_schema=schema))
            ).events()
        ]
        assert [event.kind for event in events] == ["start", "text", "end"]
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        expected = (
            {"type": "json_object"}
            if schema is None
            else {"type": "json_schema", "json_schema": {"name": "ember_output", "schema": schema}}
        )
        assert body["response_format"] == expected
        await gateway.aclose()

    asyncio.run(run())


def test_fragmented_tool_names_are_carried_to_gateway(tmp_path: Path) -> None:
    async def run() -> None:
        config = _configuration(tmp_path)
        server = _Server(
            _sse(
                _chunk({"tool_calls": [{"index": 0, "id": "synthetic-call", "type": "function"}]}),
                _chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"value":'},
                            }
                        ]
                    }
                ),
                _chunk(
                    {"tool_calls": [{"index": 0, "function": {"arguments": "3}"}}]},
                    finish="tool_calls",
                ),
                _chunk(usage=True),
                "[DONE]",
            )
        )
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        definition = ToolDefinition(
            function=ToolFunction(
                name="lookup",
                parameters={
                    "type": "object",
                    "required": ["value"],
                    "properties": {"value": {"type": "integer"}},
                },
            )
        )
        events = [
            event
            async for event in (
                await gateway.admit(_request(config, workload="P2", tools=(definition,)))
            ).events()
        ]
        tool_event = next(item for item in events if item.kind == "tools")
        assert tool_event.calls[0].name == "lookup" and tool_event.calls[0].arguments == {
            "value": 3
        }
        assert events[-1].kind == "end"
        body = json.loads(next(item.content for item in server.requests if item.method == "POST"))
        assert body["tool_choice"] == "auto" and body["tools"][0]["function"]["name"] == "lookup"
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "eof",
        "missing_usage",
        "missing_done",
        "malformed",
        "model",
        "index",
        "boolean_index",
        "tool_index",
        "usage_before_finish",
        "negative_usage",
        "changed_id",
        "after_finish",
    ],
)
def test_invalid_stream_never_publishes_success(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        parts: list[dict[str, object] | str] = [
            _chunk({"content": "Partial"}),
            _chunk(finish="stop"),
            _chunk(usage=True),
            "[DONE]",
        ]
        if fault == "eof":
            parts = parts[:1]
        elif fault == "missing_usage":
            del parts[2]
        elif fault == "missing_done":
            parts.pop()
        elif fault == "malformed":
            parts[0] = "{invalid"
        elif fault == "model":
            parts[0] = _chunk(model="other-model")
        elif fault == "index":
            parts[0] = _chunk(choices=[{"index": -1, "delta": {}}])
        elif fault == "boolean_index":
            parts[0] = _chunk(choices=[{"index": False, "delta": {}}])
        elif fault == "tool_index":
            parts[0] = _chunk(
                {"tool_calls": [{"index": 3, "function": {"name": "lookup", "arguments": "{}"}}]}
            )
        elif fault == "usage_before_finish":
            parts[0] = _chunk(usage=True)
        elif fault == "negative_usage":
            parts[2] = _chunk(usage=True) | {
                "usage": {"prompt_tokens": -1, "completion_tokens": 3, "total_tokens": 2}
            }
        elif fault == "changed_id":
            parts[1] = _chunk(finish="stop", id="different-request")
        else:
            parts.insert(2, _chunk({"content": "After finish"}))
        config = _configuration(tmp_path)
        server = _Server(_sse(*parts))
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        events = [event async for event in (await gateway.admit(_request(config))).events()]
        assert events[-1].kind == "error" and gateway.uncertain
        assert not any(item.kind == "end" for item in events)
        assert server.stream.closed
        await gateway.aclose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["line", "multiline", "metadata", "encoding", "utf8", "unfinished_event"]
)
def test_untrusted_wire_data_is_bounded(tmp_path: Path, fault: str) -> None:
    async def run() -> None:
        wire = _complete()
        if fault == "line":
            wire = b"data: " + b"x" * (1024 * 1024 + 1)
        elif fault == "multiline":
            wire = b"data: " + b"x" * 600_000 + b"\ndata: " + b"y" * 600_000 + b"\n\n"
        elif fault == "utf8":
            wire = b"data: \xff\n\n"
        elif fault == "unfinished_event":
            wire = b"data: {}\n"
        server = _Server(_Bytes(wire, split=64 * 1024))
        if fault == "metadata":
            server.config["oversized"] = "x" * (1024 * 1024)
        elif fault == "encoding":
            server.encoded = True
        upstream = VLLMUpstream(_configuration(tmp_path), server.client())
        with pytest.raises(GatewayError):
            if fault in {"metadata", "encoding"}:
                await upstream.verify()
            else:
                _ = [frame async for frame in upstream.generate(_request(upstream.config))]
        await upstream.aclose()

    asyncio.run(run())


def test_credentials_only_use_named_environment_and_http_errors_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        config = _configuration(tmp_path, api_key_environment="EMBER_TEST_VLLM_TOKEN")
        server = _Server(_complete())
        upstream = VLLMUpstream(config, server.client())
        with pytest.raises(GatewayError, match="upstream_credentials_unavailable"):
            await upstream.verify()
        assert not server.requests
        monkeypatch.setenv("EMBER_TEST_VLLM_TOKEN", "private-token")
        await upstream.verify()
        assert all(
            item.headers["authorization"] == "Bearer private-token" for item in server.requests
        )
        server.status = 503
        with pytest.raises(GatewayError, match="upstream_transport_failed") as raised:
            _ = [frame async for frame in upstream.generate(_request(config))]
        assert "private-token" not in str(raised.value)
        await upstream.aclose()

    asyncio.run(run())


def test_subscriber_detach_drains_vllm_transport_after_done(tmp_path: Path) -> None:
    class HeldStream(_Bytes):
        def __init__(self) -> None:
            super().__init__(_complete(_chunk({"content": "Visible"})))
            self.terminal_sent = asyncio.Event()
            self.release = asyncio.Event()

        async def __aiter__(self) -> AsyncIterator[bytes]:
            async for chunk in super().__aiter__():
                yield chunk
            self.terminal_sent.set()
            await self.release.wait()

    async def run() -> None:
        stream = HeldStream()
        server = _Server(stream)
        config = _configuration(tmp_path)
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        job = await gateway.admit(_request(config))
        events = job.events()
        assert (await anext(events)).kind == "start"
        await asyncio.wait_for(stream.terminal_sent.wait(), 2)
        assert (await anext(events)).kind == "text"
        await events.aclose()
        assert gateway.active is job and not stream.closed
        with pytest.raises(GatewayError, match="busy"):
            await gateway.admit(_request(config))
        closing = asyncio.create_task(gateway.aclose())
        await asyncio.sleep(0)
        assert not closing.done() and not stream.closed
        stream.release.set()
        await closing
        assert stream.closed and gateway.active is None and not gateway.uncertain

    asyncio.run(run())


def test_cancelled_owned_request_closes_http_and_requires_quiescence(tmp_path: Path) -> None:
    class HeldStream(_Bytes):
        def __init__(self) -> None:
            super().__init__(b"")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __aiter__(self) -> AsyncIterator[bytes]:
            self.started.set()
            yield _sse(_chunk({"content": "Partial"}))
            await self.release.wait()

    async def run() -> None:
        stream = HeldStream()
        server = _Server(stream)
        config = _configuration(tmp_path)
        gateway = P0Gateway(config, VLLMUpstream(config, server.client()))
        job = await gateway.admit(_request(config))
        await stream.started.wait()
        assert job.task is not None
        job.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job.task
        assert stream.closed and gateway.uncertain
        with pytest.raises(GatewayError, match="quiescence_required"):
            await gateway.admit(_request(config))
        await gateway.aclose()

    asyncio.run(run())


class _StrataStream(OpenAIStreamUpstream):
    usage_on_finish = True

    async def _attest(self) -> tuple[str, str]:
        return self.config.model_digest, "test-instance"


@pytest.mark.parametrize("terminal", ["[DONE]", "malformed"])
def test_finish_chunk_with_usage(tmp_path: Path, terminal: str) -> None:
    asyncio.run(_finish_chunk_with_usage(tmp_path, terminal))


async def _finish_chunk_with_usage(tmp_path: Path, terminal: str) -> None:
    config = _configuration(tmp_path)
    final = _chunk(
        finish="stop",
        timings={"prompt_ms": 10.0, "predicted_per_second": 59.5},
    ) | {"usage": _chunk(usage=True)["usage"]}
    server = _Server(_sse(_chunk({"content": "Hello"}), final, terminal))
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(server.respond)
    ) as client:
        upstream = _StrataStream(config, "http://127.0.0.1:8080", client)
        stream = upstream.generate(_request(config))
        assert (await anext(stream)).message.content == "Hello"
        if terminal == "[DONE]":
            frame = await anext(stream)
            assert frame.done and frame.prompt_eval_count == 12 and frame.eval_count == 3
            assert frame.prompt_eval_duration == 10_000_000
            assert frame.decode_tokens_per_second == 59.5
        else:
            with pytest.raises(GatewayError, match="invalid_upstream_stream"):
                await anext(stream)
        await stream.aclose()
