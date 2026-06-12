"""MCP server exposing terminus-mind over stdio.

Run with `tm-mcp`. Connection comes from the same env vars as the CLI
(TM_SERVER, TM_TEAM, TM_DB, TM_USER, TM_PASS), plus TM_AGENT for the
author recorded on memory commits (default "mcp").

The tool list and schemas are TOOL_SPECS verbatim — one source of truth
shared with direct-import integrations.
"""

from __future__ import annotations

import json
import os

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .mind import Mind
from .tools import TOOL_SPECS, dispatch


def build_server(mind: Mind) -> Server:
    server = Server("terminus-mind")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=s["name"],
                description=s["description"],
                inputSchema=s["parameters"],
            )
            for s in TOOL_SPECS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        # dispatch() returns JSON-serializable dicts and converts vocabulary
        # resistance into a normal {"resisted": true, ...} result; anything
        # else raising here becomes an MCP tool error via the SDK wrapper.
        result = await anyio.to_thread.run_sync(
            lambda: dispatch(mind, name, arguments or {})
        )
        return [types.TextContent(type="text", text=json.dumps(result, default=str))]

    return server


def main() -> None:
    mind = Mind(agent=os.environ.get("TM_AGENT", "mcp"))
    mind.init()
    server = build_server(mind)

    async def run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
