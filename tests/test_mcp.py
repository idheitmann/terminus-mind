"""End-to-end MCP test: spawn tm-mcp over stdio, drive it as a client."""

import json
import sys
import uuid

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from terminus_mind import TerminusClient
from terminus_mind.tools import TOOL_SPECS


@pytest.fixture()
def db_name():
    name = f"tm_mcp_test_{uuid.uuid4().hex[:8]}"
    yield name
    client = TerminusClient(db=name)
    client.delete_db()
    client.close()


def test_mcp_roundtrip(db_name):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "terminus_mind.mcp_server"],
        env={"TM_DB": db_name, "TM_AGENT": "mcp-test", "PATH": "/usr/bin:/bin"},
    )

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {s["name"] for s in TOOL_SPECS}

                def text(result):
                    return json.loads(result.content[0].text)

                ep = text(await session.call_tool(
                    "memory_observe",
                    {"content": "Ivan says he prefers dark roast coffee."}))["episode_id"]

                claim = text(await session.call_tool("memory_assert", {
                    "subject": "Ivan", "predicate": "prefers",
                    "value": "dark roast coffee",
                    "fact_text": "Ivan prefers dark roast coffee.",
                    "episode": ep, "by_human": True}))
                assert claim["claim_id"].startswith("Claim/")

                # vocabulary resistance arrives as data, not an error
                resisted = text(await session.call_tool("memory_assert", {
                    "subject": "Ivan", "predicate": "prefer",
                    "value": "tea"}))
                assert resisted["resisted"] is True
                assert resisted["suggestions"][0]["name"] == "prefers"

                hits = text(await session.call_tool(
                    "memory_recall", {"query": "coffee"}))["claims"]
                assert hits and hits[0]["fact_text"] == "Ivan prefers dark roast coffee."
                assert "_scores" in hits[0]

                review = text(await session.call_tool("memory_review", {}))
                assert len(review["review_queue"]) == 1

    anyio.run(scenario)
