"""Private local LiveKit credentials and room-scoped capability tokens."""

import json
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from livekit import api
from pydantic import Field

from .config import ConfigurationError, RuntimeModel

AGENT_IDENTITY = "ember-agent"
USER_IDENTITY = "ember-user"


class LiveKitCredentials(RuntimeModel):
    """Persist only in private runtime state; never print or put secrets in process arguments."""

    api_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")] = Field(repr=False)
    api_secret: Annotated[str, Field(min_length=32, max_length=128)] = Field(repr=False)

    def token(self, room: str, identity: str, *, agent: bool = False, audio: bool = False) -> str:
        """Authorize one participant; optional media grants permit microphone audio only."""
        return (
            api.AccessToken(self.api_key, self.api_secret)
            .with_identity(identity)
            .with_kind("agent" if agent else "standard")
            .with_ttl(timedelta(minutes=10))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room,
                    can_publish=audio,
                    can_publish_sources=["microphone"] if audio else [],
                    can_publish_data=True,
                    can_subscribe=True,
                    can_update_own_metadata=False,
                    agent=agent,
                )
            )
            .to_jwt()
        )


def credentials(path: Path, *, create: bool) -> LiveKitCredentials:
    """Load a bounded secret file, or create generated credentials during managed startup."""
    if path.exists():
        with path.open("rb") as stream:
            value = stream.read(4097)
        if len(value) > 4096:
            raise ConfigurationError("LiveKit credentials exceed their bound.")
        return LiveKitCredentials.model_validate_json(value)
    if not create:
        raise ConfigurationError("Attached LiveKit requires an existing credentials_file.")
    path.parent.mkdir(parents=True, exist_ok=True)
    value = LiveKitCredentials(
        api_key="ember_" + secrets.token_hex(12), api_secret=secrets.token_urlsafe(48)
    )
    with path.open("x", encoding="utf-8") as stream:
        stream.write(value.model_dump_json())
    path.chmod(0o600)
    return value


def server_yaml(value: LiveKitCredentials, *, port: int, tcp_port: int, udp_port: int) -> str:
    """Produce a loopback-only server configuration without shared development keys."""
    return (
        f"port: {port}\nbind_addresses: [127.0.0.1]\n"
        "rtc:\n  node_ip: 127.0.0.1\n  use_external_ip: false\n"
        f"  tcp_port: {tcp_port}\n  udp_port: {udp_port}\n  stun_servers: []\n"
        f"keys:\n  {value.api_key}: {json.dumps(value.api_secret)}\n"
        "logging:\n  level: error\nroom:\n  auto_create: false\n  max_participants: 2\n"
    )
