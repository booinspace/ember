"""Explicit configuration, P0 gateway and Phase 3 text-runtime commands."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .runtime.config import ConfigurationError, DeploymentConfig, load_config
from .runtime.health import readiness


class _StartArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["start"]
    config: Path
    open_browser: bool


class _GatewayArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["gateway"]
    config: Path
    acknowledge_quiescence: bool


class _ChatArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["chat"]
    config: Path


class _LiveKitChatArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["livekit-chat"]
    config: Path
    prompt: str | None


class _BaselineArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["baseline"]
    config: Path
    output: Path


class _NetworkArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["network-info"]
    config: Path


class _DoctorArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["doctor"]
    config: Path
    require_model_pin: bool
    json_output: bool


type _Arguments = Annotated[
    _StartArguments
    | _DoctorArguments
    | _GatewayArguments
    | _ChatArguments
    | _BaselineArguments
    | _LiveKitChatArguments
    | _NetworkArguments,
    Field(discriminator="command"),
]


def _arguments(argv: list[str] | None) -> _Arguments:
    parser = argparse.ArgumentParser(prog="ember")
    parser.add_argument(
        "--version", action="version", version="%(prog)s 0.1.0 (experimental runtime)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    network = commands.add_parser("network-info", help="Describe configured LAN listeners offline")
    network.add_argument("--config", type=Path, required=True)
    doctor = commands.add_parser("doctor", help="Validate configuration without starting services")
    doctor.add_argument("--config", type=Path, required=True)
    doctor.add_argument("--require-model-pin", action="store_true")
    doctor.add_argument("--json", dest="json_output", action="store_true")
    start = commands.add_parser("start", help="Start the local character and browser runtime")
    start.add_argument(
        "--config", type=Path, default=Path("ember-agent/config/deployment.local.toml")
    )
    start.add_argument(
        "--open-browser", action="store_true", help="Open the private browser after readiness"
    )
    room_chat = commands.add_parser("livekit-chat", help="Join the running Ember LiveKit text room")
    room_chat.add_argument("--config", type=Path, required=True)
    room_chat.add_argument("--prompt", help="Send one prompt and exit")
    gateway = commands.add_parser("gateway", help="Run the owned local P0 inference service")
    gateway.add_argument("--config", type=Path, required=True)
    gateway.add_argument(
        "--acknowledge-quiescence",
        action="store_true",
        help="Assert that the previously interrupted runner has been verified idle",
    )
    chat = commands.add_parser("chat", help="Run the memory-aware foreground text slice")
    chat.add_argument("--config", type=Path, required=True)
    baseline = commands.add_parser("baseline", help="Measure synthetic foreground gateway cases")
    baseline.add_argument("--config", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)
    adapter: TypeAdapter[_Arguments] = TypeAdapter(_Arguments)
    return adapter.validate_python(vars(parser.parse_args(argv)))


def main(argv: list[str] | None = None) -> int:
    """Run explicit maintenance/runtime commands; doctor remains offline.

    Argparse retains its help/usage exit behavior,
    and domain error messages omit input values such as credentials and private paths.
    """
    arguments = _arguments(argv)
    try:
        config = load_config(
            arguments.config,
            require_model_pin=(
                arguments.require_model_pin
                if arguments.command == "doctor"
                else arguments.command != "network-info"
            ),
        )
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if arguments.command == "network-info":
        from .runtime.network import network_info

        try:
            print(network_info(config).model_dump_json())
        except ConfigurationError as error:
            print(f"Configuration error: {error}", file=sys.stderr)
            return 2
        return 0
    if arguments.command != "doctor":
        return _runtime(arguments, config)
    report = readiness(config)
    if arguments.json_output:
        print(report.model_dump_json(indent=2))
    else:
        print("Deployment configuration is valid. Runtime health is not probed.")
        print(f"Model pin: {report.model_pin}. Service health: not probed.")
        for blocker in report.blockers:
            print(f"Pending: {blocker}")
    return 0


async def _baseline(config: DeploymentConfig, token: str) -> str:
    from .inference.client import HttpGatewayClient
    from .maintenance.baseline import measure

    # All filesystem work remains outside the running event loop at entry/exit.
    client = HttpGatewayClient(config.services.gateway.endpoint, token)
    try:
        report = await measure(config, client)
        return report.model_dump_json(indent=2)
    finally:
        await client.aclose()


def _runtime(
    arguments: _GatewayArguments
    | _ChatArguments
    | _BaselineArguments
    | _StartArguments
    | _LiveKitChatArguments,
    config: DeploymentConfig,
) -> int:
    from livekit import api, rtc
    from pydantic import ValidationError

    from ember_memory_abi.errors import MemoryError

    from .inference.contracts import GatewayError

    try:
        if not config.model_pin_configured:
            raise ConfigurationError("A verified model pin is required for generation.")
        if arguments.command == "start":
            from .runtime.livekit_runtime import run_runtime

            asyncio.run(run_runtime(config, arguments.config, open_browser=arguments.open_browser))
        elif arguments.command == "livekit-chat":
            from .runtime.livekit_runtime import run_participant

            asyncio.run(run_participant(config, prompt=arguments.prompt))
        elif arguments.command == "gateway":
            from .inference.service import serve

            serve(config, acknowledge_quiescence=arguments.acknowledge_quiescence)
        elif arguments.command == "chat":
            from .runtime.composition import LocalRuntime

            runtime = LocalRuntime(config)
            try:
                asyncio.run(runtime.chat())
            finally:
                runtime.close_workers()
        else:
            token = (config.runtime_root / "gateway.token").read_text(encoding="utf-8").strip()
            report_text = asyncio.run(_baseline(config, token))
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            with arguments.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(report_text)
            print("Content-free baseline written.")
        return 0
    except KeyboardInterrupt:
        return 0
    except (GatewayError, MemoryError, ConfigurationError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except (api.TwirpError, rtc.ConnectError, rtc.RpcError, TimeoutError):
        print("LiveKit connection or service shutdown failed.", file=sys.stderr)
        return 2
    except (OSError, ValueError, ValidationError):
        print("Runtime input or local state is invalid.", file=sys.stderr)
        return 2
