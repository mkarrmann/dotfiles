from __future__ import annotations

from typing import get_args

import pytest

from omnigent_diff_watcher.domain import DIFF_EVENT_KINDS
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

    Pinned to the diff source's kinds rather than to every ``EventKind``: this
    tool subscribes to diffs, so a kind another source emits does not belong in
    its schema.
    """
    assert set(_ALL_EVENTS) == {kind.value for kind in DIFF_EVENT_KINDS}
    assert set(get_args(EventName)) == {kind.value for kind in DIFF_EVENT_KINDS}


def test_subscribe_rejects_an_empty_selection() -> None:
    with pytest.raises(ValueError, match="at least one"):
        diff_watch_subscribe([])


def test_subscribe_rejects_an_empty_diff_selection() -> None:
    with pytest.raises(ValueError, match="at least one diff ID"):
        diff_watch_subscribe(diffs=[])


async def test_mcp_exposes_the_diff_and_generic_watch_surfaces() -> None:
    """Two surfaces, deliberately. The diff tools stay opinionated about diffs
    -- no source, no argv -- and the generic ones make no assumption about
    what is being watched. Adding a tool to either set should be a decision,
    not a side effect."""
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == {
        "diff_watch_subscribe",
        "diff_watch_unsubscribe",
        "diff_watch_status",
        "watch_subscribe",
        "watch_unsubscribe",
        "watch_status",
    }
    # The diff surface must not grow generic knobs.
    diff_subscribe = next(tool for tool in tools if tool.name == "diff_watch_subscribe")
    assert "command" not in diff_subscribe.inputSchema.get("properties", {})
    assert "source" not in diff_subscribe.inputSchema.get("properties", {})
    subscribe = next(tool for tool in tools if tool.name == "diff_watch_subscribe")
    assert "session_id" not in subscribe.inputSchema.get("properties", {})
    diffs = subscribe.inputSchema.get("properties", {}).get("diffs", {})
    assert diffs["anyOf"][0]["items"]["pattern"] == "^D[1-9][0-9]*$"
    assert diffs["anyOf"][0]["minItems"] == 1
    assert diffs["anyOf"][0]["maxItems"] == 20


def test_generic_watches_refuse_outside_a_native_session() -> None:
    """The generic surface is native-only, and has to say so.

    diff_watch_* works from any harness because a server-side policy binds its
    result. A generic watch writes to the database itself, so it must identify
    the session, which a streamed SDK session cannot supply -- and the failure
    has to name that rather than claim a Codex session is required.
    """
    from omnigent_diff_watcher import mcp_server

    assert mcp_server._NATIVE_MODE is None
    with pytest.raises(RuntimeError, match="require an Omnigent native"):
        mcp_server.watch_subscribe("jk:demo", ["true"])
