"""Native subagents as T3 Code's own OpenCode 2 client sees them, replaying a captured claude-native run."""

from __future__ import annotations

import json
from typing import Any

from conftest import Shim, T3Client
from fake_omnigent import FakeOmnigent
from test_t3_turns import assert_all_decoded, confirmed_idle, start_turn

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
