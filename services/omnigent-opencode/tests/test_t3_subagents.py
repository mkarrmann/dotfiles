"""Native subagents as T3 Code's own OpenCode 2 client sees them, replaying a captured claude-native run."""

from __future__ import annotations

import json
from typing import Any

from conftest import Shim, T3Client
from fake_omnigent import FakeOmnigent
from test_t3_turns import assert_all_decoded, confirmed_idle, consumed, create_for_thread, start_turn, thread_rules

LAUNCHED = json.dumps([{"type": "text", "text": "Async agent launched successfully.\nagentId: a4e0 (internal ID)"}])
NOTIFICATION = """<task-notification>
<task-id>a4e0</task-id>
<tool-use-id>toolu_agent</tool-use-id>
<status>completed</status>
<summary>Agent "Run echo command" finished</summary>
<result>SUBAGENT_OK</result>
</task-notification>"""


def item(fake: FakeOmnigent, conv: str, body: dict[str, Any]) -> None:
    fake.push(conv, "response.output_item.done", {"item": body})


def spawn(fake: FakeOmnigent, conv: str) -> str:
    item(
        fake,
        conv,
        {
            "type": "function_call",
            "call_id": "toolu_agent",
            "name": "Agent",
            "arguments": json.dumps({"description": "Run echo command", "subagent_type": "general-purpose", "prompt": "echo it"}),
        },
    )
    child = fake.add_child(
        {"omnigent.claude_native.tool_use_id": "toolu_agent", "omnigent.claude_native.description": "Run echo command"}
    )
    fake.push(conv, "session.created", {"conversation_id": conv, "child_session_id": child, "parent_session_id": conv})
    return child


async def test_a_background_subagent_is_a_child_session_whose_report_wakes_the_parent(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    child = spawn(fake, conv)
    child_sid = f"ses_{child}"
    item(fake, conv, {"type": "function_call_output", "call_id": "toolu_agent", "output": LAUNCHED})
    await t3.wait_for(lambda: "session.tool.success" in t3.types(sid))
    item(fake, conv, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Waiting."}]})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))

    await fake.connected(child)
    item(
        fake,
        child,
        {"type": "function_call", "call_id": "toolu_bash", "name": "Bash", "arguments": '{"command": "echo SUBAGENT_OK"}'},
    )
    item(fake, child, {"type": "function_call_output", "call_id": "toolu_bash", "output": "SUBAGENT_OK"})
    item(fake, child, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "SUBAGENT_OK"}]})
    # The child's stream and the parent's are separate; Claude reports only after the child's last message.
    await t3.wait_for(lambda: "session.text.ended" in t3.types(child_sid))
    fake.push(
        conv,
        "session.input.consumed",
        {
            "data": {
                "item_id": "item_note",
                "type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": NOTIFICATION}], "is_meta": True},
                "cleared_pending_id": None,
            }
        },
    )
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.started") == 2)
    fake.push(child, "session.status", {"status": "idle", "response_id": None})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(child_sid))

    started = next(e for e in t3.of(sid) if e["type"] == "session.tool.input.started")
    assert started["data"]["name"] == "subagent"
    called = next(e for e in t3.of(sid) if e["type"] == "session.tool.called")
    assert called["data"]["input"] == {"prompt": "echo it", "description": "Run echo command", "agent": "general-purpose"}
    created = next(e for e in t3.events if e["type"] == "session.created")
    assert (created["data"]["sessionID"], created["data"]["parentID"]) == (child_sid, sid)
    progress = next(e for e in t3.of(sid) if e["type"] == "session.tool.progress")
    assert progress["data"]["metadata"] == {"sessionID": child_sid}
    success = next(e for e in t3.of(sid) if e["type"] == "session.tool.success")
    assert success["data"]["metadata"] == {"sessionID": child_sid, "status": "running"}

    assert t3.types(child_sid)[:4] == [
        "session.created",
        "session.inbox.enqueued",
        "session.execution.started",
        "session.step.started",
    ]
    assert "session.tool.success" in t3.types(child_sid)
    assert t3.types(child_sid)[-1] == "session.execution.succeeded"

    report = next(e for e in t3.of(sid) if e["type"] == "session.inbox.enqueued" and e["data"]["item"]["type"] == "synthetic")
    assert report["data"]["item"]["payload"]["text"] == "SUBAGENT_OK"
    assert report["data"]["item"]["payload"]["metadata"]["childID"] == child_sid
    types = t3.types(sid)
    assert types.index("session.inbox.enqueued") < len(types) - 1 - types[::-1].index("session.execution.started")
    assert_all_decoded(shim, t3, sid)
    assert_all_decoded(shim, t3, child_sid)
    info = await t3.call("session.get", {"sessionID": child_sid})
    assert info["parentID"] == sid


async def test_a_foreground_subagent_returns_its_result(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    child = spawn(fake, conv)
    await t3.wait_for(lambda: "session.tool.progress" in t3.types(sid))
    item(fake, conv, {"type": "function_call_output", "call_id": "toolu_agent", "output": "SUBAGENT_OK"})
    await t3.wait_for(lambda: "session.tool.success" in t3.types(sid))
    success = next(e for e in t3.of(sid) if e["type"] == "session.tool.success")
    assert success["data"]["metadata"] == {"sessionID": f"ses_{child}"}
    assert success["data"]["content"] == [{"type": "text", "text": "SUBAGENT_OK"}]


async def test_a_childs_approval_shows_in_the_childs_session(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    child = spawn(fake, conv)
    await t3.wait_for(lambda: "session.tool.progress" in t3.types(sid))
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": "el_child",
            "params": {
                "message": "Gate",
                "policy_name": "no_new_environments",
                "target_session_id": child,
                "content_preview": "{}",
            },
        },
    )
    await t3.wait_for(lambda: any(e["type"] == "permission.asked" for e in t3.events))
    asked = next(e for e in t3.events if e["type"] == "permission.asked")["data"]
    assert asked["sessionID"] == f"ses_{child}"
    await t3.call("permission.reply", {"sessionID": f"ses_{child}", "requestID": asked["id"], "decision": "once"})
    assert fake.resolved == [(child, "el_child", {"action": "accept"})]


async def test_a_childs_output_after_its_report_is_kept(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start_turn(t3, shim, fake)
    child = spawn(fake, conv)
    child_sid = f"ses_{child}"
    item(fake, conv, {"type": "function_call_output", "call_id": "toolu_agent", "output": LAUNCHED})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))
    await fake.connected(child)
    # The parent hears of the result before the child's stream delivers its last message.
    fake.push(
        conv,
        "session.input.consumed",
        {
            "data": {
                "item_id": "item_note",
                "type": "message",
                "data": {"role": "user", "content": [{"type": "input_text", "text": NOTIFICATION}]},
                "cleared_pending_id": None,
            }
        },
    )
    await t3.wait_for(lambda: t3.types(sid).count("session.execution.started") == 2)
    item(fake, child, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "SUBAGENT_OK"}]})
    fake.push(child, "session.status", {"status": "idle", "response_id": None})
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(child_sid))
    assert [e["data"]["text"] for e in t3.of(child_sid) if e["type"] == "session.text.ended"] == ["SUBAGENT_OK"]


async def test_a_childs_rules_do_not_tie_the_parents_thread_to_the_child(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await create_for_thread(t3, shim, "th_parent")
    sid, conv = session["id"], fake.only().id
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    fake.push(conv, "session.input.consumed", consumed("pending_2", "item_2"))
    child = spawn(fake, conv)
    await t3.wait_for(lambda: f"ses_{child}" in shim.app.sessions)
    # T3 Code writes a child's rules, here for a switch out of full access, with its root thread's MCP scoping.
    asking = [{"action": a, "resource": "*", "effect": "ask"} for a in ("shell", "edit", "external_directory")]
    await t3.call("session.update", {"sessionID": f"ses_{child}", "permissions": [*asking, *thread_rules("th_parent")[1:]]})
    assert (await create_for_thread(t3, shim, "th_parent"))["id"] == sid


def report_of(call_id: str, task_id: str, description: str) -> str:
    return f"""<task-notification>
<task-id>{task_id}</task-id>
<tool-use-id>{call_id}</tool-use-id>
<status>completed</status>
<summary>Agent "{description}" finished</summary>
<result>{description} DONE</result>
</task-notification>"""


async def test_background_subagents_whose_sessions_come_before_their_calls_still_report(
    t3: T3Client, shim: Shim, fake: FakeOmnigent
) -> None:
    """Claude launched three in one message: the harness named two children from their own transcripts before the
    parent's transcript gave their calls, and the third after (a real run's order). Each call settles on its own child."""
    sid, conv = await start_turn(t3, shim, fake)
    calls = {"toolu_a": "Arrange", "toolu_b": "Dashboard", "toolu_c": "Orchest"}

    def child_of(call_id: str) -> str:
        child = fake.add_child(
            {"omnigent.claude_native.tool_use_id": call_id, "omnigent.claude_native.description": calls[call_id]}
        )
        fake.push(conv, "session.created", {"conversation_id": conv, "child_session_id": child, "parent_session_id": conv})
        return child

    children = {"toolu_a": child_of("toolu_a"), "toolu_b": child_of("toolu_b")}
    await t3.wait_for(lambda: all(f"ses_{c}" in shim.app.sessions for c in children.values()))
    for call_id, description in calls.items():
        arguments = {
            "description": description,
            "subagent_type": "fork",
            "prompt": f"do {description}",
            "run_in_background": True,
        }
        item(fake, conv, {"type": "function_call", "call_id": call_id, "name": "Agent", "arguments": json.dumps(arguments)})
    for call_id in calls:
        item(fake, conv, {"type": "function_call_output", "call_id": call_id, "output": LAUNCHED})
    children["toolu_c"] = child_of("toolu_c")
    await t3.wait_for(lambda: t3.types(sid).count("session.tool.progress") == 3)
    item(fake, conv, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Waiting."}]})
    confirmed_idle(fake, conv)
    await t3.wait_for(lambda: "session.execution.succeeded" in t3.types(sid))

    tool_of = {e["data"]["input"]["description"]: e["data"]["id"] for e in t3.of(sid) if e["type"] == "session.tool.called"}
    linked = {e["data"]["id"]: e["data"]["metadata"]["sessionID"] for e in t3.of(sid) if e["type"] == "session.tool.progress"}
    assert {d: linked[tool_of[d]] for d in calls.values()} == {calls[c]: f"ses_{children[c]}" for c in calls}
    running = {e["data"]["id"]: e["data"]["metadata"] for e in t3.of(sid) if e["type"] == "session.tool.success"}
    assert {d: running[tool_of[d]] for d in calls.values()} == {
        calls[c]: {"sessionID": f"ses_{children[c]}", "status": "running"} for c in calls
    }

    for n, call_id in enumerate(calls):
        fake.push(
            conv,
            "session.input.consumed",
            {
                "data": {
                    "item_id": f"item_note_{n}",
                    "type": "message",
                    "data": {
                        "role": "user",
                        "content": [{"type": "input_text", "text": report_of(call_id, f"task{n}", calls[call_id])}],
                    },
                    "cleared_pending_id": None,
                }
            },
        )

    def reports() -> dict[str, str]:
        return {
            e["data"]["item"]["payload"]["metadata"]["childID"]: e["data"]["item"]["payload"]["text"]
            for e in t3.of(sid)
            if e["type"] == "session.inbox.enqueued" and e["data"]["item"]["type"] == "synthetic"
        }

    await t3.wait_for(lambda: len(reports()) == 3)
    assert reports() == {f"ses_{children[c]}": f"{calls[c]} DONE" for c in calls}
    assert_all_decoded(shim, t3, sid)
