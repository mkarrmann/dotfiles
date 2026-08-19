from __future__ import annotations

import json
import os
import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


@contextmanager
def _policy_relay(
    response_data: str,
) -> Iterator[tuple[ThreadingHTTPServer, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers["Content-Length"])
            request = json.loads(self.rfile.read(length))
            assert isinstance(request, dict)
            request["_path"] = self.path
            requests.append(request)
            payload = json.dumps({"result": "POLICY_ACTION_ALLOW", "data": response_data}).encode()
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
        yield server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


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


async def test_native_codex_stdio_server_returns_policy_bound_status(tmp_path: Path) -> None:
    codex_home = tmp_path / ".omnigent/codex-native/bridge/codex-home"
    codex_home.mkdir(parents=True)
    with _policy_relay("Diff watch: off; associated diff: D116563979.") as (
        server,
        requests,
    ):
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        (codex_home.parent / "tool_relay.json").write_text(
            json.dumps(
                {
                    "url": f"http://{host}:{port}",
                    "token": "test-token",
                    "session_id": "test-session",
                }
            )
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "omnigent_diff_watcher.mcp_server", "--native-codex"],
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool("diff_watch_status", {})

    content = result.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "Diff watch: off; associated diff: D116563979."
    event = requests[0]["event"]
    assert isinstance(event, dict)
    assert event["type"] == "PHASE_TOOL_RESULT"
    request_data = event["request_data"]
    assert isinstance(request_data, dict)
    assert request_data["name"] == "mcp__diff_watch__diff_watch_status"


def _closed_loopback_port() -> int:
    """Return a loopback port with nothing listening on it."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


async def test_native_codex_falls_back_to_the_server_when_the_relay_is_stale(
    tmp_path: Path,
) -> None:
    """A stale relay advertisement must not strand the tool.

    ``tool_relay.json`` is written per runner and survives that runner's
    death, so a session whose runner restarted advertises a dead port while
    ``policy_hook.json`` still points at the live server. Observed in
    production: the relay port refused connections and every diff-watch call
    failed closed even though the server was reachable.
    """
    codex_home = tmp_path / ".omnigent/codex-native/bridge/codex-home"
    codex_home.mkdir(parents=True)
    bridge = codex_home.parent
    (bridge / "tool_relay.json").write_text(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{_closed_loopback_port()}",
                "token": "stale-token",
                "session_id": "conv_stale",
            }
        )
    )
    (bridge / "state.json").write_text(json.dumps({"session_id": "conv_live"}))

    with _policy_relay("Diff watch: ci_failure,review_comment; associated diff: D1.") as (
        server,
        requests,
    ):
        host = str(server.server_address[0])
        port = int(server.server_address[1])
        (bridge / "policy_hook.json").write_text(
            json.dumps({"ap_server_url": f"http://{host}:{port}", "ap_auth_headers": {}})
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "omnigent_diff_watcher.mcp_server", "--native-codex"],
            env={**os.environ, "CODEX_HOME": str(codex_home)},
        )
        async with (
            stdio_client(parameters) as (reader, writer),
            ClientSession(reader, writer) as session,
        ):
            await session.initialize()
            result = await session.call_tool("diff_watch_status", {})

    assert result.isError is False
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert content.text == "Diff watch: ci_failure,review_comment; associated diff: D1."
    # Routed to the session-scoped server endpoint, not the dead relay.
    assert requests[0]["_path"] == "/v1/sessions/conv_stale/policies/evaluate"
