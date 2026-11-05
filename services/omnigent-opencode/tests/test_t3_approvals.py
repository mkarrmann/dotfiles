"""Approvals, rules, forms and permission modes as T3 Code's own OpenCode 2 client sees them."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import Shim, T3Client
from fake_omnigent import FakeOmnigent
from test_t3_turns import assert_all_decoded, consumed, reply

from omnigent_opencode.approvals import T3_POLICY, T3_POLICY_HANDLER, matches

CLAUDE = {"providerID": "claude-native-ui", "id": "opus"}
CODEX = {"providerID": "codex-native-ui", "id": "gpt-5"}


def rule(action: str, effect: str, resource: str = "*") -> dict[str, str]:
    return {"action": action, "resource": resource, "effect": effect}


# The rule sets T3 Code's OpenCode driver sends for each runtime mode (adapter.ts sessionRules).
FULL_ACCESS = [rule("*", "allow")]
APPROVAL_REQUIRED = [rule("shell", "ask"), rule("edit", "ask"), rule("external_directory", "ask")]
AUTO_ACCEPT_EDITS = [rule("shell", "ask"), rule("edit", "allow"), rule("external_directory", "ask")]


async def start(
    t3: T3Client, shim: Shim, fake: FakeOmnigent, permissions: list[dict[str, str]], model: dict[str, str] = CLAUDE
) -> tuple[str, str]:
    session = await t3.call(
        "session.create",
        {"location": {"directory": shim.app.config.default_directory}, "model": model, "permissions": permissions},
    )
    sid = session["id"]
    conv = next(reversed(fake.sessions))
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    await fake.connected(conv)
    execution = shim.app.sessions[sid].execution
    assert execution is not None
    fake.push(conv, "session.input.consumed", consumed(str(execution.tracker.expected_pending_id), "item_x"))
    reply(fake, conv, "Working")
    await t3.wait_for(lambda: "session.text.delta" in t3.types(sid))
    return sid, conv


def claude_asks(fake: FakeOmnigent, conv: str, elicitation_id: str, tool: str, tool_input: dict[str, Any], **extra: Any) -> None:
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": elicitation_id,
            "params": {
                "message": f"Claude Code wants to call **{tool}**",
                "phase": "pre_tool_use",
                "policy_name": "claude_native_permission",
                "content_preview": f"{tool}({json.dumps(tool_input)})",
                "tool_name": tool,
                **extra,
            },
        },
    )


def policy_asks(fake: FakeOmnigent, conv: str, elicitation_id: str, command: str) -> None:
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": elicitation_id,
            "params": {
                "message": "New environments need approval",
                "phase": "tool_call",
                "policy_name": "no_new_environments",
                "requestedSchema": {},
                "content_preview": json.dumps({"name": "Bash", "arguments": {"command": command}}),
            },
        },
    )


def asked(t3: T3Client) -> list[dict[str, Any]]:
    return [e["data"] for e in t3.events if e["type"] == "permission.asked"]


async def test_full_access_answers_the_harness_but_never_an_omnigent_policy(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, FULL_ACCESS)
    claude_asks(fake, conv, "el_1", "Bash", {"command": "rm -rf build"})
    assert await fake.resolutions(1) == [(conv, "el_1", {"action": "accept"})]
    policy_asks(fake, conv, "el_2", "docker run alpine")
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    (request,) = asked(t3)
    assert request["resources"] == ["docker run alpine", "New environments need approval"]
    assert len(fake.resolved) == 1
    await t3.call("permission.reply", {"sessionID": sid, "requestID": request["id"], "decision": "once"})
    assert fake.resolved[-1] == (conv, "el_2", {"action": "accept"})
    await t3.wait_for(lambda: "permission.replied" in [e["type"] for e in t3.events])
    assert_all_decoded(shim, t3, sid)


async def test_a_claude_prompt_shows_as_its_kind_of_request(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED)
    claude_asks(fake, conv, "el_1", "Bash", {"command": "rm -rf build", "description": "clean"})
    claude_asks(fake, conv, "el_2", "Edit", {"file_path": "/repo/a.py", "old_string": "a", "new_string": "b"})
    await t3.wait_for(lambda: len(asked(t3)) == 2)
    shell, edit = asked(t3)
    assert (shell["action"], shell["resources"], shell["save"]) == ("shell", ["rm -rf build"], ["rm *"])
    assert (edit["action"], edit["resources"], edit["save"]) == ("edit", ["/repo/a.py"], ["*"])
    assert fake.resolved == []
    assert_all_decoded(shim, t3, sid)


async def test_auto_accept_edits_allows_edits_and_asks_for_commands(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, AUTO_ACCEPT_EDITS)
    claude_asks(fake, conv, "el_1", "Write", {"file_path": f"{shim.app.config.default_directory}/new.py", "content": "x"})
    assert await fake.resolutions(1) == [(conv, "el_1", {"action": "accept"})]
    claude_asks(fake, conv, "el_2", "Bash", {"command": "make"})
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    assert len(fake.resolved) == 1


async def test_allow_for_session_answers_later_matching_requests(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED)
    claude_asks(fake, conv, "el_1", "Bash", {"command": "rm -rf build"})
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    (request,) = asked(t3)
    # What T3 Code does for "Allow ... this session": grant the save patterns, then approve once.
    grants = [rule(request["action"], "allow", pattern) for pattern in request["save"]]
    await t3.call("session.update", {"sessionID": sid, "permissions": [*APPROVAL_REQUIRED, *grants]})
    await t3.call("permission.reply", {"sessionID": sid, "requestID": request["id"], "decision": "once"})
    claude_asks(fake, conv, "el_2", "Bash", {"command": "rm stale.log"})
    await fake.resolutions(2)
    assert [r[2] for r in fake.resolved] == [{"action": "accept"}, {"action": "accept"}]
    claude_asks(fake, conv, "el_3", "Bash", {"command": "git push"})
    await t3.wait_for(lambda: len(asked(t3)) == 2)
    assert len(fake.resolved) == 2


async def test_decline_gives_claude_t3s_reason_and_lets_the_turn_go_on(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED)
    claude_asks(fake, conv, "el_1", "Bash", {"command": "rm -rf build"})
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    (request,) = asked(t3)
    note = (
        "The user declined the shell request (rm -rf build). Do not retry it; continue without it or ask the user how to proceed."
    )
    await t3.call("session.synthetic", {"sessionID": sid, "text": note, "delivery": "steer", "resume": False})
    await t3.call(
        "permission.reply",
        {"sessionID": sid, "requestID": request["id"], "decision": "reject", "message": "The user declined this request."},
    )
    assert fake.resolved == [(conv, "el_1", {"action": "decline", "content": {"feedback": note}})]
    assert not [p for p in fake.sessions[conv].posted if p["type"] == "interrupt"]
    assert shim.app.sessions[sid].running


async def test_decline_on_a_policy_ask_denies_the_call_and_the_turn_goes_on(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED)
    t3_policy_asks(fake, conv, "el_1", "Bash", {"command": "rm -rf build"})
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    (request,) = asked(t3)
    await t3.call(
        "permission.reply",
        {"sessionID": sid, "requestID": request["id"], "decision": "reject", "message": "The user declined this request."},
    )
    assert fake.resolved == [(conv, "el_1", {"action": "cancel"})]
    assert not [p for p in fake.sessions[conv].posted if p["type"] == "interrupt"]
    assert shim.app.sessions[sid].running


async def test_plan_mode_denies_edits_the_harness_asks_about(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, [*APPROVAL_REQUIRED, rule("edit", "deny")])
    claude_asks(fake, conv, "el_1", "Edit", {"file_path": "/repo/a.py"})
    ((_, _, body),) = await fake.resolutions(1)
    assert body["action"] == "decline" and "permission rules" in body["content"]["feedback"]
    assert asked(t3) == []


async def test_exit_plan_mode_is_refused_while_t3_is_in_plan_mode(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await t3.call(
        "session.create",
        {"location": {"directory": shim.app.config.default_directory}, "model": CLAUDE, "permissions": FULL_ACCESS},
    )
    sid = session["id"]
    await t3.call("session.switchAgent", {"sessionID": sid, "agent": "plan"})
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "plan it"})
    conv = fake.only().id
    await fake.connected(conv)
    claude_asks(fake, conv, "el_1", "ExitPlanMode", {"plan": "1. do it"}, exit_plan_mode={"plan": "1. do it"})
    ((_, _, body),) = await fake.resolutions(1)
    assert body["action"] == "decline" and "ExitPlanMode" in body["content"]["feedback"]


async def test_claude_questions_are_forms_answered_by_question_text(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, FULL_ACCESS)
    questions = {
        "questions": [
            {
                "question": "Which database?",
                "header": "DB",
                "multiSelect": False,
                "options": [{"label": "Postgres"}, {"label": "SQLite"}],
            },
            {"question": "Which checks?", "multiSelect": True, "options": [{"label": "lint"}, {"label": "test"}]},
        ]
    }
    claude_asks(fake, conv, "el_1", "AskUserQuestion", questions, ask_user_question=questions)
    await t3.wait_for(lambda: "form.created" in [e["type"] for e in t3.events])
    form = next(e for e in t3.events if e["type"] == "form.created")["data"]["form"]
    assert [(f["key"], f["type"]) for f in form["fields"]] == [("q1", "string"), ("q2", "multiselect")]
    assert [f["id"] for f in await t3.call("session.form.list", {"sessionID": sid})] == [form["id"]]
    await t3.call(
        "session.form.reply", {"sessionID": sid, "formID": form["id"], "answer": {"q1": "SQLite", "q2": ["lint", "test"]}}
    )
    assert fake.resolved == [
        (conv, "el_1", {"action": "accept", "content": {"Which database?": "SQLite", "Which checks?": ["lint", "test"]}})
    ]
    assert await t3.call("session.form.list", {"sessionID": sid}) == []
    assert_all_decoded(shim, t3, sid)


async def test_a_dismissed_codex_question_is_declined(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, FULL_ACCESS, CODEX)
    questions = {"questions": [{"id": "env", "question": "Which env?", "multiSelect": False, "options": [{"label": "dev"}]}]}
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": "el_1",
            "params": {
                "message": "Codex needs input",
                "phase": "codex_request_user_input",
                "codex_method": "item/tool/requestUserInput",
                "ask_user_question": questions,
            },
        },
    )
    await t3.wait_for(lambda: "form.created" in [e["type"] for e in t3.events])
    form = next(e for e in t3.events if e["type"] == "form.created")["data"]["form"]
    assert form["fields"][0]["key"] == "env"
    await t3.call("session.form.cancel", {"sessionID": sid, "formID": form["id"]})
    assert fake.resolved == [(conv, "el_1", {"action": "cancel"})]


async def test_a_codex_command_links_to_its_tool_call(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED, CODEX)
    fake.push(
        conv,
        "response.output_item.done",
        {"item": {"type": "function_call", "call_id": "call_7", "name": "exec_command", "arguments": '{"cmd": "make"}'}},
    )
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": "el_1",
            "params": {
                "message": "Codex wants to run **make deploy**",
                "phase": "codex_command_approval",
                "policy_name": "codex_native_command_approval",
                "codex_method": "item/commandExecution/requestApproval",
                "command": "make deploy",
                "call_id": "call_7",
            },
        },
    )
    await t3.wait_for(lambda: len(asked(t3)) == 1)
    (request,) = asked(t3)
    tool = next(e for e in t3.of(sid) if e["type"] == "session.tool.input.started")
    assert (request["action"], request["resources"], request["save"]) == ("shell", ["make deploy"], ["make *"])
    assert request["source"] == {"type": "tool", "messageID": tool["data"]["assistantMessageID"], "id": tool["data"]["id"]}


def t3_policy_asks(fake: FakeOmnigent, conv: str, elicitation_id: str, tool: str, arguments: dict[str, Any]) -> None:
    fake.push(
        conv,
        "response.elicitation_request",
        {
            "elicitation_id": elicitation_id,
            "params": {
                "message": f"Agent wants to call {tool}. Approve?",
                "phase": "tool_call",
                "policy_name": T3_POLICY,
                "requestedSchema": {},
                "content_preview": json.dumps({"name": tool, "arguments": arguments}),
            },
        },
    )


async def test_a_thread_not_in_full_access_gets_the_asking_policy(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, APPROVAL_REQUIRED)
    (policy,) = fake.sessions[conv].policies
    assert (policy["name"], policy["handler"], policy["enabled"]) == (T3_POLICY, T3_POLICY_HANDLER, True)
    await t3.call("session.update", {"sessionID": sid, "permissions": FULL_ACCESS})
    await t3.wait_for(lambda: True)
    for _ in range(50):
        if not policy["enabled"]:
            break
        await asyncio.sleep(0.02)
    assert policy["enabled"] is False


async def test_full_access_adds_no_policy_until_plan_mode(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    session = await t3.call(
        "session.create",
        {"location": {"directory": shim.app.config.default_directory}, "model": CLAUDE, "permissions": FULL_ACCESS},
    )
    sid = session["id"]
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a1", "text": "hi"})
    assert fake.only().policies == []
    await shim.app.sessions[sid].interrupt()
    fake.push(fake.only().id, "session.interrupted", {"data": {}})
    await t3.wait_for(lambda: "session.execution.interrupted" in t3.types(sid))
    await t3.call("session.switchAgent", {"sessionID": sid, "agent": "plan"})
    await t3.call("session.prompt", {"sessionID": sid, "id": f"msg_t3_turn_{sid}:a2", "text": "plan it"})
    assert [p["enabled"] for p in fake.only().policies] == [True]


async def test_the_policy_asks_under_the_threads_rules(t3: T3Client, shim: Shim, fake: FakeOmnigent) -> None:
    sid, conv = await start(t3, shim, fake, AUTO_ACCEPT_EDITS)
    directory = shim.app.config.default_directory
    t3_policy_asks(fake, conv, "el_1", "Read", {"file_path": f"{directory}/a.py"})
    t3_policy_asks(fake, conv, "el_2", "Edit", {"file_path": f"{directory}/a.py"})
    assert [r[2] for r in await fake.resolutions(2)] == [{"action": "accept"}, {"action": "accept"}]
    t3_policy_asks(fake, conv, "el_3", "Bash", {"command": "git push origin main"})
    t3_policy_asks(fake, conv, "el_4", "Read", {"file_path": "/etc/hosts"})
    await t3.wait_for(lambda: len(asked(t3)) == 2)
    shell, outside = asked(t3)
    assert (shell["action"], shell["resources"], shell["save"]) == ("shell", ["git push origin main"], ["git *"])
    assert (outside["action"], outside["resources"]) == ("read", ["/etc/hosts"])
    assert len(fake.resolved) == 2
    assert_all_decoded(shim, t3, sid)


@pytest.mark.parametrize(
    ("glob", "value", "expected"),
    [
        ("*", "anything at all", True),
        ("rm *", "rm", True),
        ("rm *", "rm -rf build", True),
        ("rm *", "rmdir x", False),
        ("/repo/*", "/repo/a/b.py", True),
        ("edit", "shell", False),
    ],
)
def test_rule_patterns_match_as_opencode_does(glob: str, value: str, expected: bool) -> None:
    assert matches(glob, value) is expected
