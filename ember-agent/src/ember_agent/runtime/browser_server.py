"""The runtime owns the control server task and activation file, not another process or DB."""

import asyncio
import json
import socket
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .browser_control import BrowserControl
from .config import ConfigurationError
from .http_server import EmbeddedServer


class BrowserServer:
    """Serve prebuilt assets and controls within the existing Python application lifecycle."""

    def __init__(self, control: BrowserControl, root: Path) -> None:
        self.control = control
        self.root = root
        address = urlsplit(control.origin)
        self.server = EmbeddedServer(
            uvicorn.Config(
                control.app,
                host="0.0.0.0" if control.lan_origin else address.hostname or "127.0.0.1",
                port=address.port or 8787,
                access_log=False,
                proxy_headers=False,
                log_level="warning",
                ws="none",
                lifespan="off",
            )
        )
        self.task: asyncio.Task[None] | None = None
        self.socket: socket.socket | None = None
        self.activation_file = root / "browser-activation.json"

    async def start(self) -> None:
        """Explicit setup creates assets; runtime startup only starts its own installed server."""
        if self.task is not None:
            raise ConfigurationError("Browser server already started.")
        try:
            self.socket = await asyncio.to_thread(self._bind)
        except OSError as error:
            raise ConfigurationError("Browser control port is unavailable.") from error
        self.task = asyncio.create_task(
            self.server.serve(sockets=[self.socket]), name="ember-browser-server"
        )
        try:
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise ConfigurationError("Browser server did not start.")
                    await asyncio.sleep(0.05)
            await asyncio.to_thread(self._publish)
        except BaseException:
            await self.aclose()
            raise

    async def open_browser(self) -> bool:
        """Open the ready private activation URL off-loop; OS failure retains the runtime."""
        if self.task is None or not self.server.started:
            raise ConfigurationError("Browser server is not ready.")
        url = self.control.origin + "/#activate=" + self.control.activation
        try:
            return await asyncio.to_thread(webbrowser.open, url, new=2)
        except (OSError, webbrowser.Error):
            return False

    def _bind(self) -> socket.socket:
        address = urlsplit(self.control.origin)
        sock = socket.socket(
            socket.AF_INET6 if address.hostname == "::1" else socket.AF_INET, socket.SOCK_STREAM
        )
        try:
            host = "0.0.0.0" if self.control.lan_origin else address.hostname or "127.0.0.1"
            sock.bind((host, address.port or 8787))
            sock.setblocking(False)
            return sock
        except OSError:
            sock.close()
            raise

    def _publish(self) -> None:
        payload = json.dumps(
            {
                "url": self.control.origin + "/#activate=" + self.control.activation,
                "lan_url": self.control.lan_origin + "/#activate=" + self.control.activation
                if self.control.lan_origin
                else None,
            }
        )
        temporary = self.activation_file.with_suffix(".next")
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.chmod(0o600)
        temporary.replace(self.activation_file)

    async def aclose(self) -> None:
        """Close admission and drain request/native ownership before engine shutdown."""
        await self.control.aclose()
        self.server.should_exit = True
        if self.task is not None:
            try:
                async with asyncio.timeout(5):
                    await asyncio.shield(self.task)
            except TimeoutError:
                self.server.force_exit = True
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        await asyncio.to_thread(self.activation_file.unlink, missing_ok=True)
