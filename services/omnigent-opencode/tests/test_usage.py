"""The context size each turn reports to T3 Code, which shows its last step's tokens as the context window's fill."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from conftest import PASSWORD, Shim
from fake_omnigent import FakeOmnigent

CLAUDE = {"providerID": "claude-native-ui", "id": "opus"}


@pytest.fixture
async def http(shim: Shim) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=shim.url, auth=("opencode", PASSWORD), trust_env=False) as client:
        yield client


def frames(shim: Shim, sid: str) -> list[dict[str, Any]]:
    return [f for f in shim.published if (f.get("data") or {}).get("sessionID") == sid]


def types(shim: Shim, sid: str) -> list[str]:
    return [f["type"] for f in frames(shim, sid)]


async def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


def step_tokens(shim: Shim, sid: str, kind: str = "session.step.ended") -> list[Any]:
    return [(f["data"]).get("tokens", {}).get("input") for f in frames(shim, sid) if f["type"] == kind]


async def open_session(http: httpx.AsyncClient, shim: Shim) -> str:
    response = await http.post(
        "/api/session", json={"location": {"directory": shim.app.config.default_directory}, "model": CLAUDE}
    )
    return str(response.json()["data"]["id"])


async def run_turn(
    http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent, sid: str, n: int, usage: list[dict[str, Any]], *, fail: bool = False
) -> None:
    """One T3 Code prompt, answered, with these `session.usage` reports while it runs."""
    conv = sid.removeprefix("ses_")
    done = "session.execution.failed" if fail else "session.execution.succeeded"
    before = types(shim, sid).count(done)
    await http.post(f"/api/session/{sid}/prompt", json={"id": f"msg_t3_turn_{sid}:a{n}", "text": f"turn {n}"})
    await fake.connected(conv)
    fake.push(conv, "response.completed", {"response": {"id": f"resp_ack{n}", "model": "claude-native-ui"}})
    pending = fake.sessions[conv].pending_ids[-1]
    fake.push(
        conv,
        "session.input.consumed",
        {"data": {"cleared_pending_id": pending, "type": "message", "item_id": pending.replace("pending", "item")}},
    )
    fake.push(conv, "response.output_text.delta", {"delta": "Hello"})
    for report in usage:
        fake.push(conv, "session.usage", {"conversation_id": conv, **report})
    if fail:
        fake.push(conv, "response.failed", {"response": {"error": {"message": "boom"}}})
    else:
        fake.push(
            conv,
            "response.output_item.done",
            {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hello"}]}},
        )
        fake.push(conv, "session.status", {"status": "idle", "response_id": f"resp_{n}"})
    await wait_for(lambda: types(shim, sid).count(done) > before or "session.execution.failed" in types(shim, sid))


async def test_a_turn_reports_the_context_its_usage_report_gave(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [{"context_tokens": 1200, "context_window": 1_000_000}])
    assert step_tokens(shim, sid) == [1200]


async def test_a_report_without_the_window_still_counts(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [{"context_tokens": 3400}])
    assert step_tokens(shim, sid) == [3400]


async def test_a_cost_only_report_keeps_the_last_context(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [{"context_tokens": 5000}, {"context_tokens": None, "total_cost_usd": 1.5}])
    assert step_tokens(shim, sid) == [5000]


async def test_a_turn_without_a_report_reports_the_last_known_context(
    http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent
) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [{"context_tokens": 7000}])
    # A report that lands after its turn ended, before the next starts.
    fake.push(sid.removeprefix("ses_"), "session.usage", {"context_tokens": 7500})
    await wait_for(lambda: shim.app.sessions[sid].context_tokens == 7500)
    await run_turn(http, shim, fake, sid, 2, [])
    assert step_tokens(shim, sid) == [7000, 7500]


async def test_omnigents_label_seeds_the_context(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    fake.new_session_labels = {"omnigent.last_context_tokens": "42000"}
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [])
    assert step_tokens(shim, sid) == [42000]


async def test_a_session_that_never_reported_usage_ends_no_step(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [])
    assert "session.step.started" in types(shim, sid)
    assert step_tokens(shim, sid) == []


async def test_a_failed_turn_without_usage_reports_no_tokens(http: httpx.AsyncClient, shim: Shim, fake: FakeOmnigent) -> None:
    sid = await open_session(http, shim)
    await run_turn(http, shim, fake, sid, 1, [], fail=True)
    (failed,) = [f for f in frames(shim, sid) if f["type"] == "session.step.failed"]
    assert "tokens" not in failed["data"]
