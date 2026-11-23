"""Installed SDK conformance service; all sources and side effects are synthetic."""

import argparse
import asyncio
import gzip
import json
import os
import sys
from pathlib import Path

import uvicorn
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route


def build(mode: str) -> Server:
    async def listing(
        context: ServerRequestContext, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        del context
        if mode == "hung_discovery":
            marker = os.environ.get("MCP_CALL_FILE")
            if marker:
                Path(marker).write_text("listing")
            await asyncio.sleep(60)
        schema = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        if mode == "refs":
            schema = {
                "type": "object",
                "properties": {"text": {"$ref": "https://example.invalid/schema"}},
            }
        if mode == "pattern":
            schema["properties"]["text"]["pattern"] = "(a+)+$"
        marker = os.environ.get("MCP_VARIANT_FILE")
        variant = Path(marker).read_text() if marker else "stable"
        names = ["echo", "write", "sampling", "hang", "overflow", "error", "environment"]
        if mode == "duplicate":
            names = ["echo", "echo"]
        next_cursor = None
        if mode in {"pagination", "repeat_cursor"}:
            if params is None or params.cursor is None:
                names, next_cursor = ["echo"], "second"
            else:
                names = ["write"]
                next_cursor = "second" if mode == "repeat_cursor" else None
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description="Synthetic " + variant,
                    input_schema=schema,
                    output_schema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                    annotations=types.ToolAnnotations(read_only_hint=True),
                    _meta={"requirements": ["synthetic_fixture"]},
                )
                for name in names
            ],
            next_cursor=next_cursor,
        )

    async def call(
        context: ServerRequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult | types.InputRequiredResult:
        del context
        marker = os.environ.get("MCP_CALL_FILE")
        if marker:
            with Path(marker).open("a") as stream:
                stream.write(params.name + "\n")
        if params.name == "sampling":
            return types.InputRequiredResult(
                input_requests={
                    "sample": types.CreateMessageRequest(
                        params=types.CreateMessageRequestParams(
                            messages=[
                                types.SamplingMessage(
                                    role="user",
                                    content=types.TextContent(
                                        type="text", text="Do not sample this synthetic secret."
                                    ),
                                )
                            ],
                            max_tokens=10,
                        )
                    )
                }
            )
        if params.name == "hang":
            await asyncio.sleep(60)
        if params.name == "overflow":
            if mode == "http":
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text="x" * 100000)]
                )
            sys.stdout.buffer.write(b"x" * 100000 + b"\n")
            sys.stdout.buffer.flush()
            await asyncio.sleep(60)
        if params.name == "error":
            return types.CallToolResult(
                is_error=True,
                content=[
                    types.TextContent(type="text", text="Do not persist this remote error text.")
                ],
            )
        value = (params.arguments or {}).get("text", "")
        if params.name == "environment":
            value = json.dumps(
                {
                    "token": os.environ.get("MCP_FIXTURE_TOKEN"),
                    "unrelated": os.environ.get("EMBER_UNRELATED_SECRET"),
                    "cwd": os.getcwd(),
                }
            )
        if params.name == "write":
            value = "written"
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(value))],
            structured_content={"text": str(value)},
        )

    return Server("ember-synthetic-conformance", on_list_tools=listing, on_call_tool=call)


def legacy_nonterminal_stdio(version: str) -> None:
    """Send a raw legacy tool result that a conforming SDK server would strip."""
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        reply = {"jsonrpc": "2.0", "id": request["id"]}
        match request["method"]:
            case "initialize":
                reply["result"] = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "legacy-fixture", "version": "1"},
                }
            case "tools/list":
                reply["result"] = {
                    "tools": [
                        {"name": name, "inputSchema": {"type": "object"}}
                        for name in ("echo", "write")
                    ]
                }
            case "tools/call":
                name = request["params"]["name"]
                if marker := os.environ.get("MCP_CALL_FILE"):
                    with Path(marker).open("a") as stream:
                        stream.write(name + "\n")
                reply["result"] = {
                    "content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]
                }
                if name == "write":
                    reply["result"]["resultType"] = "input_required"
            case _:
                reply["error"] = {"code": -32601, "message": "Method not found"}
        print(json.dumps(reply), flush=True)


async def stdio(mode: str) -> None:
    server = build(mode)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    if marker := os.environ.get("MCP_PID_FILE"):
        Path(marker).write_text(str(os.getpid()))
    parser = argparse.ArgumentParser()
    parser.add_argument("mode")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.mode == "flood_discovery":
        sys.stdout.buffer.write(b"x" * (10 * 1024 * 1024))
        sys.stdout.buffer.flush()
        return
    if args.mode == "http":

        async def redirect(request: object) -> Response:
            del request
            return Response(status_code=307, headers={"Location": "/mcp"})

        async def compressed(request: object) -> Response:
            del request
            return Response(gzip.compress(b"x" * 100000), headers={"Content-Encoding": "gzip"})

        app = build(args.mode).streamable_http_app(
            json_response=True,
            stateless_http=True,
            custom_starlette_routes=[
                Route("/redirect", redirect, methods=["POST"]),
                Route("/compressed", compressed, methods=["POST"]),
            ],
        )
        expected = os.environ.get("MCP_HTTP_EXPECT_TOKEN")
        if expected:

            async def authorize(request: Request, call_next: RequestResponseEndpoint) -> Response:
                if request.headers.get("Authorization") != "Bearer " + expected:
                    return Response(status_code=401)
                return await call_next(request)

            app.add_middleware(BaseHTTPMiddleware, dispatch=authorize)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="critical", access_log=False)
    elif args.mode.startswith("legacy-nonterminal:"):
        legacy_nonterminal_stdio(args.mode.split(":", 1)[1])
    else:
        asyncio.run(stdio(args.mode))


if __name__ == "__main__":
    main()
