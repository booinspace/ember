"""Offline LAN launch information reuses deployment ports and retained service ownership."""

import ctypes
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from .config import ConfigurationError, DeploymentConfig, RuntimeModel


class FirewallPort(RuntimeModel):
    """One application-scoped local-subnet inbound rule, never a model-service port."""

    name: str
    program: str
    protocol: Literal["TCP", "UDP"]
    port: int


class NetworkInfo(RuntimeModel):
    """Content-free setup descriptor; contains no activation or inference credentials."""

    enabled: bool
    address: str | None = None
    origin: str | None = None
    rules: tuple[FirewallPort, ...] = ()


def process_image() -> Path:
    """Windows venv redirectors differ from the real firewall-filtered process image."""
    if os.name != "nt":
        return Path(sys.executable).resolve()
    buffer = ctypes.create_unicode_buffer(32768)
    size: object = ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
    if not isinstance(size, int) or size <= 0 or size >= len(buffer):
        raise ConfigurationError("The runtime process image could not be identified.")
    return Path(buffer.value).resolve()


def validate_listener(config: DeploymentConfig) -> None:
    """Reject a stale advertised LAN address before acquiring runtime or data ownership."""
    if not config.browser.lan_address:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((config.browser.lan_address, 0))
    except OSError as error:
        raise ConfigurationError(
            "Configured LAN address is not assigned here; update browser.lan_address."
        ) from error


def network_info(config: DeploymentConfig) -> NetworkInfo:
    """Resolve the four explicit LAN listener rules without services, data or model probes."""
    address = config.browser.lan_address
    if not address:
        return NetworkInfo(enabled=False)
    livekit = config.services.livekit
    executable = shutil.which(livekit.executable)
    if executable is None:
        raise ConfigurationError("Install the configured LiveKit executable before LAN setup.")
    browser_port = urlsplit(config.browser.endpoint).port
    signal_port = urlsplit(livekit.endpoint).port
    assert browser_port is not None and signal_port is not None
    image = str(process_image())
    ports: tuple[tuple[str, str, Literal["TCP", "UDP"], int], ...] = (
        ("browser", image, "TCP", browser_port),
        ("livekit", executable, "TCP", signal_port),
        ("livekit", executable, "TCP", livekit.tcp_port),
        ("livekit", executable, "UDP", livekit.udp_port),
    )
    rules = tuple(
        FirewallPort(
            name=f"Ember-LAN-{config.active_profile}-{component}-{protocol.lower()}-{port}",
            program=program,
            protocol=protocol,
            port=port,
        )
        for component, program, protocol, port in ports
    )
    return NetworkInfo(
        enabled=True, address=address, origin=f"http://{address}:{browser_port}", rules=rules
    )
