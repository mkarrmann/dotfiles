from __future__ import annotations

from typing import get_args

import pytest

from omnigent_diff_watcher.domain import DIFF_EVENT_KINDS
from omnigent_diff_watcher.mcp_server import _ALL_EVENTS, EventName, mcp

SESSION = "a989d27536ab4b1b912b0e07efc2ee21"


def test_published_event_names_match_the_watcher_domain() -> None:
    """The tool schema is a hand-written literal; drift would silently make an
    event unsubscribable from the MCP surface while the watcher still emits it.

    Pinned to the diff source's kinds rather than to every ``EventKind``: this
    tool subscribes to diffs, so a kind another source emits does not belong in
    its schema.
    """
    assert set(_ALL_EVENTS) == {kind.value for kind in DIFF_EVENT_KINDS}
    assert set(get_args(EventName)) == {kind.value for kind in DIFF_EVENT_KINDS}


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
    diff_subscribe = next(tool for tool in tools if tool.name == "diff_watch_subscribe")
    properties = diff_subscribe.inputSchema.get("properties", {})
    # The diff surface must not grow generic knobs.
    assert "command" not in properties
    assert "source" not in properties
    assert properties["diffs"]["items"]["pattern"] == "^D[1-9][0-9]*$"
    assert properties["diffs"]["minItems"] == 1


async def test_every_tool_takes_the_session_to_wake() -> None:
    """The wake address is the one thing a watch cannot infer.

    It is required on every tool, including the read-only ones: a status or
    unsubscribe call with no session would have to guess whose watches it means.
    Discovering it instead worked for exactly two harnesses -- see the module
    docstring -- so a missing ``session_id`` here is a return to that.
    """
    for tool in await mcp.list_tools():
        schema = tool.inputSchema
        assert "session_id" in schema.get("properties", {}), tool.name
        assert "session_id" in schema.get("required", []), tool.name


def test_nothing_in_the_surface_is_harness_specific() -> None:
    """No bridge-directory resolution may come back.

    Identity used to be discovered by scanning the harness's private bridge
    directory, which worked for two harnesses, coupled the tool to Omnigent's
    internal directory names, and could match two bridges at once. Taking the
    address as an argument is what replaced all of it.
    """
    from omnigent_diff_watcher import mcp_server

    for banned in (
        "_claude_bridge_dir",
        "_codex_bridge_dir",
        "_native_bridge_dir",
        "_policy_endpoints",
        "_native_policy_result",
        "_NATIVE_MODE",
    ):
        assert not hasattr(mcp_server, banned), banned


async def test_subscribe_rejects_an_empty_event_selection() -> None:
    from omnigent_diff_watcher.mcp_server import diff_watch_subscribe

    with pytest.raises(ValueError, match="at least one"):
        await diff_watch_subscribe(SESSION, ["D111179041"], [])
