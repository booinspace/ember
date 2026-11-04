"""vLLM middleware: expose only authenticated gateway routes, including dev metadata."""

import hmac
import os
import sys
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from vllm_profile import load_profile


class GatewayOnlyMiddleware:
    """Protect metadata and reject all administration even when vLLM dev mode is enabled."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        token = os.environ.get("EMBER_VLLM_TOKEN", "")
        if not 32 <= len(token) <= 4096 or not token.isascii() or not token.isalnum():
            raise ValueError("A dedicated random alphanumeric vLLM credential is required.")
        self.authorization = "Bearer " + token
        profile, self.profile_sha256 = load_profile(Path(os.environ["EMBER_VLLM_PROFILE"]))
        if any(os.environ.get(key) != value for key, value in profile.environment.items()):
            raise ValueError(
                "vLLM execution environment does not match the verified setup receipt."
            )
        if sys.argv[1:] != profile.argv:
            raise ValueError("vLLM launch arguments do not match the verified setup receipt.")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope).getlist("authorization")
        if (
            len(headers) != 1
            or not headers[0].isascii()
            or not hmac.compare_digest(headers[0], self.authorization)
        ):
            await JSONResponse({"code": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        if scope["method"] == "GET" and scope["path"] == "/ember/profile":
            await JSONResponse({"profile_sha256": self.profile_sha256})(scope, receive, send)
            return
        allowed = (
            scope["method"] == "GET"
            and scope["path"] in {"/health", "/version", "/server_info", "/v1/models"}
        ) or (scope["method"] == "POST" and scope["path"] == "/v1/chat/completions")
        if not allowed:
            await JSONResponse({"code": "route_not_available"}, status_code=404)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)
