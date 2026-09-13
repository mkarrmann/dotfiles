"""Generic delivery over stdio, HTTP, durable storage, and the real scheduler."""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from omnigent_watcher import http_api
from omnigent_watcher.domain import SubscriptionState, WatcherConfig
from omnigent_watcher.service import WatcherService
from omnigent_watcher.settings import ServiceSettings

SESSION_ID = "conv_generic_notification_test"
SUBJECT = "job:local-test"


@dataclass
class SessionEndpoint:
    status: str = "running"
    events: list[dict[str, object]] = field(default_factory=list)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(5):
        while not condition():  # noqa: ASYNC110 - external subprocess/SQLite state has no event
            await asyncio.sleep(0.02)


@asynccontextmanager
async def _server(endpoint: SessionEndpoint) -> AsyncIterator[str]:
    app = FastAPI()
    for router, prefix, tags in http_api.DEBUG_ROUTERS:
        app.include_router(router, prefix=prefix, tags=list(tags))

    @app.get("/v1/sessions/{session_id}")
    async def read_session(session_id: str) -> dict[str, object]:
        assert session_id == SESSION_ID
        return {"id": session_id, "status": endpoint.status, "archived": False}

    @app.get("/v1/sessions/{session_id}/items")
    async def read_items(session_id: str) -> dict[str, object]:
        assert session_id == SESSION_ID
        return {"data": []}

    @app.post("/v1/sessions/{session_id}/events", status_code=202)
    async def post_event(session_id: str, event: dict[str, object]) -> dict[str, bool]:
        assert session_id == SESSION_ID
        assert endpoint.status == "idle"
        endpoint.events.append(event)
        return {"accepted": True}

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await _until(lambda: server.started or task.done())
            assert server.started, "test HTTP server did not start"
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)


def _text(result: CallToolResult) -> str:
    assert result.isError is False, result.content
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


async def test_generic_watch_delivers_once_when_idle_despite_ambient_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)

    value = tmp_path / "job-status"
    value.write_text("running\n")
    polls = tmp_path / "polls"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pathlib, sys\n"
        "with pathlib.Path(sys.argv[2]).open('a') as log:\n"
        "    log.write('poll\\n')\n"
        "print(pathlib.Path(sys.argv[1]).read_text(), end='')\n"
    )

    def poll_count() -> int:
        return len(polls.read_text().splitlines()) if polls.exists() else 0

    endpoint = SessionEndpoint()
    async with _server(endpoint) as server_url:
        settings = ServiceSettings(
            server_url=server_url,
            database_path=tmp_path / "watcher.sqlite3",
            delivery_mode="enabled",
            delivery_session_allowlist=frozenset(),
            reconcile_interval_seconds=0.05,
            scheduler_error_retry_seconds=0.05,
            watcher=WatcherConfig(
                poll_interval_override_seconds=0.05,
                batch_window_seconds=0.05,
                minimum_delivery_interval_seconds=0.05,
                delivery_retry_seconds=0.05,
            ),
        )
        monkeypatch.setattr(http_api, "_settings", lambda: settings)
        service = WatcherService(settings)
        scheduler = asyncio.create_task(service.run())
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "omnigent_watcher.mcp_server"],
            env={**os.environ, "OMNIGENT_URL": server_url},
        )
        try:
            async with (
                asyncio.timeout(15),
                stdio_client(parameters) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                result = await session.call_tool(
                    "subscribe",
                    {
                        "session_id": SESSION_ID,
                        "subject": SUBJECT,
                        "command": [sys.executable, str(probe), str(value), str(polls)],
                    },
                )
                assert SUBJECT in _text(result)
                assert SUBJECT in _text(
                    await session.call_tool("status", {"session_id": SESSION_ID})
                )

                await _until(lambda: poll_count() >= 4)
                assert endpoint.events == []
                assert service.repository.open_batch_for_session(SESSION_ID) is None

                value.write_text("complete\n")

                def delivery_deferred() -> bool:
                    batch = service.repository.open_batch_for_session(SESSION_ID)
                    return batch is not None and batch.retry_count >= 1

                await _until(delivery_deferred)
                assert endpoint.events == [], "a running session must not be interrupted"

                endpoint.status = "idle"
                await _until(lambda: len(endpoint.events) == 1)
                event = endpoint.events[0]
                assert event["type"] == "message"
                data = event["data"]
                assert isinstance(data, dict)
                assert data["role"] == "user"
                assert data["content"][0]["type"] == "input_text"
                text = data["content"][0]["text"]
                assert text.startswith("[Watcher ")
                assert SUBJECT in text
                assert "act on what changed" in text

                delivered_poll_count = poll_count()
                await _until(lambda: poll_count() >= delivered_poll_count + 3)
                assert len(endpoint.events) == 1

                stopped = await session.call_tool(
                    "unsubscribe", {"session_id": SESSION_ID, "subject": SUBJECT}
                )
                assert "stopped 1 watch(es)" in _text(stopped)
                subscription = service.repository.subscription(SESSION_ID, SUBJECT)
                assert subscription is not None
                assert subscription.state is SubscriptionState.RETIRED
                assert service.repository.active_watch_requests(SESSION_ID) == []
                value.write_text("another-change\n")
                next_reconcile = service._next_reconcile
                await _until(lambda: service._next_reconcile >= next_reconcile + 0.2)
                assert len(endpoint.events) == 1
                assert not scheduler.done()
        finally:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(scheduler, timeout=5)
