from __future__ import annotations

import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


async def test_real_stdio_server_lists_and_calls_intent_tools() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnigent_diff_watcher.mcp_server"],
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "diff_watch_subscribe",
            "diff_watch_unsubscribe",
            "diff_watch_status",
        }
        result = await session.call_tool(
            "diff_watch_subscribe",
            {"events": ["review_comment"]},
        )
        assert result.isError is False
        content = result.content[0]
        assert isinstance(content, TextContent)
        assert content.text == "Diff-watch preference requested for: review_comment"
