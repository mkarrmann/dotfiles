"""Turns that ran while this server was not listening to Omnigent, and following sessions between T3 Code's visits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from conftest import Shim, T3Client
from fake_omnigent import FakeOmnigent
from test_t3_turns import assert_all_decoded, consumed, open_session, reply


async def eventually(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def user_item(item_id: str, text: str) -> dict[str, Any]:
    return {"id": item_id, "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def assistant_item(item_id: str, text: str) -> dict[str, Any]:
    return {"id": item_id, "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


async def followed(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> tuple[str, str]:
    """A session whose stream is open and whose earlier history is behind its watermark."""
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    fake.only().items.append(user_item("item_old", "before"))
    await shim.app.sessions[sid].connect()
    await eventually(lambda: shim.app.store.watermark(sid) == "item_old")
    return sid, conv


def texts(t3: T3Client, sid: str) -> list[str]:
    return [e["data"]["text"] for e in t3.of(sid) if e["type"] == "session.text.ended"]


class FakeWake:
    """T3 Code taking a trigger as `t3 thread send` would; the test plays the turn it starts."""

    def __init__(self, outcomes: list[str] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.triggers: list[str] = []

    async def __call__(self, session: Any, trigger: str) -> Any:
        self.triggers.append(trigger)
        return self.outcomes.pop(0) if self.outcomes else "sent"


async def waking(t3: T3Client, shim: Shim, fake: FakeOmnigent, wake: FakeWake) -> tuple[str, str]:
    shim.app.wake = wake
    return await followed(t3, shim, fake)


def outside(fake: FakeOmnigent, conv: str, item_id: str, text: str) -> None:
    fake.push(conv, "session.input.consumed", consumed(None, item_id, text))  # type: ignore[arg-type]


async def trigger_prompt(t3: T3Client, sid: str, text: str, attempt: str = "w1") -> str:
    message_id = f"msg_t3_turn_{sid}:{attempt}"
    await t3.call("session.prompt", {"sessionID": sid, "id": message_id, "text": text})
    return message_id


async def test_a_turn_run_while_the_stream_was_down_is_replayed(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await waking(t3, shim, fake, FakeWake(["never"]))
    fake.drop(conv)
    fake.only().items += [user_item("item_w", "[watcher] CI finished"), assistant_item("item_r", "CI is green")]
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert texts(t3, sid) == ["CI is green"]
    assert shim.app.store.watermark(sid) == "item_w"
    assert_all_decoded(shim, t3, sid)

    # A second reconnect replays nothing.
    fake.drop(conv)
    await asyncio.sleep(0.1)
    await fake.connected(conv)
    await asyncio.sleep(0.3)
    assert t3.types(sid).count("session.execution.started") == 1


async def test_a_turn_still_running_after_the_gap_continues_live(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await waking(t3, shim, fake, FakeWake(["never"]))
    fake.drop(conv)
    fake.only().status = "running"
    fake.only().items += [user_item("item_w", "[watcher] CI finished"), assistant_item("item_r", "Looking.")]
    await t3.wait_for(lambda: texts(t3, sid) == ["Looking."])
    assert "session.execution.succeeded" not in t3.types(sid)
    await fake.connected(conv)
    reply(fake, conv, "All green.")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_2"})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert texts(t3, sid) == ["Looking.", "All green."]


async def test_inputs_of_t3_code_prompts_are_not_replayed(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    wake = FakeWake(["never"])
    sid, conv = await waking(t3, shim, fake, wake)
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    ((_, _, _, item_id),) = shim.app.store.prompts(sid)
    assert item_id is not None
    fake.push(conv, "session.input.consumed", consumed(item_id.replace("item_", "pending_"), item_id, "hi"))
    reply(fake, conv, "Hello")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    # The prompt's consumption was missed in a gap; its item is the one Omnigent gave when it took the message.
    fake.drop(conv)
    fake.only().items += [user_item(item_id, "hi"), assistant_item("item_r", "Hello")]
    await eventually(lambda: shim.app.store.watermark(sid) == item_id)
    await asyncio.sleep(0.2)
    assert t3.types(sid).count("session.execution.started") == 1
    assert wake.triggers == []


async def test_an_outside_turn_waits_for_t3_codes_turn_and_answers_its_prompt(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] CI\n finished")
    reply(fake, conv, "CI is green")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await eventually(lambda: wake.triggers == ["[Omnigent] [watcher] CI finished"])
    await eventually(lambda: shim.app.sessions[sid].execution is None)
    assert t3.of(sid) == [], "T3 Code hears of the turn only through its own"

    message_id = await trigger_prompt(t3, sid, wake.triggers[0])
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert texts(t3, sid) == ["CI is green"]
    assert not [p for p in fake.only().posted if p["type"] == "message"], "the trigger is not sent to Omnigent"
    assistant = shim.app.sessions[sid].assistant_id(message_id)
    assert {e["data"]["assistantMessageID"] for e in t3.of(sid) if "assistantMessageID" in e["data"]} == {assistant}
    assert_all_decoded(shim, t3, sid)

    fake.only().items += [user_item("item_w", "[watcher] CI\n finished"), assistant_item("item_r", "CI is green")]
    history = await t3.call("message.list", {"sessionID": sid})
    shown = [(m["type"], m["id"]) for m in history["data"] if m["type"] in ("user", "assistant")]
    assert shown[-2:] == [("user", message_id), ("assistant", assistant)]


async def test_a_held_turn_still_running_continues_live_in_t3_codes_turn(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "Message from Google Chat")
    reply(fake, conv, "On it.")
    await eventually(lambda: len(wake.triggers) == 1)
    # T3 Code puts context before the message when it hands it to a fresh session.
    await trigger_prompt(t3, sid, "Earlier context\n\nUser message:\n" + wake.triggers[0])
    await t3.wait_for(lambda: texts(t3, sid) == ["On it."])
    reply(fake, conv, "Done.")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert texts(t3, sid) == ["On it.", "Done."]
    assert t3.types(sid).count("session.execution.started") == 1


async def test_a_held_turns_approval_is_listed_only_once_t3_codes_turn_begins(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] deploy")
    fake.push(
        conv,
        "response.elicitation_request",
        {"elicitation_id": "el_1", "params": {"message": "Omnigent policy asks", "content_preview": "rm -rf build"}},
    )
    await eventually(lambda: bool(shim.app.sessions[sid].approvals))
    # T3 Code rejects what it finds here when it resumes the thread.
    assert await t3.call("permission.list", {"sessionID": sid}) == []
    await trigger_prompt(t3, sid, wake.triggers[0])
    await t3.wait_for(lambda: any(e["type"] == "permission.asked" for e in t3.events))
    assert len(await t3.call("permission.list", {"sessionID": sid})) == 1


async def test_an_outside_turn_t3_code_cannot_be_asked_about_is_shown_at_once(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    sid, conv = await waking(t3, shim, fake, FakeWake(["never"]))
    outside(fake, conv, "item_w", "[watcher] CI finished")
    reply(fake, conv, "CI is green")
    await t3.wait_for(lambda: texts(t3, sid) == ["CI is green"])
    assert t3.types(sid)[0] == "session.execution.started"


async def test_a_trigger_t3_code_could_not_take_is_sent_again(
    t3: T3Client, shim: Shim, fake: FakeOmnigent, monkeypatch: Any
) -> None:
    monkeypatch.setattr("omnigent_opencode.sessions.WAKE_RETRY_MIN_S", 0.05)
    wake = FakeWake(["later", "later"])
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] CI finished")
    await eventually(lambda: len(wake.triggers) == 3)
    assert len(set(wake.triggers)) == 1


async def test_a_prompt_before_t3_codes_turn_shows_the_held_turn_and_steers_into_it(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] CI finished")
    reply(fake, conv, "Looking.")
    await eventually(lambda: len(wake.triggers) == 1)
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:u1", "text": "also check lint"})
    await t3.wait_for(lambda: texts(t3, sid) == ["Looking."])
    assert [p["data"]["content"][-1]["text"] for p in fake.only().posted if p["type"] == "message"] == ["also check lint"]
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))

    # The queued trigger's turn comes after; it has nothing left to show and is not sent to Omnigent.
    await trigger_prompt(t3, sid, wake.triggers[0], "w2")
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.succeeded") == 2)
    assert len([p for p in fake.only().posted if p["type"] == "message"]) == 1


async def test_a_stray_reply_tail_asks_t3_code_for_nothing(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[System: scheduled prompt fired]")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await eventually(lambda: shim.app.sessions[sid].execution is None)
    assert wake.triggers == [] and not shim.app.sessions[sid].held


async def test_a_trigger_with_no_held_run_is_not_sent_to_omnigent(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    # As after a restart of the daemon while T3 Code held the trigger in its queue.
    sid, conv = await waking(t3, shim, fake, FakeWake())
    await trigger_prompt(t3, sid, "[Omnigent] [watcher] CI finished")
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert not [p for p in fake.only().posted if p["type"] == "message"]


async def test_a_trigger_t3_code_reworded_goes_to_the_oldest_held_run(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] CI finished")
    reply(fake, conv, "Looking.")
    await eventually(lambda: len(wake.triggers) == 1)
    await trigger_prompt(t3, sid, "[Omnigent] something else")
    await t3.wait_for(lambda: texts(t3, sid) == ["Looking."])
    assert not [p for p in fake.only().posted if p["type"] == "message"]


async def test_a_trigger_is_never_sent_to_omnigent_while_another_run_is_held(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    wake = FakeWake(["never"])
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] CI finished")
    reply(fake, conv, "Looking.")
    await t3.wait_for(lambda: texts(t3, sid) == ["Looking."])
    # A trigger T3 Code still had queued from before, while the shown run goes on.
    await t3.try_call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:old", "text": "[Omnigent] stale"})
    assert not [p for p in fake.only().posted if p["type"] == "message"]


async def test_a_failing_wake_is_tried_again_and_runs_are_asked_for_in_order(
    t3: T3Client, shim: Shim, fake: FakeOmnigent, monkeypatch: Any
) -> None:
    monkeypatch.setattr("omnigent_opencode.sessions.WAKE_RETRY_MIN_S", 0.05)

    class Flaky(FakeWake):
        async def __call__(self, session: Any, trigger: str) -> Any:
            self.triggers.append(trigger)
            if len(self.triggers) == 1:
                raise RuntimeError("boom")
            return "sent"

    wake = Flaky()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_1", "first")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await eventually(lambda: shim.app.sessions[sid].execution is None)
    outside(fake, conv, "item_2", "second")
    await eventually(lambda: len(wake.triggers) == 3)
    assert wake.triggers == ["[Omnigent] first", "[Omnigent] first", "[Omnigent] second"]


async def test_a_held_runs_subagent_is_shown_with_it(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    from test_t3_subagents import LAUNCHED, item, spawn

    wake = FakeWake()
    sid, conv = await waking(t3, shim, fake, wake)
    outside(fake, conv, "item_w", "[watcher] run the checks")
    child = spawn(fake, conv)
    item(fake, conv, {"type": "function_call_output", "call_id": "toolu_agent", "output": LAUNCHED})
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})
    await eventually(lambda: shim.app.sessions[sid].execution is None and f"ses_{child}" in shim.app.sessions)
    await fake.connected(child)
    item(fake, child, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "child says"}]})
    await eventually(
        lambda: any(f.get("data", {}).get("text") == "child says" for r in shim.app.sessions[sid].held for f in r.events)
    )
    assert t3.events == [] or all((e.get("data") or {}).get("sessionID") not in (sid, f"ses_{child}") for e in t3.events)

    await trigger_prompt(t3, sid, wake.triggers[0])
    await t3.wait_for(lambda: "session.text.ended" in t3.types(f"ses_{child}"))
    assert t3.types(sid)[0] == "session.execution.started"


async def test_follow_picks_up_followed_sessions_and_lets_quiet_ones_go(
    t3: T3Client, shim: Shim, fake: FakeOmnigent, monkeypatch: Any
) -> None:
    sid, conv = await followed(t3, shim, fake)
    app = shim.app
    await app.sessions.pop(sid).close()
    app.config.watch_ms = 60_000
    task = asyncio.ensure_future(app.follow())
    try:
        await eventually(lambda: sid in app.sessions and app.sessions[sid]._synced.is_set())
        app.store.touch(sid, 0)
        app.sessions[sid].active = 0
    finally:
        task.cancel()
    monkeypatch.setattr("omnigent_opencode.server.SWEEP_S", 0.05)
    task = asyncio.ensure_future(app.follow())
    try:
        await eventually(lambda: sid not in app.sessions)
    finally:
        task.cancel()
    assert app.store.watermark(sid) == "item_old", "a session let go keeps its watermark"
