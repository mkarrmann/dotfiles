"""T3 Code's client through the server T3 Code runs, forwarding to the daemon over its socket."""

from __future__ import annotations

import base64
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import PASSWORD, T3Client, t3_client
from fake_omnigent import FakeOmnigent
from test_t3_turns import CLAUDE, confirmed_idle, consumed, reply

from omnigent_opencode.omnigent import OmnigentClient
from omnigent_opencode.proxy import daemon_up, make_proxy
from omnigent_opencode.server import App, Config, make_app
from omnigent_opencode.store import Store

HOME = "/home/someone/.t3-workspaces/checkouts"


@pytest.fixture
async def daemon(fake: FakeOmnigent, tmp_path: Path) -> AsyncIterator[tuple[App, Path]]:
    config = Config(
        password=None, host_spec="auto", agent_specs=["claude-native-ui"], default_directory=str(tmp_path), watch_ms=3_600_000
    )
    app = App(config, OmnigentClient(fake.url), Store(None))  # type: ignore[attr-defined]
    runner = web.AppRunner(make_app(app))
    await runner.setup()
    # A socket path must fit in 104 bytes on macOS.
    socket = Path(tempfile.mkdtemp(dir="/tmp")) / "d.sock"
    await web.UnixSite(runner, str(socket)).start()
    yield app, socket
    await runner.cleanup()


@pytest.fixture
async def forwarded(daemon: tuple[App, Path], tmp_path: Path) -> AsyncIterator[T3Client]:
    server = TestServer(make_proxy(PASSWORD, daemon[1], HOME))
    await server.start_server()
    async with t3_client(str(server.make_url("")).rstrip("/"), tmp_path) as client:
        yield client
    await server.close()


async def test_the_daemon_answers_its_probe(daemon: tuple[App, Path]) -> None:
    assert await daemon_up(daemon[1])
    assert not await daemon_up(daemon[1].with_name("missing.sock"))


async def test_a_turn_runs_through_the_forwarding_server(
    forwarded: T3Client, daemon: tuple[App, Path], fake: FakeOmnigent
) -> None:
    app, _ = daemon
    t3 = forwarded
    session = await t3.call("session.create", {"location": {"directory": app.config.default_directory}, "model": CLAUDE})
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    reply(fake, conv, "Hello")
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert [e["data"]["text"] for e in t3.of(sid) if e["type"] == "session.text.ended"] == ["Hello"]
    assert app.home_of(sid) == Path(HOME)


async def test_the_forwarding_server_checks_t3_codes_password(daemon: tuple[App, Path]) -> None:
    server = TestServer(make_proxy(PASSWORD, daemon[1], HOME))
    await server.start_server()
    authorization = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
    async with aiohttp.ClientSession() as client:
        async with client.get(server.make_url("/api/info")) as response:
            assert response.status == 401
        async with client.get(server.make_url("/api/info"), headers={"Authorization": authorization}) as response:
            assert response.status == 200
    await server.close()


async def test_a_daemon_is_stale_when_its_recorded_code_is_not_on_disk(daemon: tuple[App, Path]) -> None:
    import argparse

    from omnigent_opencode.cli import CODE_FILE, code_hash, daemon_stale

    socket = daemon[1]
    args = argparse.Namespace(socket=socket)
    (socket.parent / CODE_FILE).write_text("0123456789abcdef\n")
    assert await daemon_stale(args) == 0
    (socket.parent / CODE_FILE).write_text(code_hash() + "\n")
    assert await daemon_stale(args) == 1
    assert await daemon_stale(argparse.Namespace(socket=socket.with_name("missing.sock"))) == 1, "no daemon, nothing to restart"
