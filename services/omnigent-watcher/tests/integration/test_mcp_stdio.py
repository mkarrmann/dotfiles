"""The real stdio MCP server against the real hub router, over a real socket.

This is the only test that exercises the whole path a devserver actually
takes: a subprocess MCP server, stdio transport, an HTTP hop to the hub, the
mounted FastAPI router, and the sidecar's own database at the far end.

It exists because the previous arrangement passed while being wrong. The tools
opened the database by path, so on the hub -- the only machine anyone tested --
they hit the real file, and on every other devserver they hit an empty one no
sidecar would ever poll. A test that let the tool reach a local database could
not tell those apart. This one gives the MCP server no database at all: if it
tries to open one, it has nothing to open.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from omnigent_watcher.repository import WatcherRepository

LIVE_SESSION = "a989d27536ab4b1b912b0e07efc2ee21"
CLOSED_SESSION = "0e891a642a8b4f499c6b2eb642cff66c"
SUBJECT = "jk:presto/presto_batch:demo_knob"


@contextmanager
def _hub(database: Path) -> Iterator[str]:
    """The hub: the real watch router plus a stub session store.

    The router is mounted exactly as ``debug_router_modules`` mounts it in the
    Omnigent server, so its routing and request validation are under test too,
    not just the handler bodies.
    """
    import omnigent_watcher.http_api as http_api

    app = FastAPI()
    for router, prefix, tags in http_api.DEBUG_ROUTERS:
        app.include_router(router, prefix=prefix, tags=list(tags))

    @app.get("/v1/sessions/{session_id}")
    def read_session(session_id: str) -> dict[str, object]:
        from fastapi import HTTPException

        if session_id == LIVE_SESSION:
            return {"id": session_id, "status": "running", "archived": False}
        if session_id == CLOSED_SESSION:
            return {"id": session_id, "status": "closed", "archived": False}
        raise HTTPException(status_code=404, detail="no such session")

    # The router resolves the database through the package's own settings, so
    # point those at the test database rather than the real one.
    from omnigent_watcher.domain import WatcherConfig
    from omnigent_watcher.settings import ServiceSettings

    original = http_api._settings
    loaded = ServiceSettings(
        server_url="http://127.0.0.1:0",
        database_path=database,
        delivery_mode="log_only",
        delivery_session_allowlist=frozenset(),
        reconcile_interval_seconds=15,
        scheduler_error_retry_seconds=30,
        watcher=WatcherConfig(),
    )
    http_api._settings = lambda: loaded

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = threading.Event()
        while not server.started and not deadline.wait(0.02):
            if not thread.is_alive():
                raise RuntimeError("hub stub failed to start")
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        http_api._settings = original


def _parameters(hub_url: str) -> StdioServerParameters:
    """An MCP server with a hub and deliberately no database of its own."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnigent_watcher.mcp_server"],
        env={**os.environ, "OMNIGENT_URL": hub_url},
    )


def _text(result: CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


@pytest.mark.asyncio
async def test_a_watch_round_trips_from_a_client_to_the_hub(tmp_path: Path) -> None:
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)  # the sidecar owns migration
    value = tmp_path / "knob"
    value.write_text("false\n")

    with _hub(database) as hub_url:
        async with (
            stdio_client(_parameters(hub_url)) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            assert {tool.name for tool in (await session.list_tools()).tools} == {
                "diff_subscribe",
                "diff_unsubscribe",
                "diff_status",
                "subscribe",
                "unsubscribe",
                "status",
            }

            subscribed = await session.call_tool(
                "subscribe",
                {
                    "session_id": LIVE_SESSION,
                    "subject": SUBJECT,
                    "command": ["cat", str(value)],
                },
            )
            assert subscribed.isError is False, _text(subscribed)
            assert SUBJECT in _text(subscribed)

            listed = await session.call_tool("status", {"session_id": LIVE_SESSION})
            assert SUBJECT in _text(listed)
            assert f"cat {value}" in _text(listed)

            stopped = await session.call_tool("unsubscribe", {"session_id": LIVE_SESSION})
            assert "1 watch(es)" in _text(stopped)

    # The watch landed in the hub's database, and really stopped.
    repository = WatcherRepository(database, migrate=False)
    subscription = repository.subscription(LIVE_SESSION, SUBJECT)
    assert subscription is not None
    assert subscription.state.value == "retired"


@pytest.mark.asyncio
async def test_an_unknown_session_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)
    value = tmp_path / "knob"
    value.write_text("false\n")
    bogus = "deadbeefdeadbeefdeadbeefdeadbeef"

    with _hub(database) as hub_url:
        async with (
            stdio_client(_parameters(hub_url)) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "subscribe",
                {
                    "session_id": bogus,
                    "subject": SUBJECT,
                    "command": ["cat", str(value)],
                },
            )
            assert result.isError is True
            assert "sys_session_get_info" in _text(result)

    repository = WatcherRepository(database, migrate=False)
    assert repository.subscription(bogus, SUBJECT) is None
    assert repository.active_watch_requests(bogus) == []


@pytest.mark.asyncio
async def test_a_client_with_no_route_to_the_hub_says_so(tmp_path: Path) -> None:
    """The failure a non-hub devserver actually hit, and the message it needs.

    It used to report that the local database was at the wrong schema and
    advise restarting a service whose ExecCondition guarantees it can never
    start there. The tools must name the hub instead.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]

    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnigent_watcher.mcp_server"],
        env={**os.environ, "OMNIGENT_URL": f"http://127.0.0.1:{dead_port}"},
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        result = await session.call_tool("status", {"session_id": LIVE_SESSION})

    assert result.isError is True
    message = _text(result)
    assert "hub" in message.lower()
    assert "schema" not in message.lower()


@pytest.mark.asyncio
async def test_a_hub_without_the_router_mounted_says_what_is_missing(
    tmp_path: Path,
) -> None:
    """A hub running a server that predates the router 404s every call.

    Reporting that as a generic HTTP error would send someone looking at the
    client; the fix is a config sync and a server restart on the hub.
    """
    app = FastAPI()

    @app.get("/v1/sessions/{session_id}")
    def read_session(session_id: str) -> dict[str, object]:
        return {"id": session_id, "status": "running", "archived": False}

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            threading.Event().wait(0.02)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "omnigent_watcher.mcp_server"],
            env={**os.environ, "OMNIGENT_URL": f"http://127.0.0.1:{port}"},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool("status", {"session_id": LIVE_SESSION})
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    assert result.isError is True
    assert "debug_router_modules" in _text(result)


@pytest.mark.asyncio
async def test_the_mcp_server_never_opens_a_database(tmp_path: Path) -> None:
    """The regression that let the original bug ship.

    Storage reachable from the client is the whole defect: on a non-hub host it
    resolves to a file no sidecar polls. The module must not carry a path to
    one at all.
    """
    from omnigent_watcher import mcp_server

    for banned in ("_repository", "_database_path", "_watch_engine", "_record", "_bind"):
        assert not hasattr(mcp_server, banned), banned
