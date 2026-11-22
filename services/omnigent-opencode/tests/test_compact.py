"""`/compact` on a session whose runner has not initialized it yet (a fresh fork's, or an idle session's)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from conftest import PASSWORD, Shim
from fake_omnigent import FakeOmnigent

CLAUDE = {"providerID": "claude-native-ui", "id": "opus"}


@pytest.fixture
async def http(shim: Shim) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=shim.url, auth=("opencode", PASSWORD), trust_env=False) as client:
        yield client


def types(shim: Shim, sid: str) -> list[str]:
    return [f["type"] for f in shim.published if (f.get("data") or {}).get("sessionID") == sid]


async def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def open_session(http: httpx.AsyncClient, shim: Shim) -> str:
    response = await http.post(
        "/api/session", json={"location": {"directory": shim.app.config.default_directory}, "model": CLAUDE}
    )
    return str(response.json()["data"]["id"])


async def compact(http: httpx.AsyncClient, fake: FakeOmnigent, sid: str) -> None:
    response = await http.post(f"/api/session/{sid}/compact", json={"id": f"msg_t3_turn_{sid}:c1"})
    assert response.status_code == 200, response.text
    conv = sid.removeprefix("ses_")
    fake.push(conv, "response.compaction.in_progress", {"started_at": 1})
    fake.push(conv, "response.compaction.completed", {"summary": "We said hi."})


async def test_compact_starts_a_session_its_runner_has_not_initialized(
    http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent
) -> None:
    sid = await open_session(http, shim)
    fake.sessions[sid.removeprefix("ses_")].started = False
    await compact(http, fake, sid)
    await wait_for(lambda: "session.execution.succeeded" in types(shim, sid) or "session.execution.failed" in types(shim, sid))
    assert types(shim, sid)[-3:] == ["session.compaction.started", "session.compaction.ended", "session.execution.succeeded"]
    assert [p["type"] for p in fake.only().posted] == ["retry_session", "compact"]


async def test_compact_of_a_started_session_goes_straight_to_omnigent(
    http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent
) -> None:
    sid = await open_session(http, shim)
    await compact(http, fake, sid)
    await wait_for(lambda: "session.execution.succeeded" in types(shim, sid))
    assert [p["type"] for p in fake.only().posted] == ["compact"]
