from __future__ import annotations

from typing import get_args

import pytest

from omnigent_diff_watcher.domain import EventKind
from omnigent_diff_watcher.mcp_server import (
    _ALL_EVENTS,
    EventName,
    diff_watch_status,
    diff_watch_subscribe,
    diff_watch_unsubscribe,
    mcp,
)


def test_intent_tools_return_bounded_non_identity_results() -> None:
    assert diff_watch_subscribe() == (
        "Diff-watch preference requested for: ai_review,ci_failure,ci_green,review_comment"
    )
    assert diff_watch_subscribe(["review_comment"]) == (
        "Diff-watch preference requested for: review_comment"
    )
    assert diff_watch_subscribe(["ci_green"], ["D111179041"]) == (
        "Diff-watch preference requested for: ci_green"
    )
    assert "unsubscribe requested" in diff_watch_unsubscribe()
    assert "session policy" in diff_watch_status()


def test_published_event_names_match_the_watcher_domain() -> None:
    """The tool schema is a hand-written literal; drift would silently make an
    event unsubscribable from the MCP surface while the watcher still emits it.
    """
    assert set(_ALL_EVENTS) == {kind.value for kind in EventKind}
    assert set(get_args(EventName)) == {kind.value for kind in EventKind}


def test_subscribe_rejects_an_empty_selection() -> None:
    with pytest.raises(ValueError, match="at least one"):
        diff_watch_subscribe([])


def test_subscribe_rejects_an_empty_diff_selection() -> None:
    with pytest.raises(ValueError, match="at least one diff ID"):
        diff_watch_subscribe(diffs=[])


async def test_mcp_exposes_only_the_three_intent_tools() -> None:
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "diff_watch_subscribe",
        "diff_watch_unsubscribe",
        "diff_watch_status",
    }
    subscribe = next(tool for tool in tools if tool.name == "diff_watch_subscribe")
    assert "session_id" not in subscribe.inputSchema.get("properties", {})
    diffs = subscribe.inputSchema.get("properties", {}).get("diffs", {})
    assert diffs["anyOf"][0]["items"]["pattern"] == "^D[1-9][0-9]*$"
    assert diffs["anyOf"][0]["minItems"] == 1
    assert diffs["anyOf"][0]["maxItems"] == 20
