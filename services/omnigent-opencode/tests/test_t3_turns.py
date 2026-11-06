"""Turns as T3 Code's own OpenCode 2 client sees them, over a scripted Omnigent session."""

from __future__ import annotations

import json
from typing import Any

from conftest import Shim, T3Client
from fake_omnigent import FakeOmnigent

CLAUDE = {"providerID": "claude-native-ui", "id": "opus"}


def assert_all_decoded(shim: Shim, t3: T3Client, session_id: str) -> None:
    published = [f["type"] for f in shim.published if (f.get("data") or {}).get("sessionID") == session_id]
    assert t3.types(session_id) == published, "T3 Code dropped events it could not decode"


def consumed(pending_id: str, item_id: str, text: str = "hi") -> dict[str, Any]:
    return {
        "data": {
            "cleared_pending_id": pending_id,
            "type": "message",
            "item_id": item_id,
            "data": {"role": "user", "content": [{"type": "input_text", "text": text}]},
        }
    }


def reply(fake: FakeOmnigent, conv: str, text: str) -> None:
    fake.push(conv, "response.output_text.delta", {"delta": text})
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}},
    )


def confirmed_idle(fake: FakeOmnigent, conv: str) -> None:
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_1"})


async def open_session(t3: T3Client, shim: Shim) -> dict[str, Any]:
    return await t3.call("session.create", {"location": {"directory": shim.app.config.default_directory}, "model": CLAUDE})


async def test_catalog_lists_each_agent_as_a_provider(t3: T3Client) -> None:
    models = await t3.call("model.list", {"location": {"directory": "/tmp"}})
    assert {(m["providerID"], m["id"]) for m in models["data"]} == {("claude-native-ui", "opus"), ("codex-native-ui", "gpt-5")}
    codex = next(m for m in models["data"] if m["providerID"] == "codex-native-ui")
    assert [v["id"] for v in codex["variants"]] == ["high"]
    agents = await t3.call("agent.list", {"location": {"directory": "/tmp"}})
    assert [a["id"] for a in agents["data"]] == ["build", "plan"]


async def test_a_prompt_runs_one_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    assert sid == f"ses_{conv}"

    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    assert fake.only().posted[0]["data"]["content"][0]["text"] == "hi"
    await fake.connected(conv)
    fake.push(conv, "response.completed", {"response": {"id": "resp_ack", "model": "claude-native-ui"}})
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    reply(fake, conv, "Hello")
    confirmed_idle(fake, conv)

    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert t3.types(sid) == [
        "session.execution.started",
        "session.step.started",
        "session.text.started",
        "session.text.delta",
        "session.text.ended",
        "session.step.ended",
        "session.execution.succeeded",
    ]
    ended = next(e for e in t3.of(sid) if e["type"] == "session.text.ended")
    assert ended["data"]["text"] == "Hello"
    assert_all_decoded(shim, t3, sid)
    assert await t3.call("session.active") == {}


async def start_turn(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> tuple[str, str]:
    """A session with one execution running: its prompt posted and consumed."""
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    reply(fake, conv, "Working")
    await t3.wait_for(lambda: "session.text.delta" in t3.types(sid))
    return sid, conv


async def test_a_steer_is_delivered_into_the_running_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    steer = f"msg_t3_steer_{sid}:m2"
    await t3.call("session.prompt", {"sessionID": sid, "id": steer, "text": "also X", "delivery": "steer"})
    assert fake.only().posted[-1]["data"]["content"][0]["text"] == "also X"
    fake.push(conv, "session.input.consumed", consumed("pending_3", "item_3", "also X"))
    reply(fake, conv, "Done")
    confirmed_idle(fake, conv)

    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    types = t3.types(sid)
    assert types.count("session.execution.started") == 1
    assert (
        types.index("session.inbox.enqueued")
        < types.index("session.inbox.delivered")
        < types.index("session.execution.succeeded")
    )
    delivered = next(e for e in t3.of(sid) if e["type"] == "session.inbox.delivered")
    assert delivered["data"]["inboxID"] == steer
    assert [p["type"] for p in fake.only().posted] == ["message", "message"], "a steer must not interrupt"
    assert_all_decoded(shim, t3, sid)


async def test_history_after_a_steer_names_messages_as_the_live_events_did(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    steer = f"msg_t3_steer_{sid}:m2"
    await t3.call("session.prompt", {"sessionID": sid, "id": steer, "text": "also X", "delivery": "steer"})
    fake.push(conv, "session.input.consumed", consumed("pending_3", "item_3", "also X"))
    reply(fake, conv, "Done")
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    live = [e["data"]["assistantMessageID"] for e in t3.of(sid) if e["type"] == "session.text.ended"]
    fake.only().items = [
        {"id": "item_2", "type": "message", "role": "user", "created_at": 2, "content": [{"type": "input_text", "text": "hi"}]},
        {
            "id": "a_1",
            "type": "message",
            "role": "assistant",
            "created_at": 3,
            "content": [{"type": "output_text", "text": "Working"}],
        },
        {
            "id": "item_3",
            "type": "message",
            "role": "user",
            "created_at": 4,
            "content": [{"type": "input_text", "text": "also X"}],
        },
        {
            "id": "a_2",
            "type": "message",
            "role": "assistant",
            "created_at": 5,
            "content": [{"type": "output_text", "text": "Done"}],
        },
    ]
    listed = (await t3.call("message.list", {"sessionID": sid, "limit": 50}))["data"]
    assert [m["type"] for m in listed] == ["user", "assistant", "user", "assistant", "idle"]
    assert [m["id"] for m in listed if m["type"] == "assistant"] == live
    assert listed[2]["id"] == steer


async def test_a_steer_taken_up_after_the_turn_runs_its_own_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    steer = f"msg_t3_steer_{sid}:m2"
    await t3.call("session.prompt", {"sessionID": sid, "id": steer, "text": "also X", "delivery": "steer"})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    # Claude takes up its queued input once the turn is over.
    fake.push(conv, "session.input.consumed", consumed("pending_3", "item_3", "also X"))
    reply(fake, conv, "Now X")
    fake.push(conv, "session.status", {"status": "idle", "response_id": "resp_2"})

    await t3.wait_for(lambda: t3.types(sid).count("session.execution.succeeded") == 2)
    types = t3.types(sid)
    second = types.index("session.execution.started", types.index("session.execution.succeeded"))
    assert types[second : second + 2] == ["session.execution.started", "session.inbox.delivered"]
    assert_all_decoded(shim, t3, sid)


async def test_a_steer_never_taken_up_is_cancelled(t3: T3Client, shim: Shim, fake: FakeOmnigent, monkeypatch: Any) -> None:
    from omnigent_opencode import sessions

    monkeypatch.setattr(sessions, "STEER_DELIVERY_S", 0.2)
    sid, conv = await start_turn(t3, shim, fake)
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_steer_{sid}:m2", "text": "also X", "delivery": "steer"})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.inbox.cancelled" in t3.types(sid))
    assert t3.types(sid).count("session.execution.started") == 1
    assert_all_decoded(shim, t3, sid)


async def test_stop_interrupts_the_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    assert (await t3.call("session.interrupt", {"sessionID": sid}))["interrupted"] is True
    assert fake.only().posted[-1]["type"] == "interrupt"
    fake.push(conv, "session.interrupted", {"data": {"requested_at": 1}})
    await t3.wait_for(lambda: "session.execution.interrupted" in t3.types(sid))
    assert (await t3.call("session.interrupt", {"sessionID": sid}))["interrupted"] is False
    assert_all_decoded(shim, t3, sid)


async def test_stop_unconfirmed_by_omnigent_still_ends_the_execution(
    t3: T3Client, shim: Shim, fake: FakeOmnigent, monkeypatch: Any
) -> None:
    from omnigent_opencode import sessions

    monkeypatch.setattr(sessions, "CANCEL_GRACE_S", 0.2)
    sid, conv = await start_turn(t3, shim, fake)
    await t3.call("session.interrupt", {"sessionID": sid})
    await t3.wait_for(lambda: "session.execution.interrupted" in t3.types(sid))
    # The harness's late output belongs to the stopped turn, not a new one.
    reply(fake, conv, "late")
    fake.push(conv, "session.input.consumed", consumed(None, "item_9", "leftover"))  # type: ignore[arg-type]
    confirmed_idle(fake, conv)
    await t3.call("session.active")
    assert t3.types(sid).count("session.execution.started") == 1


async def test_a_turn_started_outside_t3_runs_as_an_execution_of_its_own(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.get", {"sessionID": sid})
    await shim.app.sessions[sid].connect()
    fake.push(conv, "session.input.consumed", consumed(None, "item_w", "[watcher] CI finished"))  # type: ignore[arg-type]
    reply(fake, conv, "CI is green")
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert t3.types(sid)[0] == "session.execution.started"
    assert_all_decoded(shim, t3, sid)


async def test_tool_calls_are_reported_with_their_output(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": '{"command": "ls"}'}},
    )
    fake.push(
        conv, "response.output_item.done", {"item": {"type": "function_call_output", "call_id": "toolu_1", "output": "a\nb"}}
    )
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    started = next(e for e in t3.of(sid) if e["type"] == "session.tool.input.started")
    assert started["data"]["name"] == "bash"
    success = next(e for e in t3.of(sid) if e["type"] == "session.tool.success")
    assert success["data"]["content"] == [{"type": "text", "text": "a\nb"}]
    assert_all_decoded(shim, t3, sid)


async def test_a_prompt_omnigent_refuses_fails_cleanly(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    sid = session["id"]
    fake.refuse_messages = True
    result = await t3.try_call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    assert result["error"]["tag"] in ("InvalidRequestError", "ClientError")
    await t3.wait_for(lambda: "session.execution.failed" in t3.types(sid))
    assert_all_decoded(shim, t3, sid)


def ask(fake: FakeOmnigent, conv: str, elicitation_id: str, command: str) -> None:
    preview = json.dumps({"name": "Bash", "arguments": {"command": command}})
    fake.push(
        conv,
        "response.elicitation_request",
        {"elicitation_id": elicitation_id, "params": {"message": "Omnigent policy asks", "content_preview": preview}},
    )


async def test_an_omnigent_approval_is_a_permission_request(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    ask(fake, conv, "el_1", "rm -rf build")
    await t3.wait_for(lambda: any(e["type"] == "permission.asked" for e in t3.events))
    asked = next(e for e in t3.events if e["type"] == "permission.asked")
    assert asked["data"]["resources"] == ["rm -rf build", "Omnigent policy asks"]
    assert "save" not in asked["data"]
    listed = await t3.call("permission.list", {"sessionID": sid})
    assert [p["id"] for p in listed] == [asked["data"]["id"]]
    await t3.call("permission.reply", {"sessionID": sid, "requestID": asked["data"]["id"], "decision": "once"})
    assert fake.resolved == [(conv, "el_1", {"action": "accept"})]
    assert await t3.call("permission.list", {"sessionID": sid}) == []


async def test_cancel_on_an_approval_cancels_it_and_stops_the_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    ask(fake, conv, "el_2", "rm -rf /")
    await t3.wait_for(lambda: any(e["type"] == "permission.asked" for e in t3.events))
    request_id = next(e for e in t3.events if e["type"] == "permission.asked")["data"]["id"]
    await t3.call("permission.reply", {"sessionID": sid, "requestID": request_id, "decision": "reject"})
    # Omnigent aborts the turn on a policy decline (a cancel would let it go on).
    assert fake.resolved == [(conv, "el_2", {"action": "decline"})]
    assert fake.only().posted[-1]["type"] == "interrupt"
    fake.push(conv, "session.interrupted", {"data": {}})
    await t3.wait_for(lambda: "session.execution.interrupted" in t3.types(sid))


async def test_history_names_prompts_and_replies_as_the_live_events_did(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "function_call", "call_id": "toolu_1", "name": "Bash", "arguments": '{"command": "ls"}'}},
    )
    fake.push(conv, "response.output_item.done", {"item": {"type": "function_call_output", "call_id": "toolu_1", "output": "a"}})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    fake.only().items = [
        {"id": "res_1", "type": "resource_event", "created_at": 1},
        {"id": "item_2", "type": "message", "role": "user", "created_at": 2, "content": '[{"type": "input_text", "text": "hi"}]'},
        {
            "id": "a_1",
            "type": "message",
            "role": "assistant",
            "created_at": 3,
            "content": '[{"type": "output_text", "text": "Working"}]',
        },
        {
            "id": "f_1",
            "type": "function_call",
            "created_at": 4,
            "call_id": "toolu_1",
            "name": "Bash",
            "arguments": '{"command": "ls"}',
        },
        {"id": "o_1", "type": "function_call_output", "created_at": 5, "call_id": "toolu_1", "output": "a"},
        {
            "id": "u_9",
            "type": "message",
            "role": "user",
            "created_at": 6,
            "content": '[{"type": "input_text", "text": "[watcher] ping"}]',
        },
    ]
    page = await t3.call("message.list", {"sessionID": sid, "limit": 100})
    messages = page["data"]
    assert [m["type"] for m in messages] == ["user", "assistant", "idle", "user"]
    assert messages[0]["id"] == f"msg_t3_turn_{sid}:a1"
    live_assistant = next(e for e in t3.of(sid) if e["type"] == "session.step.started")["data"]["assistantMessageID"]
    assert messages[1]["id"] == live_assistant
    live_tool = next(e for e in t3.of(sid) if e["type"] == "session.tool.input.started")["data"]["id"]
    assert [c["type"] for c in messages[1]["content"]] == ["text", "tool"]
    assert messages[1]["content"][1]["id"] == live_tool
    assert messages[2]["outcome"] == "succeeded"
    newest = await t3.call("message.list", {"sessionID": sid, "limit": 1, "order": "desc"})
    assert [m["type"] for m in newest["data"]] == ["user"] and newest["cursor"]["next"]


def transcript(fake: FakeOmnigent) -> None:
    """Two turns of Omnigent history: the prompt `hi`, then `more`."""
    fake.only().items = [
        {
            "id": "item_2",
            "response_id": "r1",
            "type": "message",
            "role": "user",
            "created_at": 2,
            "content": '[{"type": "input_text", "text": "hi"}]',
        },
        {
            "id": "a_1",
            "response_id": "r2",
            "type": "message",
            "role": "assistant",
            "created_at": 3,
            "content": '[{"type": "output_text", "text": "Working"}]',
        },
        {
            "id": "item_5",
            "response_id": "r3",
            "type": "message",
            "role": "user",
            "created_at": 4,
            "content": '[{"type": "input_text", "text": "more"}]',
        },
        {
            "id": "a_2",
            "response_id": "r4",
            "type": "message",
            "role": "assistant",
            "created_at": 5,
            "content": '[{"type": "output_text", "text": "More"}]',
        },
    ]


async def two_turns(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> tuple[str, str]:
    sid, conv = await start_turn(t3, shim, fake)
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a2", "text": "more"})
    fake.push(conv, "session.input.consumed", consumed("pending_3", "item_5", "more"))
    reply(fake, conv, "More")
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.succeeded") == 2)
    transcript(fake)
    return sid, conv


async def test_a_fork_keeps_history_before_the_boundary(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await two_turns(t3, shim, fake)
    forked = await t3.call("session.fork", {"sessionID": sid, "before": f"msg_t3_turn_{sid}:a2"})
    assert fake.forks == [(conv, {"up_to_response_id": "r2"})]
    assert fake.launched[-1]["workspace"] == shim.app.config.default_directory
    history = (await t3.call("message.list", {"sessionID": forked["id"], "limit": 100}))["data"]
    assert [(m["type"], m.get("id")) for m in history if m["type"] == "user"] == [("user", f"msg_t3_turn_{sid}:a1")]


async def test_rollback_points_the_session_at_a_fork_cut_at_the_boundary(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await two_turns(t3, shim, fake)
    staged = await t3.call("session.revert.stage", {"sessionID": sid, "messageID": f"msg_t3_turn_{sid}:a2", "files": False})
    assert staged["messageID"] == f"msg_t3_turn_{sid}:a2"
    await t3.call("session.revert.commit", {"sessionID": sid})
    assert shim.app.sessions[sid].conversation_id != conv
    history = (await t3.call("message.list", {"sessionID": sid, "limit": 100}))["data"]
    assert [m["type"] for m in history] == ["user", "assistant", "idle"]
    # The session keeps working on its new Omnigent session.
    new_conv = shim.app.sessions[sid].conversation_id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a3", "text": "again"})
    assert fake.sessions[new_conv].posted[-1]["data"]["content"][-1]["text"] == "again"


async def test_clearing_a_revert_runs_an_empty_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    await t3.call("session.revert.clear", {"sessionID": session["id"]})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(session["id"]))


async def test_compact_runs_as_one_execution(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.compact", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:c1"})
    assert fake.only().posted[-1]["type"] == "compact"
    fake.push(conv, "response.compaction.in_progress", {"started_at": 1})
    fake.push(conv, "response.compaction.completed", {"summary": "We said hi."})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert t3.types(sid) == [
        "session.execution.started",
        "session.compaction.started",
        "session.compaction.ended",
        "session.execution.succeeded",
    ]
    assert_all_decoded(shim, t3, sid)


async def test_an_image_attachment_is_sent_inline(t3: T3Client, shim: Shim, fake: FakeOmnigent, tmp_path: Any) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    session = await open_session(t3, shim)
    sid = session["id"]
    await t3.call(
        "session.prompt",
        {
            "sessionID": sid,
            "id": f"msg_t3_turn_{sid}:a1",
            "text": "see image",
            "files": [{"uri": f"file://{image}", "name": "shot.png"}],
        },
    )
    content = fake.only().posted[0]["data"]["content"]
    assert content[0]["type"] == "input_image" and content[0]["image_url"].startswith("data:image/png;base64,")
    assert content[-1] == {"type": "input_text", "text": "see image"}


async def test_a_reply_committed_after_its_turn_ended_is_not_shown_again(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    # The Stop hook confirms the turn before the forwarder commits the streamed reply.
    fake.push(conv, "response.output_text.delta", {"delta": "Working"})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    fake.push(conv, "session.input.consumed", consumed(None, "item_9", "[System: scheduled prompt fired]"))  # type: ignore[arg-type]
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Working"}]}},
    )
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Done"}]}},
    )
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.succeeded") == 2)
    wake = t3.of(sid)[[e["type"] for e in t3.of(sid)].index("session.execution.succeeded") + 1 :]
    assert [e["data"]["text"] for e in wake if e["type"] == "session.text.ended"] == ["Done"]


async def test_each_committed_reply_is_its_own_text(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Second"}]}},
    )
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    assert [e["data"]["text"] for e in t3.of(sid) if e["type"] == "session.text.ended"] == ["Working", "Second"]


async def stream_without_commit(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> tuple[str, str]:
    """A turn whose reply streamed but whose Stop hook came before the reply's item."""
    session = await open_session(t3, shim)
    sid = session["id"]
    conv = fake.only().id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    fake.push(conv, "response.output_text.delta", {"delta": "Working"})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    return sid, conv


def message(text: str) -> dict[str, Any]:
    return {"item": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}}


async def test_a_wake_marker_over_a_late_reply_is_no_turn(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await stream_without_commit(t3, shim, fake)
    # claude-native's forwarder took the late reply for a wake and put its marker before it.
    fake.push(conv, "session.input.consumed", consumed(None, "item_9", "[System: scheduled prompt fired]"))  # type: ignore[arg-type]
    fake.push(conv, "response.output_item.done", message("Working"))
    confirmed_idle(fake, conv)
    await t3.call("session.get", {"sessionID": sid})
    assert shim.app.sessions[sid].execution is None
    assert t3.types(sid).count("session.execution.started") == 1


async def test_a_real_scheduled_wake_is_a_turn(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await stream_without_commit(t3, shim, fake)
    fake.push(conv, "session.input.consumed", consumed(None, "item_9", "[System: scheduled prompt fired]"))  # type: ignore[arg-type]
    fake.push(conv, "response.output_item.done", message("Working"))
    fake.push(conv, "response.output_item.done", message("Checked again: done"))
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.succeeded") == 2)
    wake = t3.of(sid)[[e["type"] for e in t3.of(sid)].index("session.execution.succeeded") + 1 :]
    assert [e["data"]["text"] for e in wake if e["type"] == "session.text.ended"] == ["Checked again: done"]


async def test_an_edit_is_a_file_change_with_its_diff(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    arguments = {"file_path": "src/a.py", "old_string": "x = 1\n", "new_string": "x = 2\n"}
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "function_call", "call_id": "toolu_e", "name": "Edit", "arguments": json.dumps(arguments)}},
    )
    fake.push(conv, "response.output_item.done", {"item": {"type": "function_call_output", "call_id": "toolu_e", "output": "ok"}})
    await t3.wait_for(lambda: "session.tool.success" in t3.types(sid))
    path = f"{shim.app.config.default_directory}/src/a.py"
    called = next(e for e in t3.of(sid) if e["type"] == "session.tool.called")["data"]["input"]
    assert (called["filePath"], called["oldString"], called["newString"]) == (path, "x = 1\n", "x = 2\n")
    diff = next(e for e in t3.of(sid) if e["type"] == "session.tool.success")["data"]["metadata"]["diff"]
    assert f"--- a/{path.lstrip('/')}" in diff and "-x = 1\n" in diff and "+x = 2\n" in diff
    assert_all_decoded(shim, t3, sid)


async def test_a_codex_patch_is_a_file_change_with_its_diff(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    changes = [
        {"path": "/repo/new.txt", "kind": {"type": "add"}, "diff": "hello\n"},
        {"path": "/repo/a.py", "kind": {"type": "update"}, "diff": "@@ -1 +1 @@\n-x = 1\n+x = 2\n"},
    ]
    fake.push(
        conv,
        "response.output_item.done",
        {
            "item": {
                "type": "function_call",
                "call_id": "call_p",
                "name": "apply_patch",
                "arguments": json.dumps({"changes": changes}),
            }
        },
    )
    fake.push(
        conv, "response.output_item.done", {"item": {"type": "function_call_output", "call_id": "call_p", "output": "Done"}}
    )
    await t3.wait_for(lambda: "session.tool.success" in t3.types(sid))
    started = next(e for e in t3.of(sid) if e["type"] == "session.tool.input.started")["data"]
    assert started["name"] == "patch"
    called = next(e for e in t3.of(sid) if e["type"] == "session.tool.called")["data"]["input"]
    assert called["filePath"] == "/repo/new.txt"
    diff = next(e for e in t3.of(sid) if e["type"] == "session.tool.success")["data"]["metadata"]["diff"]
    assert "+hello\n" in diff and "--- a/repo/a.py" in diff and "+x = 2\n" in diff
