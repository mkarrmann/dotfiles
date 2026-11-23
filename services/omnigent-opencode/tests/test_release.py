"""The daemon releasing sessions whose T3 Code thread was settled, archived or deleted."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_omnigent import FakeOmnigent
from test_t3_turns import CLAUDE

from omnigent_opencode.omnigent import OmnigentClient
from omnigent_opencode.server import RELEASE_GRACE_MS, App, Config
from omnigent_opencode.store import Store
from omnigent_opencode.t3state import STATE_DB, thread_for
from omnigent_opencode.wakes import Waker

T3_SCHEMA = """
create table orchestration_v2_projection_threads (
    thread_id text primary key, active_provider_thread_id text, archived_at text, deleted_at text, payload_json text not null
);
create table orchestration_v2_projection_provider_threads (
    provider_thread_id text primary key, thread_id text not null, updated_at text not null, payload_json text not null
);
"""


class T3State:
    """The tables of a T3 Code server's state that `t3state` reads."""

    def __init__(self, home: Path) -> None:
        self.home = home
        db = home / STATE_DB
        db.parent.mkdir(parents=True)
        self._db = sqlite3.connect(db, isolation_level=None)
        self._db.executescript(T3_SCHEMA)

    def thread(self, thread_id: str, session_id: str, updated: str = "2026-11-22") -> None:
        self._db.execute(
            "insert into orchestration_v2_projection_threads values (?, ?, null, null, ?)",
            (thread_id, f"pt_{thread_id}", json.dumps({"settledOverride": None})),
        )
        self._db.execute(
            "insert into orchestration_v2_projection_provider_threads values (?, ?, ?, ?)",
            (f"pt_{thread_id}", thread_id, updated, json.dumps({"nativeThreadRef": {"nativeId": session_id}})),
        )

    def settle(self, thread_id: str, override: str | None = "settled") -> None:
        self._db.execute(
            "update orchestration_v2_projection_threads set payload_json = ? where thread_id = ?",
            (json.dumps({"settledOverride": override}), thread_id),
        )

    def archive(self, thread_id: str) -> None:
        self._db.execute(
            "update orchestration_v2_projection_threads set archived_at = '2026-11-22' where thread_id = ?", (thread_id,)
        )

    def close(self) -> None:
        self._db.close()


@pytest.fixture
def t3(tmp_path: Path) -> Iterator[T3State]:
    state = T3State(tmp_path / "t3home")
    yield state
    state.close()


def make_app(fake: FakeOmnigent, directory: Path, store: Store) -> App:
    config = Config(
        password=None, host_spec="auto", agent_specs=["claude-native-ui"], default_directory=str(directory), watch_ms=3_600_000
    )
    app = App(config, OmnigentClient(fake.url), store)  # type: ignore[attr-defined]
    app.release_grace_ms = 0
    return app


async def close_all(app: App) -> None:
    for session in list(app.sessions.values()):
        await session.close()


@pytest.fixture
async def app(fake: FakeOmnigent, tmp_path: Path) -> AsyncIterator[App]:
    app = make_app(fake, tmp_path, Store(None))
    yield app
    await close_all(app)


async def opened(app: App, t3: T3State, thread_id: str = "thread_1") -> tuple[str, str]:
    """A session T3 Code created for a thread."""
    sid = str((await app.create_session({"location": {"directory": app.config.default_directory}, "model": CLAUDE}))["id"])
    app.note_home(sid, str(t3.home))
    t3.thread(thread_id, sid)
    return sid, app.sessions[sid].conversation_id


async def sweep(app: App) -> None:
    for sid in app.store.watched(0):
        await app._release_if_done(sid)


def stops(fake: FakeOmnigent, conv: str) -> int:
    return sum(1 for event in fake.sessions[conv].posted if event.get("type") == "stop_session")


async def task_state(fake: FakeOmnigent, state: str) -> None:
    async with asyncio.timeout(5):
        while fake.scheduled_tasks[0]["state"] != state:
            await asyncio.sleep(0.01)


async def test_settling_a_thread_ends_its_wakeups_and_stops_its_runner(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    fake.sessions[conv].children = [{"id": "conv_child", "busy": False}, {"id": "conv_grandchild", "busy": False, "depth": 2}]
    fake.scheduled_tasks = [
        {"id": "task_mine", "session_id": conv, "state": "active"},
        {"id": "task_grandchilds", "session_id": "conv_grandchild", "state": "active"},
        {"id": "task_paused_already", "session_id": conv, "state": "paused"},
        {"id": "task_other", "session_id": "conv_other", "state": "active"},
        {"id": "task_new_sessions", "session_id": None, "state": "active"},
    ]
    await sweep(app)
    assert sid in app.sessions and stops(fake, conv) == 0, "a live thread's session is left alone"

    t3.settle("thread_1")
    await sweep(app)
    assert sid not in app.sessions
    assert app.store.watched(0) == [], "a released session is not followed again"
    assert sorted(c["session_id"] for c in fake.watch_cancels) == sorted([conv, "conv_child", "conv_grandchild"])
    assert all(c["sources"] == ["phabricator", "command"] for c in fake.watch_cancels)
    assert {t["id"]: t["state"] for t in fake.scheduled_tasks} == {
        "task_mine": "paused",
        "task_grandchilds": "paused",
        "task_paused_already": "paused",
        "task_other": "active",
        "task_new_sessions": "active",
    }
    assert stops(fake, conv) == 1
    assert not any("archived" in patch for patch in fake.sessions[conv].patches), "nothing is archived"

    await sweep(app)
    assert stops(fake, conv) == 1, "released once"


async def test_an_archived_thread_is_released_too(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.archive("thread_1")
    await sweep(app)
    assert sid not in app.sessions and stops(fake, conv) == 1


async def test_a_session_still_at_work_waits(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.settle("thread_1")
    omnigent = fake.sessions[conv]
    omnigent.status = "running"
    await sweep(app)
    omnigent.status = "waiting"
    await sweep(app)
    omnigent.status = "idle"
    omnigent.pending_inputs = [{"id": "pending_1"}]
    await sweep(app)
    omnigent.pending_inputs = []
    omnigent.pending_elicitations = [{"id": "elicit_1"}]
    await sweep(app)
    omnigent.pending_elicitations = []
    # A grandchild still at work, below more children than Omnigent lists by default.
    children: list[dict[str, Any]] = [{"id": f"conv_child_{n}", "busy": False} for n in range(30)]
    omnigent.children = children
    omnigent.children.append({"id": "conv_grandchild", "busy": True, "depth": 2})
    await sweep(app)
    assert sid in app.sessions and stops(fake, conv) == 0 and not fake.watch_cancels

    omnigent.children[-1]["busy"] = False
    await sweep(app)
    assert sid not in app.sessions and stops(fake, conv) == 1


async def test_a_session_t3_code_just_used_waits(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.settle("thread_1")
    app.release_grace_ms = RELEASE_GRACE_MS
    await sweep(app)
    assert sid in app.sessions and stops(fake, conv) == 0


async def test_a_cold_runner_is_not_stopped_again(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.settle("thread_1")
    fake.sessions[conv].runner_online = False
    await sweep(app)
    assert sid not in app.sessions and stops(fake, conv) == 0
    assert [c["session_id"] for c in fake.watch_cancels] == [conv]


async def test_a_session_is_released_after_a_restart(app: App, fake: FakeOmnigent, t3: T3State, tmp_path: Path) -> None:
    _, conv = await opened(app, t3)
    t3.settle("thread_1")
    restarted = make_app(fake, tmp_path, app.store)
    await restarted.release_pass()
    assert stops(fake, conv) == 1 and app.store.watched(0) == []


async def test_a_released_session_t3_code_opens_is_followed_again_with_its_tasks(
    app: App, fake: FakeOmnigent, t3: T3State
) -> None:
    sid, conv = await opened(app, t3)
    fake.scheduled_tasks = [{"id": "task_mine", "session_id": conv, "state": "active"}]
    t3.settle("thread_1")
    await sweep(app)
    assert app.store.watched(0) == [] and fake.scheduled_tasks[0]["state"] == "paused"

    # Sending a message un-settles the thread in T3 Code, which then opens the session.
    t3.settle("thread_1", None)
    await app.session(sid)
    assert app.store.watched(0) == [sid]
    await task_state(fake, "active")
    await sweep(app)
    assert sid in app.sessions and stops(fake, conv) == 1


@pytest.mark.parametrize("how", ["loaded", "used"])
async def test_t3_code_taking_a_session_up_mid_release_keeps_it(
    app: App, fake: FakeOmnigent, t3: T3State, tmp_path: Path, how: str
) -> None:
    sid, conv = await opened(app, t3)
    fake.scheduled_tasks = [{"id": "task_mine", "session_id": conv, "state": "active"}]
    t3.settle("thread_1")
    # "loaded": after a restart, the session is not loaded until T3 Code opens it mid-release.
    releasing = make_app(fake, tmp_path, app.store) if how == "loaded" else app
    if how == "used":
        releasing.release_grace_ms = 1_000
        releasing.sessions[sid].active -= 2_000
        releasing._opened_at[sid] -= 2_000
    gate = fake.watch_gate = asyncio.Event()
    release = asyncio.create_task(releasing._release_if_done(sid))
    async with asyncio.timeout(5):
        await fake.watch_blocked.wait()

    t3.settle("thread_1", None)
    if how == "loaded":
        await releasing.session(sid)
    else:
        releasing.sessions[sid].touch()
    gate.set()
    await release

    assert stops(fake, conv) == 0
    assert sid in releasing.sessions and releasing.store.watched(0) == [sid]
    await task_state(fake, "active")
    if releasing is not app:
        await close_all(releasing)


async def test_a_failed_release_is_tried_again(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.settle("thread_1")
    fake.watch_cancel_status = 500
    await app.release_pass()
    assert sid in app.sessions and app.store.watched(0) == [sid] and stops(fake, conv) == 0

    fake.watch_cancel_status = 200
    await app.release_pass()
    assert sid not in app.sessions and stops(fake, conv) == 1


async def test_a_gone_omnigent_session_is_released_quietly(app: App, fake: FakeOmnigent, t3: T3State) -> None:
    sid, conv = await opened(app, t3)
    t3.settle("thread_1")
    del fake.sessions[conv]
    await sweep(app)
    assert sid not in app.sessions and app.store.watched(0) == [] and not fake.watch_cancels


def test_thread_status_follows_t3_codes_state(t3: T3State) -> None:
    t3.thread("thread_1", "ses_1")
    assert (found := thread_for(t3.home, "ses_1")) is not None and found.status == "live" and not found.done
    t3.settle("thread_1", "active")
    assert (found := thread_for(t3.home, "ses_1")) is not None and found.status == "live"
    t3.settle("thread_1")
    assert (found := thread_for(t3.home, "ses_1")) is not None and found.status == "settled" and found.done
    t3.archive("thread_1")
    assert (found := thread_for(t3.home, "ses_1")) is not None and found.status == "archived"
    assert thread_for(t3.home, "ses_unknown") is None
    assert thread_for(t3.home.with_name("nowhere"), "ses_1") is None


def test_a_live_thread_wins_over_a_newer_archived_one(t3: T3State) -> None:
    t3.thread("thread_live", "ses_1", updated="2026-11-01")
    t3.thread("thread_archived", "ses_1", updated="2026-11-20")
    t3.archive("thread_archived")
    assert (found := thread_for(t3.home, "ses_1")) is not None and found.id == "thread_live"


async def test_a_settled_thread_takes_no_wakeup(app: App, t3: T3State, tmp_path: Path) -> None:
    sid, _ = await opened(app, t3)
    sent = tmp_path / "sent"
    fake_t3 = tmp_path / "t3"
    fake_t3.write_text(f"#!/bin/sh\ncat > {sent}\necho '{{}}'\n")
    fake_t3.chmod(0o755)
    waker = Waker(str(fake_t3), app.home_of)

    t3.settle("thread_1")
    assert await waker(app.sessions[sid], "[Omnigent] report") == "never"
    assert not sent.exists(), "a wakeup would un-settle the thread"

    t3.settle("thread_1", None)
    assert await waker(app.sessions[sid], "[Omnigent] report") == "sent"
    assert sent.read_text() == "[Omnigent] report"


def test_releases_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    store = Store(path)
    store.watch("ses_1", "conv_1", 10)
    store.release("ses_1", 20)
    store.add_paused_task("ses_1", "task_1")
    restarted = Store(path)
    assert restarted.watched(0) == [] and restarted.released("ses_1") and restarted.paused_tasks("ses_1") == ["task_1"]
