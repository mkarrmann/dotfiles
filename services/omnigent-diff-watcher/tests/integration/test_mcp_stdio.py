"""The real stdio MCP server, driven by a real MCP client.

This is the only test that exercises the server the way a harness does:
subprocess, stdio transport, JSON schemas, and the tool bodies end to end
against a real database. Everything harness-specific used to live here --
policy relays, bridge directories, ``--native`` flags -- and none of it exists
any more, because a watch now carries the session it should wake.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from omnigent_diff_watcher.repository import WatcherRepository

LIVE_SESSION = "a989d27536ab4b1b912b0e07efc2ee21"
SUBJECT = "jk:presto/presto_batch:demo_knob"


@contextmanager
def _omnigent(closed: str | None = None) -> Iterator[str]:
    """A stand-in Omnigent that answers session lookups."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            session_id = self.path.rsplit("/", 1)[-1]
            if session_id == LIVE_SESSION:
                body = {"id": session_id, "status": "running", "archived": False}
            elif session_id == closed:
                body = {"id": session_id, "status": "closed", "archived": False}
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def _server(database: Path, server_url: str) -> Iterator[StdioServerParameters]:
    yield StdioServerParameters(
        command=sys.executable,
        args=["-m", "omnigent_diff_watcher.mcp_server"],
        env={
            **os.environ,
            "OMNIGENT_DIFF_WATCHER_DATABASE": str(database),
            "OMNIGENT_URL": server_url,
        },
    )


def _text(result: CallToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


async def test_a_generic_watch_round_trips_through_the_real_stdio_server(
    tmp_path: Path,
) -> None:
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)  # the sidecar owns migration; create the schema
    value = tmp_path / "knob"
    value.write_text("false\n")

    with _omnigent() as url, _server(database, url) as parameters:
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            assert {tool.name for tool in (await session.list_tools()).tools} == {
                "diff_watch_subscribe",
                "diff_watch_unsubscribe",
                "diff_watch_status",
                "watch_subscribe",
                "watch_unsubscribe",
                "watch_status",
            }

            subscribed = await session.call_tool(
                "watch_subscribe",
                {
                    "session_id": LIVE_SESSION,
                    "subject": SUBJECT,
                    "command": ["cat", str(value)],
                },
            )
            assert subscribed.isError is False, _text(subscribed)
            assert SUBJECT in _text(subscribed)
            assert LIVE_SESSION in _text(subscribed)

            listed = await session.call_tool("watch_status", {"session_id": LIVE_SESSION})
            assert SUBJECT in _text(listed)
            assert f"cat {value}" in _text(listed)

            stopped = await session.call_tool("watch_unsubscribe", {"session_id": LIVE_SESSION})
            assert "1 watch(es)" in _text(stopped)

    # The watch really landed in the shared database, and really stopped.
    repository = WatcherRepository(database, migrate=False)
    subscription = repository.subscription(LIVE_SESSION, SUBJECT)
    assert subscription is not None
    assert subscription.state.value == "retired"


async def test_an_unknown_session_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    """The address comes from the model, so a typo must not become a watch.

    Without validation the row would be written, polled forever, and wake
    nobody -- and the caller would have been told it was watching.
    """
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)
    value = tmp_path / "knob"
    value.write_text("false\n")
    bogus = "deadbeefdeadbeefdeadbeefdeadbeef"

    with _omnigent() as url, _server(database, url) as parameters:
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "watch_subscribe",
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


async def test_a_closed_session_is_refused(tmp_path: Path) -> None:
    """A watch on a session that can no longer be woken is pure waste."""
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)
    value = tmp_path / "knob"
    value.write_text("false\n")
    closed = "0e891a642a8b4f499c6b2eb642cff66c"

    with _omnigent(closed=closed) as url, _server(database, url) as parameters:
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool(
                "watch_subscribe",
                {
                    "session_id": closed,
                    "subject": SUBJECT,
                    "command": ["cat", str(value)],
                },
            )
            assert result.isError is True
            assert "closed or archived" in _text(result)


async def test_a_stale_native_flag_does_not_stop_the_server(tmp_path: Path) -> None:
    """An installed config can still pass --native-codex, and must not break.

    The Codex config is assembled by a recursive dict merge that never deletes
    keys, so dropping `args` from the source leaves it in ~/.codex/config.toml
    -- here, and on every machine this repo cannot re-sync. Rejecting the flag
    made argparse exit before serving, so the tools just vanished from native
    Codex with nothing in the session to explain why. Found by checking the
    installed config after a real rollout, not by a test.
    """
    database = tmp_path / "watcher.sqlite3"
    WatcherRepository(database)

    with _omnigent() as url:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "omnigent_diff_watcher.mcp_server", "--native-codex"],
            env={
                **os.environ,
                "OMNIGENT_DIFF_WATCHER_DATABASE": str(database),
                "OMNIGENT_URL": url,
            },
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
    assert "watch_subscribe" in names
    assert "diff_watch_subscribe" in names
