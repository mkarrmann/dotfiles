from __future__ import annotations

from typing import get_args

import httpx
import pytest

from omnigent_watcher.domain import DIFF_EVENT_KINDS
from omnigent_watcher.mcp_server import _ALL_EVENTS, EventName, mcp

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
        "diff_subscribe",
        "diff_unsubscribe",
        "diff_status",
        "subscribe",
        "unsubscribe",
        "status",
    }
    diff_subscribe = next(tool for tool in tools if tool.name == "diff_subscribe")
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
    from omnigent_watcher import mcp_server

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
    from omnigent_watcher.mcp_server import diff_subscribe

    with pytest.raises(ValueError, match="at least one"):
        await diff_subscribe(SESSION, ["D111179041"], [])


async def test_the_session_id_accepts_an_agent_home_address() -> None:
    import re

    tools = await mcp.list_tools()
    pattern = re.compile(tools[0].inputSchema["properties"]["session_id"]["pattern"])
    assert pattern.fullmatch(SESSION)
    assert pattern.fullmatch("conv_85209e85779b406aa9e5b78b2a0a43c2")
    assert pattern.fullmatch("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ")
    assert pattern.fullmatch("agenthome:01a1130d-2ab8-7d60-a9cf-7556b6f9dc67")
    # The prefix counts against the server's 128-character limit.
    assert not pattern.fullmatch("agenthome:" + "a" * 119)
    assert not pattern.fullmatch("otherhome:ah_rrO5GPFnu14SVsq9ExhBFJ")
    assert not pattern.fullmatch("agenthome:ah rr")


async def test_an_agent_home_watch_waits_for_a_worker_that_can_wake_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older worker would look the session up in Omnigent, get a 404, and
    retire the watch as deleted -- accepted, never fired."""
    from omnigent_watcher import agenthome_client, mcp_server

    async def omnigent_only() -> frozenset[str]:
        return frozenset({"omnigent"})

    async def never_called(self: object, session_id: str) -> object:
        raise AssertionError("Agent Home must not be queried before the worker supports it")

    monkeypatch.setattr(mcp_server, "_worker_session_kinds", omnigent_only)
    monkeypatch.setattr(agenthome_client.AgentHomeClient, "get", never_called)
    with pytest.raises(ValueError, match="cannot wake Agent Home sessions yet"):
        await mcp_server._validate_session("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ")


@pytest.mark.parametrize(
    ("snapshot", "error"),
    [
        ({"exists": False}, "no live Agent Home session"),
        ({"reachable": False, "can_accept_input": False}, "no live Agent Home session"),
        ({"can_accept_input": False}, None),
        ({}, None),
    ],
)
async def test_an_agent_home_session_is_validated_against_agent_home(
    monkeypatch: pytest.MonkeyPatch, snapshot: dict[str, bool], error: str | None
) -> None:
    from omnigent_watcher import agenthome_client, mcp_server
    from omnigent_watcher.domain import SessionSnapshot

    async def both() -> frozenset[str]:
        return frozenset({"omnigent", "agenthome"})

    async def lookup(self: object, session_id: str) -> SessionSnapshot:
        return SessionSnapshot(session_id=session_id, labels={}, **snapshot)

    async def no_omnigent(session_id: str) -> None:
        raise AssertionError("an Agent Home id must not be looked up in Omnigent")

    monkeypatch.setattr(mcp_server, "_worker_session_kinds", both)
    monkeypatch.setattr(mcp_server, "_validate_omnigent_session", no_omnigent)
    monkeypatch.setattr(agenthome_client.AgentHomeClient, "get", lookup)
    if error is None:
        await mcp_server._validate_session("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ")
    else:
        with pytest.raises(ValueError, match=error):
            await mcp_server._validate_session("agenthome:ah_rrO5GPFnu14SVsq9ExhBFJ")


async def test_a_server_without_the_capability_route_means_omnigent_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from omnigent_watcher import mcp_server

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/watches/capabilities"
        return httpx.Response(404, json={"detail": "Not Found"})

    real_client = httpx.AsyncClient

    def mocked_client(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "AsyncClient", mocked_client)
    assert await mcp_server._worker_session_kinds() == {"omnigent"}
