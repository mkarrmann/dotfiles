"""Stateless MCP intent tools; Omnigent policy binds results to a session."""

from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("diff-watch", log_level="ERROR")
EventName = Literal["review_comment", "ci_failure"]


@mcp.tool()
def diff_watch_subscribe(
    events: list[EventName] | None = None,
) -> str:
    """Opt the current Omnigent session into diff review and CI notifications."""
    selected = sorted(set(["review_comment", "ci_failure"] if events is None else events))
    if not selected:
        raise ValueError("at least one event type is required")
    return "Diff-watch preference requested for: " + ",".join(selected)


@mcp.tool()
def diff_watch_unsubscribe() -> str:
    """Stop diff notifications for the current Omnigent session."""
    return "Diff-watch unsubscribe requested."


@mcp.tool()
def diff_watch_status() -> str:
    """Read the current session's diff-watch preference."""
    return "Diff-watch status is supplied by the Omnigent session policy."


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
