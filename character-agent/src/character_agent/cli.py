"""Preparation commands; character-agent start cannot advertise a fake runtime."""

import argparse
import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .runtime.config import ConfigurationError, load_config
from .runtime.health import readiness


class _StartArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["start"]


class _DoctorArguments(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    command: Literal["doctor"]
    config: Path
    require_model_pin: bool
    json_output: bool


type _Arguments = Annotated[_StartArguments | _DoctorArguments, Field(discriminator="command")]


def _arguments(argv: list[str] | None) -> _Arguments:
    parser = argparse.ArgumentParser(prog="character-agent")
    parser.add_argument("--version", action="version", version="%(prog)s 0.0.0 (phase -1)")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Validate configuration without starting services")
    doctor.add_argument("--config", type=Path, required=True)
    doctor.add_argument("--require-model-pin", action="store_true")
    doctor.add_argument("--json", dest="json_output", action="store_true")
    commands.add_parser("start", help="Reserved: application startup is implemented in phase 8")
    adapter: TypeAdapter[_Arguments] = TypeAdapter(_Arguments)
    return adapter.validate_python(vars(parser.parse_args(argv)))


def main(argv: list[str] | None = None) -> int:
    """Report validated preparation settings; return 2 on configuration/unavailable-runtime errors.

    No services are probed or started. Argparse retains its help/usage exit behavior,
    and domain error messages omit input values such as credentials and private paths.
    """
    arguments = _arguments(argv)
    if arguments.command == "start":
        print(
            "Runtime not implemented in phase -1. Use doctor --config to check preparation.",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_config(arguments.config, require_model_pin=arguments.require_model_pin)
    except ConfigurationError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    report = readiness(config)
    if arguments.json_output:
        print(report.model_dump_json(indent=2))
    else:
        print("Phase -1 configuration is valid. Application/character/voice are not implemented.")
        print(f"Model pin: {report.model_pin}. Service health: not probed.")
        for blocker in report.blockers:
            print(f"Pending: {blocker}")
    return 0
