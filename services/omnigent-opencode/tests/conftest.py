from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestServer
from fake_omnigent import FakeOmnigent

from omnigent_opencode.omnigent import OmnigentClient
from omnigent_opencode.server import App, Config, make_app
from omnigent_opencode.store import Store

PASSWORD = "secret"
T3CODE = Path(os.environ.get("T3CODE_REPO", Path.home() / "repos/t3code"))
T3_NODE_MODULES = T3CODE / "packages/provider-opencode/node_modules"
HARNESS = Path(__file__).parent / "harness/t3-client.mjs"


@pytest.fixture
async def fake() -> AsyncIterator[FakeOmnigent]:
    omnigent = FakeOmnigent()
    server = TestServer(omnigent.app)
    await server.start_server()
    omnigent.url = str(server.make_url(""))  # type: ignore[attr-defined]
    yield omnigent
    for session in omnigent.sessions.values():
        for queue in session.streams:
            queue.put_nowait(None)
    await server.close()


class Shim:
    def __init__(self, app: App, server: TestServer) -> None:
        self.app = app
        self.server = server
        self.published: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return str(self.server.make_url("")).rstrip("/")


@pytest.fixture
async def shim(fake: FakeOmnigent, tmp_path: Path) -> AsyncIterator[Shim]:
    config = Config(
        password=PASSWORD, host_spec="auto", agent_specs=["claude-native-ui", "codex-native-ui"], default_directory=str(tmp_path)
    )
    app = App(config, OmnigentClient(fake.url), Store(None))  # type: ignore[attr-defined]
    server = TestServer(make_app(app))
    await server.start_server()
    wrapper = Shim(app, server)
    publish = app.feed.publish

    def recording(frame: dict[str, Any]) -> None:
        wrapper.published.append(frame)
        publish(frame)

    app.feed.publish = recording  # type: ignore[method-assign]
    yield wrapper
    await server.close()


class T3Client:
    """T3 Code's own OpenCode 2 client, in a node subprocess (tests/harness/t3-client.mjs)."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.events: list[dict[str, Any]] = []
        self.stream_open = asyncio.Event()
        self._results: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)
        self._changed = asyncio.Event()
        self._reader = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        assert self.process.stdout is not None
        async for raw in self.process.stdout:
            line = json.loads(raw)
            if line["kind"] == "event":
                self.events.append(line["event"])
            elif line["kind"] == "stream" and line["state"] == "open":
                self.stream_open.set()
            elif line["kind"] == "result":
                self._results.pop(line["id"]).set_result(line)
            self._changed.set()

    async def call(self, name: str, args: dict[str, Any] | None = None) -> Any:
        result = await self.try_call(name, args)
        if "error" in result:
            raise AssertionError(f"{name} failed: {result['error']}")
        return result["ok"]

    async def try_call(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = next(self._ids)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._results[request_id] = future
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps({"id": request_id, "call": name, "args": args or {}}) + "\n").encode())
        await self.process.stdin.drain()
        return await asyncio.wait_for(future, 10)

    def of(self, session_id: str) -> list[dict[str, Any]]:
        return [
            e for e in self.events if (e.get("data") or {}).get("sessionID") == session_id or e.get("sessionID") == session_id
        ]

    def types(self, session_id: str) -> list[str]:
        return [e["type"] for e in self.of(session_id)]

    async def wait_for(self, predicate: Callable[[], bool], timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while not predicate():
                self._changed.clear()
                await self._changed.wait()


@pytest.fixture
async def t3(shim: Shim, tmp_path: Path) -> AsyncIterator[T3Client]:
    if shutil.which("node") is None or not T3_NODE_MODULES.is_dir():
        pytest.skip("needs node and ~/repos/t3code with its dependencies installed")
    workdir = tmp_path / "harness"
    workdir.mkdir()
    (workdir / "node_modules").symlink_to(T3_NODE_MODULES)
    shutil.copy(HARNESS, workdir / "t3-client.mjs")
    process = await asyncio.create_subprocess_exec(
        "node",
        str(workdir / "t3-client.mjs"),
        shim.url,
        PASSWORD,
        cwd=workdir,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env={**os.environ, "T3CODE_REPO": str(T3CODE)},
    )
    client = T3Client(process)
    await asyncio.wait_for(client.stream_open.wait(), 15)
    yield client
    process.kill()
    await process.wait()
